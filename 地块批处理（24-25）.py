# ============================================================
# 多城市地块分类批量处理脚本
# 功能：批量处理 37 个城市的地块数据，输出分类结果和完成报告
# ============================================================


import os
import sys
import time
from datetime import datetime
import traceback

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
import warnings
from shapely.geometry import MultiPolygon, Polygon, GeometryCollection, Point, MultiPoint, LineString, MultiLineString
from shapely.validation import make_valid
from shapely.ops import unary_union
from rtree import index
from pypinyin import lazy_pinyin
import pyproj
from pyproj.datadir import set_data_dir

try:
    if _proj_path:
        set_data_dir(_proj_path)
except Exception as e:
    print(f"⚠ set_data_dir 调用失败：{e}")

warnings.filterwarnings('ignore', category=UserWarning)

# ============================================================
# 全局路径配置
# ============================================================

# 数据根目录
DATA_ROOT = r'E:\地块分类数据\地块划分程序\DiKuai\data'

# 地块数据目录（每个城市一个shp文件）
PARCEL_BASE_DIR = os.path.join(DATA_ROOT, 'data_dikuai', '地块数据', '2025-parcel')

# POI数据目录（结构：POI2025\城市名.poi\城市名.gpkg）
POI_BASE_DIR = os.path.join(DATA_ROOT, 'data_poi', 'POI2025')

# 绿地公园数据目录（结构：2025公园与绿地广场\01北京\北京_公园绿地.shp）
LVDIPARK_BASE_DIR = os.path.join(DATA_ROOT, '绿地公园', '2025公园与绿地广场')

# OSM数据根目录（每个省份一个子目录，如 chongqing-250101-free.shp\）
OSM_ROOT = os.path.join(DATA_ROOT, 'data_osm', 'osm25')

# 输出根目录
OUTPUT_ROOT = os.path.join(DATA_ROOT, '..', 'output', '分类结果', '分类结果2025')

# ============================================================
# 城市 → OSM省份拼音目录名 映射表
# OSM目录命名规则：<省份拼音>-250101-free.shp
# ============================================================
CITY_TO_OSM_PROVINCE = {
    '北京':   'beijing',
    '成都':   'sichuan',
    '大连':   'liaoning',
    '福州':   'fujian',
    '广州':   'guangdong',
    '贵阳':   'guizhou',
    '哈尔滨': 'heilongjiang',
    '海口':   'hainan',
    '杭州':   'zhejiang',
    '合肥':   'anhui',
    '呼和浩特':'neimenggu',
    '济南':   'shandong',
    '景德镇': 'jiangxi',
    '昆明':   'yunnan',
    '拉萨':   'xizang',
    '兰州':   'gansu',
    '南昌':   'jiangxi',
    '南京':   'jiangsu',
    '南宁':   'guangxi',
    '宁波':   'zhejiang',
    '青岛':   'shandong',
    '厦门':   'fujian',
    '上海':   'shanghai',
    '深圳':   'guangdong',
    '沈阳':   'liaoning',
    '石家庄': 'hebei',
    '太原':   'shanxi',
    '天津':   'tianjin',
    '乌鲁木齐':'xinjiang',
    '武汉':   'hubei',
    '西安':   'shaanxi',
    '西宁':   'qinghai',
    '银川':   'ningxia',
    '长春':   'jilin',
    '长沙':   'hunan',
    '郑州':   'henan',
    '重庆':   'chongqing',
}

# 阈值参数
THRESHOLD_INDUSTRIAL = 0.50
THRESHOLD_GREEN = 0.45
THRESHOLD_TRAFFIC_POI = 0.60
THRESHOLD_TRAFFIC_OSM = 0.30
THRESHOLD_RESIDENTIAL_POI = 0.30
THRESHOLD_RESIDENTIAL_OSM = 0.30
THRESHOLD_WATER = 0.45

# POI 分类映射
PARCEL_TO_dlmc = {
    '居住用地': ['住、宿', '批发、零售', '居民服务', '餐饮'],
    '交通物流设施': ['交通运输、仓储'],
    '商业服务业设施用地': ['公司企业', '金融、保险', '汽车销售及服务', '商业设施、商务服务'],
    '公共管理与公共服务用地': ['教育、文化', '科研及技术服务', '卫生、社保', '运动、休闲'],
    '公用设施用地': ['公共设施'],
    '其他': [],
}
dlmc_TO_PARCEL = {}
for ptype, dlmc_list in PARCEL_TO_dlmc.items():
    for d in dlmc_list:
        dlmc_TO_PARCEL[d] = ptype

ALL_KNOWN_BI = set(dlmc_TO_PARCEL.keys())

# ============================================================
# 辅助函数
# ============================================================

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
                exploded.append(row.copy())
        elif isinstance(geom, Polygon):
            exploded.append(row.copy())
    return gpd.GeoDataFrame(exploded, crs=geo_df.crs)

def consolidate_class_with_buffer(gdf, buffer_size=0.1, chunk_size=1000):
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
                    buffered = g.buffer(buffer_size) if buffered is None else buffered.union(g.buffer(buffer_size))
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
    if gdf.crs is None:
        print(f"  ⚠ {name} 无坐标系，假定 WGS84")
        return gdf.set_crs('EPSG:4326')
    if gdf.crs.to_epsg() == 4326:
        return gdf
    return gdf.to_crs('EPSG:4326')

def get_utm_epsg(gdf_4326):
    bounds = gdf_4326.total_bounds
    lon_c = (bounds[0] + bounds[2]) / 2
    lat_c = (bounds[1] + bounds[3]) / 2
    zone = int((lon_c + 180) / 6) + 1
    epsg = 32600 + zone if lat_c >= 0 else 32700 + zone
    return epsg

def read_shapefile_with_fallback(filepath, layer_name='图层'):
    try:
        gdf = gpd.read_file(filepath)
        print(f"  ✓ {layer_name} 读取成功：{len(gdf)} 个要素")
        return gdf
    except Exception as e:
        print(f"  ⚠️ {layer_name} 读取失败：{str(e)[:100]}")
        return None

def efficient_clip(src, clip_gdf):
    if len(src) == 0:
        return gpd.GeoDataFrame(columns=['geometry'], crs=clip_gdf.crs)
    clip_sindex = clip_gdf.sindex
    clip_geoms = clip_gdf.geometry.tolist()
    rows = []
    for _, row_src in src.iterrows():
        geom_src = row_src.geometry
        if geom_src is None or geom_src.is_empty:
            continue
        for i in list(clip_sindex.intersection(geom_src.bounds)):
            inter = geom_src.intersection(clip_geoms[i])
            if not inter.is_empty:
                new_row = row_src.copy(); new_row.geometry = inter; rows.append(new_row)
    return gpd.GeoDataFrame(rows, crs=src.crs) if rows else gpd.GeoDataFrame(columns=['geometry'], crs=src.crs)

def calc_area_ratio(parcel_gdf, overlay_gdf):
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

def get_osm_paths_for_city(city_name):
    """
    根据城市名获取对应的 OSM 数据文件路径。
    OSM 目录结构：OSM_ROOT/<省份拼音>-<日期>-free.shp/gis_osm_*.shp
    支持模糊匹配不同日期的版本（如 250101、260107、260108 等）
    """
    province_pinyin = CITY_TO_OSM_PROVINCE.get(city_name)
    if province_pinyin is None:
        print(f"  ⚠️ 城市 [{city_name}] 未在映射表中，无法定位 OSM 省份目录")
        return None, None, None, None

    # 在 OSM_ROOT 目录下查找包含省份拼音的所有目录
    osm_dir_found = None
    if os.path.isdir(OSM_ROOT):
        for dir_name in os.listdir(OSM_ROOT):
            if not os.path.isdir(os.path.join(OSM_ROOT, dir_name)):
                continue
            # 目录名格式：<省份拼音>-<日期>-free.shp
            if dir_name.startswith(province_pinyin + '-'):
                osm_dir_found = os.path.join(OSM_ROOT, dir_name)
                break
    
    if osm_dir_found is None:
        print(f"  ⚠️ OSM 目录不存在（省份：{province_pinyin}）")
        return None, None, None, None

    water_path    = os.path.join(osm_dir_found, 'gis_osm_water_a_free_1.shp')
    landuse_path  = os.path.join(osm_dir_found, 'gis_osm_landuse_a_free_1.shp')
    transport_path= os.path.join(osm_dir_found, 'gis_osm_transport_a_free_1.shp')
    traffic_path  = os.path.join(osm_dir_found, 'gis_osm_traffic_a_free_1.shp')
    return water_path, landuse_path, transport_path, traffic_path

def find_lvdipark_for_city(city_name):
    """
    查找城市对应的绿地公园数据。
    目录结构：LVDIPARK_BASE_DIR\01北京\北京_公园绿地.shp
    文件夹名格式：<序号><城市名>（序号可能有空格或直连）
    """
    if not os.path.exists(LVDIPARK_BASE_DIR):
        return None

    for folder_name in os.listdir(LVDIPARK_BASE_DIR):
        folder_path = os.path.join(LVDIPARK_BASE_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        # 去掉开头数字和空格，宽松匹配城市名
        clean_name = folder_name.lstrip('0123456789').strip()
        if city_name in clean_name or clean_name in city_name:
            for file_name in os.listdir(folder_path):
                if file_name.endswith('.shp'):
                    lvdipark_path = os.path.join(folder_path, file_name)
                    print(f"  ✓ 找到绿地公园数据：{lvdipark_path}")
                    return lvdipark_path

    return None

def find_poi_for_city(city_name):
    """
    查找城市对应的POI数据。
    目录结构：POI_BASE_DIR\<城市名>.poi\<城市名>.gpkg
    """
    # 精确目录
    poi_folder = os.path.join(POI_BASE_DIR, f'{city_name}.poi')
    if os.path.exists(poi_folder):
        for file_name in os.listdir(poi_folder):
            if file_name.endswith('.gpkg') or file_name.endswith('.shp'):
                poi_path = os.path.join(poi_folder, file_name)
                print(f"  ✓ 找到 POI 数据：{poi_path}")
                return poi_path

    # 宽松匹配（文件夹名包含城市名）
    if os.path.exists(POI_BASE_DIR):
        for folder_name in os.listdir(POI_BASE_DIR):
            if city_name in folder_name:
                folder_path = os.path.join(POI_BASE_DIR, folder_name)
                if os.path.isdir(folder_path):
                    for file_name in os.listdir(folder_path):
                        if file_name.endswith('.gpkg') or file_name.endswith('.shp'):
                            poi_path = os.path.join(folder_path, file_name)
                            print(f"  ✓ 找到 POI 数据（宽松匹配）：{poi_path}")
                            return poi_path

    return None

# ============================================================
# 单个城市处理主函数
# ============================================================

def process_single_city(city_name, parcel_path):
    """处理单个城市的地块数据"""
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"正在处理城市：{city_name}")
    print(f"{'='*70}")

    try:
        # 1. 读取地块数据
        print('\n[1/8] 读取地块数据...')
        parcel = read_shapefile_with_fallback(parcel_path, '地块数据')
        if parcel is None or len(parcel) == 0:
            raise Exception(f"无法读取地块数据：{parcel_path}")

        # 2. 读取 OSM 数据（按城市对应省份读取）
        print('\n[2/8] 读取 OSM 数据...')
        water_path, landuse_path, transport_path, traffic_path = get_osm_paths_for_city(city_name)
        if None in (water_path, landuse_path, transport_path, traffic_path):
            raise Exception(f"无法定位城市 [{city_name}] 的OSM数据目录")

        water     = read_shapefile_with_fallback(water_path,     '水体数据')
        landuse   = read_shapefile_with_fallback(landuse_path,   '土地利用数据')
        transport = read_shapefile_with_fallback(transport_path, 'transport 数据')
        traffic   = read_shapefile_with_fallback(traffic_path,   'traffic 数据')

        if any([water is None, landuse is None, transport is None, traffic is None]):
            raise Exception("OSM 数据读取失败")

        # traffic 只保留指定 fclass 面要素
        traffic_fclass = ['fuel', 'parking', 'parking_multistorey', 'service']
        traffic = traffic[traffic['fclass'].isin(traffic_fclass)].copy()
        print(f'  traffic 筛选后（{traffic_fclass}）：{len(traffic)} 个要素')

        # 3. 读取绿地公园数据（可选）
        print('\n[3/8] 读取绿地公园数据...')
        lvdipark_path = find_lvdipark_for_city(city_name)
        lvdipark_available = False
        lvdipark = gpd.GeoDataFrame()
        if lvdipark_path and os.path.exists(lvdipark_path):
            try:
                lvdipark = read_shapefile_with_fallback(lvdipark_path, '绿地公园数据')
                if lvdipark is not None:
                    lvdipark = to_wgs84(lvdipark, '绿地公园')
                    lvdipark_available = True
            except Exception as e:
                print(f'  ⚠️ 绿地公园读取失败：{e}，跳过处理')
        else:
            print(f'  ℹ️ 未找到绿地公园数据（城市：{city_name}）')

        # 4. 读取 POI 数据（可选）
        print('\n[4/8] 读取 POI 数据...')
        poi_path = find_poi_for_city(city_name)
        poi_available = False
        pois = gpd.GeoDataFrame()
        if poi_path and os.path.exists(poi_path):
            try:
                pois = read_shapefile_with_fallback(poi_path, 'POI 数据')
                if pois is not None:
                    pois = to_wgs84(pois, 'POI')
                    poi_available = 'dlmc' in pois.columns
                    if not poi_available:
                        print('  ⚠️ POI 缺少 dlmc 字段，跳过 POI 判断')
            except Exception as e:
                print(f'  ⚠️ POI 读取失败：{e}')
        else:
            print(f'  ℹ️ 未找到 POI 数据（城市：{city_name}）')

        # 5. 统一转换到 WGS84
        print('\n[5/8] 坐标系统一转换到 WGS84...')
        parcel    = to_wgs84(parcel,    '地块')
        water     = to_wgs84(water,     '水体')
        landuse   = to_wgs84(landuse,   'landuse')
        transport = to_wgs84(transport, 'transport')
        traffic   = to_wgs84(traffic,   'traffic')

        # 6. 裁剪 OSM 数据到地块范围
        print('\n[6/8] 裁剪 OSM 数据...')
        landuse_clipped   = efficient_clip(landuse,   parcel)
        transport_clipped = efficient_clip(transport, parcel)
        traffic_clipped   = efficient_clip(traffic,   parcel)
        water_clipped     = efficient_clip(water,     parcel)

        industrial_raw  = landuse_clipped[landuse_clipped['fclass'] == 'industrial'].copy()
        green_raw       = landuse_clipped[landuse_clipped['fclass'].isin(
                              ['forest', 'grass', 'park', 'scrub', 'meadow'])].copy()
        residential_raw = landuse_clipped[landuse_clipped['fclass'] == 'residential'].copy()

        # 7. 几何修复与聚合
        print('\n[7/8] 几何修复与聚合...')
        _buf = 0.000001
        parcel            = clean_geometry(parcel,            min_area=1e-10)
        industrial_raw    = clean_geometry(industrial_raw,    min_area=1e-10)
        green_raw         = clean_geometry(green_raw,         min_area=1e-10)
        residential_raw   = clean_geometry(residential_raw,   min_area=1e-10)
        transport_clipped = clean_geometry(transport_clipped, min_area=1e-10)
        traffic_clipped   = clean_geometry(traffic_clipped,   min_area=1e-10)
        water_clipped     = clean_geometry(water_clipped,     min_area=1e-10)

        industrial_gdf  = consolidate_class_with_buffer(industrial_raw,  buffer_size=_buf)
        green_gdf       = consolidate_class_with_buffer(green_raw,        buffer_size=_buf)
        residential_gdf = consolidate_class_with_buffer(residential_raw,  buffer_size=_buf)
        transport_gdf   = consolidate_class_with_buffer(transport_clipped, buffer_size=_buf)
        traffic_gdf     = consolidate_class_with_buffer(traffic_clipped,   buffer_size=_buf)
        water_gdf       = consolidate_class_with_buffer(water_clipped,     buffer_size=_buf)

        traffic_osm_raw = gpd.GeoDataFrame(pd.concat([transport_gdf, traffic_gdf], ignore_index=True), crs='EPSG:4326')
        traffic_osm_gdf = consolidate_class_with_buffer(traffic_osm_raw, buffer_size=_buf)

        if lvdipark_available and len(lvdipark) > 0:
            lvdipark_gdf = consolidate_class_with_buffer(lvdipark, buffer_size=_buf)
        else:
            lvdipark_gdf = gpd.GeoDataFrame(columns=['geometry'], crs='EPSG:4326')

        # 8. 转换到 UTM 投影坐标系
        print('\n[8/8] 转换到 UTM 投影坐标系...')
        utm_epsg   = get_utm_epsg(parcel)
        target_crs = f'EPSG:{utm_epsg}'

        parcel          = parcel.to_crs(target_crs)
        industrial_gdf  = industrial_gdf.to_crs(target_crs)
        green_gdf       = green_gdf.to_crs(target_crs)
        residential_gdf = residential_gdf.to_crs(target_crs)
        traffic_osm_gdf = traffic_osm_gdf.to_crs(target_crs)
        water_gdf       = water_gdf.to_crs(target_crs)
        if poi_available:
            pois = pois.to_crs(target_crs)
        if lvdipark_available:
            lvdipark_gdf = lvdipark_gdf.to_crs(target_crs)

        # ========== 计算指标 ==========
        print('\n' + '='*60)
        print('>>> 计算各项指标')

        if 'FID' not in parcel.columns:
            parcel['FID'] = range(1, len(parcel) + 1)
        parcel = parcel.reset_index(drop=True)

        parcel['AREA']      = 0.0
        parcel['INDU_AP']   = 0.0
        parcel['GRN_AP']    = 0.0
        parcel['TFC_AP']    = 0.0
        parcel['RES_AP']    = 0.0
        parcel['WAT_AP']    = 0.0
        parcel['LVDI_PP']   = 0.0
        parcel['POI_N']     = 0
        parcel['TFC_N']     = 0
        parcel['RES_N']     = 0
        parcel['COM_N']     = 0
        parcel['PUB_N']     = 0
        parcel['UTL_N']     = 0
        parcel['OTH_N']     = 0
        parcel['TFC_PP']    = 0.0
        parcel['RES_PP']    = 0.0
        parcel['COM_PP']    = 0.0
        parcel['PUB_PP']    = 0.0
        parcel['UTL_PP']    = 0.0
        parcel['OTH_PP']    = 0.0
        parcel['IS_HuoChe'] = 0
        parcel['IS_QiChe']  = 0
        parcel['IS_JiChang']= 0
        parcel['类别']       = '待定'

        parcel['AREA']    = (parcel.geometry.area / 1_000_000).round(4)
        parcel['INDU_AP'] = calc_area_ratio(parcel, industrial_gdf).round(4)
        parcel['GRN_AP']  = calc_area_ratio(parcel, green_gdf).round(4)
        parcel['TFC_AP']  = calc_area_ratio(parcel, traffic_osm_gdf).round(4)
        parcel['RES_AP']  = calc_area_ratio(parcel, residential_gdf).round(4)
        parcel['WAT_AP']  = calc_area_ratio(parcel, water_gdf).round(4)

        if lvdipark_available and len(lvdipark_gdf) > 0:
            parcel['LVDI_PP'] = calc_area_ratio(parcel, lvdipark_gdf).round(4)

        if poi_available and len(pois) > 0:
            parcel_tmp = parcel[['geometry']].copy()
            parcel_tmp['_TMPID'] = parcel_tmp.index

            poi_join = gpd.sjoin(
                pois[['dlmc', 'xlmc', 'geometry']].copy(),
                parcel_tmp[['_TMPID', 'geometry']].copy(),
                how='inner', predicate='within'
            )
            if len(poi_join) == 0:
                poi_join = gpd.sjoin(
                    pois[['dlmc', 'xlmc', 'geometry']].copy(),
                    parcel_tmp[['_TMPID', 'geometry']].copy(),
                    how='inner', predicate='intersects'
                )

            tfc_bi = set(PARCEL_TO_dlmc['交通物流设施'])
            res_bi = set(PARCEL_TO_dlmc['居住用地'])
            com_bi = set(PARCEL_TO_dlmc['商业服务业设施用地'])
            pub_bi = set(PARCEL_TO_dlmc['公共管理与公共服务用地'])
            utl_bi = set(PARCEL_TO_dlmc['公用设施用地'])

            for tmp_id, grp in poi_join.groupby('index_right'):
                total  = len(grp)
                bi_vals = grp['dlmc']
                sm_vals = grp['xlmc'] if 'xlmc' in grp.columns else pd.Series(dtype=str)

                tfc_n = int(bi_vals.isin(tfc_bi).sum())
                res_n = int(bi_vals.isin(res_bi).sum())
                com_n = int(bi_vals.isin(com_bi).sum())
                pub_n = int(bi_vals.isin(pub_bi).sum())
                utl_n = int(bi_vals.isin(utl_bi).sum())
                oth_n = total - tfc_n - res_n - com_n - pub_n - utl_n

                parcel.at[tmp_id, 'POI_N'] = total
                parcel.at[tmp_id, 'TFC_N'] = tfc_n
                parcel.at[tmp_id, 'RES_N'] = res_n
                parcel.at[tmp_id, 'COM_N'] = com_n
                parcel.at[tmp_id, 'PUB_N'] = pub_n
                parcel.at[tmp_id, 'UTL_N'] = utl_n
                parcel.at[tmp_id, 'OTH_N'] = oth_n

                parcel.at[tmp_id, 'TFC_PP'] = round(tfc_n / total, 4) if total > 0 else 0.0
                parcel.at[tmp_id, 'RES_PP'] = round(res_n / total, 4) if total > 0 else 0.0
                parcel.at[tmp_id, 'COM_PP'] = round(com_n / total, 4) if total > 0 else 0.0
                parcel.at[tmp_id, 'PUB_PP'] = round(pub_n / total, 4) if total > 0 else 0.0
                parcel.at[tmp_id, 'UTL_PP'] = round(utl_n / total, 4) if total > 0 else 0.0
                parcel.at[tmp_id, 'OTH_PP'] = round(oth_n / total, 4) if total > 0 else 0.0

                if tfc_n > 0 and len(sm_vals) > 0:
                    tfc_sm = grp.loc[bi_vals.isin(tfc_bi), 'xlmc']
                    if (tfc_sm == '客运火车站').any():
                        parcel.at[tmp_id, 'IS_HuoChe'] = 1
                    if (tfc_sm == '客运汽车站').any():
                        parcel.at[tmp_id, 'IS_QiChe'] = 1
                    if (tfc_sm == '机场').any():
                        parcel.at[tmp_id, 'IS_JiChang'] = 1

       # ========== 判别地类 ==========
        print('\n>>> 判别地类')
        
        # Step 1: 工业
        mask = (parcel['INDU_AP'] > THRESHOLD_INDUSTRIAL)
        parcel.loc[mask, '类别'] = '工业'
        print(f'  → 工业：{mask.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 2: 绿地
        mask = (parcel['GRN_AP'] > THRESHOLD_GREEN) & (parcel['类别'] == '待定')
        parcel.loc[mask, '类别'] = '绿地'
        print(f'  → 绿地：{mask.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 3: 水体
        mask = (parcel['WAT_AP'] > THRESHOLD_WATER) & (parcel['类别'] == '待定') & (parcel['RES_AP'] < 0.4) & (parcel['RES_PP'] < 0.5) 
        parcel.loc[mask, '类别'] = '水体'
        print(f'  → 水体：{mask.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 4: 交通物流 POI
        mask = (parcel['TFC_PP'] > THRESHOLD_TRAFFIC_POI) & (parcel['类别'] == '待定')
        parcel.loc[mask, '类别'] = '交通物流设施'
        print(f'  → 交通物流（POI）：{mask.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 5: 交通物流 OSM（双重条件：阈值或相对最大）
        # 新增逻辑：TFC_AP 比 INDU_AP、GRN_AP、RES_AP 都高
        mask_relative = (parcel['TFC_AP'] > parcel['INDU_AP']) & \
                        (parcel['TFC_AP'] > parcel['GRN_AP']) & \
                        (parcel['TFC_AP'] > parcel['RES_AP']) & \
                        (parcel['类别'] == '待定') & \
                        (parcel['TFC_AP'] >= 0.26)
        # 原逻辑：TFC_AP 超过阈值且大于 RES_AP
        mask_threshold = (parcel['TFC_AP'] > THRESHOLD_TRAFFIC_OSM) & \
                         (parcel['类别'] == '待定') & \
                         (parcel['TFC_AP'] > parcel['RES_AP'])
        # 合并两个条件（满足任一即可）
        mask = mask_threshold | mask_relative
        parcel.loc[mask, '类别'] = '交通物流设施'
        print(f'  → 交通物流（OSM）：{mask.sum()} 个（其中原逻辑：{(mask_threshold & ~mask_relative).sum()} 个，新增逻辑：{(mask_relative & ~mask_threshold).sum()} 个）  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 6: 居住 POI
        mask = (parcel['RES_PP'] > THRESHOLD_RESIDENTIAL_POI) & (parcel['类别'] == '待定')
        parcel.loc[mask, '类别'] = '居住用地'
        print(f'  → 居住用地（POI）：{mask.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 7: 居住 OSM
        mask = (parcel['RES_AP'] > THRESHOLD_RESIDENTIAL_OSM) & (parcel['类别'] == '待定')
        parcel.loc[mask, '类别'] = '居住用地'
        print(f'  → 居住用地（OSM）：{mask.sum()} 个  | 剩余待定：{(parcel["类别"]=="待定").sum()} 个')
        
        # Step 8: 剩余地块取最大 POI 占比
        remain_last = parcel['类别'] == '待定'
        if remain_last.any():
            sub = parcel.loc[remain_last, ['COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP']].copy()
            cat_map = {
                'COM_PP': '商业服务业设施用地',
                'PUB_PP': '公共管理与公共服务用地',
                'UTL_PP': '公用设施用地',
                'OTH_PP': '其他',
            }
            all_zero_mask = (sub[['COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP']] == 0).all(axis=1)
            non_zero_mask = ~all_zero_mask
            if non_zero_mask.any():
                max_col = sub.loc[non_zero_mask].idxmax(axis=1)
                parcel.loc[sub.index[non_zero_mask], '类别'] = max_col.map(cat_map)
            if all_zero_mask.any():
                parcel.loc[sub.index[all_zero_mask], '类别'] = '其他'
        
        # 兜底
        leftover = (parcel['类别'] == '待定').sum()
        if leftover:
            parcel.loc[parcel['类别'] == '待定', '类别'] = '其他'
            print(f'  兜底归"其他"：{leftover} 个')
        
        # Step 9: 站点标记修正
        AREA_THRESHOLD_HUOCHE = 6.0
        AREA_THRESHOLD_QICHE = 4.0
        AREA_THRESHOLD_JICHANG = 40.0
        
        mask_huoche = (parcel['IS_HuoChe'] == 1) & (parcel['AREA'] < AREA_THRESHOLD_HUOCHE)
        mask_qiche = (parcel['IS_QiChe'] == 1) & (parcel['AREA'] < AREA_THRESHOLD_QICHE)
        mask_jichang = (parcel['IS_JiChang'] == 1) & (parcel['AREA'] < AREA_THRESHOLD_JICHANG)
        
        changed_huoche = mask_huoche.sum()
        changed_qiche = mask_qiche.sum()
        changed_jichang = mask_jichang.sum()
        
        if changed_huoche > 0:
            parcel.loc[mask_huoche, '类别'] = '交通物流设施'
            print(f'  规则 1（火车站，AREA < {AREA_THRESHOLD_HUOCHE} km²）：修正 {changed_huoche} 个地块 → 交通物流设施')
        if changed_qiche > 0:
            parcel.loc[mask_qiche, '类别'] = '交通物流设施'
            print(f'  规则 2（汽车站，AREA < {AREA_THRESHOLD_QICHE} km²）：修正 {changed_qiche} 个地块 → 交通物流设施')
        if changed_jichang > 0:
            parcel.loc[mask_jichang, '类别'] = '交通物流设施'
            print(f'  规则 3（机场，AREA < {AREA_THRESHOLD_JICHANG} km²）：修正 {changed_jichang} 个地块 → 交通物流设施')
        
        print(f'  Step 站点修正合计：{(mask_huoche | mask_qiche | mask_jichang).sum()} 个地块')
        
        # Step 10: 绿地公园修正
        if lvdipark_available:
            mask_lvd = (parcel['LVDI_PP'] > 0.4) & (parcel['GRN_AP'] > 0.4) & (parcel['类别'] != '交通物流设施')
            parcel.loc[mask_lvd, '类别'] = '绿地'
            print(f'  规则（LVDI_PP > 0.4 且 GRN_AP > 0.4 且非交通用地）：修正 {mask_lvd.sum()} 个地块 → 绿地')
        
        # Step 11: 小面积"其他"地块按相邻关系赋值
        print(f'\nStep 11：对小面积"其他"地块，按相邻关系赋值')
        mask_small_other = (parcel['类别'] != '待定') & (parcel['类别'] == '其他') & (parcel['AREA'] < 4.0)
        small_other_indices = parcel.loc[mask_small_other].index.tolist()
        
        if len(small_other_indices) > 0:
            print(f'  检测到小面积"其他"地块：{len(small_other_indices)} 个')
            print(f'  开始分析相邻关系（缓冲距离：20m）...')
            
            # 创建已赋值地块子集（排除"其他"和"待定"）
            assigned_mask = (parcel['类别'] != '其他') & (parcel['类别'] != '待定')
            assigned_parcels = parcel.loc[assigned_mask].copy()
            
            if len(assigned_parcels) > 0:
                changed_count = 0
                failed_count = 0
                
                for idx in small_other_indices:
                    smallParcel = parcel.loc[idx]
                    geom = smallParcel.geometry
                    
                    if geom is None or geom.is_empty:
                        failed_count += 1
                        continue
                    
                    # 创建 20m 缓冲区
                    try:
                        buffered_geom = geom.buffer(25)  # 20 米缓冲
                    except Exception as e:
                        print(f'    ⚠️ 地块 {idx} 缓冲失败：{e}')
                        failed_count += 1
                        continue
                    
                    # 找出与缓冲区相交的所有已赋值地块
                    intersecting = assigned_parcels[
                        assigned_parcels.geometry.intersects(buffered_geom)
                    ].copy()
                    
                    if len(intersecting) == 0:
                        print(f'    ⚠️ 地块 {idx} 未找到相邻地块，保持"其他"')
                        failed_count += 1
                        continue
                    
                    # 计算与每个相邻地块的接触长度（交集边界长度）
                    contact_info = []
                    for _, neighbor in intersecting.iterrows():
                        try:
                            # 计算原始地块与相邻地块的交集（用于确定接触边界）
                            # 使用缓冲后的几何体与相邻地块相交，得到重叠区域
                            overlap = buffered_geom.intersection(neighbor.geometry)
                            
                            if overlap.is_empty:
                                continue
                            
                            # 计算接触长度：根据交集类型分别处理
                            contact_length = 0.0
                            
                            # 情况 1：交集是面（Polygon/MultiPolygon）- 说明有重叠区域
                            if isinstance(overlap, (Polygon, MultiPolygon)):
                                # 使用交集的边界长度作为接触长度的代理
                                contact_length = overlap.boundary.length
                            
                            # 情况 2：交集是线（LineString/MultiLineString）- 说明有边界接触
                            elif isinstance(overlap, (LineString, MultiLineString)):
                                contact_length = overlap.length
                            
                            # 情况 3：交集是点（Point/MultiPoint）- 说明只有角点接触
                            elif isinstance(overlap, (Point, MultiPoint)):
                                contact_length = 0.1  # 给一个很小的正值表示接触
                            
                            # 只要有接触（contact_length > 0），就加入候选列表
                            if contact_length > 0:
                                contact_info.append({
                                    'index': neighbor.name,
                                    'category': neighbor['类别'],
                                    'contact_length': contact_length,
                                    'area': neighbor['AREA'] * 1_000_000,  # km² → m²
                                })
                        except Exception as e:
                            # 忽略单个地块的计算错误
                            continue
                    
                    if len(contact_info) == 0:
                        print(f'    ⚠️ 地块 {idx} 无法计算接触关系，保持"其他"')
                        failed_count += 1
                        continue
                    
                    # 排序规则：
                    # 1. 首先按接触长度降序排序
                    # 2. 若接触长度相同（或非常接近），按相邻地块面积降序排序
                    contact_info.sort(key=lambda x: (-x['contact_length'], -x['area']))
                    
                    # 选择接触长度最长的地块类别
                    best_match = contact_info[0]
                    parcel.loc[idx, '类别'] = best_match['category']
                    changed_count += 1
                
                print(f'  ✓ 成功修正：{changed_count} 个地块')
                if failed_count > 0:
                    print(f'  ⚠️ 无法修正（无相邻地块）：{failed_count} 个地块，保持"其他"')
            else:
                print(f'  ⚠️ 无已赋值地块可用于相邻分析')
        else:
            print(f'  无符合条件的小面积"其他"地块')
        
        # Step 12: 对剩余"待定"地块按 POI 占比判别
        remain_mask = parcel['类别'] == '其他'
        if remain_mask.any():
            print(f'\nStep 12: 剩余"其他"地块：{remain_mask.sum()} 个，开始按 POI 占比判别')
            
            # 提取待定地块的 POI 占比数据
            sub = parcel.loc[remain_mask, ['COM_PP', 'PUB_PP', 'UTL_PP', 'TFC_PP', 'RES_PP']].copy()
            
            # 定义 POI 占比到类别的映射
            cat_map = {
                'TFC_PP': '交通物流设施',
                'RES_PP': '居住用地',
                'COM_PP': '商业服务业设施用地',
                'PUB_PP': '公共管理与公共服务用地',
                'UTL_PP': '公用设施用地',
            }
            
            # 检测全零情况
            all_zero_mask = (sub[['COM_PP', 'PUB_PP', 'UTL_PP', 'TFC_PP', 'RES_PP']] == 0).all(axis=1)
            non_zero_mask = ~all_zero_mask
            
            # 对非全零的地块，取最大占比对应的类别
            if non_zero_mask.any():
                max_col = sub.loc[non_zero_mask].idxmax(axis=1)
                parcel_indices = sub.index[non_zero_mask]
                for i, idx in enumerate(parcel_indices):
                    parcel.loc[idx, '类别'] = cat_map[max_col.iloc[i]]
                print(f'  → 按 POI 最大占比判别：{non_zero_mask.sum()} 个地块')
            
            # 对全零的地块，归类为"其他"
            if all_zero_mask.any():
                parcel_indices = sub.index[all_zero_mask]
                for idx in parcel_indices:
                    parcel.loc[idx, '类别'] = '其他'
                print(f'  → POI 占比全为零，归为"其他"：{all_zero_mask.sum()} 个地块')
        
        # Step 13: 对剩余"其他"地块按 OSM 面积占比判别
        remain_mask = parcel['类别'] == '其他'
        if remain_mask.any():
            print(f'\nStep 13: 剩余"其他"地块：{remain_mask.sum()} 个，开始按 OSM 面积占比判别')
            
            # 提取待定地块的面积占比数据（AP = Area Percentage）
            sub = parcel.loc[remain_mask, ['INDU_AP', 'GRN_AP', 'TFC_AP', 'WAT_AP', 'RES_AP']].copy()
            
            # 定义面积占比到类别的映射
            cat_map = {
                'INDU_AP': '工业用地',
                'GRN_AP': '绿地',
                'TFC_AP': '交通物流设施',
                'WAT_AP': '水体',
                'RES_AP': '居住用地',
            }
            
            # 检测全零情况
            all_zero_mask = (sub[['INDU_AP', 'GRN_AP', 'TFC_AP', 'WAT_AP', 'RES_AP']] == 0).all(axis=1)
            non_zero_mask = ~all_zero_mask
            
            # 对非全零的地块，取最大占比对应的类别
            if non_zero_mask.any():
                max_col = sub.loc[non_zero_mask].idxmax(axis=1)
                parcel_indices = sub.index[non_zero_mask]
                for i, idx in enumerate(parcel_indices):
                    parcel.loc[idx, '类别'] = cat_map[max_col.iloc[i]]
                print(f'  → 按 OSM 面积最大占比判别：{non_zero_mask.sum()} 个地块')
            
            # 对全零的地块，保持为"其他"
            if all_zero_mask.any():
                print(f'  → OSM 面积占比全为零，保持"其他"：{all_zero_mask.sum()} 个地块')
        # Step 14: 对面积小于 1 平方公里的"其他"地块，按最近邻原则赋值
        print(f'\nStep 14: 对面积小于 1 平方公里的"其他"地块，按最近邻原则赋值')
        mask_small_other = (parcel['类别'] == '其他') & (parcel['AREA'] < 1.0)
        small_other_indices = parcel.loc[mask_small_other].index.tolist()
        
        if len(small_other_indices) > 0:
            print(f'  检测到小面积"其他"地块（AREA < 1.0 km²）：{len(small_other_indices)} 个')
            print(f'  开始计算最近邻地块（500m 缓冲区过滤 + R-tree 空间索引）...')
            
            # 创建已赋值地块子集（排除"其他"和"待定"）
            assigned_mask = (parcel['类别'] != '其他') & (parcel['类别'] != '待定')
            assigned_parcels = parcel.loc[assigned_mask].copy()
            
            if len(assigned_parcels) > 0:
                # 预计算所有已赋值地块的质心并构建 R-tree 空间索引
                assigned_centroids = assigned_parcels.geometry.centroid
                assigned_parcels['_centroid'] = assigned_centroids
                
                # 构建 R-tree 索引（用于快速空间查询）
                centroid_coords = [(geom.x, geom.y) for geom in assigned_centroids]
                centroid_index = index.Index()
                for i, coords in enumerate(centroid_coords):
                    centroid_index.insert(i, (coords[0], coords[1], coords[0], coords[1]))
                
                changed_count = 0
                failed_count = 0
                no_neighbor_count = 0
                
                BUFFER_DISTANCE = 2000  # 缓冲距离：2000 米
                
                for idx in small_other_indices:
                    smallParcel = parcel.loc[idx]
                    geom = smallParcel.geometry
                    
                    if geom is None or geom.is_empty:
                        failed_count += 1
                        continue
                    
                    try:
                        # 计算小地块的质心
                        centroid = geom.centroid
                        target_x, target_y = centroid.x, centroid.y
                        
                        # 创建 500m 缓冲区边界框
                        buffer_box = (
                            target_x - BUFFER_DISTANCE,
                            target_y - BUFFER_DISTANCE,
                            target_x + BUFFER_DISTANCE,
                            target_y + BUFFER_DISTANCE
                        )
                        
                        # 使用 R-tree 快速查询缓冲区内的候选点
                        candidates_in_buffer = list(centroid_index.intersection(buffer_box))
                        
                        if not candidates_in_buffer:
                            no_neighbor_count += 1
                            continue
                        
                        # 从候选集中找到精确距离最近的点
                        min_dist = float('inf')
                        nearest_idx = None
                        
                        for candidate_pos in candidates_in_buffer:
                            candidate_geom = assigned_centroids.iloc[candidate_pos]
                            dist = ((target_x - candidate_geom.x)**2 + (target_y - candidate_geom.y)**2)**0.5
                            
                            # 只考虑缓冲区内的点
                            if dist <= BUFFER_DISTANCE and dist < min_dist:
                                min_dist = dist
                                nearest_idx = candidate_pos
                        
                        if nearest_idx is None:
                            no_neighbor_count += 1
                            continue
                        
                        # 获取最近邻地块的类别
                        nearest_category = assigned_parcels.iloc[nearest_idx]['类别']
                        
                        # 赋值最近邻地块的类别
                        parcel.loc[idx, '类别'] = nearest_category
                        changed_count += 1
                        
                        if changed_count <= 5:  # 只显示前 5 个的详细信息
                            print(f'    ✓ 地块 {idx} → {nearest_category} (距离：{min_dist:.2f} m)')
                    
                    except Exception as e:
                        print(f'    ⚠️ 地块 {idx} 计算失败：{e}')
                        failed_count += 1
                        continue
                
                # 清理临时字段
                if '_centroid' in assigned_parcels.columns:
                    assigned_parcels = assigned_parcels.drop(columns=['_centroid'])
                
                print(f'  ✓ 成功修正：{changed_count} 个地块')
                if changed_count > 5:
                    print(f'  ... (其余 {changed_count - 5} 个地块已静默处理)')
                if no_neighbor_count > 0:
                    print(f'  ℹ️ 缓冲区内无已赋值地块：{no_neighbor_count} 个，保持"其他"')
                if failed_count > 0:
                    print(f'  ⚠️ 无法修正（几何错误）：{failed_count} 个地块，保持"其他"')
            else:
                print(f'  ⚠️ 无已赋值地块可用于最近邻分析')
        else:
            print(f'  无符合条件的小面积"其他"地块')
        # 兜底：确保无"待定"残留（最终检查）
        leftover = (parcel['类别'] == '待定').sum()
        if leftover:
            parcel.loc[parcel['类别'] == '待定', '类别'] = '其他'
            print(f'  最终兜底归"其他"：{leftover} 个')
        


        # ========== 保存结果 ==========
        print('\n>>> 保存结果')

        # 第一步：转换到 WGS84 地理坐标系（EPSG:4326）
        print('  正在转换坐标系到 WGS84 (EPSG:4326)...')
        parcel_wgs84 = parcel.to_crs('EPSG:4326')
        print(f'  ✓ 坐标系已转换为 WGS84')

        # 第二步：从 WGS84 转换到 CGCS2000 地理坐标系（EPSG:4490）
        print('  正在转换坐标系到 CGCS2000 (EPSG:4490)...')
        parcel_cgcs2000 = parcel_wgs84.to_crs('EPSG:4490')
        print(f'  ✓ 坐标系已转换为 CGCS2000')

        city_output_dir = os.path.join(OUTPUT_ROOT, f'{city_name} 分类结果')
        os.makedirs(city_output_dir, exist_ok=True)

        # 使用城市名命名输出文件
        output_gpkg = os.path.join(city_output_dir, f'{city_name} 地块分类2025.gpkg')
        parcel_cgcs2000.to_file(output_gpkg, driver='GPKG', encoding='utf-8')
        print(f'  ✓ GeoPackage 已保存：{output_gpkg}')

        output_shp = os.path.join(city_output_dir, f'{city_name} 地块分类2025.shp')
        parcel_shp = parcel_cgcs2000.rename(columns={'类别': 'LANDTYPE'})
        parcel_shp.to_file(output_shp, encoding='utf-8')
        print(f'  ✓ Shapefile 已保存：{output_shp}')

        elapsed = time.time() - start_time
        print(f'\n✅ {city_name} 处理完成！耗时：{elapsed:.1f}秒')

        return True, len(parcel)

    except Exception as e:
        elapsed = time.time() - start_time
        print(f'\n❌ {city_name} 处理失败！耗时：{elapsed:.1f}秒')
        print(f'错误信息：{str(e)}')
        traceback.print_exc()
        return False, 0

# ============================================================
# 批量处理主函数
# ============================================================

def main():
    print("="*70)
    print("多城市地块分类批量处理程序")
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    os.makedirs(OUTPUT_ROOT, exist_ok=True)

    # 扫描城市地块文件（宽松匹配：文件名包含城市名 + 以 _parcel2025.shp 结尾）
    print('\n[步骤 1] 扫描城市地块数据...')
    cities_data = []

    if not os.path.exists(PARCEL_BASE_DIR):
        print(f"  ❌ 地块数据目录不存在：{PARCEL_BASE_DIR}")
        return

    # 目标城市列表（来自映射表）
    target_cities = set(CITY_TO_OSM_PROVINCE.keys())

    for file_name in os.listdir(PARCEL_BASE_DIR):
        if not file_name.endswith('_parcel2025.shp'):
            continue
        # 从文件名提取城市名：去掉 _parcel2025.shp 后缀
        base = file_name.replace('_parcel2025.shp', '')
        # 宽松匹配：base 中包含目标城市名，或目标城市名包含 base
        matched_city = None
        for city in target_cities:
            if city in base or base in city:
                matched_city = city
                break
        if matched_city is None:
            print(f"  ⚠ 文件 [{file_name}] 无法匹配到已知城市，跳过")
            continue
        parcel_path = os.path.join(PARCEL_BASE_DIR, file_name)
        cities_data.append((matched_city, parcel_path))

    cities_data.sort(key=lambda x: x[0])

    print(f'  ✓ 共发现 {len(cities_data)} 个城市')
    for city_name, path in cities_data:
        print(f'    - {city_name}  ({os.path.basename(path)})')

    # 批量处理
    print(f'\n[步骤 2] 开始批量处理（共{len(cities_data)}个城市）...')
    print('='*70)

    results = []
    total_start = time.time()

    for idx, (city_name, parcel_path) in enumerate(cities_data, 1):
        print(f'\n>>> 进度：{idx}/{len(cities_data)} ({idx/len(cities_data)*100:.1f}%)')
        success, count = process_single_city(city_name, parcel_path)
        results.append({
            '城市': city_name,
            '状态': '成功' if success else '失败',
            '地块数': count,
            '时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

    total_elapsed = time.time() - total_start

    # ========== 输出完成报告 ==========
    print('\n' + '='*70)
    print('批量处理完成报告')
    print('='*70)
    print(f'总耗时：{total_elapsed:.1f}秒')
    if cities_data:
        print(f'平均每个城市：{total_elapsed/len(cities_data):.1f}秒')
    print(f'成功：{sum(1 for r in results if r["状态"]=="成功")} 个')
    print(f'失败：{sum(1 for r in results if r["状态"]=="失败")} 个')
    print('\n详细结果：')
    print('-'*70)
    print(f'{"城市":<15} {"状态":<8} {"地块数":<10} {"处理时间"}')
    print('-'*70)
    for r in results:
        status_icon = '✓' if r['状态'] == '成功' else '✗'
        print(f"{status_icon} {r['城市']:<12} {r['状态']:<8} {r['地块数']:<10} {r['时间']}")
    print('-'*70)

    report_path = os.path.join(OUTPUT_ROOT, '批量处理完成报告.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('='*70 + '\n')
        f.write('多城市地块分类批量处理完成报告\n')
        f.write('='*70 + '\n\n')
        f.write(f'处理时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write(f'总城市数：{len(cities_data)}\n')
        f.write(f'成功：{sum(1 for r in results if r["状态"]=="成功")} 个\n')
        f.write(f'失败：{sum(1 for r in results if r["状态"]=="失败")} 个\n')
        f.write(f'总耗时：{total_elapsed:.1f}秒\n')
        if cities_data:
            f.write(f'平均每个城市：{total_elapsed/len(cities_data):.1f}秒\n\n')
        f.write('详细结果:\n')
        f.write('-'*70 + '\n')
        f.write(f'{"城市":<15} {"状态":<8} {"地块数":<10} {"处理时间"}\n')
        f.write('-'*70 + '\n')
        for r in results:
            status_icon = '✓' if r['状态'] == '成功' else '✗'
            f.write(f"{status_icon} {r['城市']:<12} {r['状态']:<8} {r['地块数']:<10} {r['时间']}\n")
        f.write('-'*70 + '\n')

    print(f'\n✓ 完成报告已保存：{report_path}')
    print('\n✅ 全部处理完成！')


if __name__ == '__main__':
    main()
    print("OSM数据根目录：", OSM_ROOT,       "存在？", os.path.exists(OSM_ROOT))
    print("地块数据目录：", PARCEL_BASE_DIR,  "存在？", os.path.exists(PARCEL_BASE_DIR))
    print("绿地公园目录：", LVDIPARK_BASE_DIR,"存在？", os.path.exists(LVDIPARK_BASE_DIR))
    print("POI目录：",      POI_BASE_DIR,      "存在？", os.path.exists(POI_BASE_DIR))