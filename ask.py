from typing import Any


import os
from openai import OpenAI
import chromadb

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="http://dashscope.aliyuncs.com/compatible-mode/v1",
)

#-----连上已有的库（注意：不重新建库，直接读）----
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_collection(name="cjj1_2008")

question = "沥青混合料面层能不能在雨天施工？"        # 常规查询
question = "路基压实度检验用什么方法？"              # 检验方法类
question = "水泥混凝土面层养护期间能开放交通吗？"     # 跨章节
question = "桥梁伸缩缝怎么施工？"                    # ⚠️ 规范里没有,测兜底

#-----1.问题向量化 ------
q_resp = client.embeddings.create(
    model="qwen3.7-text-embedding",
    input=[question],
)
q_vector = q_resp.data[0].embedding

#------2. 检索 top 3 ------
results = collection.query(
    query_embeddings=[q_vector],
    n_results=3,
)

print("====检索到的条款 =====")
for doc, meta, dist in zip(
    results["documents"][0],
    results["metadatas"][0],
    results["distances"][0],

):
    print(f"[距离{dist:.4f}]第{meta['clause']}条 P{meta['page']}: {doc[:40]}")

#----- 3. 组装上下文，交给大模型-----
context = "\n\n".join(
   ( f"【{m['source']} 第{m['clause']}条，第{m['page']}页】\n{d}"
    for d , m in zip(results["documents"][0], results["metadatas"][0])
   )
)

prompt = f"""你是施工规范查询助手。请严格根据以下规范原文回答问题。
规范原文：
{context}

问题：{question}

要求：
1. 只能依据上面提供的规范原文回答，禁止使用你自己的知识补充。
2. 如果原文中没有相关规定，必须明确回答"提供的规范条文中未提及"，不得编造。
3. 回答末尾必须标注依据的规范名称、条号和页码。
"""

answer = client.chat.completions.create(
    model="qwen-plus",
    messages=[{"role": "user", "content": prompt}],
)

print("\n=== 回答 ===")
print(answer.choices[0].message.content)