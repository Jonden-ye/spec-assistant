import os
import csv
from openai import OpenAI
import chromadb
from eval_questions import QUESTIONS

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_collection(name="cjj1_2008")

rows= []

for qid, question,expected,category in QUESTIONS:
    #问题向量化
    q_resp = client.embeddings.create(
        model="qwen3.7-text-embedding",
        input=[question]
    )
    q_vector = q_resp.data[0].embedding

    #检索 top 3
    results = collection.query(query_embeddings=[q_vector],n_results=3)
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    hit_clauses = [m["clause"] for m in metas]

    #判断是否命中期望条款
    if expected is None:
        hit = "-"
    else:
        wanted = expected.split("丨")
        matched = [w for w in wanted if w in hit_clauses]
        hit = f"{len(matched)}/{len(wanted)}"

    rows.append({
         "题号": qid,
        "类别": category,
        "问题": question,
        "期望条号": expected if expected else "应拒答",
        "top1条号": hit_clauses[0],
        "top1距离": round(dists[0], 4),
        "top2条号": hit_clauses[1],
        "top2距离": round(dists[1], 4),
        "top3条号": hit_clauses[2],
        "top3距离": round(dists[2], 4),
        "命中": hit,
    })

    print(f"{qid} [{dists[0]:.4f}] {hit_clauses[0]}  命中：{hit}")

    #写CSV
with open("eval_baseline.csv","w",newline="",encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f,fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"\n完成，共{len(rows)}题，已写入eval_baseline.csv")