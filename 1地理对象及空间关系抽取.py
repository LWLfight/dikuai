from openai import OpenAI
import os
import time
import re

# ==================== 配置参数 ====================
# 控制使用前多少条文本数据进行事件抽取
target_count = 500  # 修改此值以调整处理的文本数量

# 配置API密钥（建议通过环境变量设置）
api_key = os.getenv("QWEN_API_KEY")

if not api_key:
    # 如果没有环境变量，可以直接在这里填写API密钥（不推荐生产环境使用）
    api_key = "sk-8ff1763f597644bc8dd12e18cdef4eb5"  # 请替换为你的实际API密钥

if not api_key:
    raise ValueError("请设置 QWEN_API_KEY 环境变量或在代码中配置API密钥")

# 创建OpenAI客户端实例
client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)


def extract_geo_spatial_relations(text):
    """
    第一阶段：使用通义千问大模型从文本中提取地理对象（名城要素）及其空间关系。
    这对应于论文中"地理对象及其空间关系抽取"任务。
    
    Args:
        text: 需要提取信息的文本内容
        
    Returns:
        list: 提取的代码化对象列表
    """
    
    # 结合代码化本体、任务描述与Few-Shot/CoT的提示词模板
    prompt = f"""# ==========================================
# 知识图谱本体定义 (Ontology Definition)
# ==========================================
from typing import Literal

class Entity:
    def __init__(self, ID: str, name: str):
        self.ID = ID
        self.name = name

class CityEle(Entity):
    \"\"\"
    名城要素类：风险事件的物理载体与响应事件的目标。
    提取参数：唯一标识(ID), 要素名称(eleNam), 要素类型(eleTyp), 几何范围(geometry), 历史价值(hisVal)。
    \"\"\"
    def __init__(self, ID: str, eleNam: str, eleTyp: str, geometry: Literal['point', 'linestring', 'polygon'], hisVal: str):
        super().__init__(ID=ID, name=eleNam)

class Relation:
    def __init__(self, head_entity, tail_entity, relation_type: str):
        self.head_entity = head_entity
        self.tail_entity = tail_entity
        self.relation_type = relation_type

class SpaRel(Relation):
    \"\"\"
    空间关系类：描述两个名城要素之间的静态空间关联。
    \"\"\"
    def __init__(self, head_entity: CityEle, tail_entity: CityEle, relation_type: str):
        super().__init__(head_entity, tail_entity, relation_type)

# ==========================================
# 任务描述与抽取指令 (Task Constraints)
# ==========================================
你是一名资深的历史文化名城保护与图谱知识抽取专家。这是一个将自然语言文本转化为结构化代码的信息提取任务。
上面定义了实体（Entity）类及关系（Relation）类。请将用户输入的非结构化文本转化为若干个 `CityEle` 和 `SpaRel` 的对象实例代码。

### 提取规则与约束：
1. **严格忠于原文**：仅提取文本中明确提及的信息，绝对不添加主观推测内容。如果类型、价值等属性未提及，请使用 "None"。几何范围通常为 'point', 'linestring', 'polygon' 之一。
2. **CityEle提取**：仅提取静态的地理要素或建筑实体（如街道、建筑、水系、区域），自动忽略文本中的车辆、人员等移动物体或抽象概念。
3. **SpaRel提取**：仅提取表示两地物间存在的静态空间关系（如“临近”、“下游”、“包含”、“相交”），自动过滤“行驶至”、“涌入”等动态动作。
4. **输出格式限定**：不要输出任何多余的解释性文本，必须且只能输出包含思维链推理注释和最终 `results` 变量的 Python 代码格式。

# ==========================================
# 思维链与代码输出参考案例 (Few-Shot & CoT Example)
# ==========================================

# 用户输入示例 (User Input):
# text = "郑东新区白沙镇一带临近贾鲁河，属于常庄水库泄洪的下游地区，受灾严重。"

# 你的输出必须严格遵循以下思维链与代码结构：
'''
[思维链推理分析]
1. 提取名城要素 (CityEle)：文本中提到了三个静态地理实体：“郑东新区白沙镇”（类型推断：行政区，面状polygon）、“贾鲁河”（类型推断：河流/水系，线状linestring）、“常庄水库”（类型推断：水库/水系，面状polygon）。
2. 提取空间关系 (SpaRel)：描述了白沙镇和贾鲁河之间的空间关系是“临近”；描述了白沙镇处于常庄水库的“下游”地区。
'''

# 实例化结果
ele1 = CityEle(ID="ele_001", eleNam="郑东新区白沙镇", eleTyp="行政区", geometry="polygon", hisVal="None")
ele2 = CityEle(ID="ele_002", eleNam="贾鲁河", eleTyp="河流", geometry="linestring", hisVal="None")
ele3 = CityEle(ID="ele_003", eleNam="常庄水库", eleTyp="水库", geometry="polygon", hisVal="None")

rel1 = SpaRel(head_entity=ele1, tail_entity=ele2, relation_type="临近")
rel2 = SpaRel(head_entity=ele1, tail_entity=ele3, relation_type="下游")

results = [ele1, ele2, ele3, rel1, rel2]

# ==========================================
# 当前待处理文本 (Current Input)
# ==========================================
text = "{text}"
"""
    
    try:
        completion = client.chat.completions.create(
            model="qwen-plus", 
            messages=[
                {"role": "system", "content": "你是专业的自然语言处理专家和知识图谱数据工程师，擅长将非结构化文本转化为面向对象的结构化Python代码。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,  # 极低温度保证代码生成的稳定性
            max_tokens=2000
        )
        
        response_content = completion.choices[0].message.content
        return response_content
        
    except Exception as e:
        print(f"调用API失败: {e}")
        return ""


def split_text_into_segments(content, segment_count=None):
    """将文本分割成多个独立的数据条目"""
    pattern = r'^\[\d+\]标题[:：]'
    # 需要跳过的元数据行模式（如：关键词：xxx）
    skip_pattern = r'^(关键词|关键字|标签|分类|类别)[:：]'
    
    segments = []
    lines = content.split('\n')
    current_segment = []
    in_segment = False
    
    for line in lines:
        line_stripped = line.strip()
        is_new_segment = bool(re.match(pattern, line_stripped))
        is_metadata = bool(re.match(skip_pattern, line_stripped, re.IGNORECASE))
        
        # 如果是元数据行，直接跳过
        if is_metadata:
            continue
        
        if is_new_segment and current_segment:
            segment_text = '\n'.join(current_segment).strip()
            if segment_text:
                segments.append(segment_text)
            current_segment = [line]
            in_segment = True
        elif is_new_segment and not current_segment:
            current_segment = [line]
            in_segment = True
        else:
            if in_segment or line_stripped:
                current_segment.append(line)
                in_segment = True
                
    if current_segment:
        segment_text = '\n'.join(current_segment).strip()
        if segment_text:
            segments.append(segment_text)
            
    if segment_count:
        return segments[:segment_count]
    return segments


def read_input_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='gbk') as f:
                return f.read()
        except Exception as e:
            print(f"读取文件失败 (GBK fallback): {e}")
            return None
    except Exception as e:
        print(f"读取文件失败: {e}")
        return None


def extract_title_from_segment(segment_text):
    first_line = segment_text.split('\n')[0].strip()
    match = re.match(r'^\[\d+\]标题[:：]\s*(.+)', first_line)
    if match:
        return match.group(1).strip()
    return first_line


def save_result_to_txt(segment_index, segment_text, extraction_result, output_file):
    """将抽取的代码化结果追加保存到txt文件"""
    try:
        title = extract_title_from_segment(segment_text)
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f"第 {segment_index} 条数据\n")
            f.write(f"标题：{title}\n")
            f.write("提取结果（结构化代码）：\n")
            f.write(extraction_result + "\n")
            f.write("-" * 50 + "\n\n")
        return True
    except Exception as e:
        print(f"保存文件失败: {e}")
        return False


def main():
    input_file = "事件素材.txt"
    output_file = "实体与关系.txt"
    
    print("=" * 70)
    print("事理图谱第一阶段抽取：名城要素及空间关系 - 基于通义千问")
    print(f"处理数量：前 {target_count} 条文本数据")
    print("=" * 70)
    
    if not os.path.exists(input_file):
        print(f"错误：找不到输入文件 '{input_file}'")
        return
        
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("地理对象及其空间关系代码化提取结果\n")
            f.write(f"提取时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 70 + "\n\n")
    except Exception as e:
        print(f"创建输出文件失败: {e}")
        return
        
    content = read_input_file(input_file)
    if not content:
        return
        
    segments = split_text_into_segments(content, target_count)
    
    success_count = 0
    fail_count = 0
    
    for idx, segment in enumerate(segments, 1):
        print(f"\n正在处理第 {idx}/{len(segments)} 个片段...")
        
        # 调用API进行第一阶段：地理对象及空间关系抽取
        result_code = extract_geo_spatial_relations(segment)
        
        if result_code:
            save_success = save_result_to_txt(idx, segment, result_code, output_file)
            if save_success:
                success_count += 1
                print(f"✓ 成功提取并保存代码")
            else:
                fail_count += 1
        else:
            fail_count += 1
            print("✗ 提取失败或返回为空")
            
        if idx < len(segments):
            time.sleep(1.5)
            
    print("\n" + "=" * 70)
    print(f"提取完成！成功: {success_count}, 失败: {fail_count}")
    print(f"结果保存在: {output_file}")


if __name__ == "__main__":
    main()