import geopandas as gpd
import os
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon, LineString
import glob
import shapely
from shapely.validation import make_valid
import math
from shapely.ops import unary_union

# 获取脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))

# Define paths
city = 'chengdu'
city_name = '成都市'  # 城市中文名
parcel_path = os.path.join(script_dir, f'data/data_dikuai/{city}/chengdu.shp')
osm_dir = os.path.join(script_dir, 'data/data_osm/sichuan-260107-free.shp')
water_path = os.path.join(osm_dir, 'gis_osm_water_a_free_1.shp')
landuse_path = os.path.join(osm_dir, 'gis_osm_landuse_a_free_1.shp')
traffic_path = os.path.join(osm_dir, 'gis_osm_traffic_a_free_1.shp')
transport_path = os.path.join(osm_dir, 'gis_osm_transport_a_free_1.shp')

# 建成区边界路径
builtup_pattern = os.path.join(script_dir, f'data/data_area/建成区2025/{city_name}*.shp')

output_dir = os.path.join(script_dir, f'./output/{city}.output')
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# ========== 辅助函数：删除已存在的 shapefile 文件 ==========
def remove_shapefile_files(filepath):
    """删除与给定路径相关的所有 shapefile 文件"""
    base = os.path.splitext(filepath)[0]
    extensions = ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix']
    for ext in extensions:
        f = base + ext
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"  已删除旧文件：{f}")
            except Exception as e:
                print(f"  无法删除 {f}：{e}")

# Read files
print("正在读取文件...")
parcel = gpd.read_file(parcel_path)
water = gpd.read_file(water_path)
landuse = gpd.read_file(landuse_path)
traffic = gpd.read_file(traffic_path)
transport = gpd.read_file(transport_path)

# 读取建成区边界
print("\n=== 读取建成区边界 ===")
builtup_files = glob.glob(builtup_pattern)
if not builtup_files:
    raise FileNotFoundError(f"未找到建成区边界文件：{builtup_pattern}")
builtup = gpd.read_file(builtup_files[0])
print(f"已加载建成区边界：{builtup_files[0]}")

# 统一转换为地理坐标系 GCS_WGS_1984 (EPSG:4326)
print("\n=== 坐标系统一转换 (GCS_WGS_1984) ===")
target_crs = 'EPSG:4326'

def check_and_convert_crs(gdf, layer_name):
    if gdf.crs is None:
        print(f"  ⚠️  {layer_name} 缺少坐标系信息，跳过转换")
        return gdf
    epsg_code = gdf.crs.to_epsg()
    crs_str = str(gdf.crs)
    if epsg_code == 4326:
        print(f"  [OK] {layer_name} 已是 WGS84")
        return gdf
    else:
        crs_name = gdf.crs.name.upper()
        is_cgcs2000_projected = (
            'CGCS2000' in crs_name or 
            (epsg_code and epsg_code in range(4491, 4524))
        ) and epsg_code != 4490
        if is_cgcs2000_projected:
            gdf = gdf.to_crs('EPSG:4490')
            gdf = gdf.to_crs(target_crs)
            return gdf
        else:
            return gdf.to_crs(target_crs)

parcel = check_and_convert_crs(parcel, "地块数据")
water = check_and_convert_crs(water, "水体数据")
landuse = check_and_convert_crs(landuse, "土地利用数据")
traffic_merged_temp = gpd.GeoDataFrame(pd.concat([traffic, transport], ignore_index=True))
traffic_merged_temp = check_and_convert_crs(traffic_merged_temp, "交通用地数据")
builtup = check_and_convert_crs(builtup, "建成区边界")

print("\n所有图层已统一为 GCS_WGS_1984 (EPSG:4326)")

# Step 1: 提取各类用地并裁剪到地块范围
print("\n=== 提取并裁剪各地类到地块范围 ===")
green_classes = ['forest', 'grass', 'park', 'scrub', 'meadow']
green = landuse[landuse['fclass'].isin(green_classes)]
if len(green) > 0:
    green_clipped = gpd.overlay(green, parcel, how='intersection')
else:
    green_clipped = green.copy()

industrial = landuse[landuse['fclass'] == 'industrial']
if len(industrial) > 0:
    industrial_clipped = gpd.overlay(industrial, parcel, how='intersection')
else:
    industrial_clipped = industrial.copy()

if len(traffic_merged_temp) > 0:
    traffic_clipped = gpd.overlay(traffic_merged_temp, parcel, how='intersection')
else:
    traffic_clipped = traffic_merged_temp.copy()

# Step 2: 合并所有需要裁剪掉的用地类型（记录用）
all_obstacles_list = []
if len(green_clipped) > 0: all_obstacles_list.append(green_clipped)
if len(industrial_clipped) > 0: all_obstacles_list.append(industrial_clipped)
if len(traffic_clipped) > 0: all_obstacles_list.append(traffic_clipped)
if len(all_obstacles_list) > 0:
    all_obstacles = gpd.GeoDataFrame(pd.concat(all_obstacles_list, ignore_index=True))
else:
    all_obstacles = gpd.GeoDataFrame(columns=landuse.columns, crs=target_crs)

# Step 3: 从地块中移除所有障碍区域
print("\n=== 从地块中移除重叠区域 ===")
remaining = parcel.copy()
all_to_remove_list = []
if len(water) > 0:
    water_clipped = gpd.overlay(water, parcel, how='intersection')
    if len(water_clipped) > 0:
        all_to_remove_list.append(water_clipped)
if len(green_clipped) > 0: all_to_remove_list.append(green_clipped)
if len(industrial_clipped) > 0: all_to_remove_list.append(industrial_clipped)
if len(traffic_clipped) > 0: all_to_remove_list.append(traffic_clipped)

if len(all_to_remove_list) > 0:
    all_to_remove = gpd.GeoDataFrame(pd.concat(all_to_remove_list, ignore_index=True))
    all_union = all_to_remove.geometry.unary_union
    def diff_geom(g):
        if g is None or g.is_empty: return g
        try:
            return g.difference(all_union)
        except:
            try:
                return make_valid(g).difference(all_union)
            except:
                return g
    remaining['geometry'] = remaining.geometry.apply(diff_geom)
    remaining = remaining[~remaining.geometry.is_empty].reset_index(drop=True)

# Step 3.4: 处理水体要素
def explode_multipolygons(geo_df):
    exploded_rows = []
    for idx, row in geo_df.iterrows():
        geom = row.geometry
        if isinstance(geom, MultiPolygon):
            for polygon in geom.geoms:
                new_row = row.copy()
                new_row.geometry = polygon
                exploded_rows.append(new_row)
        elif isinstance(geom, Polygon):
            exploded_rows.append(row.copy())
    return gpd.GeoDataFrame(exploded_rows, crs=geo_df.crs)

water_clipped_for_result = None
if len(water) > 0:
    water_in_parcel = gpd.overlay(water, parcel, how='intersection')
    if len(water_in_parcel) > 0:
        water_clipped_for_result = explode_multipolygons(water_in_parcel)
        water_clipped_for_result = water_clipped_for_result.reset_index(drop=True)
        water_clipped_for_result['类别'] = '水体'

# Step 4: 准备最终结果
remaining = explode_multipolygons(remaining)
remaining = remaining.reset_index(drop=True)
remaining['类别'] = '未赋值'

final_result_list = []
if len(green_clipped) > 0:
    green_clipped = explode_multipolygons(green_clipped)
    green_clipped['类别'] = '绿地'
    final_result_list.append(green_clipped)
if len(industrial_clipped) > 0:
    industrial_clipped = explode_multipolygons(industrial_clipped)
    industrial_clipped['类别'] = '工业用地'
    final_result_list.append(industrial_clipped)
if len(traffic_clipped) > 0:
    traffic_clipped = explode_multipolygons(traffic_clipped)
    traffic_clipped['类别'] = '交通用地'
    final_result_list.append(traffic_clipped)
if water_clipped_for_result is not None and len(water_clipped_for_result) > 0:
    final_result_list.append(water_clipped_for_result)
final_result_list.append(remaining)

final_result = gpd.GeoDataFrame(pd.concat(final_result_list, ignore_index=True))
final_result = final_result.reset_index(drop=True)
final_result['FID'] = range(1, len(final_result) + 1)

# Step 5: 拓扑检查与重叠处理（原函数完全保留）
def check_and_fix_overlaps_fast(gdf, priority_order):
    # （此处为原函数完整实现，未做任何修改）
    print(f"\n=== 拓扑检查开始（使用空间索引优化）===")
    groups = {}
    for cls in priority_order:
        groups[cls] = gdf[gdf['类别'] == cls].copy()
    processed_gdfs = []
    all_processed_geoms = []
    for i, current_class in enumerate(priority_order):
        current_gdf = groups[current_class]
        if len(current_gdf) == 0: continue
        if i > 0 and len(all_processed_geoms) > 0:
            from rtree import index
            idx = index.Index()
            for j, (geom, _) in enumerate(all_processed_geoms):
                if not geom.is_empty and geom.is_valid:
                    idx.insert(j, geom.bounds)
            processed_rows = []
            total_clipped = 0
            for idx_row, row in current_gdf.iterrows():
                geom = row.geometry
                if geom.is_empty or not geom.is_valid: continue
                possible_matches_idx = list(idx.intersection(geom.bounds))
                if not possible_matches_idx:
                    processed_rows.append(row.copy())
                    continue
                higher_priority_union_parts = [all_processed_geoms[match_idx][0] for match_idx in possible_matches_idx if not all_processed_geoms[match_idx][0].is_empty and all_processed_geoms[match_idx][0].is_valid]
                if not higher_priority_union_parts:
                    processed_rows.append(row.copy())
                    continue
                higher_union = unary_union(higher_priority_union_parts)
                diff_result = geom.difference(higher_union)
                if diff_result.is_empty:
                    total_clipped += 1
                    continue
                if isinstance(diff_result, Polygon):
                    if diff_result.area > 0:
                        new_row = row.copy()
                        new_row.geometry = diff_result
                        processed_rows.append(new_row)
                elif isinstance(diff_result, MultiPolygon):
                    for poly in diff_result.geoms:
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
        final_gdf = gpd.GeoDataFrame(pd.concat(processed_gdfs, ignore_index=True), crs=gdf.crs)
    else:
        final_gdf = gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    return final_gdf

def check_overlaps_fast(gdf, tolerance=0.001):
    # （此处为原函数完整实现，未做任何修改）
    if len(gdf) < 2: return 0
    from rtree import index
    valid_mask = gdf.geometry.apply(lambda x: x is not None and x.is_valid and not x.is_empty)
    valid_gdf = gdf[valid_mask].reset_index(drop=True)
    if len(valid_gdf) < 2: return 0
    idx = index.Index()
    for i, geom in enumerate(valid_gdf.geometry):
        idx.insert(i, geom.bounds)
    overlap_count = 0
    checked_pairs = set()
    batch_size = 1000
    total = len(valid_gdf)
    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        for i in range(batch_start, batch_end):
            geom_i = valid_gdf.geometry.iloc[i]
            possible_matches = list(idx.intersection(geom_i.bounds))
            for j in possible_matches:
                if i >= j: continue
                pair_key = (i, j)
                if pair_key in checked_pairs: continue
                checked_pairs.add(pair_key)
                geom_j = valid_gdf.geometry.iloc[j]
                try:
                    if geom_i.intersects(geom_j):
                        intersection = geom_i.intersection(geom_j)
                        if intersection.area > tolerance:
                            overlap_count += 1
                except:
                    try:
                        g1 = make_valid(geom_i) if not geom_i.is_valid else geom_i
                        g2 = make_valid(geom_j) if not geom_j.is_valid else geom_j
                        if g1.intersects(g2):
                            intersection = g1.intersection(g2)
                            if intersection.area > tolerance:
                                overlap_count += 1
                    except:
                        continue
    return overlap_count

priority_order = ['绿地', '水体', '工业用地', '交通用地', '未赋值']
final_result = check_and_fix_overlaps_fast(final_result, priority_order)
# ========== 优化后的投影与拓扑修复函数（融合连通分量分组） ==========
def project_to_utm(gdf):
    """根据数据的平均经度自动确定 UTM 投影带，返回投影后的 GeoDataFrame 和 EPSG 代码。"""
    bounds = gdf.total_bounds
    lon_center = (bounds[0] + bounds[2]) / 2.0
    utm_zone = int((lon_center + 180) / 6) + 1
    epsg = 32600 + utm_zone  # 北半球
    print(f"  自动选择 UTM 投影: EPSG:{epsg} (zone {utm_zone})")
    gdf_proj = gdf.to_crs(f'EPSG:{epsg}')
    return gdf_proj, epsg
def fix_topo_issues_advanced(gdf, tolerance_m=0.001, area_threshold=1.0, max_iter=1):
    """
    高级拓扑修复（连通分量分组优化版）：
    1. 一次性检测所有重叠/距离过近问题；
    2. 删除小面积(<1㎡)及点状要素（距离过近错误）；
    3. 对剩余问题要素，利用并查集构建连通分量；
    4. 对每个分量内的重叠要素按类别分组合并，并与好要素交互；
    5. 距离过近要素单独与最近好要素合并（不看类别）。
    """
    from shapely.ops import unary_union
    from rtree import index
    from collections import defaultdict

    if len(gdf) < 2:
        return gdf

    gdf = gdf.reset_index(drop=True)
    n = len(gdf)
    print(f"  开始高级拓扑修复，共 {n} 个要素，容差={tolerance_m}m，面积阈值={area_threshold}m²")

    # 1. 检测所有拓扑问题
    print("  检测拓扑问题...")
    idx_all = index.Index()
    for i, geom in enumerate(gdf.geometry):
        if geom.is_valid and not geom.is_empty:
            idx_all.insert(i, geom.bounds)

    problem_type = [0] * n   # 0:无问题, 1:重叠, 2:距离过近
    overlap_pairs = []
    dist_pairs = []
    checked = set()
    valid_mask = [not geom.is_empty and geom.is_valid for geom in gdf.geometry]

    for i in range(n):
        if not valid_mask[i]:
            continue
        geom_i = gdf.geometry.iloc[i]
        candidates = list(idx_all.intersection(geom_i.bounds))
        for j in candidates:
            if i >= j or not valid_mask[j]:
                continue
            if (i, j) in checked:
                continue
            checked.add((i, j))
            geom_j = gdf.geometry.iloc[j]
            # 重叠检查
            if geom_i.intersects(geom_j):
                inter = geom_i.intersection(geom_j)
                if inter.area > tolerance_m * tolerance_m:
                    problem_type[i] = max(problem_type[i], 1)
                    problem_type[j] = max(problem_type[j], 1)
                    overlap_pairs.append((i, j))
                    continue
            # 距离检查
            dist = geom_i.distance(geom_j)
            if dist < tolerance_m:
                if problem_type[i] == 0:
                    problem_type[i] = 2
                if problem_type[j] == 0:
                    problem_type[j] = 2
                dist_pairs.append((i, j))

    # 2. 删除小面积和点状要素（类型2）
    all_bad = [i for i, t in enumerate(problem_type) if t > 0]
    to_delete = set()
    for i in all_bad:
        geom = gdf.geometry.iloc[i]
        if geom.is_empty:
            to_delete.add(i)
            continue
        if problem_type[i] == 2 and geom.geom_type in ('Point', 'MultiPoint'):
            to_delete.add(i)
            continue
        if geom.area < area_threshold:
            to_delete.add(i)
            continue

    # 剩余的问题要素（未被删除）
    bad_indices = [i for i in all_bad if i not in to_delete]
    good_indices = [i for i in range(n) if problem_type[i] == 0 and i not in to_delete]

    print(f"    无问题要素: {len(good_indices)}，有问题要素: {len(all_bad)}")
    print(f"    已删除小面积/点状要素: {len(to_delete)}")
    if not good_indices:
        print("    没有可用的好要素，无法修复，保留原样")
        return gdf

    # 3. 构建好要素的几何和属性
    good_geoms = []
    good_attrs = []
    for i in good_indices:
        geom = gdf.geometry.iloc[i]
        if not geom.is_valid:
            geom = make_valid(geom)
        if not geom.is_empty:
            good_geoms.append(geom)
            good_attrs.append(gdf.iloc[i])

    # 好要素的空间索引
    good_sindex = index.Index()
    for pos, geom in enumerate(good_geoms):
        good_sindex.insert(pos, geom.bounds)

    # 好要素并集（用于剪裁）
    union_good = unary_union(good_geoms) if good_geoms else None

    # 4. 处理重叠问题（类型1）—— 使用连通分量分组
    overlap_indices = [i for i in bad_indices if problem_type[i] == 1]
    if overlap_indices:
        print(f"    处理重叠问题，涉及 {len(overlap_indices)} 个要素")
        # 构建并查集，仅用于重叠要素
        parent = list(range(n))
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[ry] = rx
        for i, j in overlap_pairs:
            # 只考虑未被删除的重叠对
            if i not in to_delete and j not in to_delete:
                union(i, j)
        # 收集连通分量
        comp_map = defaultdict(list)
        for i in overlap_indices:
            if i not in to_delete:
                comp_map[find(i)].append(i)

        # 对每个分量处理
        for comp in comp_map.values():
            # 收集分量内所有几何，并确保有效
            comp_geoms = []
            comp_attrs = []
            for i in comp:
                geom = gdf.geometry.iloc[i]
                if not geom.is_valid:
                    geom = make_valid(geom)
                if not geom.is_empty:
                    comp_geoms.append(geom)
                    comp_attrs.append(gdf.iloc[i])
            if not comp_geoms:
                continue

            # 减去好要素并集
            if union_good is not None:
                comp_geoms = [g.difference(union_good) for g in comp_geoms]
            # 过滤空几何
            comp_geoms = [g for g in comp_geoms if not g.is_empty]

            # 按类别分组
            by_cat = defaultdict(list)
            for geom, attr in zip(comp_geoms, comp_attrs):
                cat = attr['类别']
                by_cat[cat].append(geom)

            # 对每个类别内的几何合并
            for cat, geoms in by_cat.items():
                merged = unary_union(geoms)
                if merged.is_empty:
                    continue
                if isinstance(merged, MultiPolygon):
                    parts = list(merged.geoms)
                else:
                    parts = [merged]

                for part in parts:
                    area = part.area
                    if area < area_threshold:
                        print(f"      删除小面积重叠要素（面积 {area:.4f}m²）")
                        continue

                    # 查找最近的好要素
                    candidates = list(good_sindex.intersection(part.bounds))
                    if not candidates:
                        # 无好要素，直接加入
                        new_row = comp_attrs[0].copy()  # 用分量内第一个要素的属性
                        new_row.geometry = part
                        new_row['类别'] = cat
                        good_attrs.append(new_row)
                        good_geoms.append(part)
                        good_sindex.insert(len(good_geoms)-1, part.bounds)
                        continue

                    # 计算距离，优先找同类别
                    best_pos = None
                    min_dist = float('inf')
                    for pos in candidates:
                        g = good_geoms[pos]
                        if g.is_empty or not g.is_valid:
                            continue
                        d = part.distance(g)
                        if d < min_dist:
                            min_dist = d
                            best_pos = pos

                    if best_pos is not None and good_attrs[best_pos]['类别'] == cat:
                        # 同类别，合并
                        old_geom = good_geoms[best_pos]
                        new_geom = old_geom.union(part)
                        if not new_geom.is_valid:
                            new_geom = make_valid(new_geom)
                        if not new_geom.is_empty:
                            good_sindex.delete(best_pos, old_geom.bounds)
                            good_geoms[best_pos] = new_geom
                            good_sindex.insert(best_pos, new_geom.bounds)
                    else:
                        # 类别不同或无合适好要素，新建
                        new_row = comp_attrs[0].copy()
                        new_row.geometry = part
                        new_row['类别'] = cat
                        good_attrs.append(new_row)
                        good_geoms.append(part)
                        good_sindex.insert(len(good_geoms)-1, part.bounds)

    # 5. 处理距离过近问题（类型2）—— 保持与好要素合并
    dist_indices = [i for i in bad_indices if problem_type[i] == 2]
    if dist_indices:
        print(f"    处理距离过近问题，涉及 {len(dist_indices)} 个要素")
        for i in dist_indices:
            geom = gdf.geometry.iloc[i]
            if not geom.is_valid:
                geom = make_valid(geom)
            if geom.is_empty:
                continue
            # 已删除点和小面积，这里只处理剩余的
            candidates = list(good_sindex.intersection(geom.bounds))
            if not candidates:
                candidates = range(len(good_geoms))
            min_dist = float('inf')
            best_pos = None
            for pos in candidates:
                g = good_geoms[pos]
                if g.is_empty or not g.is_valid:
                    continue
                d = geom.distance(g)
                if d < min_dist:
                    min_dist = d
                    best_pos = pos
            if best_pos is not None:
                old_geom = good_geoms[best_pos]
                new_geom = old_geom.union(geom)
                if not new_geom.is_valid:
                    new_geom = make_valid(new_geom)
                if not new_geom.is_empty:
                    good_sindex.delete(best_pos, old_geom.bounds)
                    good_geoms[best_pos] = new_geom
                    good_sindex.insert(best_pos, new_geom.bounds)
                print(f"      合并要素 {i} 到好要素位置 {best_pos}")
            else:
                # 无好要素，保留
                good_attrs.append(gdf.iloc[i].copy())
                good_geoms.append(geom)
                good_sindex.insert(len(good_geoms)-1, geom.bounds)

        # 6. 构建最终结果（确保几何为多边形，删除线和点）
    final_rows = []
    for geom, attr in zip(good_geoms, good_attrs):
        if geom.is_empty:
            continue
        # 检查几何类型，只保留多边形
        if geom.geom_type in ('Polygon', 'MultiPolygon'):
            # 对多边形，尝试修复自相交等
            if not geom.is_valid:
                geom_fixed = geom.buffer(0)
                if geom_fixed.is_empty:
                    print(f"      警告：无效多边形无法修复，跳过")
                    continue
                if geom_fixed.geom_type not in ('Polygon', 'MultiPolygon'):
                    print(f"      警告：修复后变为 {geom_fixed.geom_type}，跳过")
                    continue
                geom = geom_fixed
            row = attr.copy()
            row.geometry = geom
            final_rows.append(row)
        else:
            # 删除线或点要素
            print(f"      删除非多边形要素：类型 {geom.geom_type}")

    result = gpd.GeoDataFrame(final_rows, crs=gdf.crs)
    result = result.reset_index(drop=True)
    print(f"  修复完成，最终要素数量: {len(result)}")
    return result
def fix_topo_issues_in_utm(gdf, tolerance_m=0.001, area_threshold=1.0):
    """将数据投影到 UTM 后进行高级拓扑修复，再转回原坐标系"""
    if len(gdf) == 0:
        return gdf
    original_crs = gdf.crs
    gdf_proj, epsg = project_to_utm(gdf)
    gdf_proj_fixed = fix_topo_issues_advanced(gdf_proj, tolerance_m, area_threshold)
    gdf_fixed = gdf_proj_fixed.to_crs(original_crs)
    return gdf_fixed
# ========== 拓扑验证与清理函数 ==========
def check_topology(gdf, tolerance_m=0.001):
    """检查重叠和距离过近，返回（重叠数量，距离过近数量）"""
    if len(gdf) < 2:
        return 0, 0
    gdf_proj, _ = project_to_utm(gdf)
    from rtree import index
    idx = index.Index()
    for i, geom in enumerate(gdf_proj.geometry):
        if geom.is_valid and not geom.is_empty:
            idx.insert(i, geom.bounds)
    overlap_count = 0
    dist_count = 0
    checked = set()
    for i in range(len(gdf_proj)):
        geom_i = gdf_proj.geometry.iloc[i]
        if geom_i.is_empty or not geom_i.is_valid:
            continue
        candidates = list(idx.intersection(geom_i.bounds))
        for j in candidates:
            if i >= j:
                continue
            if (i, j) in checked:
                continue
            checked.add((i, j))
            geom_j = gdf_proj.geometry.iloc[j]
            if geom_j.is_empty or not geom_j.is_valid:
                continue
            if geom_i.intersects(geom_j):
                inter = geom_i.intersection(geom_j)
                if inter.area > tolerance_m * tolerance_m:
                    overlap_count += 1
            dist = geom_i.distance(geom_j)
            if dist < tolerance_m:
                dist_count += 1
    return overlap_count, dist_count

def clean_geometry(geom):
    """清理几何：修复无效、删除非多边形"""
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.geom_type not in ('Polygon', 'MultiPolygon'):
        return None
    return geom

# ========== 循环修复直到拓扑正确 ==========
print("\n=== 开始拓扑修复与验证（循环直到无错误） ===")
max_iterations = 10
for iteration in range(max_iterations):
    print(f"\n--- 第 {iteration+1} 次修复迭代 ---")
    # 执行拓扑修复
    final_result = fix_topo_issues_in_utm(final_result, tolerance_m=0.001, area_threshold=1.0)
    
    # 清理几何：修复无效、删除非多边形
    final_result['geometry'] = final_result.geometry.apply(clean_geometry)
    final_result = final_result[~final_result.geometry.isnull()].reset_index(drop=True)
    
    # 检查拓扑错误
    overlap, dist = check_topology(final_result)
    print(f"  重叠错误数：{overlap}，距离过近错误数：{dist}")
    
    if overlap == 0 and dist == 0:
        print("  拓扑检查通过！")
        break
    else:
        print(f"  仍有拓扑错误，将进行下一次修复...")
# else:
#     # 达到最大迭代次数仍未解决
#     overlap, dist = check_topology(final_result)
#     raise RuntimeError(f"经过 {max_iterations} 次修复后仍存在拓扑错误：重叠 {overlap}，距离过近 {dist}")

# ========== 保存结果 ==========
output_path = os.path.join(output_dir, '地块_clip.shp')
remove_shapefile_files(output_path)
final_result.to_file(output_path)
print("\n处理完成！")
# ========== 新增功能：细碎、细长要素处理（优化版） ==========
print("\n" + "="*60)
print("开始处理细碎、细长要素...")

# ------------------- 读取数据 -------------------
input_shp = os.path.join(output_dir, '地块_clip.shp')
if not os.path.exists(input_shp):
    raise FileNotFoundError(f"未找到文件：{input_shp}")
remaining = gpd.read_file(input_shp)
print(f"初始要素数量：{len(remaining)}")

# 创建三个容器
biaoji = gpd.GeoDataFrame(columns=remaining.columns, crs=remaining.crs)
yisi   = gpd.GeoDataFrame(columns=remaining.columns, crs=remaining.crs)   # 保留以备后用
fuben  = gpd.GeoDataFrame(columns=remaining.columns, crs=remaining.crs)

# ------------------- 辅助函数（已定义，此处重复确保完整性） -------------------
def ensure_projected_crs(gdf):
    """自动转换到适合的UTM投影坐标系"""
    if gdf.crs is None:
        raise ValueError("数据缺少坐标系信息，无法转换")
    if gdf.crs.is_projected and gdf.crs.axis_info[0].unit_name == 'metre':
        return gdf
    bounds = gdf.total_bounds
    center_lon = (bounds[0] + bounds[2]) / 2
    utm_zone = int((center_lon + 180) // 6) + 1
    center_lat = (bounds[1] + bounds[3]) / 2
    hemisphere = 'north' if center_lat >= 0 else 'south'
    epsg_code = 32600 + utm_zone if hemisphere == 'north' else 32700 + utm_zone
    print(f"转换到投影坐标系：EPSG:{epsg_code}")
    return gdf.to_crs(f'EPSG:{epsg_code}')

def explode_multipolygons(gdf):
    """拆分MultiPolygon为多个Polygon记录"""
    if len(gdf) == 0:
        return gdf
    exploded = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == 'Polygon':
            exploded.append(row)
        elif geom.geom_type == 'MultiPolygon':
            for poly in geom.geoms:
                new_row = row.copy()
                new_row.geometry = poly
                exploded.append(new_row)
    return gpd.GeoDataFrame(exploded, crs=gdf.crs)

def safe_area(geom):
    """安全计算面积，无效几何返回0"""
    if geom is None or geom.is_empty:
        return 0.0
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
        return geom.area
    except:
        return 0.0

def morphological_skeleton(geom, buffer_size=15):
    """形态学主体：负缓冲再正缓冲"""
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
    """计算长宽比（最小外接矩形长边/短边）"""
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

# ------------------- Step 1: 坐标系统转换 -------------------
print("\n[Step 1] 转换到投影坐标系...")
remaining = ensure_projected_crs(remaining)

# ------------------- Step 2: 拆分多部分要素 -------------------
print("\n[Step 2] 拆分多部分几何...")
remaining = explode_multipolygons(remaining).reset_index(drop=True)
print(f"  拆分后要素数量：{len(remaining)}")

# ------------------- Step 3: 筛选小面积要素（< 2500㎡） -------------------
print("\n[Step 3] 筛选小面积要素...")
remaining['_area'] = remaining.geometry.apply(safe_area)
small_mask = remaining['_area'] < 2500
biaoji = remaining[small_mask].copy().reset_index(drop=True)
remaining = remaining[~small_mask].copy().reset_index(drop=True)
print(f"  移入 biaoji 的小要素数量：{len(biaoji)}")

# ========== 新增：保存小面积和非小面积要素 ==========后续要删除
print("\n[保存中间结果]")

# 保存小面积要素（不含临时面积列）
small_output_path = os.path.join(output_dir, '小面积.shp')
remove_shapefile_files(small_output_path)
biaoji_save = biaoji.copy()
biaoji_save.drop(columns=['_area'], inplace=True)
if len(biaoji_save) > 0:
    biaoji_save.to_file(small_output_path)
    print(f"  小面积要素已保存至：{small_output_path} (共{len(biaoji_save)}个要素)")
else:
    print(f"  无小面积要素，跳过保存")

# 保存非小面积要素（不含临时面积列）
remaining_output_path = os.path.join(output_dir, '非小面积.shp')
remove_shapefile_files(remaining_output_path)
remaining_save = remaining.copy()
remaining_save.drop(columns=['_area'], inplace=True)
remaining_save.to_file(remaining_output_path)
print(f"  非小面积要素已保存至：{remaining_output_path} (共{len(remaining_save)}个要素)")

# 清理临时面积列（供后续步骤使用）
remaining.drop(columns=['_area'], inplace=True)
biaoji.drop(columns=['_area'], inplace=True)

# ------------------- Step 4: 识别细长部分并裁剪 -------------------
print("\n[Step 4] 识别细长部分...")
# 复制剩余要素到 fuben（用于形态学计算，但保持几何与 remaining 同步）
fuben = remaining.copy(deep=True).reset_index(drop=True)
# 记录哪些要素被裁剪过，以及裁剪后剩余部分
new_biaoji_list = []          # 存放本次识别出的细长部分（将加入 biaoji）
to_update = {}                # 记录 remaining 索引 -> 更新后的几何（主体融合后）

BUFFER_SIZE = 15              # 可调参数
AREA_THRESHOLD = 1000         # 融合面积阈值
RATIO_THRESHOLD = 3.0         # 融合长宽比阈值

for idx in range(len(fuben)):
    row = fuben.loc[idx]
    geom_orig = row.geometry
    if geom_orig is None or geom_orig.is_empty:
        continue

    # 计算主体
    main_body = morphological_skeleton(geom_orig, BUFFER_SIZE)
    if main_body is None or main_body.is_empty:
        # 整个要素视为细长部分，放入 biaoji
        new_biaoji_list.append(row)
        continue

    # 裁剪得到外部部分
    try:
        outside = geom_orig.difference(main_body)
    except:
        continue
    if outside.is_empty:
        continue

    # 拆分外部部分为独立多边形
    if outside.geom_type == 'Polygon':
        parts = [outside]
    elif outside.geom_type == 'MultiPolygon':
        parts = list(outside.geoms)
    else:
        continue

    # 分类：融合部分（与主体合并）和细长部分（放入 biaoji）
    merge_parts = []
    slender_parts = []
    for part in parts:
        area_part = safe_area(part)
        if area_part < AREA_THRESHOLD or aspect_ratio(part) < RATIO_THRESHOLD:
            merge_parts.append(part)
        else:
            slender_parts.append(part)

    # 更新主体：合并需要融合的部分
    if merge_parts:
        merged_main = unary_union([main_body] + merge_parts)
        # 更新 fuben 和 remaining 中的几何（主体融合后）
        fuben.at[idx, 'geometry'] = merged_main
        to_update[idx] = merged_main
    else:
        merged_main = main_body
        to_update[idx] = merged_main   # 即使没有融合部分，也要更新为准确的主体

    # 细长部分存入 biaoji（保留原属性）
    for part in slender_parts:
        new_row = row.copy()
        new_row.geometry = part
        new_biaoji_list.append(new_row)

# 更新 remaining 的几何（同步主体融合后的结果）
for idx, new_geom in to_update.items():
    remaining.at[idx, 'geometry'] = new_geom

# 将本次细长部分加入 biaoji
if new_biaoji_list:
    new_biaoji_gdf = gpd.GeoDataFrame(new_biaoji_list, crs=remaining.crs)
    biaoji = pd.concat([biaoji, new_biaoji_gdf], ignore_index=True)

print(f"  处理后 remaining 要素数量：{len(remaining)}")
print(f"  当前 biaoji 总数量：{len(biaoji)}")

# 注意：此时 remaining 中的要素已经减去了 slender_parts（因为它们是 difference 得到的），
# 且主体已更新（可能融合了部分外部区域），所以 remaining 的覆盖范围已包括所有非细长区域，
# 而 slender_parts 全部在 biaoji 中，等待后续融合或合并回 remaining。

# ------------------- Step 5: biaoji 要素融合判断（优化：使用空间索引，单次融合） -------------------
if len(biaoji) > 0:
    # 拆分 biaoji 中可能的多部分几何
    biaoji = explode_multipolygons(biaoji).reset_index(drop=True)
    # 计算面积并排序
    biaoji['_area'] = biaoji.geometry.apply(safe_area)
    biaoji.sort_values('_area', ascending=True, inplace=True)
    biaoji.reset_index(drop=True, inplace=True)
    biaoji.drop(columns=['_area'], inplace=True)

    # 构建空间索引
    if len(remaining) > 0:
        rem_sindex = remaining.sindex
    else:
        rem_sindex = None
    if len(biaoji) > 0:
        bj_sindex = biaoji.sindex
    else:
        bj_sindex = None

    # 标记需要从 biaoji 中移除的索引（融合成功）
    to_remove = set()

    # 辅助函数：安全获取有效几何
    def safe_geom(geom):
        if geom is None:
            return None
        if not geom.is_valid:
            try:
                geom = make_valid(geom)
            except:
                return None
        return geom

    # 第一轮：与 remaining 融合
    if len(remaining) > 0:
        for idx_b, row_b in biaoji.iterrows():
            if idx_b in to_remove:
                continue
            geom_b = row_b.geometry
            if geom_b is None or geom_b.is_empty:
                continue
            # 确保 biaoji 几何有效
            geom_b = safe_geom(geom_b)
            if geom_b is None or geom_b.is_empty:
                continue
            # 查询可能接触的 remaining 要素
            possible = list(rem_sindex.intersection(geom_b.bounds))
            for idx_r in possible:
                geom_r = remaining.geometry.iloc[idx_r]
                if geom_r is None or geom_r.is_empty:
                    continue
                geom_r = safe_geom(geom_r)
                if geom_r is None or geom_r.is_empty:
                    continue
                # 检查是否接触（使用 buffer(0) 修复微小无效）
                try:
                    if not geom_r.touches(geom_b):
                        continue
                except Exception:
                    # 如果 touches 异常，尝试修复后重试
                    try:
                        if not geom_r.buffer(0).touches(geom_b.buffer(0)):
                            continue
                    except:
                        continue
                try:
                    contact = geom_r.intersection(geom_b)
                    if contact.is_empty or contact.geom_type not in ('LineString', 'MultiLineString'):
                        continue
                    contact_len = contact.length
                    perimeter_b = geom_b.length
                    if contact_len > 0.1 * perimeter_b:
                        # 融合
                        new_geom = unary_union([geom_r, geom_b])
                        remaining.at[idx_r, 'geometry'] = new_geom
                        to_remove.add(idx_b)
                        break
                except:
                    continue

    # 第二轮：biaoji 内部融合（仅对未被移除的要素）
    if len(biaoji) > 1:
        # 获取未被移除的索引列表
        remaining_indices = [i for i in range(len(biaoji)) if i not in to_remove]
        if remaining_indices:
            # 创建子集便于操作
            biaoji_sub = biaoji.loc[remaining_indices].copy().reset_index(drop=True)
            # 重新建立索引映射
            orig_idx_map = {new_idx: old_idx for new_idx, old_idx in enumerate(remaining_indices)}
            if len(biaoji_sub) > 0:
                sub_sindex = biaoji_sub.sindex
                sub_to_remove = set()
                for new_idx, row_b in biaoji_sub.iterrows():
                    if new_idx in sub_to_remove:
                        continue
                    geom_b = row_b.geometry
                    if geom_b is None or geom_b.is_empty:
                        continue
                    geom_b = safe_geom(geom_b)
                    if geom_b is None or geom_b.is_empty:
                        continue
                    possible = list(sub_sindex.intersection(geom_b.bounds))
                    for new_other in possible:
                        if new_other <= new_idx:
                            continue
                        geom_other = biaoji_sub.geometry.iloc[new_other]
                        if geom_other is None or geom_other.is_empty:
                            continue
                        geom_other = safe_geom(geom_other)
                        if geom_other is None or geom_other.is_empty:
                            continue
                        # 检查是否接触
                        try:
                            if not geom_b.touches(geom_other):
                                continue
                        except Exception:
                            try:
                                if not geom_b.buffer(0).touches(geom_other.buffer(0)):
                                    continue
                            except:
                                continue
                        try:
                            contact = geom_b.intersection(geom_other)
                            if contact.is_empty or contact.geom_type not in ('LineString', 'MultiLineString'):
                                continue
                            contact_len = contact.length
                            perimeter_b = geom_b.length
                            if contact_len > 0.1 * perimeter_b:
                                # 融合到另一个要素（面积较小的融合到较大的）
                                new_geom = unary_union([geom_b, geom_other])
                                biaoji_sub.at[new_other, 'geometry'] = new_geom
                                sub_to_remove.add(new_idx)
                                break
                        except:
                            continue
                # 将融合结果写回原 biaoji
                for new_idx in sub_to_remove:
                    old_idx = orig_idx_map[new_idx]
                    to_remove.add(old_idx)
                # 更新未移除的几何（如果被融合的目标要素也更新了）
                for new_idx in range(len(biaoji_sub)):
                    if new_idx not in sub_to_remove:
                        old_idx = orig_idx_map[new_idx]
                        biaoji.at[old_idx, 'geometry'] = biaoji_sub.at[new_idx, 'geometry']
    # 移除融合成功的 biaoji 要素
    biaoji = biaoji[~biaoji.index.isin(to_remove)].copy().reset_index(drop=True)

print(f"  融合后 remaining 要素数量：{len(remaining)}")
print(f"  融合后 biaoji 剩余数量：{len(biaoji)}")
# ------------------- Step 6: biaoji 剩余要素加入 remaining -------------------
print("\n[Step 6] 将 biaoji 剩余要素加入 remaining...")
if len(biaoji) > 0:
    # 确保列一致（防止属性差异）
    common_cols = list(set(remaining.columns) & set(biaoji.columns))
    if 'geometry' not in common_cols:
        common_cols.append('geometry')
    remaining = pd.concat([remaining[common_cols], biaoji[common_cols]], ignore_index=True)
    print(f"  合并后 remaining 要素数量：{len(remaining)}")
else:
    print("  无剩余 biaoji 要素")

# ------------------- Step 7: 最终拓扑整理（确保所有几何为 Polygon） -------------------
print("\n[Step 7] 最终拓扑整理...")

# 辅助函数：修复并确保几何为 Polygon（如果为 MultiPolygon，返回拆分后的列表）
def fix_and_explode(geom):
    if geom is None or geom.is_empty:
        return None
    try:
        if not geom.is_valid:
            geom = make_valid(geom)
        if geom.geom_type == 'Polygon':
            return [geom]  # 返回列表便于统一处理
        elif geom.geom_type == 'MultiPolygon':
            return list(geom.geoms)  # 拆分为多个 Polygon
        else:
            return None
    except:
        return None

# 遍历所有要素，修复并拆分
new_rows = []
for idx, row in remaining.iterrows():
    geom = row.geometry
    if geom is None or geom.is_empty:
        continue
    polys = fix_and_explode(geom)
    if polys is None:
        continue
    for poly in polys:
        if poly.is_empty or poly.area <= 1e-6:  # 极小面跳过
            continue
        new_row = row.copy()
        new_row.geometry = poly
        new_rows.append(new_row)

if new_rows:
    remaining = gpd.GeoDataFrame(new_rows, crs=remaining.crs).reset_index(drop=True)
else:
    remaining = gpd.GeoDataFrame(columns=remaining.columns, crs=remaining.crs)

# 移除空几何（二次确保）
remaining = remaining[~remaining.geometry.is_empty].reset_index(drop=True)
# 重新编号 FID
remaining['FID'] = range(1, len(remaining)+1)

print(f"  最终要素数量：{len(remaining)}")

# ------------------- Step 8: 保存 -------------------
output_final = os.path.join(output_dir, '最终地块.shp')
remove_shapefile_files(output_final)
remaining.to_file(output_final)
print(f"\n处理完成！最终要素数量：{len(remaining)}")
print(f"结果已保存至：{output_final}")

# 可选：验证总面积一致性（允许极小浮点误差）
original = gpd.read_file(input_shp)
# 计算原始总面积（投影下）
original_proj = ensure_projected_crs(original)
original_area = original_proj.geometry.area.sum()
final_area = remaining.geometry.area.sum()
print(f"原始总面积（投影）: {original_area:.2f} ㎡")
print(f"最终总面积（投影）: {final_area:.2f} ㎡")
print(f"面积差异: {final_area - original_area:.2f} ㎡ (应接近0)")