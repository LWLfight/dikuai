import geopandas as gpd
import os
import pandas as pd

# 获取脚本所在目录作为基准目录
base_dir = os.path.dirname(os.path.abspath(__file__))
print(f"脚本所在目录：{base_dir}")

# 定义路径（相对于脚本所在目录）
city = 'guangzhou'
output_dir = os.path.join(base_dir, f'{city}.output')
cleaned_path = os.path.join(output_dir, 'cleaned_not_divided.shp')
poi_path = os.path.join(base_dir, city, 'test_POI.shp')  # POI 是文件不是目录
classified_path = os.path.join(output_dir, '赋值后的地块.shp')  # 输出文件

# 验证输入文件是否存在
if not os.path.exists(cleaned_path):
    print(f"错误：找不到文件 {cleaned_path}")
    print(f"当前工作目录：{os.getcwd()}")
    if os.path.exists(output_dir):
        print(f"输出目录内容：{os.listdir(output_dir)}")
    else:
        print(f"输出目录不存在：{output_dir}")
    
    # 尝试在父目录查找
    parent_output_dir = os.path.join(os.path.dirname(base_dir), f'{city}.output')
    if os.path.exists(parent_output_dir):
        print(f"提示：在父目录找到输出目录 {parent_output_dir}")
    exit(1)

if not os.path.exists(poi_path):
    print(f"错误：找不到 POI 文件 {poi_path}")
    exit(1)

print(f"读取地块数据：{cleaned_path}")
print(f"读取 POI 数据：{poi_path}")

# 读取 cleaned_not_divided.shp
parcels = gpd.read_file(cleaned_path)

# 读取 POI 数据（单个文件）
pois = gpd.read_file(poi_path)

# 统一 CRS 到 CGCS2000 (EPSG:4490)
target_crs = 'EPSG:4490'
parcels = parcels.to_crs(target_crs)
pois = pois.to_crs(target_crs)

# 添加 'class' 属性基于 type
def assign_class(type):
    if not isinstance(type, str):
        return '其他'
    
    # 使用包含匹配：只要 type 字符串中包含指定字符即可
    if '生活服务' in type or '餐饮服务' in type or '体育休闲服务' in type:
        return '居住用地'
    elif '体育休闲服务' in type or 'N' in type or 'L' in type:
        return '工业用地'
    elif '医疗保健服务' in type or 'p' in type or 'Q' in type:
        return '交通用地'
    elif '政府机构' in type or 'B' in type or 'W' in type:
        return '绿地'
    else:
        return '其他'

pois['class'] = pois['type'].apply(assign_class)

# 使用 spatial join 计算每个 parcel 内 POI 的 class 分布
print(f"POI 数据要素数量：{len(pois)}")
print(f"地块数据要素数量：{len(parcels)}")
print(f"POI 数据字段：{pois.columns.tolist()}")
print(f"地块数据字段：{parcels.columns.tolist()}")

# 确保 parcels 有唯一标识符
if 'fid' not in parcels.columns:
    parcels = parcels.reset_index(drop=True)
    parcels['fid'] = parcels.index + 1

joined = gpd.sjoin(pois, parcels, how='inner', predicate='within')

print(f"空间连接后要素数量：{len(joined)}")
print(f"连接后字段：{joined.columns.tolist()}")

# 确定用于分组的列名
group_col = 'index_right' if 'index_right' in joined.columns else 'fid'
print(f"使用分组列：{group_col}")

# 按 parcel index 和 class 分组计数
class_counts = joined.groupby([group_col, 'class']).size().reset_index(name='count')

# 为每个 parcel 计算总 POI 数
total_counts = class_counts.groupby(group_col)['count'].sum().reset_index(name='total')

# 合并并计算占比
class_counts = class_counts.merge(total_counts, on=group_col)
class_counts['proportion'] = class_counts['count'] / class_counts['total']

# 为每个 parcel 找到占比最多的 class（主导类别）
def get_dominant_class(group):
    if len(group) == 0:
        return '未知'
    
    # 排序占比降序
    sorted_group = group.sort_values('proportion', ascending=False)
    max_prop = sorted_group.iloc[0]['proportion']
    max_class = sorted_group.iloc[0]['class']
    
    # 如果只有两种 class
    if len(group) == 2:
        if max_prop > 0.5:
            return max_class
        else:
            return '未知'  # 或其他处理，如果平分或都不超过 50%
    else:
        return max_class  # 一般情况，取最多

dominant_classes = class_counts.groupby(group_col).apply(get_dominant_class, include_groups=False).reset_index(name='类别')

# 为每个 parcel 统计各类别 POI 数量
poi_counts = class_counts.pivot_table(index=group_col, columns='class', values='count', fill_value=0).reset_index()

# 确保所有类别列都存在
required_columns = ['居住用地', '工业用地', '交通用地', '绿地', '其他']
for col in required_columns:
    if col not in poi_counts.columns:
        poi_counts[col] = 0

# 重命名 POI 数量列
poi_counts = poi_counts.rename(columns={
    '居住用地': 'POI_居住',
    '工业用地': 'POI_工业',
    '交通用地': 'POI_交通',
    '绿地': 'POI_绿地',
    '其他': 'POI_其他'
})

# 合并主导类别和 POI 数量回 parcels
parcels = parcels.merge(dominant_classes, left_on='fid', right_on=group_col, how='left')
parcels = parcels.merge(poi_counts, on=group_col, how='left')

# 填充缺失值
parcels['类别'] = parcels['类别'].fillna('未知')
parcels['POI_居住'] = parcels['POI_居住'].fillna(0).astype(int)
parcels['POI_工业'] = parcels['POI_工业'].fillna(0).astype(int)
parcels['POI_交通'] = parcels['POI_交通'].fillna(0).astype(int)
parcels['POI_绿地'] = parcels['POI_绿地'].fillna(0).astype(int)
parcels['POI_其他'] = parcels['POI_其他'].fillna(0).astype(int)

# 移除临时列
parcels = parcels.drop(columns=[group_col])

# 保存结果
parcels.to_file(classified_path)

print(f"分类完成，已保存到 {classified_path}")