# ============================================================
# 【关键】必须在所有 import 之前设置 PROJ 数据库路径。
# pyproj 在被 import 时就初始化全局 context，若此时找不到数据库
# 则后续所有 EPSG 解析均会报 "no database context specified"。
# 解决方案：import 之前通过 os.environ 设置 PROJ_DATA 环境变量，
# 再配合 import 后调用 set_data_dir 双重保障。
# ============================================================
import os
import sys

# 步骤1：在任何 pyproj/geopandas import 之前设置 PROJ_DATA 环境变量
def _set_proj_path_early():
    # 优先从 CONDA_PREFIX 推断（Windows conda 环境）
    conda_env = os.environ.get('CONDA_PREFIX', '')
    candidates = []
    if conda_env:
        candidates.append(os.path.join(conda_env, 'Library', 'share', 'proj'))
    # 兜底：从当前 Python 可执行文件路径推断
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
            os.environ['PROJ_LIB']  = p  # 兼容旧版 pyproj
            print(f"✓ [早期] 已设置 PROJ_DATA 环境变量：{p}")
            return p
    print("⚠ [早期] 未找到 PROJ 数据库目录，EPSG 解析可能失败")
    return None

_proj_path = _set_proj_path_early()

# 步骤2：import 其他库
import geopandas as gpd
import pandas as pd
import numpy as np
import warnings
from shapely.geometry import MultiPolygon, Polygon, GeometryCollection
from shapely.validation import make_valid
from shapely.ops import unary_union
from rtree import index
from pypinyin import lazy_pinyin
import pyproj
from pyproj.datadir import set_data_dir

# 步骤3：import 完成后再次调用 set_data_dir 双重保障
try:
    if _proj_path:
        set_data_dir(_proj_path)
        print(f"✓ [二次] set_data_dir 已设置：{_proj_path}")
except Exception as e:
    print(f"⚠ set_data_dir 调用失败：{e}")

warnings.filterwarnings('ignore', category=UserWarning)

# ============================================================
# 路径配置
# ============================================================
script_dir = os.path.dirname(os.path.abspath(__file__))

city      = 'beijing'
city_name = '北京'

parcel_path    = os.path.join(script_dir, f'data/data_dikuai/地块23/{city}/{city}_parcel.shp')
osm_dir        = os.path.join(script_dir, 'data/data_osm/osm23/china-240101-free.shp')
water_path     = os.path.join(osm_dir, 'gis_osm_water_a_free_1.shp')
landuse_path   = os.path.join(osm_dir, 'gis_osm_landuse_a_free_1.shp')
transport_path = os.path.join(osm_dir, 'gis_osm_transport_a_free_1.shp')
traffic_path   = os.path.join(osm_dir, 'gis_osm_traffic_a_free_1.shp')
poi_path       = os.path.join(script_dir, f'data/data_poi/{city_name}.poi', f'{city_name}.gpkg')

# 绿地公园数据路径（2023 版本）
lvdipark_base = os.path.join(script_dir, 'data/绿地公园/2023公园与绿地广场')
# 查找包含 city_name 的城市文件夹
lvdipark_city_folder = None
if os.path.exists(lvdipark_base):
    for folder_name in os.listdir(lvdipark_base):
        if city_name in folder_name:
            lvdipark_city_folder = os.path.join(lvdipark_base, folder_name)
            break

# 在找到的城市文件夹中查找第一个.shp 文件
lvdipark_path = None
if lvdipark_city_folder and os.path.exists(lvdipark_city_folder):
    for file_name in os.listdir(lvdipark_city_folder):
        if file_name.endswith('.shp'):
            lvdipark_path = os.path.join(lvdipark_city_folder, file_name)
            print(f"  ✓ 找到绿地公园数据：{lvdipark_path}")
            break
    if not lvdipark_path:
        print(f"  ⚠️ 在城市文件夹 {lvdipark_city_folder} 中未找到.shp 文件")
else:
    print(f"  ⚠️ 未找到绿地公园城市文件夹（搜索路径：{lvdipark_base}，城市：{city_name}）")

output_dir = os.path.join(script_dir, f'./output/output23/{city}.output')
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# ============================================================
# 阈值参数（可调节）
# ============================================================
THRESHOLD_INDUSTRIAL      = 0.50   # 工业 OSM 面积占比
THRESHOLD_GREEN           = 0.50   # 绿地 OSM 面积占比
THRESHOLD_TRAFFIC_POI     = 0.60   # 交通物流 POI 占比
THRESHOLD_TRAFFIC_OSM     = 0.30   # 交通物流 OSM 面积占比（补充）
THRESHOLD_RESIDENTIAL_POI = 0.30   # 居住用地 POI 占比
THRESHOLD_RESIDENTIAL_OSM = 0.30   # 居住用地 OSM 面积占比（补充）
THRESHOLD_WATER           = 0.50   # 水体 OSM 面积占比

# ============================================================
# POI 分类映射表（完整9类，工业/绿地/水体无对应POI）
# ============================================================
PARCEL_TO_KindNameBi = {
    '居住用地'               : ['住、宿', '批发、零售', '居民服务', '餐饮'],
    '交通物流设施'           : ['交通运输、仓储'],
    '商业服务业设施用地'     : ['公司企业', '金融、保险', '汽车销售及服务', '商业设施、商务服务'],
    '公共管理与公共服务用地' : ['教育、文化', '科研及技术服务', '卫生、社保', '运动、休闲'],
    '公用设施用地'           : ['公共设施'],
    '其他'                   : [],
}
KindNameBi_TO_PARCEL = {}
for ptype, dlmc_list in PARCEL_TO_KindNameBi.items():
    for d in dlmc_list:
        KindNameBi_TO_PARCEL[d] = ptype

# 所有"有映射"的KindNameBi集合（用于计算"其他POI"）
ALL_KNOWN_BI = set(KindNameBi_TO_PARCEL.keys())

# ============================================================
# ========== 辅助函数（完整保留原始逻辑）==========
# ============================================================

def remove_shapefile_files(filepath):
    base = os.path.splitext(filepath)[0]
    for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix']:
        f = base + ext
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"  已删除旧文件：{f}")
            except:
                pass

def safe_make_valid(geom):
    if geom is None or geom.is_empty:
        return None
    try:
        geom = geom.buffer(0)
        if not geom.is_valid:
            geom = make_valid(geom)
        polys = []
        if isinstance(geom, (Polygon, MultiPolygon)):
            polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
        elif isinstance(geom, GeometryCollection):
            for g in geom.geoms:
                if isinstance(g, (Polygon, MultiPolygon)):
                    polys.extend([g] if isinstance(g, Polygon) else list(g.geoms))
        valid_polys = [p for p in polys if p.is_valid and p.area > 0]
        if not valid_polys:
            return None
        return valid_polys[0] if len(valid_polys) == 1 else MultiPolygon(valid_polys)
    except:
        return None

def clean_geometry(gdf, min_area=1e-6):
    if len(gdf) == 0:
        return gdf
    gdf['_clean_geom'] = gdf.geometry.apply(safe_make_valid)
    gdf = gdf[~gdf['_clean_geom'].isna()].copy()
    if len(gdf) == 0:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    new_rows = []
    for _, row in gdf.iterrows():
        geom = row['_clean_geom']
        if isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                new_row = row.copy(); new_row.geometry = poly; new_rows.append(new_row)
        elif isinstance(geom, Polygon):
            new_row = row.copy(); new_row.geometry = geom; new_rows.append(new_row)
    gdf_clean = gpd.GeoDataFrame(new_rows, crs=gdf.crs).drop(columns=['_clean_geom'])
    gdf_clean = gdf_clean[gdf_clean.geometry.area > min_area].reset_index(drop=True)
    return gdf_clean

def explode_multipolygons(geo_df):
    exploded = []
    for _, row in geo_df.iterrows():
        geom = row.geometry
        if isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                new_row = row.copy(); new_row.geometry = poly; exploded.append(new_row)
        elif isinstance(geom, Polygon):
            exploded.append(row.copy())
    return gpd.GeoDataFrame(exploded, crs=geo_df.crs)

def consolidate_class_with_buffer(gdf, buffer_size=0.1, chunk_size=1000):
    """分块合并同类要素，避免大图层 unary_union 内存爆炸。"""
    if len(gdf) == 0:
        return gpd.GeoDataFrame(columns=['geometry'], crs=gdf.crs)
    geoms = gdf.geometry.tolist()
    chunks = [geoms[i:i+chunk_size] for i in range(0, len(geoms), chunk_size)]
    partial_union = []
    for chunk in chunks:
        if len(chunk) == 1:
            buffered = chunk[0].buffer(buffer_size)
        else:
            try:
                buffered = unary_union(chunk).buffer(buffer_size)
            except:
                buffered = None
                for g in chunk:
                    buffered = g.buffer(buffer_size) if buffered is None \
                               else buffered.union(g.buffer(buffer_size))
        if buffered is None or buffered.is_empty:
            continue
        eroded = buffered.buffer(-buffer_size)
        if not eroded.is_empty:
            partial_union.append(eroded)
    if not partial_union:
        return gpd.GeoDataFrame(columns=['geometry'], crs=gdf.crs)
    final_union = unary_union(partial_union)
    temp = gpd.GeoDataFrame(geometry=[final_union], crs=gdf.crs)
    temp = clean_geometry(temp, min_area=1e-6)
    return explode_multipolygons(temp).reset_index(drop=True)

def to_wgs84(gdf, name=''):
    """任意坐标系 → WGS84 (EPSG:4326)"""
    if gdf.crs is None:
        print(f"  ⚠ {name} 无坐标系，假定 WGS84")
        return gdf.set_crs('EPSG:4326')
    if gdf.crs.to_epsg() == 4326:
        return gdf
    return gdf.to_crs('EPSG:4326')

def get_utm_epsg(gdf_4326):
    """根据 WGS84 数据范围自动推算 UTM 带号"""
    bounds  = gdf_4326.total_bounds          # [minx, miny, maxx, maxy]
    lon_c   = (bounds[0] + bounds[2]) / 2
    lat_c   = (bounds[1] + bounds[3]) / 2
    zone    = int((lon_c + 180) / 6) + 1
    epsg    = 32600 + zone if lat_c >= 0 else 32700 + zone
    print(f"  数据中心 ({lon_c:.4f}, {lat_c:.4f}) → UTM Zone {zone} → EPSG:{epsg}")
    return epsg

def to_geographic_crs(gdf):
    if gdf.crs is not None and gdf.crs.to_epsg() == 4326:
        return gdf
    return gdf.to_crs('EPSG:4326')

def read_shapefile_with_fallback(filepath, layer_name='图层'):
    """
    三级降级读取策略：
      1. gpd.read_file（默认 pyogrio 引擎）
      2. gpd.read_file（fiona 引擎）
      3. fiona 手动逐要素读取（一次循环同时取 geometry+properties，
         避免两次迭代不一致；用 fiona.prop_type 兼容带洞 Polygon）
    """
    # 第1级：pyogrio 引擎（geopandas 默认）
    try:
        gdf = gpd.read_file(filepath)
        print(f"  ✓ {layer_name} 读取成功：{len(gdf)} 个要素")
        return gdf
    except Exception as e:
        print(f"  ⚠️ {layer_name} pyogrio 引擎失败：{e}")

    # 第2级：fiona 引擎
    try:
        gdf = gpd.read_file(filepath, engine='fiona')
        print(f"  ✓ {layer_name} fiona 引擎成功：{len(gdf)} 个要素")
        return gdf
    except Exception as e:
        print(f"  ⚠️ {layer_name} fiona 引擎失败：{e}")

    # 第3级：fiona 手动逐要素读取
    # 关键修复：在同一次 for 循环中同时提取 geometry 和 properties，
    # 避免二次迭代为空；用 fiona.geometry.shape 正确处理带洞 Polygon。
    try:
        import fiona
        from fiona.geometry import shape as fiona_shape
        geometries      = []
        properties_list = []
        crs_wkt         = None
        with fiona.open(filepath, 'r') as src:
            crs_wkt = src.crs_wkt if hasattr(src, 'crs_wkt') else None
            for feat in src:
                geom = feat.geometry
                if geom is not None:
                    try:
                        geometries.append(fiona_shape(geom))
                    except Exception:
                        geometries.append(None)
                else:
                    geometries.append(None)
                properties_list.append(dict(feat.properties))
        gdf = gpd.GeoDataFrame(properties_list, geometry=geometries)
        # 尝试设置坐标系
        if crs_wkt:
            try:
                gdf = gdf.set_crs(crs_wkt, allow_override=True)
            except Exception:
                pass
        print(f"  ✓ {layer_name} 手动读取成功：{len(gdf)} 个要素"
              f"{'（已设置CRS）' if gdf.crs else '（无坐标系）'}")
        return gdf
    except Exception as e:
        raise RuntimeError(f"  ✗ {layer_name} 三级读取全部失败：{e}") from e

def efficient_clip(src, clip_gdf):
    """rtree 空间索引裁剪，保留所有与地块范围有交集的要素。"""
    if len(src) == 0:
        return gpd.GeoDataFrame(columns=['geometry'], crs=clip_gdf.crs)
    clip_sindex = clip_gdf.sindex
    clip_geoms  = clip_gdf.geometry.tolist()
    rows = []
    for _, row_src in src.iterrows():
        geom_src = row_src.geometry
        if geom_src is None or geom_src.is_empty:
            continue
        for i in list(clip_sindex.intersection(geom_src.bounds)):
            inter = geom_src.intersection(clip_geoms[i])
            if not inter.is_empty:
                new_row = row_src.copy(); new_row.geometry = inter; rows.append(new_row)
    return (gpd.GeoDataFrame(rows, crs=src.crs)
            if rows else gpd.GeoDataFrame(columns=['geometry'], crs=src.crs))

def calc_area_ratio(parcel_gdf, overlay_gdf):
    """
    计算 overlay_gdf 在每个地块内的面积占比。
    返回 Series，index 与 parcel_gdf 对齐，值域 [0, 1]。
    """
    if len(overlay_gdf) == 0:
        return pd.Series(0.0, index=parcel_gdf.index)
    idx_tree = index.Index()
    geoms_ov = overlay_gdf.geometry.tolist()
    for i, g in enumerate(geoms_ov):
        if g is not None and not g.is_empty:
            idx_tree.insert(i, g.bounds)
    ratios = []
    for _, row in parcel_gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.area == 0:
            ratios.append(0.0); continue
        possible = list(idx_tree.intersection(geom.bounds))
        area_sum = sum(
            geom.intersection(geoms_ov[i]).area
            for i in possible
            if geoms_ov[i] is not None and not geoms_ov[i].is_empty
        )
        ratios.append(area_sum / geom.area)
    return pd.Series(ratios, index=parcel_gdf.index)

def hanzi_initials(text: str) -> str:
    result = []
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            py = lazy_pinyin(ch)
            if py:
                result.append(py[0][0].upper())
    return ''.join(result)

# ============================================================
# ========== 主流程 ==========
# ============================================================
def main():

    # ----------------------------------------------------------
    # A. 读取所有原始数据
    # ----------------------------------------------------------
    print('=' * 60)
    print('正在读取文件...')
    parcel    = read_shapefile_with_fallback(parcel_path,    '地块数据')
    water     = read_shapefile_with_fallback(water_path,     '水体数据')
    landuse   = read_shapefile_with_fallback(landuse_path,   '土地利用数据')
    transport = read_shapefile_with_fallback(transport_path, 'transport数据')
    traffic   = read_shapefile_with_fallback(traffic_path,   'traffic数据')

    # traffic 只保留指定 fclass 面要素
    traffic_fclass = ['fuel', 'parking', 'parking_multistorey', 'service']
    traffic = traffic[traffic['fclass'].isin(traffic_fclass)].copy()
    print(f'  traffic 筛选后（{traffic_fclass}）：{len(traffic)} 个要素')

    # 读取 POI（允许缺失）
    poi_available = False
    pois = gpd.GeoDataFrame()
    if os.path.exists(poi_path):
        try:
            pois = gpd.read_file(poi_path)
            print(f'  ✓ POI 读取成功：{len(pois)} 个要素')
            poi_available = 'KindNameBi' in pois.columns
            if not poi_available:
                print('  ⚠️ POI 缺少 KindNameBi 字段，跳过 POI 判断')
            else:
                kind_counts = pois['KindNameBi'].value_counts()
                print('  POI 类别分布（前10）：')
                for kind, cnt in kind_counts.head(10).items():
                    print(f'    {kind}: {cnt}')
        except Exception as e:
            print(f'  ⚠️ POI 读取失败：{e}')
    else:
        print(f'  ⚠️ 未找到 POI 文件：{poi_path}')

    # 读取绿地公园数据（可选，不存在则跳过）
    lvdipark_available = False
    lvdipark = gpd.GeoDataFrame()
    if lvdipark_path and os.path.exists(lvdipark_path):
        try:
            lvdipark = read_shapefile_with_fallback(lvdipark_path, '绿地公园数据')
            # 检查并转换坐标系到 WGS84
            if lvdipark.crs is None:
                print(f'  ⚠️ 绿地公园数据无坐标系，假定 WGS84')
                lvdipark = lvdipark.set_crs('EPSG:4326')
            elif lvdipark.crs.to_epsg() != 4326:
                print(f'  ✓ 绿地公园数据坐标系为 {lvdipark.crs.to_epsg()}，转换到 WGS84')
                lvdipark = lvdipark.to_crs('EPSG:4326')
            else:
                print(f'  ✓ 绿地公园数据已是 WGS84 坐标系')
            lvdipark_available = True
            print(f'  ✓ 绿地公园读取成功：{len(lvdipark)} 个要素')
        except Exception as e:
            print(f'  ⚠️ 绿地公园读取失败：{e}，跳过处理')
    else:
        print(f'  ℹ️ 未找到绿地公园数据，跳过处理')

    # ==========================================================
    # B. 第一步：所有数据统一转换到 WGS84 地理坐标系 (EPSG:4326)
    #    目的：统一基准，消除各数据源坐标系差异；
    #          裁剪、几何修复、聚合均在 WGS84 下进行。
    #    注意：WGS84 是角度单位（度），此阶段不做任何面积/距离计算。
    # ==========================================================
    print('\n=== Step B：所有数据统一转换到 WGS84 地理坐标系 (EPSG:4326) ===')
    parcel    = to_wgs84(parcel,    '地块')
    water     = to_wgs84(water,     '水体')
    landuse   = to_wgs84(landuse,   'landuse')
    transport = to_wgs84(transport, 'transport')
    traffic   = to_wgs84(traffic,   'traffic')
    if poi_available:
        pois  = to_wgs84(pois,      'POI')
    if lvdipark_available:
        lvdipark = to_wgs84(lvdipark, '绿地公园')
    print('  ✓ 所有数据已统一到 WGS84 (EPSG:4326)')

    # ----------------------------------------------------------
    # C. 裁剪：在 WGS84 下将 OSM 数据裁剪到地块总范围
    #    空间裁剪只做范围筛选，不涉及面积计算，WGS84 下可正常执行。
    # ----------------------------------------------------------
    print('\n=== Step C：裁剪 OSM 数据到地块范围（WGS84）===')
    landuse_clipped   = efficient_clip(landuse,   parcel)
    transport_clipped = efficient_clip(transport, parcel)
    traffic_clipped   = efficient_clip(traffic,   parcel)
    water_clipped     = efficient_clip(water,     parcel)
    print(f'  landuse 裁剪后：{len(landuse_clipped)}')
    print(f'  transport 裁剪后：{len(transport_clipped)}')
    print(f'  traffic 裁剪后：{len(traffic_clipped)}')
    print(f'  water 裁剪后：{len(water_clipped)}')

    # 按 fclass 从裁剪后的 landuse 中提取各子集
    industrial_raw  = landuse_clipped[landuse_clipped['fclass'] == 'industrial'].copy()
    green_raw       = landuse_clipped[landuse_clipped['fclass'].isin(
                          ['forest', 'grass', 'park', 'scrub', 'meadow'])].copy()
    residential_raw = landuse_clipped[landuse_clipped['fclass'] == 'residential'].copy()
    print(f'  industrial：{len(industrial_raw)}  '
          f'green：{len(green_raw)}  residential：{len(residential_raw)}')

    # ----------------------------------------------------------
    # D. 几何修复：在 WGS84 下修复拓扑错误
    #    buffer(0) / make_valid 等操作与坐标系无关，WGS84 下可正常执行。
    #    min_area 阈值此处为角度平方单位（极小值），仅用于过滤退化图形。
    # ----------------------------------------------------------
    print('\n=== Step D：几何修复（WGS84）===')
    parcel            = clean_geometry(parcel,            min_area=1e-10)
    industrial_raw    = clean_geometry(industrial_raw,    min_area=1e-10)
    green_raw         = clean_geometry(green_raw,         min_area=1e-10)
    residential_raw   = clean_geometry(residential_raw,   min_area=1e-10)
    transport_clipped = clean_geometry(transport_clipped, min_area=1e-10)
    traffic_clipped   = clean_geometry(traffic_clipped,   min_area=1e-10)
    water_clipped     = clean_geometry(water_clipped,     min_area=1e-10)
    if lvdipark_available:
        lvdipark = clean_geometry(lvdipark, min_area=1e-10)
    print(f'  地块：{len(parcel)}  工业：{len(industrial_raw)}  绿地：{len(green_raw)}')
    print(f'  居住：{len(residential_raw)}  transport：{len(transport_clipped)}')
    print(f'  traffic：{len(traffic_clipped)}  water：{len(water_clipped)}')

    # ----------------------------------------------------------
    # E. 同类要素聚合：在 WGS84 下合并同类面要素
    #    buffer_size 单位为度（WGS84），0.000001° ≈ 0.1m，
    #    仅用于弥合极小缝隙，不影响面积计算结果。
    # ----------------------------------------------------------
    print('\n=== Step E：同类要素聚合（WGS84）===')
    _buf = 0.000001    # WGS84 下的 buffer 值（度），等效约 0.1m，仅消除缝隙用
    industrial_gdf  = consolidate_class_with_buffer(industrial_raw,    buffer_size=_buf)
    green_gdf       = consolidate_class_with_buffer(green_raw,         buffer_size=_buf)
    residential_gdf = consolidate_class_with_buffer(residential_raw,   buffer_size=_buf)
    transport_gdf   = consolidate_class_with_buffer(transport_clipped,  buffer_size=_buf)
    traffic_gdf     = consolidate_class_with_buffer(traffic_clipped,    buffer_size=_buf)
    water_gdf       = consolidate_class_with_buffer(water_clipped,      buffer_size=_buf)
    # 交通 OSM = transport + traffic 合并后再聚合
    traffic_osm_raw = gpd.GeoDataFrame(
        pd.concat([transport_gdf, traffic_gdf], ignore_index=True),
        crs='EPSG:4326')
    traffic_osm_gdf = consolidate_class_with_buffer(traffic_osm_raw, buffer_size=_buf)
    print(f'  industrial：{len(industrial_gdf)}  green：{len(green_gdf)}')
    print(f'  residential：{len(residential_gdf)}  transport：{len(transport_gdf)}')
    print(f'  traffic：{len(traffic_gdf)}  water：{len(water_gdf)}')
    print(f'  交通 OSM 合并（transport+traffic）：{len(traffic_osm_gdf)}')
    
    # 绿地公园数据聚合（如果可用）
    if lvdipark_available and len(lvdipark) > 0:
        lvdipark_gdf = consolidate_class_with_buffer(lvdipark, buffer_size=_buf)
        print(f'  绿地公园聚合结果：{len(lvdipark_gdf)}')
    else:
        lvdipark_gdf = gpd.GeoDataFrame(columns=['geometry'], crs='EPSG:4326')
        print(f'  绿地公园：无数据或跳过聚合')

    # ==========================================================
    # F. 第二步：转换到 WGS84 UTM 投影坐标系（单位：米）
    #    必须在所有裁剪、修复、聚合完成之后才做此转换，
    #    因为后续面积计算必须在投影坐标系（米）下才能精确。
    #    UTM 带号根据地块 WGS84 范围中心经纬度自动推算。
    # ==========================================================
    print('\n=== Step F：转换到 WGS84 UTM 投影坐标系（用于精确面积计算）===')
    utm_epsg   = get_utm_epsg(parcel)     # parcel 此时仍为 WGS84，可直接推算
    target_crs = f'EPSG:{utm_epsg}'
    print(f'  推算投影坐标系：{target_crs}')

    parcel          = parcel.to_crs(target_crs)
    industrial_gdf  = industrial_gdf.to_crs(target_crs)
    green_gdf       = green_gdf.to_crs(target_crs)
    residential_gdf = residential_gdf.to_crs(target_crs)
    traffic_osm_gdf = traffic_osm_gdf.to_crs(target_crs)
    water_gdf       = water_gdf.to_crs(target_crs)
    if poi_available:
        pois        = pois.to_crs(target_crs)
    if lvdipark_available:
        lvdipark_gdf  = lvdipark_gdf.to_crs(target_crs)
    print(f'  ✓ 地块、各类 OSM 聚合结果、POI、绿地公园已转换到 {target_crs}（单位：米）')

    # ----------------------------------------------------------
    # G. 初始化地块结果表
    #    此时 parcel 已为 UTM 投影坐标系（单位：米），可进行精确面积计算。
    #    字段命名规则（均为短名，兼容 shapefile 10字符限制）：
    #      面积占比字段：后缀 AP（Area Proportion，值域 0~1）
    #      POI数量字段：后缀 N（Number）
    #      POI占比字段：后缀 PP（POI Proportion，值域 0~1）
    # ----------------------------------------------------------
    if 'FID' not in parcel.columns:
        parcel['FID'] = range(1, len(parcel) + 1)
    parcel = parcel.reset_index(drop=True)

    # ---------- 地块面积（平方千米，UTM投影坐标系下精确计算）----------
    parcel['AREA']    = 0.0    # 地块面积（平方千米）

    # ---------- 面积占比指标 ----------
    parcel['INDU_AP'] = 0.0    # 工业 (industrial) 面积 / 地块面积
    parcel['GRN_AP']  = 0.0    # 绿地 (forest+grass+park+scrub+meadow) 面积 / 地块面积
    parcel['TFC_AP']  = 0.0    # 交通 OSM(transport+traffic) 面积 / 地块面积
    parcel['RES_AP']  = 0.0    # 居住 (residential) 面积 / 地块面积
    parcel['WAT_AP']  = 0.0    # 水体 (water) 面积 / 地块面积
    parcel['LVDI_PP'] = 0.0    # 绿地公园面积 / 地块面积（2023公园与绿地广场数据）

    # ---------- POI 数量指标 ----------
    parcel['POI_N']   = 0      # 地块内所有 POI 总数
    parcel['TFC_N']   = 0      # 交通物流 POI 数
    parcel['RES_N']   = 0      # 居住用地 POI 数
    parcel['COM_N']   = 0      # 商业服务业 POI 数
    parcel['PUB_N']   = 0      # 公共管理与公共服务 POI 数
    parcel['UTL_N']   = 0      # 公用设施 POI 数
    parcel['OTH_N']   = 0      # 其他（未归类）POI 数

    # ---------- POI 占比指标（各类 POI 数 / POI 总数）----------
    parcel['TFC_PP']  = 0.0    # 交通物流 POI / POI 总数
    parcel['RES_PP']  = 0.0    # 居住用地 POI / POI 总数
    parcel['COM_PP']  = 0.0    # 商业服务业 POI / POI 总数
    parcel['PUB_PP']  = 0.0    # 公共管理与公共服务 POI / POI 总数
    parcel['UTL_PP']  = 0.0    # 公用设施 POI / POI 总数
    parcel['OTH_PP']  = 0.0    # 其他 POI / POI 总数

    # ---------- 站点标记字段 ----------
    # IS_HuoChe ：地块内"交通物流设施"类POI中有KindNameSm="客运火车站"时为1，否则为0
    # IS_QiChe  ：地块内"交通物流设施"类POI中有KindNameSm="客运汽车站"时为1，否则为0
    # IS_JiChang：地块内"交通物流设施"类POI中有KindNameSm="机场"时为1，否则为0
    parcel['IS_HuoChe']  = 0    # 是否含客运火车站（0/1）
    parcel['IS_QiChe']   = 0    # 是否含客运汽车站（0/1）
    parcel['IS_JiChang'] = 0    # 是否含机场（0/1）

    # ---------- 类别字段（最后赋值）----------
    parcel['类别']    = '待定'

    # ==========================================================
    # ① 全量计算所有面积占比指标（对所有地块）
    # ==========================================================
    print('\n' + '=' * 60)
    print('>>> 阶段一：全量计算指标（所有地块）')

    print('\n  计算地块面积（平方千米）...')
    # 此时 parcel 已在 UTM 投影坐标系（单位：米）下，.area 即为平方米，需转换为平方千米
    parcel['AREA'] = (parcel.geometry.area / 1_000_000).round(4)
    print(f'  ✓ 地块面积计算完成，面积范围：'
          f'{parcel["AREA"].min():.4f} ~ {parcel["AREA"].max():.4f} km²')

    print('\n  计算工业面积占比...')
    parcel['INDU_AP'] = calc_area_ratio(parcel, industrial_gdf).round(4)

    print('  计算绿地面积占比...')
    parcel['GRN_AP']  = calc_area_ratio(parcel, green_gdf).round(4)

    print('  计算交通OSM面积占比...')
    parcel['TFC_AP']  = calc_area_ratio(parcel, traffic_osm_gdf).round(4)

    print('  计算居住OSM面积占比...')
    parcel['RES_AP']  = calc_area_ratio(parcel, residential_gdf).round(4)

    print('  计算水体面积占比...')
    parcel['WAT_AP']  = calc_area_ratio(parcel, water_gdf).round(4)

    # 计算绿地公园面积占比（如果数据可用）
    if lvdipark_available and len(lvdipark_gdf) > 0:
        print('  计算绿地公园面积占比...')
        parcel['LVDI_PP'] = calc_area_ratio(parcel, lvdipark_gdf).round(4)
        print(f'  ✓ 绿地公园面积占比计算完成')
    else:
        print('  ℹ️ 无绿地公园数据，跳过 LVDI_PP 计算')

    print('  ✓ 全量面积占比指标计算完成')

    # ==========================================================
    # ② 全量计算所有 POI 指标（对所有地块）
    # ==========================================================
    if poi_available and len(pois) > 0:
        print('\n  计算全量 POI 指标...')

        # 为所有地块建立临时ID，执行一次大空间连接
        parcel_tmp = parcel[['geometry']].copy()
        parcel_tmp['_TMPID'] = parcel_tmp.index

        poi_join = gpd.sjoin(
            pois[['KindNameBi', 'KindNameSm', 'geometry']].copy(),
            parcel_tmp[['_TMPID', 'geometry']].copy(),
            how='inner', predicate='within'
        )
        # 若 within 无结果则退回 intersects
        if len(poi_join) == 0:
            print('  ⚠️ within 无结果，改用 intersects...')
            poi_join = gpd.sjoin(
                pois[['KindNameBi', 'KindNameSm', 'geometry']].copy(),
                parcel_tmp[['_TMPID', 'geometry']].copy(),
                how='inner', predicate='intersects'
            )
        print(f'  空间连接匹配 POI：{len(poi_join)} 条')

        # 定义各类别 KindNameBi 集合
        tfc_bi  = set(PARCEL_TO_KindNameBi['交通物流设施'])
        res_bi  = set(PARCEL_TO_KindNameBi['居住用地'])
        com_bi  = set(PARCEL_TO_KindNameBi['商业服务业设施用地'])
        pub_bi  = set(PARCEL_TO_KindNameBi['公共管理与公共服务用地'])
        utl_bi  = set(PARCEL_TO_KindNameBi['公用设施用地'])

        # 逐地块统计各类 POI 数，同时判断火车站/汽车站
        for tmp_id, grp in poi_join.groupby('index_right'):
            total   = len(grp)
            bi_vals = grp['KindNameBi']
            sm_vals = grp['KindNameSm'] if 'KindNameSm' in grp.columns else pd.Series(dtype=str)

            tfc_n  = int(bi_vals.isin(tfc_bi).sum())
            res_n  = int(bi_vals.isin(res_bi).sum())
            com_n  = int(bi_vals.isin(com_bi).sum())
            pub_n  = int(bi_vals.isin(pub_bi).sum())
            utl_n  = int(bi_vals.isin(utl_bi).sum())
            oth_n  = total - tfc_n - res_n - com_n - pub_n - utl_n

            parcel.at[tmp_id, 'POI_N']  = total
            parcel.at[tmp_id, 'TFC_N']  = tfc_n
            parcel.at[tmp_id, 'RES_N']  = res_n
            parcel.at[tmp_id, 'COM_N']  = com_n
            parcel.at[tmp_id, 'PUB_N']  = pub_n
            parcel.at[tmp_id, 'UTL_N']  = utl_n
            parcel.at[tmp_id, 'OTH_N']  = oth_n

            # 占比（避免除零）
            parcel.at[tmp_id, 'TFC_PP'] = round(tfc_n / total, 4)
            parcel.at[tmp_id, 'RES_PP'] = round(res_n / total, 4)
            parcel.at[tmp_id, 'COM_PP'] = round(com_n / total, 4)
            parcel.at[tmp_id, 'PUB_PP'] = round(pub_n / total, 4)
            parcel.at[tmp_id, 'UTL_PP'] = round(utl_n / total, 4)  # 修复：原代码误用 oth_n
            parcel.at[tmp_id, 'OTH_PP'] = round(oth_n / total, 4)

            # ------ 火车站 / 汽车站 / 机场标记 ------
            # 仅在"交通物流设施"类（KindNameBi in tfc_bi）的 POI 中检查 KindNameSm
            if tfc_n > 0 and len(sm_vals) > 0:
                tfc_sm = grp.loc[bi_vals.isin(tfc_bi), 'KindNameSm']
                if (tfc_sm == '客运火车站').any():
                    parcel.at[tmp_id, 'IS_HuoChe']  = 1
                if (tfc_sm == '客运汽车站').any():
                    parcel.at[tmp_id, 'IS_QiChe']   = 1
                if (tfc_sm == '机场').any():
                    parcel.at[tmp_id, 'IS_JiChang'] = 1

        poi_parcel_cnt = (parcel['POI_N'] > 0).sum()
        print(f'  ✓ 全量 POI 指标计算完成，有 POI 地块：{poi_parcel_cnt} 个')
    else:
        print('\n  ⚠️ 无 POI 数据，所有 POI 字段保持 0')

    # ==========================================================
    # ③ 依据指标串行判别地类（严格只对"待定"地块赋值）
    # ==========================================================
    print('\n' + '=' * 60)
    print('>>> 阶段二：串行判别地类')

    # ------ Step 1：工业用地 ------
    # 所有地块均已有 INDU_AP，直接用
    print(f'\nStep 1：工业用地（INDU_AP > {THRESHOLD_INDUSTRIAL:.0%}）')
    mask_s1 = (parcel['INDU_AP'] > THRESHOLD_INDUSTRIAL)
    parcel.loc[mask_s1, '类别'] = '工业'
    print(f'  → 工业：{mask_s1.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 2：绿地 ------
    print(f'\nStep 2：绿地（GRN_AP > {THRESHOLD_GREEN:.0%}，仅待定地块）')
    mask_s2 = (parcel['GRN_AP'] > THRESHOLD_GREEN) & (parcel['类别'] == '待定')
    parcel.loc[mask_s2, '类别'] = '绿地'
    print(f'  → 绿地：{mask_s2.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 3（逻辑节点）------
    print(f'\nStep 3：确认进入交通物流判别，当前待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 4：交通物流设施 POI ------
    print(f'\nStep 4：交通物流设施——POI（TFC_PP > {THRESHOLD_TRAFFIC_POI:.0%}，仅待定地块）')
    mask_s4 = (parcel['TFC_PP'] > THRESHOLD_TRAFFIC_POI) & (parcel['类别'] == '待定')
    parcel.loc[mask_s4, '类别'] = '交通物流设施'
    print(f'  → 交通物流（POI）：{mask_s4.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 5：交通物流设施 OSM 补充 ------
    print(f'\nStep 5：交通物流设施——OSM补充（TFC_AP > {THRESHOLD_TRAFFIC_OSM:.0%}，仅待定地块）')
    mask_s5 = (parcel['TFC_AP'] > THRESHOLD_TRAFFIC_OSM) & (parcel['类别'] == '待定') & (parcel['TFC_AP'] > parcel['RES_AP']) 
    parcel.loc[mask_s5, '类别'] = '交通物流设施'
    print(f'  → 交通物流（OSM）：{mask_s5.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 6（逻辑节点）------
    print(f'\nStep 6：确认进入居住用地判别，当前待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 7：居住用地 POI ------
    print(f'\nStep 7：居住用地——POI（RES_PP > {THRESHOLD_RESIDENTIAL_POI:.0%}，仅待定地块）')
    mask_s7 = (parcel['RES_PP'] > THRESHOLD_RESIDENTIAL_POI) & (parcel['类别'] == '待定')
    parcel.loc[mask_s7, '类别'] = '居住用地'
    print(f'  → 居住用地（POI）：{mask_s7.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 8：居住用地 OSM 补充 ------
    print(f'\nStep 8：居住用地——OSM补充（RES_AP > {THRESHOLD_RESIDENTIAL_OSM:.0%}，仅待定地块）')
    mask_s8 = (parcel['RES_AP'] > THRESHOLD_RESIDENTIAL_OSM) & (parcel['类别'] == '待定')
    parcel.loc[mask_s8, '类别'] = '居住用地'
    print(f'  → 居住用地（OSM）：{mask_s8.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 9：水体 ------
    print(f'\nStep 9：水体（WAT_AP > {THRESHOLD_WATER:.0%}，仅待定地块）')
    mask_s9 = (parcel['WAT_AP'] > THRESHOLD_WATER) & (parcel['类别'] == '待定')
    parcel.loc[mask_s9, '类别'] = '水体'
    print(f'  → 水体：{mask_s9.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')

    # ------ Step 10：剩余地块取最大 POI 占比细分 ------
    # 对象：Step9 后仍"待定"的地块
    # 规则：从 COM_PP / PUB_PP / UTL_PP / OTH_PP 中取最大值确定类别
    #        （无额外阈值，直接取最大；有 POI 才参与比较）
    #        特殊情况：若四类占比都为 0，则直接归为"其他"
    print(f'\nStep 10：剩余地块 POI 细分（商业/公共管理/公用设施/其他）')
    remain_last = parcel['类别'] == '待定'
    print(f'  进入 Step10 的地块数：{remain_last.sum()}')

    if remain_last.any():
        sub = parcel.loc[remain_last, ['COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP']].copy()
        cat_map = {
            'COM_PP': '商业服务业设施用地',
            'PUB_PP': '公共管理与公共服务用地',
            'UTL_PP': '公用设施用地',
            'OTH_PP': '其他',
        }
        
        # 检查是否所有 POI 占比都为 0
        all_zero_mask = (sub[['COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP']] == 0).all(axis=1)
        
        # 对于非全 0 的地块，取最大值对应的类别
        non_zero_mask = ~all_zero_mask
        if non_zero_mask.any():
            max_col = sub.loc[non_zero_mask].idxmax(axis=1)
            # 关键修复：使用 sub.index[non_zero_mask] 获取正确的原始索引
            parcel.loc[sub.index[non_zero_mask], '类别'] = max_col.map(cat_map)
            
            # 调试统计（仅非全 0 地块）
            print(f'    非全 0 地块分类统计：')
            for col, name in cat_map.items():
                cnt = (max_col == col).sum()
                if cnt > 0:
                    print(f'      {name} (max={col}): {cnt} 个')
        
        # 对于全 0 的地块，直接归为"其他"
        if all_zero_mask.any():
            parcel.loc[sub.index[all_zero_mask], '类别'] = '其他'
            print(f'    其他（POI 全为 0）：{all_zero_mask.sum()} 个')

    # 兜底：确保无"待定"残留
    leftover = (parcel['类别'] == '待定').sum()
    if leftover:
        parcel.loc[parcel['类别'] == '待定', '类别'] = '其他'
        print(f'  兜底归"其他"：{leftover} 个')

    # ==========================================================
    # Step 11：基于站点标记强制修正类别（全量检查，可覆盖任何已有类别）
    #   规则 1：IS_HuoChe == 1 且 AREA < 4.0 km² (4,000,000 m²) → 交通物流设施
    #   规则 2：IS_QiChe  == 1 且 AREA < 1.5 km² (1,500,000 m²) → 交通物流设施
    #   规则 3：IS_JiChang == 1 且 AREA < 30.0 km² (30,000,000 m²) → 交通物流设施
    # ==========================================================
    print(f'\nStep 11：基于站点标记强制修正类别')

    # 规则 1：含客运火车站 且 面积 < 4.0 km² (4,000,000 m²)
    AREA_THRESHOLD_HUOCHE = 6.0   # 4.0 km²，单位：平方千米
    mask_huoche = (parcel['IS_HuoChe'] == 1) & (parcel['AREA'] < AREA_THRESHOLD_HUOCHE)
    changed_huoche = mask_huoche.sum()
    if changed_huoche > 0:
        parcel.loc[mask_huoche, '类别'] = '交通物流设施'
    print(f'  规则1（火车站，AREA < {AREA_THRESHOLD_HUOCHE:,} m²）：'
          f'修正 {changed_huoche} 个地块 → 交通物流设施')

    # 规则 2：含客运汽车站 且 面积 < 1.5 km² (1,500,000 m²)
    AREA_THRESHOLD_QICHE  = 4   # 1.5 km²，单位：平方千米
    mask_qiche = (parcel['IS_QiChe'] == 1) & (parcel['AREA'] < AREA_THRESHOLD_QICHE)
    changed_qiche = mask_qiche.sum()
    if changed_qiche > 0:
        parcel.loc[mask_qiche, '类别'] = '交通物流设施'
    print(f'  规则 2（汽车站，AREA < {AREA_THRESHOLD_QICHE} km²）：'
          f'修正 {changed_qiche} 个地块 → 交通物流设施')

    # 规则 3：含机场 且 面积 < 30.0 km² (30,000,000 m²)
    AREA_THRESHOLD_JICHANG = 40.0  # 30.0 km²，单位：平方千米
    mask_jichang = (parcel['IS_JiChang'] == 1) & (parcel['AREA'] < AREA_THRESHOLD_JICHANG)
    changed_jichang = mask_jichang.sum()
    if changed_jichang > 0:
        parcel.loc[mask_jichang, '类别'] = '交通物流设施'
    print(f'  规则 3（机场，AREA < {AREA_THRESHOLD_JICHANG} km²）：'
          f'修正 {changed_jichang} 个地块 → 交通物流设施')
    print(f'  Step11 合计修正：{(mask_huoche | mask_qiche | mask_jichang).sum()} 个地块')

    # ==========================================================
    # Step 12：基于绿地公园占比和 OSM 绿地占比强制修正类别（仅当绿地公园数据可用时）
    #   规则：LVDI_PP > 0.4 且 GRN_AP > 0.4 且 当前类别不是"交通物流设施" → 绿地
    # ==========================================================
    if lvdipark_available:
        print(f'\nStep 12：基于绿地公园 +OSM 绿地占比修正类别（LVDI_PP > 0.4 且 GRN_AP > 0.4，仅非交通用地）')
        mask_lvd = (parcel['LVDI_PP'] > 0.4) & (parcel['GRN_AP'] > 0.4) & (parcel['类别'] != '交通物流设施')
        changed_lvd = mask_lvd.sum()
        if changed_lvd > 0:
            parcel.loc[mask_lvd, '类别'] = '绿地'
            print(f'  规则（LVDI_PP > 0.4 且 GRN_AP > 0.4 且非交通用地）：修正 {changed_lvd} 个地块 → 绿地')
        else:
            print(f'  无符合条件的地块')
    else:
        print(f'\nℹ️ 无绿地公园数据，跳过 Step12 判断')

    # ==========================================================
    # Step 13：对小面积"其他"地块强制修正为绿地
    #   规则：类别 == "其他" 且 AREA < 2.0 km² → 绿地
    #   目的：避免将细碎小地块误判为"其他"，统一归并为绿地
    # ==========================================================
    print(f'\nStep 13：对小面积"其他"地块强制修正为绿地（AREA < 2.0 km²）')
    mask_small_other = (parcel['类别'] == '其他') & (parcel['AREA'] < 2.0)
    changed_small_other = mask_small_other.sum()
    if changed_small_other > 0:
        parcel.loc[mask_small_other, '类别'] = '绿地'
        print(f'  规则（类别="其他" 且 AREA < 2.0 km²）：修正 {changed_small_other} 个地块 → 绿地')
    else:
        print(f'  无符合条件的地块')

    # ==========================================================
    # 汇总
    # ==========================================================
    print('\n' + '=' * 60)
    print('分类汇总：')
    for cat, cnt in parcel['类别'].value_counts().items():
        print(f'  {cat}：{cnt} 个')

    # ==========================================================
    # 输出文件
    # ==========================================================
    print('\n=== 输出结果 ===')

    # 保存前转回 WGS84 地理坐标系
    parcel_wgs84 = to_geographic_crs(parcel)

    # ----------------------------------------------------------
    # 主输出：GeoPackage（推荐，支持中文字段名、无字段长度和数量限制）
    # ----------------------------------------------------------
    output_gpkg = os.path.join(output_dir, '地块分类结果.gpkg')
    
    # 若文件已存在，采用自动添加数字后缀的策略生成唯一文件名
    if os.path.exists(output_gpkg):
        base_name = os.path.splitext(output_gpkg)[0]
        ext = os.path.splitext(output_gpkg)[1]
        counter = 1
        while os.path.exists(f"{base_name}{counter}{ext}"):
            counter += 1
        output_gpkg = f"{base_name}{counter}{ext}"
        print(f"  ⚠️ 原文件已存在，使用新文件名：{output_gpkg}")
    
    parcel_wgs84.to_file(output_gpkg, driver='GPKG', encoding='utf-8')
    print(f'  GeoPackage 已保存：{output_gpkg}')
    print(f'  字段数：{len(parcel_wgs84.columns)}  要素数：{len(parcel_wgs84)}')

    # ----------------------------------------------------------
    # 备用输出：Shapefile
    # 注意：Shapefile 不支持中文字段名，写入前将"类别"重命名为"LANDTYPE"。
    # 其余字段名均为纯英文且不超过 10 字符，可正常写出。
    # ----------------------------------------------------------
    output_shp = os.path.join(output_dir, '地块分类结果.shp')
    
    # 若文件已存在，采用自动添加数字后缀的策略生成唯一文件名
    if os.path.exists(output_shp):
        base_name = os.path.splitext(output_shp)[0]
        ext = os.path.splitext(output_shp)[1]
        counter = 1
        while os.path.exists(f"{base_name}{counter}{ext}"):
            counter += 1
        output_shp = f"{base_name}{counter}{ext}"
        print(f"  ⚠️ 原 Shapefile 已存在，使用新文件名：{output_shp}")
    
    try:
        parcel_shp = parcel_wgs84.rename(columns={'类别': 'LANDTYPE'})
        parcel_shp.to_file(output_shp, encoding='utf-8')
        print(f'  Shapefile 已保存：{output_shp}')
        print(f'  （Shapefile 中"类别"字段已重命名为"LANDTYPE"，其余字段不变）')
    except Exception as e:
        print(f'  ⚠️ Shapefile 保存失败：{e}，请直接使用 GeoPackage 格式')

    # 字段说明 txt
    txt_path = os.path.join(output_dir, '属性字段说明.txt')
    SEP  = '=' * 72
    SEP2 = '-' * 72
    
    # 构建字段说明文档内容
    lines = [
        SEP,
        '  地块分类结果 — 属性字段说明',
        SEP,
        '',
        '【坐标系处理流程】',
        '  ① 读取后        → 所有数据统一转换到 WGS84 地理坐标系 (EPSG:4326)',
        '  ② 裁剪/修复/聚合 → 在 WGS84 下完成（不涉及面积/距离计算）',
        '  ③ 计算指标前    → 转换到 WGS84 UTM 投影坐标系（单位：米，精确面积计算）',
        f'                    本次推算投影坐标系：EPSG:{utm_epsg}',
        '  ④ 保存前        → 转回 WGS84 地理坐标系 (EPSG:4326) 输出',
        '  ⑤ AREA 字段     → 平方米转换为平方千米（÷1,000,000），便于大面积数据表达',
        '',
        '【最终输出地类（共9类）】',
        '  工业 / 绿地 / 交通物流设施 / 居住用地 / 水体',
        '  商业服务业设施用地 / 公共管理与公共服务用地 / 公用设施用地 / 其他',
        '',
        SEP2,
        '【字段列表】',
        SEP2,
        '',
        '  ▌ 分类结果',
        '  类别/LANDTYPE  最终判定地类（9 类之一）（GeoPackage 字段名：类别；Shapefile 字段名：LANDTYPE）',
        '',
        '  ▌ 地块面积',
        '  AREA       地块面积（平方千米，UTM 投影坐标系下精确计算）',
        '',
        '  ▌ OSM 面积占比指标（各类 OSM 面积 / 地块面积，全量地块均计算）',
        '  INDU_AP    industrial 面积 / 地块面积',
        '  GRN_AP     (forest+grass+park+scrub+meadow) 面积 / 地块面积',
        '  TFC_AP     (transport 全部 + traffic 中 fuel/parking/parking_multistorey/service) 面积 / 地块面积',
        '  RES_AP     residential 面积 / 地块面积',
        '  WAT_AP     water 面积 / 地块面积',
    ]
    
    # 如果绿地公园数据可用，添加相关说明
    if lvdipark_available:
        lines.extend([
            '',
            '  ▌ 绿地公园面积占比指标（2023公园与绿地广场数据）',
            '  LVDI_PP    绿地公园面积 / 地块面积（来源于 data/绿地公园/2023公园与绿地广场/{城市}/*.shp）',
        ])
    
    lines.extend([
        '',
        '  ▌ POI 数量指标（全量地块均计算）',
        '  POI_N      地块内 POI 总数',
        '  TFC_N      交通物流类 POI 数（KindNameBi=交通运输、仓储）',
        '  RES_N      居住用地类 POI 数（KindNameBi in 住宿/批发零售/居民服务/餐饮）',
        '  COM_N      商业服务业类 POI 数（KindNameBi in 公司企业/金融保险/汽车销售及服务/商业设施商务服务）',
        '  PUB_N      公共管理与公共服务类 POI 数（KindNameBi in 教育文化/科研/卫生社保/运动休闲）',
        '  UTL_N      公用设施类 POI 数（KindNameBi=公共设施）',
        '  OTH_N      其他 POI 数（不属于以上任何类别的 POI）',
        '',
        '  ▌ POI 占比指标（各类 POI 数 / POI_N，全量地块均计算；无 POI 地块值为 0）',
        '  TFC_PP     TFC_N / POI_N',
        '  RES_PP     RES_N / POI_N',
        '  COM_PP     COM_N / POI_N',
        '  PUB_PP     PUB_N / POI_N',
        '  UTL_PP     UTL_N / POI_N',
        '  OTH_PP     OTH_N / POI_N',
        '',
        '  ▌ 站点标记字段（基于 POI KindNameSm 精确匹配，全量地块均计算）',
        '  IS_HuoChe  地块内"交通物流设施"类 POI 中含 KindNameSm="客运火车站"时为 1，否则为 0',
        '  IS_QiChe   地块内"交通物流设施"类 POI 中含 KindNameSm="客运汽车站"时为 1，否则为 0',
        '  IS_JiChang 地块内"交通物流设施"类 POI 中含 KindNameSm="机场"时为 1，否则为 0',
        '',
        SEP2,
        '【判别流程（阶段二，严格串行，每步只对"待定"地块赋值）】',
        SEP2,
        '',
        f'  Step1  INDU_AP > {THRESHOLD_INDUSTRIAL:.0%}                    → 工业',
        f'  Step2  GRN_AP  > {THRESHOLD_GREEN:.0%}                    → 绿地',
        f'  Step4  TFC_PP  > {THRESHOLD_TRAFFIC_POI:.0%}（POI 方法）          → 交通物流设施',
        f'  Step5  TFC_AP  > {THRESHOLD_TRAFFIC_OSM:.0%}（OSM 面积补充）        → 交通物流设施',
        f'  Step7  RES_PP  > {THRESHOLD_RESIDENTIAL_POI:.0%}（POI 方法）          → 居住用地',
        f'  Step8  RES_AP  > {THRESHOLD_RESIDENTIAL_OSM:.0%}（OSM 面积补充）        → 居住用地',
        f'  Step9  WAT_AP  > {THRESHOLD_WATER:.0%}                    → 水体',
        '  Step10 取 COM_PP/PUB_PP/UTL_PP/OTH_PP 中最大值对应类别',
        '         → 商业服务业设施用地 / 公共管理与公共服务用地 / 公用设施用地 / 其他',
        '  Step11 全量检查站点标记（可覆盖任何已有类别）：',
        '    规则 1：IS_HuoChe==1  且 AREA < 4.0 km²   → 交通物流设施',
        '    规则 2：IS_QiChe==1   且 AREA < 1.5 km²   → 交通物流设施',
        '    规则 3：IS_JiChang==1 且 AREA < 30.0 km²  → 交通物流设施',
    ])
    
    # 如果绿地公园数据可用，添加 Step12 说明
    if lvdipark_available:
        lines.extend([
            '  Step12 绿地公园 +OSM 绿地占比修正（仅当导入绿地公园数据时）：',
            '    规则：LVDI_PP > 0.4 且 GRN_AP > 0.4 且 非交通物流设施 → 绿地',
        ])
    
    lines.extend([
        '  Step13 小面积"其他"地块修正：',
        '    规则：类别="其他" 且 AREA < 2.0 km² → 绿地',
        '',
        '【注意】',
        '  - 阶段一（指标计算）对全量地块计算，不受判别顺序影响。',
        '  - 阶段二（判别赋值）严格串行，已判别地块不再被后续步骤覆盖。',
        '  - Step10 无阈值限制，直接取 4 类 POI 占比中最大值；无 POI 地块归"其他"。',
        SEP,
    ])
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'  字段说明已保存：{txt_path}')
    print('\n✅ 处理完成！')


if __name__ == '__main__':
    main()