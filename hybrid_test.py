import os
import re
import jieba
from rank_bm25 import BM25Okapi
from openai import OpenAI
import chromadb
from chunker import load_chunks

def tokenize(text):
    tokens = jieba.cut(text)
    result = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if re.fullmatch(r"[\d.．\-—]+",t):
            continue
        if len(t) == 1 and not t.isalnum():
            continue
        if len(t) == 1 and not t.isalnum():
            continue
        result.append(t)
    return result

# -----准备两路检索器 -----
chunks = load_chunks()
clauses = [c["clause"] for c in chunks ]

bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_collection(name="cjj1_2008")

def hybrid_search(question,top_k=3,recall_k=20,rrf_k=60):
    """混合检索：BM25 + 向量，RRF 融合"""

    #----第一路：BM25 ----
    scores = bm25.get_scores(tokenize(question))
    bm25_ranked = sorted(enumerate(scores),key=lambda x:x[1],reverse=True)[:recall_k]
    bm25_rank = {clauses[idx]: rank for rank,(idx, _) in enumerate(bm25_ranked,start=1)}

    #----第二路：向量 -----
    q_resp = client.embeddings.create(
        model="qwen3.7-text-embedding",
        input=[question]
    )
    q_vector = q_resp.data[0].embedding
    vec_results = collection.query(query_embeddings=[q_vector], n_results=recall_k)
    vec_clauses = [m["clause"] for m in vec_results["metadatas"][0]]
    vec_rank = {c: rank for rank, c in enumerate(vec_clauses, start=1)}
    vec_dist = {c: d for c, d in zip(vec_clauses, vec_results["distances"][0])}

    #----RRF 融合 -----
    all_clauses = set(bm25_rank) | set(vec_rank)
    fused = []
    for c in all_clauses:
        score = 0.0
        if c in bm25_rank:
            score +=1/(rrf_k +bm25_rank[c])
        if c in vec_rank:
            score +=1/(rrf_k + vec_rank[c])
        fused.append((c,score,bm25_rank.get(c),vec_rank.get(c),vec_dist.get(c)))

    fused.sort(key=lambda x:x[1],reverse=True)
    return fused[:top_k]

# ---- 试三道题 ----
for q in ["基层能用贴薄层的方法修补吗？",
          "路面井盖怎么做到车经过的时候不响？",
          "快速路主干路与次干路支路的平整度标准有何区别？"]:
    print(f"\n=== {q} ===")
    for clause, score, br, vr, vd in hybrid_search(q):
        print(f"  {clause:<10} RRF={score:.5f}  BM25排名={br}  向量排名={vr}")