import geopandas as gpd
import os
import pandas as pd
import numpy as np
import glob
import math
import pyproj
import warnings
from shapely.geometry import MultiPolygon, Polygon, LineString, GeometryCollection, Point
from shapely.validation import make_valid
from shapely.ops import unary_union, snap
from rtree import index

warnings.filterwarnings('ignore', category=UserWarning)

# 设置 pyproj 数据路径
pyproj.datadir.set_data_dir(os.path.join(os.environ.get('CONDA_PREFIX', ''), 'Library', 'share', 'proj'))

# 获取脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))

# 定义路径
city = 'beijing'
city_name = '北京市'
parcel_path = os.path.join(script_dir, f'data/data_dikuai/北京/北京建成区_parcel.shp')
osm_dir = os.path.join(script_dir, 'data/data_osm/osm25/beijing-260107-free.shp')
water_path = os.path.join(osm_dir, 'gis_osm_water_a_free_1.shp')
landuse_path = os.path.join(osm_dir, 'gis_osm_landuse_a_free_1.shp')
traffic_path = os.path.join(osm_dir, 'gis_osm_traffic_a_free_1.shp')
transport_path = os.path.join(osm_dir, 'gis_osm_transport_a_free_1.shp')

# 新增：水经注绿地水系数据和公园绿地数据路径
sjz_green_path = os.path.join(script_dir, f'data/水经注绿地水系数据/{city_name}/{city_name}_绿地.shp')
sjz_water_path = os.path.join(script_dir, f'data/水经注绿地水系数据/{city_name}/{city_name}_水系.shp')
# 公园绿地数据使用城市简称（不带"市"字）
city_short = city_name.replace('市', '')
park_green_path = os.path.join(script_dir, f'data/绿地公园/2025公园与绿地广场/01{city_short}/{city_short}_公园绿地.shp')

builtup_pattern = os.path.join(script_dir, f'data/data_area/建成区2025/{city_name}*.shp')
output_dir = os.path.join(script_dir, f'./output/实验/北京/{city}.output')
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# ========== 辅助函数 ==========
def remove_shapefile_files(filepath):
    base = os.path.splitext(filepath)[0]
    extensions = ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix']
    for ext in extensions:
        f = base + ext
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"  已删除旧文件：{f}")
            except:
                pass

def final_processing(gdf):
    """执行最终处理：联合分析、几何修复、删除相同项等"""
    # 联合分析（如果需要，可以传入额外的图层列表，但这里保持原样，只处理自身）
    union_layers = [gdf]  # 默认只处理自身
    if len(union_layers) > 1:
        print("执行联合分析（Union）...")
        union_result = union_layers[0]
        for layer in union_layers[1:]:
            union_result = gpd.overlay(union_result, layer, how='union')
        union_result = clean_geometry(union_result, min_area=1e-6)
        gdf = union_result
        print(f"  联合后要素数：{len(gdf)}")
    else:
        print("未提供多个图层，跳过联合分析。")

    # 几何修复（第一次）
    print("执行几何修复（第一次）...")
    gdf = clean_geometry(gdf, min_area=1.0)
    print(f"  修复后要素数：{len(gdf)}")

    # 删除相同项
    print("删除几何重复项...")
    gdf = gdf.drop_duplicates(subset='geometry', keep='first')
    print(f"  去重后要素数：{len(gdf)}")

    # 几何修复（第二次）
    print("执行几何修复（第二次）...")
    gdf = clean_geometry(gdf, min_area=1.0)
    print(f"  再次修复后要素数：{len(gdf)}")

    # 确保所有几何为多边形并拆分多部件
    gdf = explode_multipolygons(gdf).reset_index(drop=True)

    # 重建 FID
    gdf['FID'] = range(1, len(gdf) + 1)
    print("最终处理完成。")
    return gdf
def safe_make_valid(geom):
    """安全修复几何，返回有效的多边形或 None"""
    if geom is None or geom.is_empty:
        return None
    try:
        # 先尝试 buffer(0) 修复微小缝隙/自相交
        geom = geom.buffer(0)
        if not geom.is_valid:
            geom = make_valid(geom)
        # 提取所有多边形（丢弃线、点）
        polys = []
        if isinstance(geom, (Polygon, MultiPolygon)):
            polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
        elif isinstance(geom, GeometryCollection):
            for g in geom.geoms:
                if isinstance(g, (Polygon, MultiPolygon)):
                    polys.extend([g] if isinstance(g, Polygon) else list(g.geoms))
        # 过滤有效且面积大于0的多边形
        valid_polys = [p for p in polys if p.is_valid and p.area > 0]
        if not valid_polys:
            return None
        if len(valid_polys) == 1:
            return valid_polys[0]
        else:
            return MultiPolygon(valid_polys)
    except:
        return None

def clean_geometry(gdf, min_area=1e-6):
    """彻底清理几何：修复无效、删除退化、保留多边形、拆分多部分"""
    if len(gdf) == 0:
        return gdf
    # 修复几何
    gdf['_clean_geom'] = gdf.geometry.apply(safe_make_valid)
    # 删除空几何
    gdf = gdf[~gdf['_clean_geom'].isna()].copy()
    if len(gdf) == 0:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    # 展开 MultiPolygon
    new_rows = []
    for idx, row in gdf.iterrows():
        geom = row['_clean_geom']
        if isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                new_row = row.copy()
                new_row.geometry = poly
                new_rows.append(new_row)
        elif isinstance(geom, Polygon):
            new_row = row.copy()
            new_row.geometry = geom
            new_rows.append(new_row)
    gdf_clean = gpd.GeoDataFrame(new_rows, crs=gdf.crs).drop(columns=['_clean_geom'])
    # 删除面积小于阈值的碎片
    gdf_clean = gdf_clean[gdf_clean.geometry.area > min_area].reset_index(drop=True)
    return gdf_clean

def explode_multipolygons(geo_df):
    exploded = []
    for _, row in geo_df.iterrows():
        geom = row.geometry
        if isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                new_row = row.copy()
                new_row.geometry = poly
                exploded.append(new_row)
        elif isinstance(geom, Polygon):
            exploded.append(row.copy())
    return gpd.GeoDataFrame(exploded, crs=geo_df.crs)

def consolidate_class_with_buffer(gdf, buffer_size=0.1):
    """合并同类要素，使用缓冲再负缓冲消除微小缝隙，然后拆分为独立多边形"""
    if len(gdf) == 0:
        return gpd.GeoDataFrame(columns=['geometry'], crs=gdf.crs)
    # 先正缓冲再负缓冲，消除缝隙
    buffered = gdf.geometry.buffer(buffer_size).unary_union
    if buffered.is_empty:
        return gpd.GeoDataFrame(columns=['geometry'], crs=gdf.crs)
    eroded = buffered.buffer(-buffer_size)
    if eroded.is_empty:
        return gpd.GeoDataFrame(columns=['geometry'], crs=gdf.crs)
    temp = gpd.GeoDataFrame(geometry=[eroded], crs=gdf.crs)
    temp = clean_geometry(temp, min_area=1e-6)
    if len(temp) == 0:
        return gpd.GeoDataFrame(columns=['geometry'], crs=gdf.crs)
    return explode_multipolygons(temp).reset_index(drop=True)

def ensure_projected_crs(gdf, target_crs=None):
    if gdf.crs is None:
        raise ValueError("数据缺少坐标系信息")
    if target_crs:
        return gdf.to_crs(target_crs)
    if gdf.crs.is_projected and gdf.crs.axis_info[0].unit_name == 'metre':
        return gdf
    bounds = gdf.total_bounds
    center_lon = (bounds[0] + bounds[2]) / 2
    utm_zone = int((center_lon + 180) // 6) + 1
    center_lat = (bounds[1] + bounds[3]) / 2
    hemisphere = 'north' if center_lat >= 0 else 'south'
    epsg_code = 32600 + utm_zone if hemisphere == 'north' else 32700 + utm_zone
    proj_crs = f'EPSG:{epsg_code}'
    print(f"  转换到投影坐标系：{proj_crs}")
    return gdf.to_crs(proj_crs)

def to_geographic_crs(gdf):
    if gdf.crs is not None and gdf.crs.to_epsg() == 4326:
        return gdf
    return gdf.to_crs('EPSG:4326')

def read_shapefile_with_fallback(filepath, layer_name="图层"):
    try:
        gdf = gpd.read_file(filepath)
        print(f"  ✓ {layer_name} 读取成功：{len(gdf)} 个要素")
        return gdf
    except Exception as e:
        print(f"  ⚠️ {layer_name} 读取失败：{e}")
        try:
            gdf = gpd.read_file(filepath, engine='fiona')
            print(f"  ✓ {layer_name} 使用 fiona 引擎读取成功：{len(gdf)} 个要素")
            return gdf
        except Exception as e2:
            print(f"  ⚠️ fiona 引擎也失败：{e2}")
            import fiona
            from shapely.geometry import shape
            with fiona.open(filepath, 'r') as src:
                geometries = [shape(feat.geometry) for feat in src]
            gdf = gpd.GeoDataFrame(geometry=geometries)
            print(f"  ✓ {layer_name} 已手动创建（暂不设置坐标系）：{len(gdf)} 个要素")
            return gdf

# ========== 读取文件 ==========
print("正在读取文件...")
parcel = read_shapefile_with_fallback(parcel_path, "地块数据")
water = read_shapefile_with_fallback(water_path, "水体数据（OSM）")
landuse = read_shapefile_with_fallback(landuse_path, "土地利用数据")
traffic = read_shapefile_with_fallback(traffic_path, "交通数据")
transport = read_shapefile_with_fallback(transport_path, "运输数据")

# 新增：读取水经注绿地水系数据和公园绿地数据
sjz_green = read_shapefile_with_fallback(sjz_green_path, "水经注绿地数据")
sjz_water = read_shapefile_with_fallback(sjz_water_path, "水经注水体数据")
park_green = read_shapefile_with_fallback(park_green_path, "公园绿地数据")

builtup_files = glob.glob(builtup_pattern)
if not builtup_files:
    raise FileNotFoundError(f"未找到建成区边界文件：{builtup_pattern}")
builtup = gpd.read_file(builtup_files[0])
print(f"已加载建成区边界：{builtup_files[0]}")

# ========== 统一转换为投影坐标系 ==========
print("\n=== 统一转换到投影坐标系 ===")
# 先估算地块的投影
proj_crs = None
if parcel.crs is None:
    raise ValueError("地块数据缺少坐标系信息")
if not parcel.crs.is_projected:
    bounds = parcel.total_bounds
    center_lon = (bounds[0] + bounds[2]) / 2
    utm_zone = int((center_lon + 180) // 6) + 1
    center_lat = (bounds[1] + bounds[3]) / 2
    hemisphere = 'north' if center_lat >= 0 else 'south'
    epsg_code = 32600 + utm_zone if hemisphere == 'north' else 32700 + utm_zone
    proj_crs = f'EPSG:{epsg_code}'
    print(f"  估算投影坐标系：{proj_crs}")
else:
    proj_crs = parcel.crs

# 转换所有数据到同一投影
parcel = ensure_projected_crs(parcel, proj_crs)
water = ensure_projected_crs(water, proj_crs)
landuse = ensure_projected_crs(landuse, proj_crs)
traffic = ensure_projected_crs(traffic, proj_crs)
transport = ensure_projected_crs(transport, proj_crs)
builtup = ensure_projected_crs(builtup, proj_crs)
sjz_green = ensure_projected_crs(sjz_green, proj_crs)
sjz_water = ensure_projected_crs(sjz_water, proj_crs)
park_green = ensure_projected_crs(park_green, proj_crs)

# 合并交通和运输
traffic_merged = gpd.GeoDataFrame(pd.concat([traffic, transport], ignore_index=True), crs=proj_crs)

# 预修复每个图层
print("\n=== 预修复各图层 ===")
parcel = clean_geometry(parcel, min_area=1e-6)
water = clean_geometry(water, min_area=1e-6)
landuse = clean_geometry(landuse, min_area=1e-6)
traffic_merged = clean_geometry(traffic_merged, min_area=1e-6)
builtup = clean_geometry(builtup, min_area=1e-6)
sjz_green = clean_geometry(sjz_green, min_area=1e-6)
sjz_water = clean_geometry(sjz_water, min_area=1e-6)
park_green = clean_geometry(park_green, min_area=1e-6)

print(f"地块修复后要素数：{len(parcel)}")
print(f"水体修复后要素数（OSM）：{len(water)}")
print(f"土地利用修复后要素数：{len(landuse)}")
print(f"交通用地修复后要素数：{len(traffic_merged)}")
print(f"水经注绿地修复后要素数：{len(sjz_green)}")
print(f"水经注水体修复后要素数：{len(sjz_water)}")
print(f"公园绿地修复后要素数：{len(park_green)}")

# ========== Step 0: 合并多源绿地和水体数据 ==========
print("\n=== 合并多源绿地和水体数据 ===")

# 合并三个绿地数据：OSM绿地类 + 水经注绿地 + 公园绿地
green_sources = []
if len(sjz_green) > 0:
    green_sources.append(sjz_green[['geometry']])
    print(f"  添加水经注绿地：{len(sjz_green)} 个要素")
if len(park_green) > 0:
    green_sources.append(park_green[['geometry']])
    print(f"  添加公园绿地：{len(park_green)} 个要素")

if green_sources:
    merged_additional_green = gpd.GeoDataFrame(pd.concat(green_sources, ignore_index=True), crs=proj_crs)
    print(f"  合并后的附加绿地数据：{len(merged_additional_green)} 个要素")
else:
    merged_additional_green = gpd.GeoDataFrame(columns=['geometry'], crs=proj_crs)
    print("  无附加绿地数据")

# 合并两个水体数据：OSM水体 + 水经注水体
water_sources = []
if len(water) > 0:
    water_sources.append(water[['geometry']])
    print(f"  添加OSM水体：{len(water)} 个要素")
if len(sjz_water) > 0:
    water_sources.append(sjz_water[['geometry']])
    print(f"  添加水经注水体：{len(sjz_water)} 个要素")

if water_sources:
    merged_water_all = gpd.GeoDataFrame(pd.concat(water_sources, ignore_index=True), crs=proj_crs)
    print(f"  合并后的总水体数据：{len(merged_water_all)} 个要素")
else:
    merged_water_all = gpd.GeoDataFrame(columns=['geometry'], crs=proj_crs)
    print("  无水体数据")

# ========== Step 1: 提取各类用地并裁剪到地块范围 ==========
print("\n=== 提取并裁剪各地类到地块范围 ===")
green_classes = ['forest', 'grass', 'park', 'scrub', 'meadow']
green_osm = landuse[landuse['fclass'].isin(green_classes)].copy()
industrial = landuse[landuse['fclass'] == 'industrial'].copy()

def overlay_clip(src, clip, name):
    if len(src) == 0:
        return gpd.GeoDataFrame(columns=['geometry'], crs=proj_crs)
    try:
        clipped = gpd.overlay(src, clip, how='intersection')
    except:
        # 逐要素交集回退
        rows = []
        for _, row_src in src.iterrows():
            for _, row_clip in clip.iterrows():
                inter = row_src.geometry.intersection(row_clip.geometry)
                if not inter.is_empty:
                    new_row = row_src.copy()
                    new_row.geometry = inter
                    rows.append(new_row)
        clipped = gpd.GeoDataFrame(rows, crs=proj_crs) if rows else gpd.GeoDataFrame(columns=['geometry'], crs=proj_crs)
    clipped = clean_geometry(clipped, min_area=1e-6)
    print(f"  {name} 裁剪后要素数：{len(clipped)}")
    return clipped

# 先裁剪OSM绿地类
green_osm_clipped = overlay_clip(green_osm, parcel, "OSM绿地类")

# 裁剪附加绿地（水经注+公园）
if len(merged_additional_green) > 0:
    green_additional_clipped = overlay_clip(merged_additional_green, parcel, "附加绿地（水经注+公园）")
else:
    green_additional_clipped = gpd.GeoDataFrame(columns=['geometry'], crs=proj_crs)

# 合并所有绿地数据
green_clipped_sources = []
if len(green_osm_clipped) > 0:
    green_clipped_sources.append(green_osm_clipped[['geometry']])
if len(green_additional_clipped) > 0:
    green_clipped_sources.append(green_additional_clipped[['geometry']])

if green_clipped_sources:
    green_clipped = gpd.GeoDataFrame(pd.concat(green_clipped_sources, ignore_index=True), crs=proj_crs)
    print(f"  合并后总绿地要素数：{len(green_clipped)}")
else:
    green_clipped = gpd.GeoDataFrame(columns=['geometry'], crs=proj_crs)

industrial_clipped = overlay_clip(industrial, parcel, "工业用地")
traffic_clipped = overlay_clip(traffic_merged, parcel, "交通用地")

# 水体数据使用10.1米缓冲区后的地块进行裁剪
print("  创建10.1米缓冲区地块用于水体裁剪...")
parcel_buffered_for_water = parcel.copy()
parcel_buffered_for_water['geometry'] = parcel_buffered_for_water.geometry.buffer(10.1)
parcel_buffered_for_water = clean_geometry(parcel_buffered_for_water, min_area=1e-6)
water_clipped = overlay_clip(merged_water_all, parcel_buffered_for_water, "水体（OSM+水经注，使用10.1m缓冲地块）")
print(f"  水体（未裁剪）：{len(water_clipped)} 个要素")

# ========== Step 2: 对各类用地进行内部合并拆分 ==========
print("\n=== 对各类用地进行内部合并拆分 ===")
green_clipped = consolidate_class_with_buffer(green_clipped, buffer_size=0.1)
industrial_clipped = consolidate_class_with_buffer(industrial_clipped, buffer_size=0.1)
traffic_clipped = consolidate_class_with_buffer(traffic_clipped, buffer_size=0.1)
water_clipped = consolidate_class_with_buffer(water_clipped, buffer_size=0.1)

print(f"  绿地合并后：{len(green_clipped)} 个多边形")
print(f"  工业用地合并后：{len(industrial_clipped)} 个多边形")
print(f"  交通用地合并后：{len(traffic_clipped)} 个多边形")
print(f"  水体合并后：{len(water_clipped)} 个多边形")

# ========== Step 3: 构建障碍物并从未赋值地块中移除 ==========
print("\n=== 从地块中移除障碍区域 ===")
remaining = parcel.copy()
all_to_remove_list = []
for gdf in [water_clipped, green_clipped, industrial_clipped, traffic_clipped]:
    if len(gdf) > 0:
        all_to_remove_list.append(gdf)

if all_to_remove_list:
    all_to_remove = gpd.GeoDataFrame(pd.concat(all_to_remove_list, ignore_index=True), crs=proj_crs)
    # 分块合并障碍物（避免一次性大并集）
    chunk_size = 100
    union_chunks = []
    for i in range(0, len(all_to_remove), chunk_size):
        chunk = all_to_remove.iloc[i:i+chunk_size]
        # 先缓冲再并集
        buffered = chunk.geometry.buffer(0.1).unary_union
        union_chunks.append(buffered)
    all_union = unary_union(union_chunks)
    if not all_union.is_empty:
        all_union = all_union.buffer(-0.1)  # 负缓冲回缩
    if not all_union.is_empty:
        temp_union = gpd.GeoDataFrame(geometry=[all_union], crs=proj_crs)
        temp_union = clean_geometry(temp_union, min_area=1e-6)
        if len(temp_union) > 0:
            all_union = temp_union.geometry.iloc[0]
        else:
            all_union = None

    if all_union and not all_union.is_empty:
        def diff_geom(g):
            try:
                diff = g.difference(all_union)
                if diff.is_empty:
                    return None
                diff_gdf = gpd.GeoDataFrame(geometry=[diff], crs=proj_crs)
                diff_gdf = clean_geometry(diff_gdf, min_area=1e-6)
                if len(diff_gdf) == 0:
                    return None
                return diff_gdf.geometry.iloc[0]
            except:
                return None
        remaining['geometry'] = remaining.geometry.apply(diff_geom)
        remaining = remaining[~remaining.geometry.isna()].reset_index(drop=True)

# 拆分剩余地块中的 MultiPolygon
remaining = explode_multipolygons(remaining).reset_index(drop=True)

# ========== Step 4: 构建最终结果集 ==========
print("\n=== 构建最终结果集 ===")
final_result_list = []
for gdf, label in [(green_clipped, '绿地'), (industrial_clipped, '工业用地'),
                   (traffic_clipped, '交通用地'), (water_clipped, '水体')]:
    if len(gdf) > 0:
        gdf = gdf.copy()
        gdf['类别'] = label
        final_result_list.append(gdf)

if len(remaining) > 0:
    remaining['类别'] = '未赋值'
    final_result_list.append(remaining)

final_result = gpd.GeoDataFrame(pd.concat(final_result_list, ignore_index=True), crs=proj_crs)
final_result = final_result.reset_index(drop=True)
print(f"合并后总要素数：{len(final_result)}")

# ========== Step 5: 拓扑检查与重叠处理 ==========
def check_and_fix_overlaps_fast(gdf, priority_order):
    print(f"\n=== 开始重叠修复 ===")
    groups = {cls: gdf[gdf['类别'] == cls].copy() for cls in priority_order}
    processed_gdfs = []
    all_processed_geoms = []

    for i, current_class in enumerate(priority_order):
        current_gdf = groups[current_class]
        if len(current_gdf) == 0:
            continue

        if i > 0 and all_processed_geoms:
            idx = index.Index()
            for j, (geom, _) in enumerate(all_processed_geoms):
                if not geom.is_empty and geom.is_valid:
                    idx.insert(j, geom.bounds)

            processed_rows = []
            for _, row in current_gdf.iterrows():
                geom = row.geometry
                if geom.is_empty or not geom.is_valid:
                    continue

                possible = list(idx.intersection(geom.bounds))
                if not possible:
                    processed_rows.append(row.copy())
                    continue

                higher_geoms = [all_processed_geoms[m][0] for m in possible if not all_processed_geoms[m][0].is_empty and all_processed_geoms[m][0].is_valid]
                if not higher_geoms:
                    processed_rows.append(row.copy())
                    continue

                higher_union = unary_union(higher_geoms)
                diff = geom.difference(higher_union)
                if diff.is_empty:
                    continue

                diff_gdf = gpd.GeoDataFrame(geometry=[diff], crs=gdf.crs)
                diff_gdf = clean_geometry(diff_gdf, min_area=1e-6)
                if len(diff_gdf) == 0:
                    continue
                for poly in diff_gdf.geometry:
                    if poly.area > 0:
                        new_row = row.copy()
                        new_row.geometry = poly
                        processed_rows.append(new_row)

            if processed_rows:
                processed_gdf = gpd.GeoDataFrame(processed_rows, crs=gdf.crs)
                processed_gdfs.append(processed_gdf)
                for _, row in processed_gdf.iterrows():
                    if not row.geometry.is_empty and row.geometry.is_valid:
                        all_processed_geoms.append((row.geometry, row['类别']))
        else:
            processed_gdfs.append(current_gdf)
            for _, row in current_gdf.iterrows():
                if not row.geometry.is_empty and row.geometry.is_valid:
                    all_processed_geoms.append((row.geometry, row['类别']))

    if processed_gdfs:
        final = gpd.GeoDataFrame(pd.concat(processed_gdfs, ignore_index=True), crs=gdf.crs)
    else:
        final = gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return final

priority_order = ['水体','绿地',  '工业用地', '交通用地', '未赋值']
final_result = check_and_fix_overlaps_fast(final_result, priority_order)

# ========== Step 6: 最终拓扑清理 ==========
print("\n=== 最终拓扑清理 ===")
final_result = clean_geometry(final_result, min_area=1.0)  # 删除面积小于1平方米的碎片
print(f"清理后要素数：{len(final_result)}")

# 检查并修复仍存在的无效几何
invalid = ~final_result.geometry.is_valid
if invalid.any():
    print(f"  仍有 {invalid.sum()} 个无效几何，尝试再次修复")
    final_result = clean_geometry(final_result, min_area=1.0)

# 确保只有多边形
final_result = final_result[final_result.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].reset_index(drop=True)
# 拆分 MultiPolygon
if any(final_result.geometry.geom_type == 'MultiPolygon'):
    print("  拆分 MultiPolygon...")
    final_result = explode_multipolygons(final_result).reset_index(drop=True)

# 重建 FID
final_result['FID'] = range(1, len(final_result) + 1)
# =============================================================================
# 新增模块：联合分析 + 几何修复（前后） + 删除相同项（基于 shape 字段）
# =============================================================================
print("\n" + "="*60)
print("开始执行最终处理：联合分析、几何修复、删除相同项...")

# 1. 如果需要联合分析，请在此处定义要联合的图层列表
#    例如，如果您有多个独立的要素图层（如原始地块、各类用地等），
#    可以将其组合成列表 union_layers。这里默认只使用 remaining 自身，
#    联合自身无意义，因此跳过。
union_layers = [remaining]   # 可修改为 [parcel, green_clipped, ...] 等
if len(union_layers) > 1:
    print("执行联合分析（Union）...")
    # 依次叠加联合
    union_result = union_layers[0]
    for layer in union_layers[1:]:
        union_result = gpd.overlay(union_result, layer, how='union')
    # 清理联合结果
    union_result = clean_geometry(union_result, min_area=1e-6)
    remaining = union_result
    print(f"  联合后要素数：{len(remaining)}")
else:
    print("未提供多个图层，跳过联合分析。")

# 2. 几何修复（第一次）
print("执行几何修复（第一次）...")
remaining = clean_geometry(remaining, min_area=1.0)   # 1 平方米阈值
print(f"  修复后要素数：{len(remaining)}")

# 3. 删除相同项（基于 shape 字段）
print("删除几何重复项...")
# 精确去重（也可使用容差去重，如需容差请修改 tolerance 参数）
remaining = remaining.drop_duplicates(subset='geometry', keep='first')
print(f"  去重后要素数：{len(remaining)}")

# 4. 几何修复（第二次）
print("执行几何修复（第二次）...")
remaining = clean_geometry(remaining, min_area=1.0)
print(f"  再次修复后要素数：{len(remaining)}")

# 5. 确保所有几何为多边形并拆分多部件
remaining = explode_multipolygons(remaining).reset_index(drop=True)

# 6. 重建 FID
remaining['FID'] = range(1, len(remaining) + 1)
print("最终处理完成。")
# =============================================================================
# ========== Step 7: 保存中间结果 ==========
output_path = os.path.join(output_dir, '地块_clip.shp')
remove_shapefile_files(output_path)
final_result.to_file(output_path)
print(f"\n中间结果已保存至：{output_path}")
# =============================================================================
# =============================================================================
# 新增模块：细碎、细长要素处理
# =============================================================================
print("\n" + "="*60)
print("开始处理细碎、细长要素...")

input_shp = os.path.join(output_dir, '地块_clip.shp')
if not os.path.exists(input_shp):
    raise FileNotFoundError(f"未找到文件：{input_shp}")
remaining = gpd.read_file(input_shp)
print(f"初始要素数量：{len(remaining)}")
if '类别' in remaining.columns:
    remaining['类别'] = remaining['类别'].astype(str)

# 确保在投影坐标系下
remaining = ensure_projected_crs(remaining, proj_crs)

# ---------- 辅助函数（针对此模块） ----------
def safe_area(geom):
    if geom is None or geom.is_empty:
        return 0.0
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
        return geom.area
    except:
        return 0.0

def morphological_skeleton(geom, buffer_size=15):
    if geom is None or geom.is_empty:
        return None
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
        buffered = geom.buffer(-buffer_size)
        if buffered.is_empty:
            return None
        return buffered.buffer(buffer_size)
    except:
        return None

def aspect_ratio(geom):
    if geom is None or geom.is_empty:
        return 0.0
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
        rect = geom.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
        edges = []
        for i in range(4):
            dx = coords[i][0] - coords[i+1][0]
            dy = coords[i][1] - coords[i+1][1]
            edges.append(math.hypot(dx, dy))
        edges.sort()
        return edges[-1] / edges[0] if edges[0] > 0 else 0.0
    except:
        return 0.0

def shape_index(geom):
    """紧凑度指数 = 周长² / (4π * 面积)，圆为1，越大越细长"""
    if geom is None or geom.is_empty:
        return float('inf')
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
        perimeter = geom.length
        area = geom.area
        if area <= 0:
            return float('inf')
        return (perimeter * perimeter) / (4 * math.pi * area)
    except:
        return float('inf')

# ---------- Step 1: 拆分多部分要素 ----------
print("\n[Step 1] 拆分多部分几何...")
remaining = explode_multipolygons(remaining).reset_index(drop=True)
remaining=final_processing(remaining)
print(f"  拆分后要素数量：{len(remaining)}")
# 保存 Step1 结果
if len(remaining) > 0:
    step1_path = os.path.join(output_dir, 'Step1_拆分后.shp')
    remove_shapefile_files(step1_path)
    remaining.to_file(step1_path)
    print(f"  已保存 Step1 结果：{step1_path}")

# ---------- Step 2: 筛选小面积要素（< 2500㎡） ----------
print("\n[Step 2] 筛选小面积要素...")
remaining=final_processing(remaining)
remaining['_area'] = remaining.geometry.apply(safe_area)
small_mask = remaining['_area'] < 2500
biaoji = remaining[small_mask].copy().reset_index(drop=True)
remaining = remaining[~small_mask].copy().reset_index(drop=True)
print(f"  移入 biaoji 的小要素数量：{len(biaoji)}")
remaining.drop(columns=['_area'], inplace=True)
remaining=final_processing(remaining)
biaoji.drop(columns=['_area'], inplace=True)

# 保存 Step2 结果
if len(remaining) > 0:
    step2_big_path = os.path.join(output_dir, 'Step2_大要素.shp')
    remove_shapefile_files(step2_big_path)
    remaining.to_file(step2_big_path)
    print(f"  已保存大要素：{step2_big_path}")
if len(biaoji) > 0:
    step2_small_path = os.path.join(output_dir, 'Step2_小面积要素.shp')
    remove_shapefile_files(step2_small_path)
    biaoji.to_file(step2_small_path)
    print(f"  已保存小面积要素：{step2_small_path}")

# ---------- Step 3: 识别细长部分并裁剪 ----------
print("\n[Step 3] 识别细长部分...")
fuben = remaining.copy(deep=True).reset_index(drop=True)   # 备份，用于遍历
new_biaoji_list = []
to_update = {}

BUFFER_SIZE = 15
AREA_THRESHOLD = 1000
RATIO_THRESHOLD = 3.0
PERIMETER_THRESHOLD = 200
SHAPE_INDEX_THRESHOLD = 2.0

for idx in range(len(fuben)):
    row = fuben.loc[idx]
    geom_orig = row.geometry
    if geom_orig is None or geom_orig.is_empty:
        continue

    main_body = morphological_skeleton(geom_orig, BUFFER_SIZE)
    if main_body is None or main_body.is_empty:
        new_biaoji_list.append(row)
        continue

    try:
        outside = geom_orig.difference(main_body)
    except:
        continue
    if outside.is_empty:
        continue

    if outside.geom_type == 'Polygon':
        parts = [outside]
    elif outside.geom_type == 'MultiPolygon':
        parts = list(outside.geoms)
    else:
        continue

    merge_parts = []
    slender_parts = []
    for part in parts:
        area_part = safe_area(part)
        # 满足任一条件即合并回主体
        if (area_part < AREA_THRESHOLD or
            aspect_ratio(part) < RATIO_THRESHOLD or
            shape_index(part) < SHAPE_INDEX_THRESHOLD or
            part.length < PERIMETER_THRESHOLD):
            merge_parts.append(part)
        else:
            slender_parts.append(part)

    if merge_parts:
        merged_main = unary_union([main_body] + merge_parts)
        to_update[idx] = merged_main
    else:
        to_update[idx] = main_body

    for part in slender_parts:
        new_row = row.copy()
        new_row.geometry = part
        new_biaoji_list.append(new_row)

# 更新 remaining 的几何
for idx, new_geom in to_update.items():
    remaining.at[idx, 'geometry'] = new_geom

# 保存 Step3 主体更新后的 remaining
if len(remaining) > 0:
    step3_main_path = os.path.join(output_dir, 'Step3_主体更新后.shp')
    remove_shapefile_files(step3_main_path)
    remaining.to_file(step3_main_path)
    print(f"  已保存主体更新后的要素：{step3_main_path}")

# 保存本次识别出的细长部分（尚未加入 biaoji）
if new_biaoji_list:
    slender_gdf = gpd.GeoDataFrame(new_biaoji_list, crs=remaining.crs)
    step3_slender_path = os.path.join(output_dir, 'Step3_细长部分.shp')
    remove_shapefile_files(step3_slender_path)
    slender_gdf.to_file(step3_slender_path)
    print(f"  已保存细长部分：{step3_slender_path}")

# 将细长部分加入 biaoji
if new_biaoji_list:
    new_biaoji_gdf = gpd.GeoDataFrame(new_biaoji_list, crs=remaining.crs)
    biaoji = pd.concat([biaoji, new_biaoji_gdf], ignore_index=True)

print(f"  处理后 remaining 要素数量：{len(remaining)}")
print(f"  当前 biaoji 总数量：{len(biaoji)}")

# 保存合并后的 biaoji（小面积+细长）
if len(biaoji) > 0:
    step3_biaoji_path = os.path.join(output_dir, 'Step3_合并后biaoji.shp')
    remove_shapefile_files(step3_biaoji_path)
    biaoji.to_file(step3_biaoji_path)
    print(f"  已保存合并后 biaoji：{step3_biaoji_path}")

# ---------- Step 4: biaoji 融合（与 remaining 及内部） ----------
if len(biaoji) > 0:
    # 拆分并清理 biaoji
    biaoji = explode_multipolygons(biaoji).reset_index(drop=True)
    biaoji['_area'] = biaoji.geometry.apply(safe_area)
    biaoji.sort_values('_area', ascending=True, inplace=True)
    biaoji.reset_index(drop=True, inplace=True)
    biaoji.drop(columns=['_area'], inplace=True)

    # 保存融合前的 biaoji（用于对比）
    step4_before_path = os.path.join(output_dir, 'Step4_融合前biaoji.shp')
    remove_shapefile_files(step4_before_path)
    biaoji.to_file(step4_before_path)
    print(f"  已保存融合前 biaoji：{step4_before_path}")

    # ========== 强化的融合函数（增加缓冲区扩展合并） ==========
    def aggressive_merge(remaining, biaoji, buffer_touch=0.5, iter_max=5):
        """
        将 biaoji 积极合并到 remaining，处理包含、接触、邻近等所有情况。
        参数:
            remaining: GeoDataFrame (大要素)
            biaoji: GeoDataFrame (待融合的小要素)
            buffer_touch: 用于判断接触的缓冲距离（米），设小值以避免过度扩展
            iter_max: 最大迭代次数
        返回:
            remaining, biaoji
        """
        if len(biaoji) == 0 or len(remaining) == 0:
            return remaining, biaoji

        # 辅助函数：清理几何
        def fix_geom(g):
            if g is None or g.is_empty:
                return None
            if not g.is_valid:
                g = make_valid(g)
            # 确保是面类型，如果是线或点，返回 None
            if g.geom_type not in ('Polygon', 'MultiPolygon'):
                # 尝试 buffer(0) 转成多边形
                g = g.buffer(0)
                if g.geom_type not in ('Polygon', 'MultiPolygon'):
                    return None
            return g

        # 预清理
        remaining=final_processing(remaining)
        remaining.geometry = remaining.geometry.apply(fix_geom)
        remaining = remaining[~remaining.geometry.isna()].reset_index(drop=True)
        biaoji.geometry = biaoji.geometry.apply(fix_geom)
        biaoji = biaoji[~biaoji.geometry.isna()].reset_index(drop=True)

        # ---------- 正常迭代（接触/相交） ----------
        for iteration in range(iter_max):
            prev_biaoji_count = len(biaoji)
            print(f"    正常迭代 {iteration+1}: 融合前 biaoji 数量 = {len(biaoji)}")

            if len(biaoji) == 0:
                break

            # 构建空间索引
            rem_sindex = remaining.sindex
            remaining_geoms = remaining.geometry.tolist()
            biaoji_geoms = biaoji.geometry.tolist()

            to_remove = set()
            updates = {}  # remaining 索引 -> 新几何

            for idx_b, geom_b in enumerate(biaoji_geoms):
                if idx_b in to_remove:
                    continue
                if geom_b is None or geom_b.is_empty:
                    continue

                # 查找可能相交的 remaining 要素（扩展一点边界）
                bounds_b = geom_b.bounds
                expanded = (bounds_b[0] - buffer_touch, bounds_b[1] - buffer_touch,
                            bounds_b[2] + buffer_touch, bounds_b[3] + buffer_touch)
                possible = list(rem_sindex.intersection(expanded))

                if not possible:
                    continue

                candidates = []

                for idx_r in possible:
                    geom_r = updates.get(idx_r, remaining_geoms[idx_r])
                    if geom_r is None or geom_r.is_empty:
                        continue
                    if not geom_r.is_valid:
                        geom_r = make_valid(geom_r)
                        if geom_r.is_empty:
                            continue

                    # 包含关系
                    if geom_r.contains(geom_b):
                        candidates.append((idx_r, float('inf'), geom_r, True))
                        break
                    else:
                        if geom_r.intersects(geom_b):
                            inter = geom_r.intersection(geom_b)
                            if not inter.is_empty:
                                if inter.geom_type in ('Polygon', 'MultiPolygon'):
                                    score = inter.area / geom_b.area
                                else:
                                    score = inter.length / geom_b.length if geom_b.length > 0 else 0
                                candidates.append((idx_r, score, geom_r, False))
                        else:
                            buff_b = geom_b.buffer(buffer_touch)
                            if buff_b.intersects(geom_r):
                                inter = buff_b.intersection(geom_r)
                                if not inter.is_empty:
                                    if inter.geom_type in ('Polygon', 'MultiPolygon'):
                                        score = inter.area / (geom_b.area + 1e-6) * 0.5
                                    else:
                                        score = inter.length / (geom_b.length + 1e-6) * 0.5
                                    candidates.append((idx_r, score, geom_r, False))

                if not candidates:
                    continue

                candidates.sort(key=lambda x: x[1], reverse=True)
                best_idx, best_score, best_geom, is_contain = candidates[0]

                # 合并 biaoji 到最佳候选
                try:
                    new_geom_best = unary_union([best_geom, geom_b])
                    if not new_geom_best.is_valid:
                        new_geom_best = make_valid(new_geom_best)
                    if new_geom_best.is_empty:
                        continue
                    updates[best_idx] = new_geom_best
                except:
                    continue

                # 从其他候选的 remaining 要素中减去该 biaoji
                for cand in candidates[1:]:
                    idx_other = cand[0]
                    geom_other = updates.get(idx_other, remaining_geoms[idx_other])
                    if geom_other is None or geom_other.is_empty:
                        continue
                    if not geom_other.intersects(geom_b):
                        continue
                    try:
                        diff = geom_other.difference(geom_b)
                        if diff.is_empty:
                            continue
                        if not diff.is_valid:
                            diff = make_valid(diff)
                        if diff.is_empty:
                            continue
                        updates[idx_other] = diff
                    except:
                        continue

                to_remove.add(idx_b)

            # 应用 updates
            for idx, new_geom in updates.items():
                remaining.at[idx, 'geometry'] = new_geom

            # 移除已融合的 biaoji
            biaoji = biaoji[~biaoji.index.isin(to_remove)].copy().reset_index(drop=True)

            print(f"    正常迭代 {iteration+1}: 融合后 biaoji 数量 = {len(biaoji)}")
            if len(biaoji) == prev_biaoji_count:
                break

        # ---------- 缓冲区扩展合并（如果仍有 biaoji） ----------
        if len(biaoji) > 0:
            print(f"  正常迭代结束，剩余 biaoji {len(biaoji)}，开始缓冲区扩展合并...")
            buffer_sizes = [1.0, 2.0, 3.0]  # 依次尝试的缓冲距离（米）
            for buffer_size in buffer_sizes:
                prev_count = len(biaoji)
                biaoji=final_processing(biaoji)
                print(f"    尝试缓冲区 {buffer_size}m...")
                # 使用当前缓冲区大小进行合并
                # 注意：这里需要循环合并直到无变化（因为一次合并后可能其他 biaoji 也符合条件）
                while True:
                    if len(biaoji) == 0:
                        break
                    # 构建空间索引
                    rem_sindex = remaining.sindex
                    remaining_geoms = remaining.geometry.tolist()
                    biaoji_geoms = biaoji.geometry.tolist()
                    to_remove = set()
                    updates = {}

                    for idx_b, geom_b in enumerate(biaoji_geoms):
                        if idx_b in to_remove:
                            continue
                        if geom_b is None or geom_b.is_empty:
                            continue

                        # 创建缓冲区
                        buffered = geom_b.buffer(buffer_size)
                        if buffered.is_empty:
                            continue

                        # 查找与缓冲区相交的 remaining 要素
                        possible = list(rem_sindex.intersection(buffered.bounds))
                        if not possible:
                            continue

                        # 计算每个候选与缓冲区的相交面积，取最大
                        best_idx = None
                        best_area = -1
                        best_geom = None
                        for idx_r in possible:
                            geom_r = updates.get(idx_r, remaining_geoms[idx_r])
                            if geom_r is None or geom_r.is_empty:
                                continue
                            if not geom_r.is_valid:
                                geom_r = make_valid(geom_r)
                                if geom_r.is_empty:
                                    continue
                            inter = buffered.intersection(geom_r)
                            if not inter.is_empty and inter.area > best_area:
                                best_area = inter.area
                                best_idx = idx_r
                                best_geom = geom_r

                        if best_idx is None:
                            continue

                        # 合并前裁剪：从 geom_b 中减去与 best_geom 重叠的部分（避免合并后重叠）
                        try:
                            # 先计算重叠部分
                            overlap = geom_b.intersection(best_geom)
                            if not overlap.is_empty:
                                # 减去重叠部分
                                geom_to_merge = geom_b.difference(overlap)
                                if geom_to_merge.is_empty:
                                    # 如果 biaoji 完全被包含，则无需保留 biaoji 的几何，直接合并即可
                                    new_geom_best = best_geom  # 但这样会丢失空洞？实际上 biaoji 被包含，合并后不变
                                    # 更准确：将 biaoji 合并到 best_geom 就是 best_geom 自身（因为包含）
                                    # 但我们需要更新 updates，不过几何无变化，可以跳过
                                    # 为了简单，直接保留 best_geom，但更新 updates 无效，跳过
                                    # 我们仍将该 biaoji 标记为移除，但不更新几何
                                    to_remove.add(idx_b)
                                    continue
                            else:
                                geom_to_merge = geom_b

                            # 合并
                            new_geom_best = unary_union([best_geom, geom_to_merge])
                            if not new_geom_best.is_valid:
                                new_geom_best = make_valid(new_geom_best)
                            if new_geom_best.is_empty:
                                continue
                            updates[best_idx] = new_geom_best
                        except:
                            continue

                        # 从其他可能与缓冲区相交的 remaining 要素中减去该 biaoji
                        for idx_r in possible:
                            if idx_r == best_idx:
                                continue
                            geom_other = updates.get(idx_r, remaining_geoms[idx_r])
                            if geom_other is None or geom_other.is_empty:
                                continue
                            if not geom_other.intersects(geom_b):
                                continue
                            try:
                                diff = geom_other.difference(geom_b)
                                if diff.is_empty:
                                    continue
                                if not diff.is_valid:
                                    diff = make_valid(diff)
                                if diff.is_empty:
                                    continue
                                updates[idx_r] = diff
                            except:
                                continue

                        to_remove.add(idx_b)

                    # 应用 updates
                    for idx, new_geom in updates.items():
                        remaining.at[idx, 'geometry'] = new_geom

                    # 移除已融合的 biaoji
                    biaoji = biaoji[~biaoji.index.isin(to_remove)].copy().reset_index(drop=True)

                    if len(biaoji) == prev_count:
                        break
                    prev_count = len(biaoji)
                    print(f"      缓冲区 {buffer_size}m 合并后 biaoji 数量 = {len(biaoji)}")

                if len(biaoji) == 0:
                    break
                print(f"    缓冲区 {buffer_size}m 结束，剩余 biaoji {len(biaoji)}")

        # 最终清理
        remaining=final_processing(remaining)
        remaining = clean_geometry(remaining, min_area=1e-6)
        return remaining, biaoji

    # 调用融合函数
    remaining=final_processing(remaining)
    remaining, biaoji = aggressive_merge(remaining, biaoji, buffer_touch=0.5, iter_max=5)

    # 保存融合后 remaining（清理非多边形几何）
    if len(remaining) > 0:
        remaining = clean_geometry(remaining, min_area=1e-6)
        step4_remaining_path = os.path.join(output_dir, 'Step4_融合后remaining.shp')
        remove_shapefile_files(step4_remaining_path)
        remaining.to_file(step4_remaining_path)
        print(f"  已保存融合后 remaining：{step4_remaining_path}")

    # 保存剩余 biaoji（融合后可能还有未被合并的小面）
    if len(biaoji) > 0:
        biaoji = clean_geometry(biaoji, min_area=1e-6)
        step4_biaoji_remain_path = os.path.join(output_dir, 'Step4_剩余biaoji.shp')
        remove_shapefile_files(step4_biaoji_remain_path)
        biaoji.to_file(step4_biaoji_remain_path)
        print(f"  已保存剩余 biaoji：{step4_biaoji_remain_path}")

    # 4.2 biaoji 内部融合（可选，使用相同得分机制）
    if len(biaoji) > 1:
        bj_sindex = biaoji.sindex
        bj_to_remove = set()
        for idx_b, row_b in biaoji.iterrows():
            if idx_b in bj_to_remove:
                continue
            geom_b = row_b.geometry
            if geom_b is None or geom_b.is_empty:
                continue
            if not geom_b.is_valid:
                geom_b = make_valid(geom_b)
                if geom_b.is_empty:
                    continue

            possible = list(bj_sindex.intersection(geom_b.bounds))
            best_other = None
            best_score = -1
            for idx_other in possible:
                if idx_other <= idx_b or idx_other in bj_to_remove:
                    continue
                geom_other = biaoji.geometry.iloc[idx_other]
                if geom_other is None or geom_other.is_empty:
                    continue
                if not geom_other.is_valid:
                    geom_other = make_valid(geom_other)
                    if geom_other.is_empty:
                        continue
                try:
                    if geom_b.contains(geom_other) or geom_other.within(geom_b):
                        best_other = idx_other
                        best_score = float('inf')
                        break
                    if geom_b.intersects(geom_other):
                        inter = geom_b.intersection(geom_other)
                        if inter.is_empty:
                            continue
                        if inter.geom_type in ('LineString', 'MultiLineString'):
                            score = inter.length / geom_b.length if geom_b.length > 0 else 0
                        elif inter.geom_type in ('Polygon', 'MultiPolygon'):
                            score = inter.area / geom_b.area if geom_b.area > 0 else 0
                        else:
                            continue
                        if score > best_score:
                            best_score = score
                            best_other = idx_other
                except:
                    continue

            if best_other is not None and (best_score == float('inf') or best_score >= 0.05):
                new_geom = unary_union([geom_b, biaoji.geometry.iloc[best_other]])
                biaoji.at[best_other, 'geometry'] = new_geom
                bj_to_remove.add(idx_b)

        biaoji = biaoji[~biaoji.index.isin(bj_to_remove)].copy().reset_index(drop=True)

        # 保存内部融合后的剩余 biaoji
        if len(biaoji) > 0:
            biaoji = clean_geometry(biaoji, min_area=1e-6)
            step4_internal_path = os.path.join(output_dir, 'Step4_内部融合后biaoji.shp')
            remove_shapefile_files(step4_internal_path)
            biaoji.to_file(step4_internal_path)
            print(f"  已保存内部融合后 biaoji：{step4_internal_path}")

print(f"  融合后 remaining 要素数量：{len(remaining)}")
print(f"  融合后 biaoji 剩余数量：{len(biaoji)}")

# ---------- Step 5: 将剩余 biaoji 加入 remaining ----------
print("\n[Step 5] 将剩余 biaoji 加入 remaining...")
remaining=final_processing(remaining)
if len(biaoji) > 0:
    common_cols = list(set(remaining.columns) & set(biaoji.columns))
    if 'geometry' not in common_cols:
        common_cols.append('geometry')
    remaining = pd.concat([remaining[common_cols], biaoji[common_cols]], ignore_index=True)
    print(f"  合并后 remaining 要素数量：{len(remaining)}")
    # 保存合并后结果（清理几何）
    remaining = clean_geometry(remaining, min_area=1e-6)
    step5_path = os.path.join(output_dir, 'Step5_合并后.shp')
    remove_shapefile_files(step5_path)
    remaining.to_file(step5_path)
    print(f"  已保存 Step5 结果：{step5_path}")

# ---------- Step 6: 最终拓扑整理与重叠检查 ----------
print("\n[Step 6] 最终拓扑整理...")

# 先清理无效和极小面
remaining=final_processing(remaining)
remaining = clean_geometry(remaining, min_area=1.0)
remaining = remaining[remaining.geometry.area > 0].reset_index(drop=True)

# 确保所有几何为 Polygon（拆分 MultiPolygon）
remaining=final_processing(remaining)
remaining = explode_multipolygons(remaining).reset_index(drop=True)

# 再次检查重叠（使用之前的优先级顺序）
if len(remaining) > 0:
    remaining = check_and_fix_overlaps_fast(remaining, priority_order)

# 最终清理
remaining = clean_geometry(remaining, min_area=1.0)
remaining = remaining[remaining.geometry.area > 0].reset_index(drop=True)

# 保存最终整理后结果（尚未转WGS84）
step6_path = os.path.join(output_dir, 'Step6_最终整理后.shp')
remove_shapefile_files(step6_path)
remaining.to_file(step6_path)
print(f"  已保存 Step6 结果：{step6_path}")

# 重建 FID
remaining['FID'] = range(1, len(remaining) + 1)
remaining=final_processing(remaining)
# 转换回 WGS84
# remaining = to_geographic_crs(remaining)

# ---------- Step 7: 保存最终结果 ----------
output_final = os.path.join(output_dir, '最终地块4（全域水体）.shp')
remove_shapefile_files(output_final)
remaining.to_file(output_final)
print(f"\n处理完成！最终要素数量：{len(remaining)}")
print(f"结果已保存至：{output_final}")

# 验证总面积一致性
original = gpd.read_file(input_shp)
original_proj = ensure_projected_crs(original, proj_crs)
original_area = original_proj.geometry.area.sum()
final_proj = ensure_projected_crs(remaining, proj_crs)
final_area = final_proj.geometry.area.sum()
print(f"原始总面积（投影）: {original_area:.2f} ㎡")
print(f"最终总面积（投影）: {final_area:.2f} ㎡")
print(f"面积差异: {final_area - original_area:.2f} ㎡ (应接近0)")