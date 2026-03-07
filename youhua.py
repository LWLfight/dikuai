import geopandas as gpd
import os
import shapely
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import make_valid
import math

# 定义路径
city = 'guangzhou'
# 确保路径相对于脚本本身，而不是当前工作目录
base_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(base_dir, f'{city}.output')
not_divided_path = os.path.join(output_dir, 'not_divided.shp')
cleaned_path = os.path.join(output_dir, 'cleaned_not_divided.shp')
# 如果输出目录不存在，则报错提示用户
if not os.path.isdir(output_dir):
    raise FileNotFoundError(f"输出目录不存在：{output_dir}")

# 读取文件
remaining = gpd.read_file(not_divided_path)

# 确保CRS一致（假设为米单位，如果需要转换为投影坐标系：remaining = remaining.to_crs(epsg=3857)）
crs = remaining.crs

# 参数定义（根据用户指定）
min_area_threshold = 2500.0  # 最小面积阈值，平方米
compactness_ratio_threshold = 6.0  # 周长面积比阈值 = perimeter² / (4π * area) > 7 表示不紧致
mid_area_threshold = 20000.0  # 用于周长面积比判断的面积阈值
buffer_distance = 10.0  # 形态学开运算的缓冲距离（腐蚀/膨胀距离），可调整为更适合的值，如 5.0 米根据数据
min_width_threshold = 30.0  # 最小宽度阈值，米，小于此宽度的部分将被裁剪删除

# 函数：计算周长面积比 = perimeter² / (4π * area)
def compactness_ratio(geom):
    if geom.is_empty or geom.area <= 0:
        return 0.0
    perimeter = geom.length
    area = geom.area
    return (perimeter ** 2) / (4 * math.pi * area)

# 函数：估算最小宽度（使用短轴长度作为近似）
def min_width_estimate(geom):
    """
    估算几何要素的最小宽度
    使用边界框的短轴长度作为近似值
    """
    if geom.is_empty or geom.area <= 0:
        return float('inf')
    
    # 使用 oriented envelope（定向包络线）来获取更准确的宽度
    try:
        envelope = geom.minimum_rotated_rectangle
        if envelope.geom_type == 'Polygon':
            # 获取矩形的边长
            coords = list(envelope.exterior.coords)
            # 计算相邻边的长度
            side1 = shapely.geometry.LineString(coords[0:2]).length
            side2 = shapely.geometry.LineString(coords[1:3]).length
            # 返回较短的边作为宽度估计
            return min(side1, side2)
    except:
        pass
    
    # 备用方法：使用边界框
    bounds = geom.bounds  # (minx, miny, maxx, maxy)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    return min(width, height)

# 函数：基于宽度裁剪几何，保留宽度大于阈值的部分
def clip_by_width(geom, width_threshold=30.0):
    """
    分析几何要素的宽度分布，裁剪掉宽度小于阈值的部分
    使用局部缓冲区分析和迭代方法来识别窄区域
    
    参数:
        geom: 输入的几何要素
        width_threshold: 最小宽度阈值（米）
    
    返回:
        保留宽度大于阈值的几何部分
    """
    if geom.is_empty or geom.area <= 0:
        return geom
    
    # 如果整体宽度满足要求，直接返回
    if min_width_estimate(geom) >= width_threshold:
        return geom
    
    # 使用负缓冲 + 正缓冲来识别"骨架"区域（宽度较大部分）
    # 负缓冲距离设为阈值的一半，可以消除窄的部分
    erosion_dist = -width_threshold / 2.0
    skeleton = geom.buffer(erosion_dist)
    
    if skeleton.is_empty or skeleton.area <= 0:
        # 整个要素都很窄，全部删除
        return shapely.geometry.Polygon()
    
    # 正缓冲恢复原始大小
    expanded = skeleton.buffer(-erosion_dist)
    
    # 与原始几何求交，保留宽的部分
    result = geom.intersection(expanded)
    
    return result

# 函数：形态学开运算（腐蚀后膨胀）
# 此版本不仅对几何做开运算，还明确获取被裁剪出来的部分并删除它们。
# 这种处理有助于"拆分复杂要素",把细长突出部分与主块分离。
def morphological_open(geom):
    if geom.is_empty or geom.area <= 0:
        return geom
    
    # 修复无效几何
    geom = make_valid(geom)
    
    # Step 1: 先应用宽度裁剪，删除宽度小于 30m 的部分
    geom = clip_by_width(geom, min_width_threshold)
    
    if geom.is_empty or geom.area <= 0:
        return shapely.geometry.Polygon()
    
    # Step 2: 腐蚀：负缓冲
    eroded = geom.buffer(-buffer_distance)
    if eroded.is_empty or eroded.area <= 0:
        # 整体都被腐蚀掉，直接丢弃
        return shapely.geometry.Polygon()
    
    # Step 3: 膨胀：正缓冲
    opened = eroded.buffer(buffer_distance)
    if opened.is_empty or opened.area <= 0:
        return shapely.geometry.Polygon()
    
    # 被裁剪掉的区域（通常是细长部分）
    removed = geom.difference(opened)
    # 可视化或调试时可将 removed 单独保存，但最终我们直接丢弃
    
    # 对打开后的结果进行同样的紧致度/面积过滤
    candidates = []
    if isinstance(opened, MultiPolygon):
        candidates = list(opened.geoms)
    else:
        candidates = [opened]
    
    valid_parts = []
    for part in candidates:
        if part.is_empty or part.area < min_area_threshold:
            continue
        
        # 检查周长面积比
        ratio = compactness_ratio(part)
        if (ratio > compactness_ratio_threshold and part.area < mid_area_threshold):
            continue
        
        # 再次检查最小宽度，确保没有遗漏
        min_width = min_width_estimate(part)
        if min_width < min_width_threshold:
            continue
            
        valid_parts.append(part)
    
    if not valid_parts:
        return shapely.geometry.Polygon()
    elif len(valid_parts) == 1:
        return valid_parts[0]
    else:
        return MultiPolygon(valid_parts)

# 第一步：explode MultiPolygon成单个Polygon
remaining = remaining.explode(index_parts=True).reset_index(drop=True)

# 第二步：删除面积 < 2000 m² 的要素
remaining = remaining[remaining.area >= min_area_threshold]

# 第三步：删除 周长面积比 > 7 且 面积 < 20000 m² 的要素
remaining['compactness_ratio'] = remaining.geometry.apply(compactness_ratio)
mask = ~((remaining['compactness_ratio'] > compactness_ratio_threshold) & (remaining.area < mid_area_threshold))
remaining = remaining[mask].drop(columns=['compactness_ratio'])

# 第四步：应用形态学开运算到剩余要素，删除细长部分
print(f"处理前图斑数：{len(remaining)}")
remaining['geometry'] = remaining.geometry.apply(morphological_open)

# 移除空几何
remaining = remaining[~remaining.geometry.is_empty]
print(f"形态学运算后图斑数：{len(remaining)}")

# 第五步：将 MultiPolygon 拆分为单个 Polygon 要素
print("正在拆分 MultiPolygon 为单部分 Polygon...")
original_count = len(remaining)
remaining = remaining.explode(index_parts=True).reset_index(drop=True)
new_count = len(remaining)
print(f"  拆分前图斑数：{original_count}, 拆分后图斑数：{new_count}")

# 第六步：删除面积 < 2500 m² 的要素（最终清理）
print(f"清理前图斑数：{len(remaining)}")
remaining = remaining[remaining.area >= min_area_threshold]
print(f"清理后图斑数：{len(remaining)}")

# 保存处理后的文件
remaining.to_file(cleaned_path)

print("优化处理完成：cleaned_not_divided.shp 已保存。")