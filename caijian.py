import geopandas as gpd
import os
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon, box
import glob

# Define paths
city = 'guangzhou'
city_name = '广州市'  # 城市中文名
parcel_path = f'./dikuai1/{city}/{city}.shp'
osm_dir = './dikuai1/guangdong-260107-free.shp'
water_path = os.path.join(osm_dir, 'gis_osm_water_a_free_1.shp')
landuse_path = os.path.join(osm_dir, 'gis_osm_landuse_a_free_1.shp')
traffic_path = os.path.join(osm_dir, 'gis_osm_traffic_a_free_1.shp')
transport_path = os.path.join(osm_dir, 'gis_osm_transport_a_free_1.shp')

# 建成区边界路径（使用城市中文名匹配.shp 文件，实际目录名为'建成区-2025'）
builtup_pattern = f'./dikuai1/建成区-2025/{city_name}*.shp'

output_dir = f'./dikuai1/{city}.output'
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

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

# Step 1: Merge traffic and transport to create 交通用地
print("正在合并交通用地...")
traffic_merged = gpd.GeoDataFrame(pd.concat([traffic, transport], ignore_index=True))

# 重要：统一转换为 CGCS2000 投影坐标系 (米单位),确保面积和空间计算准确
print("\n=== 坐标系统一转换 (CGCS2000) ===")
# CGCS2000 地理坐标系
geo_crs = 'EPSG:4490'
# 根据长沙的经度位置（约东经 113°），选择 CGCS2000 / 3-degree Gauss-Kruger zone 38
# 带号计算：(经度 + 1.5) / 3 = (113 + 1.5) / 3 ≈ 38.17，取整为 38 带
projected_crs = 'EPSG:4548'  # CGCS2000 / 3-degree Gauss-Kruger CM 114E（38 带）

print(f"地理坐标系：{geo_crs}")
print(f"投影坐标系：{projected_crs}")

# 将所有数据转换到 CGCS2000 投影坐标系
print("转换地块数据坐标系...")
parcel = parcel.to_crs(projected_crs)

print("转换水体数据坐标系...")
water = water.to_crs(projected_crs)

print("转换土地利用数据坐标系...")
landuse = landuse.to_crs(projected_crs)

print("转换交通用地数据坐标系...")
traffic_merged = traffic_merged.to_crs(projected_crs)

print("转换建成区边界坐标系...")
builtup = builtup.to_crs(projected_crs)

print("所有图层已转换为 CGCS2000 投影坐标系")

# 提取绿地
print("\n正在提取绿地...")
green_classes = ['forest', 'grass', 'park', 'scrub', 'meadow']
green = landuse[landuse['fclass'].isin(green_classes)]

# 提取工业用地
print("\n正在提取工业用地...")
industrial = landuse[landuse['fclass'] == 'industrial']

# 使用建成区边界裁剪交通用地、绿地、工业用地（水体不裁剪）
print("\n=== 使用建成区边界裁剪用地类型 ===")

# 裁剪交通用地
print("正在裁剪交通用地到建成区范围...")
if len(traffic_merged) > 0:
    traffic_clipped_builtup = gpd.overlay(traffic_merged, builtup, how='intersection')
    print(f"  裁剪前图斑数：{len(traffic_merged)}, 裁剪后图斑数：{len(traffic_clipped_builtup)}")
else:
    traffic_clipped_builtup = traffic_merged.copy()
    print("  无交通用地数据，跳过")

# 裁剪绿地
print("正在裁剪绿地到建成区范围...")
if len(green) > 0:
    green_clipped_builtup = gpd.overlay(green, builtup, how='intersection')
    print(f"  裁剪前图斑数：{len(green)}, 裁剪后图斑数：{len(green_clipped_builtup)}")
else:
    green_clipped_builtup = green.copy()
    print("  无绿地数据，跳过")

# 裁剪工业用地
print("正在裁剪工业用地到建成区范围...")
if len(industrial) > 0:
    industrial_clipped_builtup = gpd.overlay(industrial, builtup, how='intersection')
    print(f"  裁剪前图斑数：{len(industrial)}, 裁剪后图斑数：{len(industrial_clipped_builtup)}")
else:
    industrial_clipped_builtup = industrial.copy()
    print("  无工业用地数据，跳过")

# 保存裁剪后的用地类型
print("\n=== 保存裁剪后的用地类型 ===")

# 保存绿地
green_path = os.path.join(output_dir, '绿地.shp')
if len(green_clipped_builtup) > 0:
    green_clipped_builtup.to_file(green_path)
    print(f"绿地已保存到：{green_path}")
else:
    print("绿地数据为空，未保存")

# 保存工业用地
industrial_path = os.path.join(output_dir, '工业用地.shp')
if len(industrial_clipped_builtup) > 0:
    industrial_clipped_builtup.to_file(industrial_path)
    print(f"工业用地已保存到：{industrial_path}")
else:
    print("工业用地数据为空，未保存")

# 保存水体（不裁剪，保留完整范围）
print("\n正在保存水体（完整范围，不裁剪）...")
water_output_path = os.path.join(output_dir, '水体.shp')
water.to_file(water_output_path)
print(f"水体已保存到：{water_output_path}")

# 保存交通用地
traffic_path_output = os.path.join(output_dir, '交通用地.shp')
if len(traffic_clipped_builtup) > 0:
    traffic_clipped_builtup.to_file(traffic_path_output)
    print(f"交通用地已保存到：{traffic_path_output}")
else:
    print("交通用地数据为空，未保存")

# Step 2: Clip sequentially from parcel
print("\n=== 开始差异分析操作 ===")
remaining = parcel.copy()
print(f"初始图斑数：{len(remaining)}")

# 关键优化：使用地块数据的边界对 OSM 数据进行预裁剪
# 避免因范围不匹配导致的几何切割异常和细碎图斑
print("\n=== 预裁剪 OSM 数据到地块范围 ===")
parcel_bounds = parcel.total_bounds
print(f"地块范围：X[{parcel_bounds[0]:.2f}, {parcel_bounds[2]:.2f}], Y[{parcel_bounds[1]:.2f}, {parcel_bounds[3]:.2f}]")

# 创建地块范围的边界框
from shapely.geometry import box
parcel_bbox = box(*parcel_bounds)
parcel_bbox_gdf = gpd.GeoDataFrame(geometry=[parcel_bbox], crs=projected_crs)

# 对每个 OSM 图层进行预裁剪
print("裁剪水体到地块范围...")
water_clipped = gpd.overlay(water, parcel_bbox_gdf, how='intersection')
print(f"  裁剪后水体图斑数：{len(water_clipped)}")

print("裁剪交通用地到地块范围...")
traffic_clipped = gpd.overlay(traffic_merged, parcel_bbox_gdf, how='intersection')
print(f"  裁剪后交通用地图斑数：{len(traffic_clipped)}")

print("裁剪绿地到地块范围...")
green_clipped = gpd.overlay(green, parcel_bbox_gdf, how='intersection')
print(f"  裁剪后绿地图斑数：{len(green_clipped)}")

print("裁剪工业用地到地块范围...")
industrial_clipped = gpd.overlay(industrial, parcel_bbox_gdf, how='intersection')
print(f"  裁剪后工业用地图斑数：{len(industrial_clipped)}")

# 使用预裁剪后的数据进行差异分析
print("\n=== 执行差异分析 (Difference) ===")

# Difference with water
print("正在裁剪水域...")
if len(water_clipped) > 0:
    remaining = gpd.overlay(remaining, water_clipped, how='difference')
    print(f"  剩余图斑数：{len(remaining)}")
else:
    print("  无水体数据，跳过")

# Difference with 交通用地
print("正在裁剪交通用地...")
if len(traffic_clipped) > 0:
    remaining = gpd.overlay(remaining, traffic_clipped, how='difference')
    print(f"  剩余图斑数：{len(remaining)}")
else:
    print("  无交通用地数据，跳过")

# Difference with 绿地
print("正在裁剪绿地...")
if len(green_clipped) > 0:
    remaining = gpd.overlay(remaining, green_clipped, how='difference')
    print(f"  剩余图斑数：{len(remaining)}")
else:
    print("  无绿地数据，跳过")

# Difference with 工业用地
print("正在裁剪工业用地...")
if len(industrial_clipped) > 0:
    remaining = gpd.overlay(remaining, industrial_clipped, how='difference')
    print(f"  剩余图斑数：{len(remaining)}")
else:
    print("  无工业用地数据，跳过")

# 重置 FID，确保每个独立要素都有唯一的 FID
print("\n=== 重置 FID ===")

def explode_multipolygons(geo_df):
    """将多部分几何拆分为单部分几何"""
    exploded_rows = []
    
    for idx, row in geo_df.iterrows():
        geom = row.geometry
        if isinstance(geom, MultiPolygon):
            # 如果是 MultiPolygon，拆分为多个 Polygon
            for polygon in geom.geoms:
                new_row = row.copy()
                new_row.geometry = polygon
                exploded_rows.append(new_row)
        elif isinstance(geom, Polygon):
            # 如果已经是 Polygon，直接添加
            exploded_rows.append(row.copy())
        # 忽略其他类型的几何
    
    return gpd.GeoDataFrame(exploded_rows, crs=geo_df.crs)

# 拆分多部分要素
print("正在拆分 MultiPolygon 为单部分 Polygon...")
original_count = len(remaining)
remaining = explode_multipolygons(remaining)
new_count = len(remaining)
print(f"  拆分前图斑数：{original_count}, 拆分后图斑数：{new_count}")

# 重置索引并分配唯一 FID
remaining = remaining.reset_index(drop=True)
remaining['FID'] = range(1, len(remaining) + 1)
print(f"  FID 已重置，范围：1-{len(remaining)}")

# Save the remaining
output_path = os.path.join(output_dir, 'not_divided.shp')
remaining.to_file(output_path)
print(f"\n最终结果已保存到：{output_path}")
print("处理完成！")