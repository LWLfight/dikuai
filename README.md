# 地块划分程序

基于 GIS 数据的城市地块划分和分类工具，实现地块的裁剪、优化和分类功能。

## 项目结构

```
dikuai1/
├── caijian.py                 # 步骤 1: 数据裁剪和预处理
├── youhua.py                  # 步骤 2: 地块优化处理（消除细碎多边形）  
├── fenlei.py                  # 步骤 3: 地块分类赋值
├── {city}/                    # 城市地块数据（如 guangzhou/）
├── 建成区 -2025/              # 各城市建成区边界数据
└── {province}-260107-free.shp # OpenStreetMap 数据
```

## 环境配置

### Python 依赖

```bash
pip install -r requirements.txt
```

或手动安装：
```bash
pip install geopandas pandas shapely numpy
```

### 系统要求

- Python 3.8+
- 操作系统：Windows / Linux / macOS
- 建议内存：8GB 以上

## 使用说明

### ⚠️ 重要：运行顺序

必须按照以下顺序依次执行：

**caijian.py → youhua.py → fenlei.py**

### 配置说明

在运行前需要修改三个脚本中的城市参数：

**caijian.py**:
```python
city = 'guangzhou'      # 城市英文名
city_name = '广州市'     # 城市中文名
```

**youhua.py**:
```python
city = 'guangzhou'      # 与 caijian.py 保持一致
```

**fenlei.py**:
```python
city = 'guangzhou'      # 与前两个脚本保持一致
```

### 运行示例

```bash
# 步骤 1: 裁剪和预处理
python caijian.py

# 步骤 2: 优化处理
python youhua.py

# 步骤 3: 分类赋值
python fenlei.py
```

## 输出文件

程序会在对应城市的 `.output` 目录中生成结果：

```
guangzhou.output/
├── not_divided.shp           # 步骤 1 输出
├── cleaned_not_divided.shp   # 步骤 2 输出
└── 赋值后的地块.shp          # 步骤 3 输出（最终结果）
```

## 数据处理流程

1. **caijian.py** - 裁剪预处理
   - 读取地块、OSM、建成区数据
   - 统一转换为 CGCS2000 坐标系
   - 提取交通、绿地、工业、水体用地
   - 使用建成区边界裁剪（除水体外）
   - 从地块中依次差分解译各类用地

2. **youhua.py** - 优化处理
   - 读取未分割的地块数据
   - 形态学开运算优化几何形状
   - 移除狭小和不规则图斑
   - 清理和优化地块边界

3. **fenlei.py** - 分类赋值
   - 读取优化后的地块和 POI 数据
   - 基于 POI 类型进行空间关联
   - 为每个地块分配功能类别
   - 输出分类结果

## 注意事项

1. **执行顺序**: 必须严格按照 `caijian.py → youhua.py → fenlei.py` 的顺序执行
2. **城市一致性**: 三个脚本中的 `city` 参数必须保持一致
3. **坐标系统一**: 所有数据统一使用 CGCS2000 坐标系（EPSG:4490）
4. **数据完整性**: 确保 Shapefile 文件完整（.shp, .shx, .dbf 等）
5. **输出目录**: 程序会自动创建输出目录

## 常见问题

**Q: 找不到 Shapefile 文件？**
- 检查文件路径是否正确
- 确认 Shapefile 组件完整

**Q: 坐标系转换错误？**
- 确保安装了 pyproj 库
- 验证 EPSG 代码正确性

**Q: 内存不足？**
- 建议 8GB 以上内存
- 可尝试分批处理数据
