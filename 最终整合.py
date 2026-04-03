# ============================================================
# 三年地块稳定性分析与类别统一脚本
#
# 核心逻辑：
#   1. 读取 2023、2024、2025 三年已分类地块（gpkg/shp）
#   2. 以 2024 年地块为基准，空间匹配三年对应地块
#   3. 筛选"三年稳定地块"：
#        - IoU ≥ 0.8（形状变化不太大）
#        - 面积变化率 < 20%（相对 2024 年）
#        - 三年均需匹配（2023、2024、2025 都存在对应地块）
#   4. 对稳定地块确定统一类别：
#        - 两年及以上判别为同一类别 → 保留该多数类别
#        - 三年三种不同类别 → 收集三年对应地块的指标均值，
#          代入原判别逻辑重新分类
#   5. 水体传播（任一年为水体则扩散到三年所有重叠地块）：
#        - 从任一年水体地块出发，用一对多重叠匹配（≥80%面积重叠）
#          找出其他两年所有被覆盖地块，均强制设为水体
#        - 水体传播优先级最高，覆盖其他所有判别结果
#   6. 不稳定地块两两年再匹配（23-24 & 24-25）：
#        - 对三年稳定地块以外的不稳定地块，分别做 23-24 和 24-25 匹配
#        - 对新匹配到的"两年稳定地块"：
#            · 两年类别相同 → 直接采用
#            · 两年类别不同 → 用两年指标均值代入判别逻辑重新分类
#        - 两两匹配中发现水体同样触发水体传播
#   7. 其他类别修正：对仍为"其他"的地块，若其他年份有非"其他"地块
#      与其重叠面积≥80%，则用该非"其他"类别覆盖。
#   8. 将所有统一类别写回三年各自完整地块；
#      未被任何匹配覆盖的地块类别属性保持原值不变
#   9. 分别保存三年整合后的完整地块数据（各一份 gpkg + shp）
# ============================================================

import os
import sys
import time
from collections import Counter
from datetime import datetime
import traceback
import warnings

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
from shapely.geometry import MultiPolygon, Polygon, GeometryCollection
from shapely.validation import make_valid
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

DATA_ROOT = r'E:\地块分类数据\地块划分程序\DiKuai\data'

OUTPUT_ROOT_2023  = os.path.join(DATA_ROOT, '..', 'output', '分类结果最新', '分类结果2023')
OUTPUT_ROOT_2024  = os.path.join(DATA_ROOT, '..', 'output', '分类结果最新', '分类结果2024')
OUTPUT_ROOT_2025  = os.path.join(DATA_ROOT, '..', 'output', '分类结果最新', '分类结果2025')
FINAL_OUTPUT_ROOT = os.path.join(DATA_ROOT, '..', 'output', '分类结果最新', '三年整合结果')

# ============================================================
# 稳定性筛选参数（按需求调整为 0.8 和 20%）
# ============================================================

# 形状稳定：IoU（Intersection over Union）须 ≥ 此值
IOU_THRESHOLD = 0.70   # 修改为 0.8

# 面积稳定：各年面积相对 2024 年的变化率（绝对值）须 < 此值
AREA_CHANGE_THRESHOLD = 0.30   # 修改为 20%

# ============================================================
# 判别阈值（与原脚本保持完全一致）
# ============================================================
THRESHOLD_INDUSTRIAL      = 0.50
THRESHOLD_GREEN           = 0.45
THRESHOLD_TRAFFIC_POI     = 0.60
THRESHOLD_TRAFFIC_OSM     = 0.30
THRESHOLD_RESIDENTIAL_POI = 0.30
THRESHOLD_RESIDENTIAL_OSM = 0.30
THRESHOLD_WATER           = 0.45

# ============================================================
# 城市列表
# ============================================================
CITY_LIST = [
    '北京', '成都', '大连', '福州', '广州', '贵阳', '哈尔滨', '海口',
    '杭州', '合肥', '呼和浩特', '济南', '景德镇', '昆明', '拉萨', '兰州',
    '南昌', '南京', '南宁', '宁波', '青岛', '厦门', '上海', '深圳',
    '沈阳', '石家庄', '太原', '天津', '乌鲁木齐', '武汉', '西安', '西宁',
    '银川', '长春', '长沙', '郑州', '重庆',
]


# 数值指标字段（用于三年均值计算）
NUMERIC_METRIC_COLS = [
    'INDU_AP', 'GRN_AP', 'TFC_AP', 'RES_AP', 'WAT_AP',
    'LVDI_PP', 'POI_N',
    'TFC_N', 'RES_N', 'COM_N', 'PUB_N', 'UTL_N', 'OTH_N',
    'TFC_PP', 'RES_PP', 'COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP',
    'IS_HuoChe', 'IS_QiChe', 'IS_JiChang',
]

# 类别字段名候选（gpkg 用"类别"，shp 可能截断为"LANDTYPE"）
CATEGORY_COL_CANDIDATES = ['类别', 'LANDTYPE']

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
    except Exception:
        return None


def clean_geometry(gdf, min_area=1e-10):
    if len(gdf) == 0:
        return gdf
    gdf = gdf.copy()
    gdf['geometry'] = gdf.geometry.apply(safe_make_valid)
    gdf = gdf[~gdf['geometry'].isna()].reset_index(drop=True)
    gdf = gdf[gdf.geometry.area > min_area].reset_index(drop=True)
    return gdf


def to_wgs84_geo(gdf, name=''):
    """
    强制将 GeoDataFrame 转换为 WGS84 地理坐标系 (EPSG:4326)
    """
    if gdf is None or len(gdf) == 0:
        return gdf
    
    if gdf.crs is None:
        print(f"  ⚠ {name} 无坐标系，假定为 WGS84 (EPSG:4326)")
        return gdf.set_crs('EPSG:4326')
    
    if gdf.crs.is_geographic:
        epsg = gdf.crs.to_epsg()
        if epsg == 4326:
            return gdf
        else:
            print(f"  ℹ {name} 当前地理坐标系为 EPSG:{epsg}，转换为 WGS84 (EPSG:4326)")
            return gdf.to_crs('EPSG:4326')
    else:
        print(f"  ℹ {name} 当前为投影坐标系 ({gdf.crs})，先转换为 WGS84 地理坐标系")
        return gdf.to_crs('EPSG:4326')


def get_utm_epsg(gdf_4326):
    bounds = gdf_4326.total_bounds
    lon_c  = (bounds[0] + bounds[2]) / 2
    lat_c  = (bounds[1] + bounds[3]) / 2
    zone   = int((lon_c + 180) / 6) + 1
    return 32600 + zone if lat_c >= 0 else 32700 + zone


def get_category_col(gdf):
    for col in CATEGORY_COL_CANDIDATES:
        if col in gdf.columns:
            return col
    return None


def find_result_file(city_name, output_root, year):
    """查找城市分类结果文件，优先 gpkg，其次 shp"""
    city_dir = os.path.join(output_root, f'{city_name} 分类结果')
    if not os.path.exists(city_dir) and os.path.exists(output_root):
        for folder in os.listdir(output_root):
            if city_name in folder:
                city_dir = os.path.join(output_root, folder)
                break
    if not os.path.exists(city_dir):
        return None
    for fname in os.listdir(city_dir):
        if fname.endswith('.gpkg') and str(year) in fname:
            return os.path.join(city_dir, fname)
    for fname in os.listdir(city_dir):
        if fname.endswith('.shp') and str(year) in fname:
            return os.path.join(city_dir, fname)
    return None


def load_parcel_result(filepath, year):
    try:
        gdf = gpd.read_file(filepath)
        print(f"  ✓ {year}年 读取成功：{len(gdf)} 个地块（{os.path.basename(filepath)}）")

        # ========== 新增：读入时强制交通设施分类（阈值：机场<100km²，火车站<20km²） ==========
        cat_col = get_category_col(gdf)
        if cat_col is not None and 'AREA' in gdf.columns:
            # 机场强制分类
            if 'IS_JiChang' in gdf.columns:
                mask_airport = (gdf['IS_JiChang'] == 1) & (gdf['AREA'] < 100.0)
                if mask_airport.any():
                    gdf.loc[mask_airport, cat_col] = '交通物流设施'
                    print(f"    ✈ 强制设置 {mask_airport.sum()} 个机场地块（面积<100km²）为「交通物流设施」")
            # 火车站强制分类
            if 'IS_HuoChe' in gdf.columns:
                mask_rail = (gdf['IS_HuoChe'] == 1) & (gdf['AREA'] < 20.0)
                if mask_rail.any():
                    gdf.loc[mask_rail, cat_col] = '交通物流设施'
                    print(f"    🚆 强制设置 {mask_rail.sum()} 个火车站地块（面积<20km²）为「交通物流设施」")
        # ===============================================================================

        gdf = to_wgs84_geo(gdf, f'{year}年地块')
        gdf = clean_geometry(gdf)
        return gdf
    except Exception as e:
        print(f"  ❌ {year}年 读取失败：{e}")
        return None


# ============================================================
# 空间匹配函数
# ============================================================

def calc_iou(geom_a, geom_b):
    try:
        inter = geom_a.intersection(geom_b)
        union = geom_a.union(geom_b)
        if union.is_empty or union.area == 0:
            return 0.0
        return inter.area / union.area
    except Exception:
        return 0.0


def match_parcels_to_base(base_gdf, other_gdf):
    match_pos  = {}
    match_iou  = {}
    other_sindex = other_gdf.sindex

    for pos_i in range(len(base_gdf)):
        base_geom = base_gdf.geometry.iloc[pos_i]
        if base_geom is None or base_geom.is_empty:
            match_pos[pos_i] = None
            match_iou[pos_i] = 0.0
            continue

        candidates = list(other_sindex.intersection(base_geom.bounds))
        best_iou   = 0.0
        best_pos_j = None

        for pos_j in candidates:
            other_geom = other_gdf.geometry.iloc[pos_j]
            if other_geom is None or other_geom.is_empty:
                continue
            iou = calc_iou(base_geom, other_geom)
            if iou > best_iou:
                best_iou   = iou
                best_pos_j = pos_j

        if best_iou >= IOU_THRESHOLD:
            match_pos[pos_i] = best_pos_j
            match_iou[pos_i] = best_iou
        else:
            match_pos[pos_i] = None
            match_iou[pos_i] = best_iou

    return match_pos, match_iou


def match_parcels_overlap_many(source_gdf, target_gdf, overlap_threshold=0.80):
    result = {}
    target_sindex = target_gdf.sindex

    for src_pos in range(len(source_gdf)):
        src_geom = source_gdf.geometry.iloc[src_pos]
        if src_geom is None or src_geom.is_empty:
            result[src_pos] = []
            continue

        candidates = list(target_sindex.intersection(src_geom.bounds))
        matched = []
        for tgt_pos in candidates:
            tgt_geom = target_gdf.geometry.iloc[tgt_pos]
            if tgt_geom is None or tgt_geom.is_empty:
                continue
            try:
                inter_area = src_geom.intersection(tgt_geom).area
                tgt_area   = tgt_geom.area
                if tgt_area > 0 and inter_area / tgt_area >= overlap_threshold:
                    matched.append(tgt_pos)
            except Exception:
                continue
        result[src_pos] = matched

    return result


def match_two_years(gdf_a, gdf_b, iou_threshold=None):
    if iou_threshold is None:
        iou_threshold = IOU_THRESHOLD

    match_pos = {}
    match_iou = {}
    sindex_b  = gdf_b.sindex

    for pos_a in range(len(gdf_a)):
        geom_a = gdf_a.geometry.iloc[pos_a]
        if geom_a is None or geom_a.is_empty:
            match_pos[pos_a] = None
            match_iou[pos_a] = 0.0
            continue

        candidates = list(sindex_b.intersection(geom_a.bounds))
        best_iou  = 0.0
        best_pos_b = None

        for pos_b in candidates:
            geom_b = gdf_b.geometry.iloc[pos_b]
            if geom_b is None or geom_b.is_empty:
                continue
            iou = calc_iou(geom_a, geom_b)
            if iou > best_iou:
                best_iou   = iou
                best_pos_b = pos_b

        if best_iou >= iou_threshold:
            match_pos[pos_a] = best_pos_b
            match_iou[pos_a] = best_iou
        else:
            match_pos[pos_a] = None
            match_iou[pos_a] = best_iou

    return match_pos, match_iou


# ============================================================
# 判别逻辑（与原脚本完全一致，批量版）
# ============================================================

def classify_parcels(df):
    result = pd.Series('待定', index=df.index, dtype=str)

    def col(name, default=0.0):
        return df[name] if name in df.columns else pd.Series(default, index=df.index)

    result[col('INDU_AP') > THRESHOLD_INDUSTRIAL] = '工业'
    result[(col('GRN_AP') > THRESHOLD_GREEN) & (result == '待定')] = '绿地'

    mask_water = (
        (col('WAT_AP') > THRESHOLD_WATER) &
        (result == '待定') &
        (col('RES_AP') < 0.4) &
        (col('RES_PP') < 0.5)
    )
    result[mask_water] = '水体'

    result[(col('TFC_PP') > THRESHOLD_TRAFFIC_POI) & (result == '待定')] = '交通物流设施'

    mask_rel = (
        (col('TFC_AP') > col('INDU_AP')) &
        (col('TFC_AP') > col('GRN_AP'))  &
        (col('TFC_AP') > col('RES_AP'))  &
        (result == '待定') &
        (col('TFC_AP') >= 0.26)
    )
    mask_thr = (
        (col('TFC_AP') > THRESHOLD_TRAFFIC_OSM) &
        (result == '待定') &
        (col('TFC_AP') > col('RES_AP'))
    )
    result[mask_rel | mask_thr] = '交通物流设施'

    result[(col('RES_PP') > THRESHOLD_RESIDENTIAL_POI) & (result == '待定')] = '居住用地'
    result[(col('RES_AP') > THRESHOLD_RESIDENTIAL_OSM) & (result == '待定')] = '居住用地'

    remain = result == '待定'
    if remain.any():
        sub = df.loc[remain].copy()
        for c in ['COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP']:
            if c not in sub.columns:
                sub[c] = 0.0
        sub = sub[['COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP']]
        cat_map = {
            'COM_PP': '商业服务业设施用地',
            'PUB_PP': '公共管理与公共服务用地',
            'UTL_PP': '公用设施用地',
            'OTH_PP': '其他',
        }
        all_zero = (sub == 0).all(axis=1)
        non_zero = ~all_zero
        if non_zero.any():
            result.loc[sub.index[non_zero]] = sub.loc[non_zero].idxmax(axis=1).map(cat_map)
        if all_zero.any():
            result.loc[sub.index[all_zero]] = '其他'

    if 'AREA' in df.columns:
        if 'IS_HuoChe' in df.columns:
            result[(col('IS_HuoChe') == 1) & (col('AREA') < 20.0)]   = '交通物流设施'   # 原为6.0，改为20.0
        if 'IS_QiChe' in df.columns:
            result[(col('IS_QiChe')  == 1) & (col('AREA') < 4.0)]   = '交通物流设施'
        if 'IS_JiChang' in df.columns:
            result[(col('IS_JiChang') == 1) & (col('AREA') < 100.0)] = '交通物流设施'   # 原为40.0，改为100.0

    if 'LVDI_PP' in df.columns:
        result[
            (col('LVDI_PP') > 0.4) &
            (col('GRN_AP')  > 0.4) &
            (result != '交通物流设施')
        ] = '绿地'

    result[result == '待定'] = '其他'
    return result


# ============================================================
# 保存单年整合结果
# ============================================================

def save_year_result(gdf_utm, city_name, year, cat_col):
    city_output_dir = os.path.join(FINAL_OUTPUT_ROOT, f'{city_name} 整合结果')
    os.makedirs(city_output_dir, exist_ok=True)

    gdf_wgs84 = gdf_utm.to_crs('EPSG:4326')
    gdf_out = gdf_wgs84.to_crs('EPSG:4490')

    gpkg_path = os.path.join(city_output_dir, f'{city_name}_地块整合{year}.gpkg')
    gdf_out.to_file(gpkg_path, driver='GPKG', encoding='utf-8')
    print(f"    ✓ {year}年 GeoPackage：{gpkg_path}")

    shp_out = gdf_out.copy()
    if cat_col in shp_out.columns and cat_col != 'LANDTYPE':
        shp_out = shp_out.rename(columns={cat_col: 'LANDTYPE'})
    shp_path = os.path.join(city_output_dir, f'{city_name}_地块整合{year}.shp')
    shp_out.to_file(shp_path, encoding='utf-8')
    print(f"    ✓ {year}年 Shapefile ：{shp_path}")


# ============================================================
# 新增：其他类别修正函数
# ============================================================

def correct_other_category(gdf_target, gdf_ref1, gdf_ref2, cat_col, overlap_threshold=0.80):
    """
    对 gdf_target 中类别为 '其他' 的地块，检查 gdf_ref1 和 gdf_ref2 中
    是否存在非 '其他' 地块与其重叠面积占该参考地块面积 ≥ overlap_threshold，
    若存在，则取重叠面积最大的那个参考地块的类别进行赋值。
    """
    other_mask = gdf_target[cat_col] == '其他'
    if not other_mask.any():
        return gdf_target
    
    gdf_target = gdf_target.copy()
    for idx in gdf_target.index[other_mask]:
        geom = gdf_target.geometry[idx]
        best_cat = None
        best_ratio = 0.0
        
        # 合并两个参考数据框
        refs = []
        if gdf_ref1 is not None and len(gdf_ref1) > 0:
            refs.append(gdf_ref1)
        if gdf_ref2 is not None and len(gdf_ref2) > 0:
            refs.append(gdf_ref2)
        
        for ref_gdf in refs:
            # 空间索引查找候选
            possible = ref_gdf.sindex.intersection(geom.bounds)
            for ref_pos in possible:
                ref_geom = ref_gdf.geometry.iloc[ref_pos]
                ref_cat = ref_gdf[cat_col].iloc[ref_pos]
                if ref_cat == '其他':
                    continue
                try:
                    inter_area = geom.intersection(ref_geom).area
                    ref_area = ref_geom.area
                    if ref_area > 0:
                        ratio = inter_area / ref_area
                        if ratio >= overlap_threshold and ratio > best_ratio:
                            best_ratio = ratio
                            best_cat = ref_cat
                except:
                    continue
        
        if best_cat is not None:
            gdf_target.at[idx, cat_col] = best_cat
    
    return gdf_target


# ============================================================
# 单城市三年整合处理
# ============================================================

def process_city_three_years(city_name):
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"正在处理城市：{city_name}")
    print(f"{'='*70}")

    # ── 1. 读取三年数据 ────────────────────────────────────────────
    print('\n[1/5] 查找并读取三年分类结果...')

    path_23 = find_result_file(city_name, OUTPUT_ROOT_2023, 2023)
    path_24 = find_result_file(city_name, OUTPUT_ROOT_2024, 2024)
    path_25 = find_result_file(city_name, OUTPUT_ROOT_2025, 2025)

    if not all([path_23, path_24, path_25]):
        missing = [str(y) for y, p in [(2023, path_23), (2024, path_24), (2025, path_25)] if not p]
        print(f"  ❌ 缺少以下年份数据：{missing}，跳过 {city_name}")
        return False, 0, 0

    gdf_23 = load_parcel_result(path_23, 2023)
    gdf_24 = load_parcel_result(path_24, 2024)
    gdf_25 = load_parcel_result(path_25, 2025)

    if any(g is None or len(g) == 0 for g in [gdf_23, gdf_24, gdf_25]):
        print(f"  ❌ 存在空数据，跳过 {city_name}")
        return False, 0, 0

    cat_col_23 = get_category_col(gdf_23)
    cat_col_24 = get_category_col(gdf_24)
    cat_col_25 = get_category_col(gdf_25)

    if not all([cat_col_23, cat_col_24, cat_col_25]):
        missing_cols = [str(y) for y, c in [(2023, cat_col_23), (2024, cat_col_24), (2025, cat_col_25)] if not c]
        print(f"  ❌ 以下年份缺少类别字段：{missing_cols}，跳过 {city_name}")
        return False, 0, 0

    # ── 2. 转换到 UTM 投影坐标系 ───────────────────────────────────
    print('\n[2/5] 转换到 WGS84 UTM 投影坐标系...')
    utm_epsg   = get_utm_epsg(gdf_24)
    target_crs = f'EPSG:{utm_epsg}'
    print(f"  目标坐标系：{target_crs} (WGS84 UTM)")

    gdf_23_utm = gdf_23.to_crs(target_crs).reset_index(drop=True)
    gdf_24_utm = gdf_24.to_crs(target_crs).reset_index(drop=True)
    gdf_25_utm = gdf_25.to_crs(target_crs).reset_index(drop=True)

    area_24 = gdf_24_utm.geometry.area

    # ── 3. 空间匹配 ────────────────────────────────────────────────
    print('\n[3/5] 空间匹配（以 2024 年为基准）...')

    print(f"  2024→2023 匹配（2023共 {len(gdf_23_utm)} 个）...")
    match_23, iou_23 = match_parcels_to_base(gdf_24_utm, gdf_23_utm)
    found_23 = sum(1 for v in match_23.values() if v is not None)
    print(f"  → IoU≥{IOU_THRESHOLD} 匹配到：{found_23} / {len(gdf_24_utm)} 个")

    print(f"  2024→2025 匹配（2025共 {len(gdf_25_utm)} 个）...")
    match_25, iou_25 = match_parcels_to_base(gdf_24_utm, gdf_25_utm)
    found_25 = sum(1 for v in match_25.values() if v is not None)
    print(f"  → IoU≥{IOU_THRESHOLD} 匹配到：{found_25} / {len(gdf_24_utm)} 个")

    # ── 4. 筛选稳定地块 ───────────────────────────────────────────
    print(f'\n[4/5] 筛选稳定地块'
          f'（三年均匹配 & IoU≥{IOU_THRESHOLD} & 面积变化<{AREA_CHANGE_THRESHOLD*100:.0f}%）...')

    stable_info = {}
    for pos_i in range(len(gdf_24_utm)):
        pos_j23 = match_23.get(pos_i)
        pos_j25 = match_25.get(pos_i)
        if pos_j23 is None or pos_j25 is None:
            continue
        base_area = area_24.iloc[pos_i]
        if base_area == 0:
            continue
        area_23_j = gdf_23_utm.geometry.iloc[pos_j23].area
        area_25_j = gdf_25_utm.geometry.iloc[pos_j25].area
        change_23 = abs(area_23_j - base_area) / base_area
        change_25 = abs(area_25_j - base_area) / base_area
        if change_23 < AREA_CHANGE_THRESHOLD and change_25 < AREA_CHANGE_THRESHOLD:
            stable_info[pos_i] = {'pos_23': pos_j23, 'pos_25': pos_j25}

    n_stable = len(stable_info)
    print(f"  → 稳定地块数量：{n_stable} / {len(gdf_24_utm)} 个")
    print(f"  → 非稳定地块（保持原类别）：{len(gdf_24_utm) - n_stable} 个")

    if n_stable == 0:
        print(f"  ⚠ 无三年稳定地块，仍执行后续步骤")

    # ── 5. 对稳定地块确定统一类别 ─────────────────────────────────
    print('\n[5/7] 确定统一类别并写回三年数据...')

    avail_metrics = [
        c for c in NUMERIC_METRIC_COLS
        if c in gdf_24_utm.columns
        and c in gdf_23_utm.columns
        and c in gdf_25_utm.columns
    ]

    stable_pos_24 = set(stable_info.keys())
    stable_pos_23 = set(v['pos_23'] for v in stable_info.values())
    stable_pos_25 = set(v['pos_25'] for v in stable_info.values())

    cats_24 = gdf_24_utm[cat_col_24].astype(str)
    cats_23 = gdf_23_utm[cat_col_23].astype(str)
    cats_25 = gdf_25_utm[cat_col_25].astype(str)

    final_categories = {}
    reclassify_rows = []
    reclassify_pos = []

    count_consistent = 0
    count_majority = 0
    count_reclassify = 0

    for pos_i in stable_info.keys():
        pos_j23 = stable_info[pos_i]['pos_23']
        pos_j25 = stable_info[pos_i]['pos_25']
        cat_24 = cats_24.iloc[pos_i]
        cat_23 = cats_23.iloc[pos_j23]
        cat_25 = cats_25.iloc[pos_j25]
        all_cats = [cat_23, cat_24, cat_25]
        unique_cats = set(all_cats)

        if len(unique_cats) == 1:
            final_categories[pos_i] = cat_24
            count_consistent += 1
        elif len(unique_cats) == 2:
            dominant = Counter(all_cats).most_common(1)[0][0]
            final_categories[pos_i] = dominant
            count_majority += 1
        else:
            row_dict = {}
            for c in avail_metrics:
                v24 = gdf_24_utm[c].iloc[pos_i]
                v23 = gdf_23_utm[c].iloc[pos_j23]
                v25 = gdf_25_utm[c].iloc[pos_j25]
                row_dict[c] = float(np.mean([v24, v23, v25]))
            if 'AREA' in gdf_24_utm.columns:
                row_dict['AREA'] = float(gdf_24_utm['AREA'].iloc[pos_i])
            else:
                row_dict['AREA'] = gdf_24_utm.geometry.iloc[pos_i].area / 1_000_000
            reclassify_rows.append(row_dict)
            reclassify_pos.append(pos_i)
            count_reclassify += 1

    if reclassify_rows:
        mean_df = pd.DataFrame(reclassify_rows).reset_index(drop=True)
        for c in ['INDU_AP', 'GRN_AP', 'WAT_AP', 'TFC_AP', 'RES_AP',
                  'TFC_PP', 'RES_PP', 'COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP',
                  'IS_HuoChe', 'IS_QiChe', 'IS_JiChang', 'AREA', 'LVDI_PP']:
            if c not in mean_df.columns:
                mean_df[c] = 0.0
        reclass_cats = classify_parcels(mean_df)
        for list_idx, pos_i in enumerate(reclassify_pos):
            final_categories[pos_i] = reclass_cats.iloc[list_idx]

    print(f"  → 三年一致：{count_consistent} 个")
    print(f"  → 两年一致（多数投票）：{count_majority} 个")
    print(f"  → 三年不同（均值重判）：{count_reclassify} 个")

    # ── 6. 水体传播 ───────────────────────────────────────────────
    print('\n[6/7] 水体传播（任一年为水体则扩散至三年所有重叠地块）...')

    def build_effective_cats(gdf, cat_col, stable_pos_this_year, final_cats_by_24, stable_info_map, year_key):
        effective = {}
        for pos in range(len(gdf)):
            effective[pos] = str(gdf[cat_col].iloc[pos])
        for pos_24, info in stable_info_map.items():
            if pos_24 in final_cats_by_24:
                unified = final_cats_by_24[pos_24]
                if year_key == '24':
                    effective[pos_24] = unified
                elif year_key == '23':
                    effective[info['pos_23']] = unified
                elif year_key == '25':
                    effective[info['pos_25']] = unified
        return effective

    eff_23 = build_effective_cats(gdf_23_utm, cat_col_23, stable_pos_23, final_categories, stable_info, '23')
    eff_24 = build_effective_cats(gdf_24_utm, cat_col_24, stable_pos_24, final_categories, stable_info, '24')
    eff_25 = build_effective_cats(gdf_25_utm, cat_col_25, stable_pos_25, final_categories, stable_info, '25')

    water_pos_23 = {pos for pos, cat in eff_23.items() if cat == '水体'}
    water_pos_24 = {pos for pos, cat in eff_24.items() if cat == '水体'}
    water_pos_25 = {pos for pos, cat in eff_25.items() if cat == '水体'}

    water_force_23 = set(water_pos_23)
    water_force_24 = set(water_pos_24)
    water_force_25 = set(water_pos_25)

    WATER_OVERLAP = 0.80  # 按需求改为 0.8

    def propagate_water(src_gdf, src_water_pos, tgt_gdf, tgt_force_set, label):
        if not src_water_pos:
            return
        water_src = src_gdf.iloc[list(src_water_pos)].reset_index(drop=True)
        overlap_map = match_parcels_overlap_many(water_src, tgt_gdf, WATER_OVERLAP)
        n_propagated = 0
        for local_idx, tgt_pos_list in overlap_map.items():
            for tgt_pos in tgt_pos_list:
                if tgt_pos not in tgt_force_set:
                    tgt_force_set.add(tgt_pos)
                    n_propagated += 1
        if n_propagated > 0:
            print(f"    {label} 水体传播：新增 {n_propagated} 个地块强制设为水体")

    propagate_water(gdf_23_utm, water_pos_23, gdf_24_utm, water_force_24, '23→24')
    propagate_water(gdf_23_utm, water_pos_23, gdf_25_utm, water_force_25, '23→25')
    propagate_water(gdf_24_utm, water_pos_24, gdf_23_utm, water_force_23, '24→23')
    propagate_water(gdf_24_utm, water_pos_24, gdf_25_utm, water_force_25, '24→25')
    propagate_water(gdf_25_utm, water_pos_25, gdf_23_utm, water_force_23, '25→23')
    propagate_water(gdf_25_utm, water_pos_25, gdf_24_utm, water_force_24, '25→24')

    print(f"  → 最终水体地块数：2023年 {len(water_force_23)} 个 / "
          f"2024年 {len(water_force_24)} 个 / 2025年 {len(water_force_25)} 个")

    for pos_i, info in stable_info.items():
        if (pos_i in water_force_24 or
                info['pos_23'] in water_force_23 or
                info['pos_25'] in water_force_25):
            final_categories[pos_i] = '水体'

    for pos in water_force_23:
        eff_23[pos] = '水体'
    for pos in water_force_24:
        eff_24[pos] = '水体'
    for pos in water_force_25:
        eff_25[pos] = '水体'

    # ── 7. 不稳定地块两两年再匹配 ─────────────────────────────────
    print('\n[7/7] 不稳定地块两两年再匹配（23-24 & 24-25）...')

    unstable_pos_24 = [p for p in range(len(gdf_24_utm)) if p not in stable_pos_24]
    unstable_pos_23 = [p for p in range(len(gdf_23_utm)) if p not in stable_pos_23]
    unstable_pos_25 = [p for p in range(len(gdf_25_utm)) if p not in stable_pos_25]

    print(f"  不稳定地块数：2023年 {len(unstable_pos_23)} / "
          f"2024年 {len(unstable_pos_24)} / 2025年 {len(unstable_pos_25)}")

    gdf_23_unstable = gdf_23_utm.iloc[unstable_pos_23].reset_index(drop=True)
    gdf_24_unstable = gdf_24_utm.iloc[unstable_pos_24].reset_index(drop=True)
    gdf_25_unstable = gdf_25_utm.iloc[unstable_pos_25].reset_index(drop=True)

    avail_metrics_2324 = [c for c in NUMERIC_METRIC_COLS
                          if c in gdf_23_utm.columns and c in gdf_24_utm.columns]
    avail_metrics_2425 = [c for c in NUMERIC_METRIC_COLS
                          if c in gdf_24_utm.columns and c in gdf_25_utm.columns]

    new_stable_cats_23 = {}
    new_stable_cats_24 = {}
    new_stable_cats_25 = {}

    count_new_stable_2324 = 0
    count_new_stable_2425 = 0

    def two_year_classify(pos_a_orig, pos_b_orig, gdf_a_full, gdf_b_full,
                          cat_a, cat_b, avail_m, year_a, year_b,
                          out_cats_a, out_cats_b, water_force_a, water_force_b):
        cat_a_eff = '水体' if pos_a_orig in water_force_a else str(cat_a)
        cat_b_eff = '水体' if pos_b_orig in water_force_b else str(cat_b)

        if cat_a_eff == '水体' or cat_b_eff == '水体':
            unified = '水体'
            water_force_a.add(pos_a_orig)
            water_force_b.add(pos_b_orig)
        elif cat_a_eff == cat_b_eff:
            unified = cat_a_eff
        else:
            row_dict = {}
            for c in avail_m:
                va = float(gdf_a_full[c].iloc[pos_a_orig]) if c in gdf_a_full.columns else 0.0
                vb = float(gdf_b_full[c].iloc[pos_b_orig]) if c in gdf_b_full.columns else 0.0
                row_dict[c] = float(np.mean([va, vb]))
            if 'AREA' in gdf_a_full.columns:
                row_dict['AREA'] = float(gdf_a_full['AREA'].iloc[pos_a_orig])
            else:
                row_dict['AREA'] = gdf_a_full.geometry.iloc[pos_a_orig].area / 1_000_000
            tmp_df = pd.DataFrame([row_dict])
            for c in ['INDU_AP', 'GRN_AP', 'WAT_AP', 'TFC_AP', 'RES_AP',
                      'TFC_PP', 'RES_PP', 'COM_PP', 'PUB_PP', 'UTL_PP', 'OTH_PP',
                      'IS_HuoChe', 'IS_QiChe', 'IS_JiChang', 'AREA', 'LVDI_PP']:
                if c not in tmp_df.columns:
                    tmp_df[c] = 0.0
            unified = str(classify_parcels(tmp_df).iloc[0])

        out_cats_a[pos_a_orig] = unified
        out_cats_b[pos_b_orig] = unified
        return unified

    # 23-24
    print(f"  23-24 再匹配...")
    if len(gdf_23_unstable) > 0 and len(gdf_24_unstable) > 0:
        match_2324, _ = match_two_years(gdf_24_unstable, gdf_23_unstable)
        for local_24, local_23 in match_2324.items():
            if local_23 is None:
                continue
            orig_24 = unstable_pos_24[local_24]
            orig_23 = unstable_pos_23[local_23]
            base_area = gdf_24_utm.geometry.iloc[orig_24].area
            if base_area == 0:
                continue
            area_23_j = gdf_23_utm.geometry.iloc[orig_23].area
            if abs(area_23_j - base_area) / base_area >= AREA_CHANGE_THRESHOLD:
                continue
            two_year_classify(
                orig_24, orig_23,
                gdf_24_utm, gdf_23_utm,
                eff_24.get(orig_24, cats_24.iloc[orig_24]),
                eff_23.get(orig_23, cats_23.iloc[orig_23]),
                avail_metrics_2324, 2024, 2023,
                new_stable_cats_24, new_stable_cats_23,
                water_force_24, water_force_23,
            )
            count_new_stable_2324 += 1
    print(f"  → 23-24 新稳定地块：{count_new_stable_2324} 对")

    # 24-25
    print(f"  24-25 再匹配...")
    if len(gdf_24_unstable) > 0 and len(gdf_25_unstable) > 0:
        match_2425, _ = match_two_years(gdf_24_unstable, gdf_25_unstable)
        for local_24, local_25 in match_2425.items():
            if local_25 is None:
                continue
            orig_24 = unstable_pos_24[local_24]
            orig_25 = unstable_pos_25[local_25]
            base_area = gdf_24_utm.geometry.iloc[orig_24].area
            if base_area == 0:
                continue
            area_25_j = gdf_25_utm.geometry.iloc[orig_25].area
            if abs(area_25_j - base_area) / base_area >= AREA_CHANGE_THRESHOLD:
                continue
            two_year_classify(
                orig_24, orig_25,
                gdf_24_utm, gdf_25_utm,
                eff_24.get(orig_24, cats_24.iloc[orig_24]),
                eff_25.get(orig_25, cats_25.iloc[orig_25]),
                avail_metrics_2425, 2024, 2025,
                new_stable_cats_24, new_stable_cats_25,
                water_force_24, water_force_25,
            )
            count_new_stable_2425 += 1
    print(f"  → 24-25 新稳定地块：{count_new_stable_2425} 对")
    print(f"  → 两两匹配新增水体：2023年 {sum(1 for p in water_force_23 if p not in water_pos_23)} 个 / "
          f"2024年 {sum(1 for p in water_force_24 if p not in water_pos_24)} 个 / "
          f"2025年 {sum(1 for p in water_force_25 if p not in water_pos_25)} 个（含水体传播）")

    # ── 8. 写回三年完整地块（尚未进行其他类别修正）─────────────────
    gdf_23_out = gdf_23_utm.copy()
    gdf_24_out = gdf_24_utm.copy()
    gdf_25_out = gdf_25_utm.copy()

    # 写三年稳定地块
    for pos_i, unified_cat in final_categories.items():
        pos_j23 = stable_info[pos_i]['pos_23']
        pos_j25 = stable_info[pos_i]['pos_25']
        gdf_24_out.at[pos_i,   cat_col_24] = unified_cat
        gdf_23_out.at[pos_j23, cat_col_23] = unified_cat
        gdf_25_out.at[pos_j25, cat_col_25] = unified_cat

    # 写两两匹配新稳定地块
    for pos, cat in new_stable_cats_23.items():
        gdf_23_out.at[pos, cat_col_23] = cat
    for pos, cat in new_stable_cats_24.items():
        gdf_24_out.at[pos, cat_col_24] = cat
    for pos, cat in new_stable_cats_25.items():
        gdf_25_out.at[pos, cat_col_25] = cat

    # 水体强制写回
    for pos in water_force_23:
        gdf_23_out.at[pos, cat_col_23] = '水体'
    for pos in water_force_24:
        gdf_24_out.at[pos, cat_col_24] = '水体'
    for pos in water_force_25:
        gdf_25_out.at[pos, cat_col_25] = '水体'

    # ========== 新增：其他类别修正 ==========
    print('\n  其他类别修正（用其他年份非"其他"地块覆盖本年的"其他"）...')
    # 注意：修正时使用已经过上述处理的数据（gdf_xx_out），且修正后可能改变类别
    # 为避免循环依赖，按年份顺序修正，修正后的结果会用于后续年份的参考（更合理）
    # 先修正2023年（参考2024和2025）
    gdf_23_out = correct_other_category(gdf_23_out, gdf_24_out, gdf_25_out, cat_col_23, overlap_threshold=0.80)
    # 修正2024年（参考已修正的2023和原始的2025，为了稳妥，使用修正后的2023和未修正的2025？这里统一用当前已修正的）
    gdf_24_out = correct_other_category(gdf_24_out, gdf_23_out, gdf_25_out, cat_col_24, overlap_threshold=0.80)
    # 修正2025年（参考已修正的2023和2024）
    gdf_25_out = correct_other_category(gdf_25_out, gdf_23_out, gdf_24_out, cat_col_25, overlap_threshold=0.80)

    # =======================================
        # ========== 交通传播（源：交通类别 + 火车站/机场标记，优先级最高） ==========
    print('\n  交通传播（仅从「交通类别且(火车站或机场标记=1)」的地块出发，扩散至三年所有重叠地块）...')

    # 获取各年份经过所有前序处理后的类别（来自 gdf_xx_out）
    final_cat_23 = gdf_23_out[cat_col_23]
    final_cat_24 = gdf_24_out[cat_col_24]
    final_cat_25 = gdf_25_out[cat_col_25]

    # 原始指标中的火车站/机场标记（使用未修改前的 gdf_xx_utm，它们保留了原始字段）
    def has_rail_airport(gdf):
        mask = pd.Series(False, index=gdf.index)
        if 'IS_HuoChe' in gdf.columns:
            mask |= (gdf['IS_HuoChe'] == 1)
        if 'IS_JiChang' in gdf.columns:
            mask |= (gdf['IS_JiChang'] == 1)
        return mask

    has_rail_airport_23 = has_rail_airport(gdf_23_utm)
    has_rail_airport_24 = has_rail_airport(gdf_24_utm)
    has_rail_airport_25 = has_rail_airport(gdf_25_utm)

    # 有效源：最终类别为交通 且 有火车站/机场标记
    src_23 = set(gdf_23_out.index[(final_cat_23 == '交通物流设施') & has_rail_airport_23])
    src_24 = set(gdf_24_out.index[(final_cat_24 == '交通物流设施') & has_rail_airport_24])
    src_25 = set(gdf_25_out.index[(final_cat_25 == '交通物流设施') & has_rail_airport_25])

    print(f'    有效源地块数（交通+火车站/机场）：2023年 {len(src_23)} / 2024年 {len(src_24)} / 2025年 {len(src_25)}')

    # 用于存储最终强制为交通的地块索引（初始为空，由传播填充）
    force_traffic_23 = set()
    force_traffic_24 = set()
    force_traffic_25 = set()

    def propagate_traffic_by_src(src_gdf, src_indices, tgt_gdf, tgt_force_set, overlap_threshold=0.90, src_label='', tgt_label=''):
        if not src_indices:
            return 0
        src_traffic = src_gdf.iloc[list(src_indices)].reset_index(drop=True)
        overlap_map = match_parcels_overlap_many(src_traffic, tgt_gdf, overlap_threshold)
        added = 0
        for local_idx, tgt_pos_list in overlap_map.items():
            for tgt_pos in tgt_pos_list:
                if tgt_pos not in tgt_force_set:
                    tgt_force_set.add(tgt_pos)
                    added += 1
        if added > 0:
            print(f'      {src_label} → {tgt_label} 新增 {added} 个交通地块')
        return added

    # 双向传播：源年份的有效交通地块传播到其他两年
    propagate_traffic_by_src(gdf_23_out, src_23, gdf_24_out, force_traffic_24, 0.90, '2023', '2024')
    propagate_traffic_by_src(gdf_23_out, src_23, gdf_25_out, force_traffic_25, 0.90, '2023', '2025')
    propagate_traffic_by_src(gdf_24_out, src_24, gdf_23_out, force_traffic_23, 0.90, '2024', '2023')
    propagate_traffic_by_src(gdf_24_out, src_24, gdf_25_out, force_traffic_25, 0.90, '2024', '2025')
    propagate_traffic_by_src(gdf_25_out, src_25, gdf_23_out, force_traffic_23, 0.90, '2025', '2023')
    propagate_traffic_by_src(gdf_25_out, src_25, gdf_24_out, force_traffic_24, 0.90, '2025', '2024')

    # 强制写回交通类别（覆盖任何原有类别，包括水体）
    for pos in force_traffic_23:
        gdf_23_out.at[pos, cat_col_23] = '交通物流设施'
    for pos in force_traffic_24:
        gdf_24_out.at[pos, cat_col_24] = '交通物流设施'
    for pos in force_traffic_25:
        gdf_25_out.at[pos, cat_col_25] = '交通物流设施'

    # 同时，源地块本身也确保为交通（其实已经是，但以防后续被意外覆盖，再次确保）
    for pos in src_23:
        gdf_23_out.at[pos, cat_col_23] = '交通物流设施'
    for pos in src_24:
        gdf_24_out.at[pos, cat_col_24] = '交通物流设施'
    for pos in src_25:
        gdf_25_out.at[pos, cat_col_25] = '交通物流设施'

    # 合并源地块和传播得到的地块，用于统计
    final_traffic_23 = src_23.union(force_traffic_23)
    final_traffic_24 = src_24.union(force_traffic_24)
    final_traffic_25 = src_25.union(force_traffic_25)

    print(f'    → 最终交通地块数：2023年 {len(final_traffic_23)} 个 / '
          f'2024年 {len(final_traffic_24)} 个 / 2025年 {len(final_traffic_25)} 个')
    # ==========================================================

    # ── 9. 保存三年完整地块 ────────────────────────────────────────
    print('\n  保存三年整合后完整地块数据...')
    save_year_result(gdf_23_out, city_name, 2023, cat_col_23)
    save_year_result(gdf_24_out, city_name, 2024, cat_col_24)
    save_year_result(gdf_25_out, city_name, 2025, cat_col_25)

    elapsed = time.time() - start_time
    print(f'\n✅ {city_name} 处理完成！'
          f'三年稳定地块 {n_stable} 个 / 2024年总地块 {len(gdf_24_utm)} 个 / '
          f'两两匹配新稳定（23-24）{count_new_stable_2324} 对、（24-25）{count_new_stable_2425} 对，'
          f'耗时 {elapsed:.1f}秒')

    return True, n_stable, len(gdf_24_utm)


# ============================================================
# 批量处理主函数
# ============================================================

def main():
    print("=" * 70)
    print("三年地块稳定性分析与类别统一批量处理程序")
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"\n参数设置：")
    print(f"  IoU 阈值（形状稳定）   : ≥ {IOU_THRESHOLD}")
    print(f"  面积变化率阈值         : < {AREA_CHANGE_THRESHOLD * 100:.0f}%")
    print(f"  稳定条件               : 三年均需匹配（缺任一年则为非稳定）")
    print(f"  2023 年结果根目录      : {OUTPUT_ROOT_2023}")
    print(f"  2024 年结果根目录      : {OUTPUT_ROOT_2024}")
    print(f"  2025 年结果根目录      : {OUTPUT_ROOT_2025}")
    print(f"  整合输出根目录         : {FINAL_OUTPUT_ROOT}")

    os.makedirs(FINAL_OUTPUT_ROOT, exist_ok=True)

    results     = []
    total_start = time.time()

    for idx, city_name in enumerate(CITY_LIST, 1):
        print(f'\n>>> 进度：{idx}/{len(CITY_LIST)} ({idx/len(CITY_LIST)*100:.1f}%)')
        try:
            success, stable_cnt, total_cnt = process_city_three_years(city_name)
        except Exception as e:
            print(f'\n❌ {city_name} 处理异常：{e}')
            traceback.print_exc()
            success, stable_cnt, total_cnt = False, 0, 0

        results.append({
            '城市':       city_name,
            '状态':       '成功' if success else '失败',
            '稳定地块数':  stable_cnt,
            '2024总地块':  total_cnt,
            '时间':       datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })

    total_elapsed = time.time() - total_start

    print('\n' + '=' * 70)
    print('批量处理完成报告')
    print('=' * 70)
    print(f'总耗时：{total_elapsed:.1f}秒')
    if CITY_LIST:
        print(f'平均每个城市：{total_elapsed / len(CITY_LIST):.1f}秒')
    suc = sum(1 for r in results if r['状态'] == '成功')
    fal = sum(1 for r in results if r['状态'] == '失败')
    print(f'成功：{suc} 个  失败：{fal} 个')
    print('\n详细结果：')
    print('-' * 75)
    print(f'{"城市":<12} {"状态":<6} {"稳定地块":>8} {"2024总地块":>10}  {"处理时间"}')
    print('-' * 75)
    for r in results:
        icon = '✓' if r['状态'] == '成功' else '✗'
        print(f"{icon} {r['城市']:<10} {r['状态']:<6} "
              f"{r['稳定地块数']:>8} {r['2024总地块']:>10}  {r['时间']}")
    print('-' * 75)

    report_path = os.path.join(FINAL_OUTPUT_ROOT, '三年整合处理报告.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('=' * 70 + '\n')
        f.write('三年地块稳定性分析与类别统一 — 完成报告\n')
        f.write('=' * 70 + '\n\n')
        f.write(f'处理时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write(f'总城市数：{len(CITY_LIST)}\n')
        f.write(f'成功：{suc} 个  失败：{fal} 个\n')
        f.write(f'总耗时：{total_elapsed:.1f}秒\n\n')
        f.write('稳定性筛选参数：\n')
        f.write(f'  IoU 阈值 : ≥ {IOU_THRESHOLD}（形状变化不太大）\n')
        f.write(f'  面积变化 : < {AREA_CHANGE_THRESHOLD * 100:.0f}%\n')
        f.write('  覆盖范围 : 三年均需匹配，非稳定地块保持原类别\n\n')
        f.write('详细结果：\n')
        f.write('-' * 75 + '\n')
        f.write(f'{"城市":<12} {"状态":<6} {"稳定地块":>8} {"2024总地块":>10}  {"处理时间"}\n')
        f.write('-' * 75 + '\n')
        for r in results:
            icon = '✓' if r['状态'] == '成功' else '✗'
            f.write(f"{icon} {r['城市']:<10} {r['状态']:<6} "
                    f"{r['稳定地块数']:>8} {r['2024总地块']:>10}  {r['时间']}\n")
        f.write('-' * 75 + '\n')

    print(f'\n✓ 完成报告已保存：{report_path}')
    print('\n✅ 全部处理完成！')


if __name__ == '__main__':
    main()