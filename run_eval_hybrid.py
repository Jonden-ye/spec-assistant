import os
import re
import csv
import jieba
from rank_bm25 import BM25Okapi
from openai import OpenAI
import chromadb
from chunker import load_chunks
from eval_questions import QUESTIONS

def tokenize(text):
    tokens = jieba.cut(text)
    result = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if re.fullmatch(r"[\d.．\-—]+", t):
            continue
        if len(t) == 1 and not t.isalnum():
            continue
        result.append(t)
    return result

#-----准备两路检索器-----
chunks =load_chunks()
clauses = [c["clause"] for c in chunks]
bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_collection(name="cjj1_2008")

def hybrid_search(question,top_k=3,recall_k=20,rrf_k=60):
    scores = bm25.get_scores(tokenize(question))
    bm25_ranked = sorted(enumerate(scores),key=lambda x : x[1],reverse=True)[:recall_k]
    bm25_rank = {clauses[idx]: rank for rank,(idx, _) in enumerate(bm25_ranked,start=1)}

    q_resp = client.embeddings.create(
        model="qwen3.7-text-embedding",
        input=[question],
    )
    q_vector = q_resp.data[0].embedding
    vec_results = collection.query(query_embeddings=[q_vector], n_results=recall_k)
    vec_clauses = [m["clause"] for m in vec_results["metadatas"][0]]
    vec_rank = {c: rank for rank, c in enumerate(vec_clauses,start=1)}
    vec_dist = {c: d for c, d in zip(vec_clauses,vec_results["distances"][0])}

    all_clauses = set(bm25_rank) | set(vec_rank)
    fused = []
    for c in all_clauses:
        score = 0.0
        if c in bm25_rank:
            score += 1 / (rrf_k + bm25_rank[c])
        if c in vec_rank:
            score += 1 / (rrf_k + vec_rank[c])
        fused.append((c, score, bm25_rank.get(c), vec_rank.get(c), vec_dist.get(c)))

    fused.sort(key=lambda x: x[1], reverse=True)
    return fused[:top_k]


# ---- 跑 31 题 ----
rows = []
hit_count = 0
total_count = 0

for qid, question, expected, category in QUESTIONS:
    fused = hybrid_search(question)
    hit_clauses = [f[0] for f in fused]

    if expected is None:
        hit = "-"
    else:
        wanted = expected.split("|")
        matched = [w for w in wanted if w in hit_clauses]
        hit = f"{len(matched)}/{len(wanted)}"
        total_count += len(wanted)
        hit_count += len(matched)

    rows.append({
        "题号": qid,
        "类别": category,
        "问题": question,
        "期望条号": expected if expected else "应拒答",
        "top1条号": fused[0][0],
        "top1_RRF": round(fused[0][1], 5),
        "top1_BM25排名": fused[0][2],
        "top1_向量排名": fused[0][3],
        "top1_向量距离": round(fused[0][4], 4) if fused[0][4] is not None else "",
        "top2条号": fused[1][0],
        "top3条号": fused[2][0],
        "命中": hit,
    })

    print(f"{qid}  {fused[0][0]:<10} 命中:{hit}")

with open("eval_hybrid.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"\n完成，共 {len(rows)} 题")
print(f"检索召回率：{hit_count}/{total_count} = {hit_count/total_count:.1%}")