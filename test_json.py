import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

#关键：在提示词里明确要求返回JSON ，并给出格式
prompt = """请从下面这句话里,提取出工程信息。

句子:基坑长12米,宽8米,深5米,需要用排桩支护。

请只返回一个 JSON 对象,不要有任何其他文字,格式如下:
{"length": 数字, "width": 数字, "depth": 数字, "method": "支护方式"}"""

response = client.chat.completions.create(
    model="qwen-plus",
    messages=[{"role": "user", "content": prompt}]
)
ai_output = response.choices[0].message.content

print("AI 原始输出：")
print(ai_output)
print("---")

# 把 JSON 文本 → Python 字典
data = json.loads(ai_output)

print("解析后的类型：",type(data))
print("---")

#现在可以像字典一样取值了
print("长度：",data["length"])
print("宽度:", data["width"])
print("深度:", data["depth"])
print("支护方式:", data["method"])
print("---")

# 关键:拿到的是真正的数字,可以直接计算!
volume = data["length"] * data["width"] * data["depth"]
print(f"基坑体积 = {data['length']} × {data['width']} × {data['depth']} = {volume} 立方米")

print("\n===== 演示:处理带代码块的情况 =====")

# 模拟 AI 返回了带代码块包裹的输出
messy_output = """```json
{"length": 20, "width": 10, "depth": 6, "method": "地下连续墙"}
```"""

print("模拟的脏输出:")
print(messy_output)

# 清洗:去掉代码块标记
clean = messy_output.strip()              # 去掉首尾空白
clean = clean.replace("```json", "")      # 去掉开头的 ```json
clean = clean.replace("```", "")          # 去掉结尾的 ```
clean = clean.strip()                     # 再去一次空白

print("\n清洗后:")
print(clean)

# 现在可以正常解析了
data2 = json.loads(clean)
print("\n解析成功,深度是:", data2["depth"])
