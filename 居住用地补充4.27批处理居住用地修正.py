import geopandas as gpd
import os
import sys
import glob
import gc
from pathlib import Path

# ============================================================
# 【关键】必须在所有 import 之前设置 PROJ 数据库路径
# ============================================================
def _set_proj_path_early():
    conda_env = os.environ.get('CONDA_PREFIX', '')
    candidates = []
    if conda_env:
        candidates.append(os.path.join(conda_env, 'Library', 'share', 'proj'))
    python_base = os.path.dirname(sys.executable)
    candidates += [
        os.path.join(python_base, 'Library', 'share', 'proj'),
        os.path.join(python_base, '..', 'Library', 'share', 'proj'),
        os.path.join(python_base, 'share', 'proj'),
    ]
    for p in candidates:
        p = os.path.normpath(p)
        if os.path.isdir(p):
            os.environ['PROJ_DATA'] = p
            os.environ['PROJ_LIB']  = p
            print(f"✓ [早期] 已设置 PROJ_DATA 环境变量：{p}")
            return p
    print("⚠ [早期] 未找到 PROJ 数据库目录，EPSG 解析可能失败")
    return None

_proj_path = _set_proj_path_early()

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Point
import pyproj
from pyproj.datadir import set_data_dir

try:
    if _proj_path:
        set_data_dir(_proj_path)
except Exception as e:
    print(f"⚠ set_data_dir 调用失败：{e}")

# 获取脚本所在目录作为基准目录
base_dir = os.path.dirname(os.path.abspath(__file__))
print(f"脚本所在目录：{base_dir}")

# ============================================================
# 参数配置
# ============================================================
THRESHOLD_RESIDENTIAL_POI_RATIO = 0.30      # 居住 POI 占比阈值 (30%)
THRESHOLD_POI_DENSITY = 100                 # POI 密度阈值 (POI总数/面积*1000000 > 2.5)

# POI 分类映射（居住用地）
RESIDENTIAL_POI_CATS = ['住、宿', '批发、零售', '居民服务', '餐饮']

# 站点关键词（匹配 xlmc 字段）
STATION_KEYWORDS = {
    '客运火车站': '火车站',
    '客运汽车站': '汽车站',
    '机场': '机场'
}

# ============================================================
# 辅助函数
# ============================================================
def to_wgs84(gdf, layer_name='图层'):
    """转换为 WGS84 地理坐标系 (EPSG:4326)"""
    if gdf.crs is None:
        print(f"  ⚠ {layer_name} 缺少坐标系信息，假定 WGS84")
        return gdf.set_crs('EPSG:4326')
    
    current_epsg = gdf.crs.to_epsg()
    
    # 如果已是 WGS84 地理坐标系
    if current_epsg == 4326:
        print(f"  ✓ {layer_name} 已是 WGS84 地理坐标系 (EPSG:4326)")
        return gdf
    
    # 从其他坐标系转换到 WGS84
    print(f"  → {layer_name} 从 EPSG:{current_epsg} 转换到 WGS84 (EPSG:4326)...")
    return gdf.to_crs('EPSG:4326')

def add_area_m2(gdf):
    """为 GeoDataFrame 添加面积字段（平方米），需要投影坐标系"""
    # 保存原始 CRS
    orig_crs = gdf.crs
    if orig_crs is None:
        raise ValueError("数据缺少坐标系，无法计算面积")
    # 如果已经是投影坐标系（单位米），直接计算
    if orig_crs.is_projected:
        gdf_proj = gdf
    else:
        # 估算 UTM 投影
        bounds = gdf.total_bounds
        lon = (bounds[0] + bounds[2]) / 2
        lat = (bounds[1] + bounds[3]) / 2
        utm_zone = int((lon + 180) / 6) + 1
        epsg = 32600 + utm_zone if lat >= 0 else 32700 + utm_zone
        print(f"  自动选择 UTM 投影：EPSG:{epsg}")
        gdf_proj = gdf.to_crs(f'EPSG:{epsg}')
    area_m2 = gdf_proj.geometry.area
    gdf['面积'] = area_m2
    return gdf

def sjoin_points_to_parcels(parcels, points, predicate='within'):
    """空间连接：点落入地块内，返回连接后的 GeoDataFrame"""
    if len(parcels) == 0 or len(points) == 0:
        return gpd.GeoDataFrame()
    # 确保都在 WGS84 下进行空间查询
    parcels_w = to_wgs84(parcels, '地块')
    points_w = to_wgs84(points, 'POI')
    joined = gpd.sjoin(points_w, parcels_w[['geometry']], how='inner', predicate=predicate)
    return joined

def find_parcel_file(city_dir):
    """
    在城市目录中查找地块文件（融合后的用地功能数据）
    格式：城市名_用地功能.shp
    """
    pattern = '**/*_用地功能.shp'
    matches = list(Path(city_dir).glob(pattern))
    
    if matches:
        return str(matches[0])
    
    return None

def find_poi_file(city_name):
    """
    根据城市名称查找对应的 POI 文件
    """
    poi_dir = os.path.join(base_dir, 'data', 'data_poi', '合并后的POI')
    poi_path = os.path.join(poi_dir, f'{city_name}.gpkg')
    
    if os.path.exists(poi_path):
        return poi_path
    
    return None

def process_single_city(city_name, parcel_path, poi_path):
    """
    处理单个城市的地块分类
    返回处理结果和处理状态
    """
    print(f"\n{'='*60}")
    print(f"开始处理城市：{city_name}")
    print(f"{'='*60}")
    
    try:
        # 验证输入文件
        if not os.path.exists(parcel_path):
            print(f"✗ 错误：找不到地块文件 {parcel_path}")
            return None, 'failed'
        if not os.path.exists(poi_path):
            print(f"✗ 错误：找不到 POI 文件 {poi_path}")
            return None, 'failed'

        print(f"读取地块数据：{parcel_path}")
        print(f"读取 POI 数据：{poi_path}")

        # 读取数据
        parcels = gpd.read_file(parcel_path)
        pois = gpd.read_file(poi_path)

        print(f"\n=== 数据基本信息 ===")
        print(f"  地块数量：{len(parcels)}")
        print(f"  POI 数量：{len(pois)}")
        print(f"  地块坐标系：{parcels.crs}")
        print(f"  POI 坐标系：{pois.crs}")
        
        # 检查必要字段
        if '类别' not in parcels.columns:
            print("✗ 错误：地块数据中没有'类别'字段")
            return None, 'failed'
        if '面积' not in parcels.columns:
            print("✗ 错误：地块数据中没有'面积'字段")
            return None, 'failed'

        # ============================================================
        # 步骤1：将所有"居住"类别改为"其他"
        # ============================================================
        print("\n=== 步骤1：重置居住类别为其他 ===")
        residential_count = (parcels['类别'] == '居住').sum()
        parcels.loc[parcels['类别'] == '居住', '类别'] = '其他'
        print(f"  ✓ 已将 {residential_count} 个'居住'地块改为'其他'")
        print(f"  当前类别分布:\n{parcels['类别'].value_counts()}")

        # ============================================================
        # 步骤2：分离"其他"地块和已赋值地块
        # ============================================================
        parcels_other = parcels[parcels['类别'] == '其他'].copy().reset_index(drop=True)
        parcels_assigned = parcels[parcels['类别'] != '其他'].copy()
        
        print(f"\n=== 地块分类统计 ===")
        print(f"  待判别地块（其他）：{len(parcels_other)}")
        print(f"  已赋值地块：{len(parcels_assigned)}")
        
        if len(parcels_other) == 0:
            print("  没有待判别的地块，无需进一步处理")
            result = parcels.copy()
        else:
            # ============================================================
            # 步骤3：转换 POI 坐标系：WGS84 → CGCS2000
            # ============================================================
            print("\n=== 步骤2：转换 POI 坐标系 ===")
            # 先确保 POI 是 WGS84
            if pois.crs is None:
                print("  ⚠ POI 数据缺少坐标系，假定 WGS84")
                pois = pois.set_crs('EPSG:4326')
            
            current_epsg = pois.crs.to_epsg()
            if current_epsg != 4326:
                print(f"  → POI 从 EPSG:{current_epsg} 转换到 WGS84 (EPSG:4326)...")
                pois = pois.to_crs('EPSG:4326')
            else:
                print(f"  ✓ POI 已是 WGS84 地理坐标系")
            
            # 再从 WGS84 转换到 CGCS2000 (EPSG:4490)
            print(f"  → POI 从 WGS84 (EPSG:4326) 转换到 CGCS2000 (EPSG:4490)...")
            pois = pois.to_crs('EPSG:4490')
            print(f"  ✓ POI 坐标转换完成：{pois.crs}")
            
            # 注意：地块数据的坐标系保持不变，不需要转换
            
            # ============================================================
            # 步骤4：计算 POI 统计信息并进行居住用地判别
            # ============================================================
            print("\n=== 步骤3：计算 POI 统计并判别居住用地 ===")
            
            # 初始化 POI 相关字段
            parcels_other['POI'] = 0
            parcels_other['RES_RATIO'] = 0.0
            
            # 筛选居住类 POI
            pois_residential = pois[pois['dlmc'].isin(RESIDENTIAL_POI_CATS)].copy()
            print(f"  居住类 POI 数量：{len(pois_residential)}")
            
            # 空间连接：所有 POI 落到"其他"地块内
            print("  → 执行空间连接（所有 POI）...")
            joined_all_poi = gpd.sjoin(pois[['geometry']], parcels_other[['geometry']], how='inner', predicate='within')
            poi_count = joined_all_poi.groupby('index_right').size()
            
            # 赋值 POI 总数
            for idx in poi_count.index:
                if idx < len(parcels_other):
                    parcels_other.loc[idx, 'POI'] = int(poi_count[idx])
            print(f"  ✓ 已计算 POI 总数")
            
            # 空间连接：居住类 POI 落到"其他"地块内
            print("  → 执行空间连接（居住类 POI）...")
            joined_res_poi = gpd.sjoin(pois_residential[['geometry']], parcels_other[['geometry']], how='inner', predicate='within')
            res_poi_count = joined_res_poi.groupby('index_right').size()
            
            # 计算居住类 POI 占比
            for idx in res_poi_count.index:
                if idx < len(parcels_other) and parcels_other.loc[idx, 'POI'] > 0:
                    parcels_other.loc[idx, 'RES_RATIO'] = round(res_poi_count[idx] / parcels_other.loc[idx, 'POI'], 4)
            print(f"  ✓ 已计算居住类 POI 占比")
            
            # ============================================================
            # 步骤5：应用双重判别条件
            # 条件1：居住 POI 占比 > 30%
            # 条件2：根据地块面积设置不同的 POI 密度阈值
            #   - 面积 < 1,000,000 m²: POI 密度 > 3
            #   - 1,000,000 ≤ 面积 < 5,000,000 m²: POI 密度 > 10
            #   - 5,000,000 ≤ 面积 < 8,000,000 m²: POI 密度 > 20
            #   - 面积 ≥ 8,000,000 m²: POI 密度 > 100
            # ============================================================
            print("\n=== 步骤4：应用居住用地判别规则 ===")
            print(f"  判别条件：")
            print(f"    1. 居住 POI 占比 > {THRESHOLD_RESIDENTIAL_POI_RATIO*100:.0f}%")
            print(f"    2. 根据地块面积设置不同的 POI 密度阈值：")
            print(f"       - 面积 < 1,000,000 m²: POI 密度 > 3")
            print(f"       - 1,000,000 ≤ 面积 < 5,000,000 m²: POI 密度 > 10")
            print(f"       - 5,000,000 ≤ 面积 < 8,000,000 m²: POI 密度 > 20")
            print(f"       - 面积 ≥ 8,000,000 m²: POI 密度 > 100")
            
            # 计算 POI 密度
            parcels_other['POI_DENSITY'] = parcels_other['POI'] / parcels_other['面积'] * 1000000
            
            # 先筛选出满足条件1的地块（居住 POI 占比 > 30%）
            condition1 = parcels_other['RES_RATIO'] > THRESHOLD_RESIDENTIAL_POI_RATIO
            candidates_idx = parcels_other[condition1].index
            
            if len(candidates_idx) > 0:
                print(f"\n  → 满足条件1（居住 POI 占比 > 30%）的地块数：{len(candidates_idx)}")
                
                # 根据面积设置不同的密度阈值
                convert_idx_list = []
                
                for idx in candidates_idx:
                    area = parcels_other.loc[idx, '面积']
                    density = parcels_other.loc[idx, 'POI_DENSITY']
                    
                    # 根据面积确定密度阈值
                    if area < 1000000:
                        threshold = 3
                    elif area < 5000000:
                        threshold = 10
                    elif area < 8000000:
                        threshold = 20
                    else:
                        threshold = 100
                    
                    # 判断是否满足密度条件
                    if density > threshold:
                        convert_idx_list.append(idx)
                
                convert_idx = parcels_other.index[convert_idx_list]
                
                if len(convert_idx) > 0:
                    parcels_other.loc[convert_idx, '类别'] = '居住'
                    print(f"  ✓ 已将 {len(convert_idx)} 个地块判别为'居住'")
                    
                    # 输出一些统计信息
                    converted_parcels = parcels_other.loc[convert_idx]
                    print(f"    平均 POI 数：{converted_parcels['POI'].mean():.1f}")
                    print(f"    平均居住 POI 占比：{converted_parcels['RES_RATIO'].mean():.2%}")
                    print(f"    平均 POI 密度：{converted_parcels['POI_DENSITY'].mean():.2f}")
                    
                    # 按面积区间统计
                    small = converted_parcels[converted_parcels['面积'] < 1000000]
                    medium1 = converted_parcels[(converted_parcels['面积'] >= 1000000) & (converted_parcels['面积'] < 5000000)]
                    medium2 = converted_parcels[(converted_parcels['面积'] >= 5000000) & (converted_parcels['面积'] < 8000000)]
                    large = converted_parcels[converted_parcels['面积'] >= 8000000]
                    
                    print(f"    面积分布：")
                    print(f"      < 1,000,000 m²: {len(small)} 个")
                    print(f"      1,000,000-5,000,000 m²: {len(medium1)} 个")
                    print(f"      5,000,000-8,000,000 m²: {len(medium2)} 个")
                    print(f"      ≥ 8,000,000 m²: {len(large)} 个")
                else:
                    print(f"\n  → 没有满足条件的地块")
            else:
                print(f"\n  → 没有满足条件1的地块")
            
            # 清理临时字段
            if 'POI_DENSITY' in parcels_other.columns:
                parcels_other = parcels_other.drop(columns=['POI_DENSITY'])
            
            # 合并已赋值地块和处理后的"其他"地块
            result = pd.concat([parcels_assigned, parcels_other], ignore_index=True)
            
            # 验证地块数量
            total_after = len(result)
            total_before = len(parcels)
            if total_after != total_before:
                print(f"\n  ⚠ 警告：合并后地块数量不一致！合并前={total_before}, 合并后={total_after}")
            else:
                print(f"\n  ✓ 地块数量验证通过：{total_after} = {total_before}")
        
        # ============================================================
        # 最终结果整理
        # ============================================================
        print("\n=== 最终结果整理 ===")
        
        # 【关键】只保留"面积"和"类别"两个字段
        print("  → 只保留'面积'和'类别'字段...")
        if '面积' not in result.columns or '类别' not in result.columns:
            print("  ✗ 错误：结果中缺少必要字段")
            return None, 'error'
        
        result = result[['geometry', '面积', '类别']].copy()
        print(f"  ✓ 已保留字段：{list(result.columns)}")
        print(f"  ✓ 地块坐标系保持不变：{result.crs}")
        if result.crs.to_epsg() == 4490:
            print(f"  ✓ 已是 CGCS2000 地理坐标系")
        else:
            print(f"  ⚠ 注意：地块坐标系为 {result.crs}，按要求保持不变")
        
        # 释放内存
        del parcels, pois, parcels_other, parcels_assigned
        gc.collect()
        
        return result, 'success'
        
    except Exception as e:
        print(f"\n✗ 处理城市 {city_name} 时发生错误：{e}")
        import traceback
        traceback.print_exc()
        return None, 'error'

# ============================================================
# 主程序：批量处理所有城市
# ============================================================
print("\n" + "="*60)
print("开始批量处理城市地块分类（居住用地重新判别）")
print("="*60)

# 定义输入目录路径（融合后的数据）
input_dir = os.path.join(base_dir, 'output', '第一批城市分类结果', '融合后')

if not os.path.exists(input_dir):
    print(f"✗ 错误：找不到输入目录 {input_dir}")
    exit(1)

# 扫描所有城市目录
city_dirs = [d for d in os.listdir(input_dir) 
             if os.path.isdir(os.path.join(input_dir, d))]

if not city_dirs:
    print("✗ 错误：输入目录下没有找到城市文件夹")
    exit(1)

print(f"\n发现 {len(city_dirs)} 个城市：{', '.join(sorted(city_dirs))}")

# 统计信息
stats = {
    'total': len(city_dirs),
    'success': 0,
    'failed': 0,
    'skipped': 0
}

# 输出根目录（最新用地功能）
output_base_dir = os.path.join(base_dir, 'output', '第一批城市分类结果', '用地功能5.12')
os.makedirs(output_base_dir, exist_ok=True)

# 批量处理每个城市
for idx, city_dir_name in enumerate(sorted(city_dirs), 1):
    print(f"\n\n{'#'*60}")
    print(f"# 处理进度：[{idx}/{len(city_dirs)}] - {city_dir_name}")
    print(f"{'#'*60}")
    
    city_dir_path = os.path.join(input_dir, city_dir_name)
    
    # 步骤1：查找地块文件
    print(f"\n[步骤1] 查找地块文件...")
    parcel_file = find_parcel_file(city_dir_path)
    
    if not parcel_file:
        print(f"✗ 跳过 {city_dir_name}：未找到地块文件（*_用地功能.shp）")
        stats['skipped'] += 1
        continue
    
    print(f"  ✓ 找到地块文件：{os.path.basename(parcel_file)}")
    
    # 步骤2：确定城市名称（用于查找POI文件）
    city_name = city_dir_name
    
    # 步骤3：查找POI文件
    print(f"\n[步骤2] 查找POI文件...")
    poi_file = find_poi_file(city_name)
    
    if not poi_file:
        print(f"✗ 跳过 {city_dir_name}：未找到POI文件 {city_name}.gpkg")
        stats['skipped'] += 1
        continue
    
    print(f"  ✓ 找到POI文件：{os.path.basename(poi_file)}")
    
    # 步骤4：处理单个城市
    print(f"\n[步骤3] 开始处理...")
    result, status = process_single_city(city_name, parcel_file, poi_file)
    
    if status != 'success' or result is None:
        print(f"\n✗ {city_name} 处理失败")
        stats['failed'] += 1
        continue
    
    # 步骤5：保存结果
    print(f"\n[步骤4] 保存结果...")
    
    # 将面积字段保留两位小数
    result['面积'] = result['面积'].round(2)
    print(f"  → 面积字段已保留两位小数")
    
    city_output_dir = os.path.join(output_base_dir, city_name)
    os.makedirs(city_output_dir, exist_ok=True)
    
    output_filename = f"{city_name}_用地功能.shp"
    output_path = os.path.join(city_output_dir, output_filename)
    
    try:
        result.to_file(output_path, encoding='utf-8', driver='ESRI Shapefile')
        print(f"  ✓ 文件保存成功：{output_path}")
        
        # 验证保存的文件
        final_verify = gpd.read_file(output_path)
        print(f"  ✓ 验证通过：{len(final_verify)} 个要素，坐标系：{final_verify.crs}")
        print(f"  ✓ 类别分布:\n{final_verify['类别'].value_counts()}")
        
        stats['success'] += 1
        
    except Exception as e:
        print(f"  ✗ 保存失败：{e}")
        stats['failed'] += 1
        import traceback
        traceback.print_exc()
    
    # 清理内存
    del result
    gc.collect()

# ============================================================
# 输出最终统计
# ============================================================
print("\n\n" + "="*60)
print("批量处理完成 - 最终统计")
print("="*60)
print(f"总城市数：{stats['total']}")
print(f"成功处理：{stats['success']}")
print(f"失败：{stats['failed']}")
print(f"跳过（缺少文件）：{stats['skipped']}")
print(f"\n输出目录：{output_base_dir}")
print("="*60)