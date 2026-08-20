import os
from chunker import load_chunks
from openai import OpenAI
import chromadb

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="http://dashscope.aliyuncs.com/compatible-mode/v1",
)
chunks = load_chunks()
#-----1. 向量化  -----
texts = [c["text"] for c in chunks]

vectors = []
BATCH = 10
for i in range(0,len(texts),BATCH):
    batch = texts[i:i+ BATCH]
    resp = client.embeddings.create(
        model="qwen3.7-text-embedding",
        input=batch,
    )
    vectors.extend(item.embedding for item in resp.data)
    print(f"已向量化{len(vectors)}/{len(texts)}")

print(f"向量化完成：{len(vectors)}条，每条{len(vectors[0])}维")


#-----2. 存进 Chroma ------
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_or_create_collection(name="cjj1_2008")

try:
    chroma_client.delete_collection(name="cjj1_2008")
except Exception:
    pass
collection = chroma_client.get_or_create_collection(name="cjj1_2008")


collection.add(
    ids=[c["clause"] for c in chunks],
    embeddings=vectors,
    documents=texts,
    metadatas=[
        {
            "clause":c["clause"],
            "chapter":c["chapter"],
            "section":c["section"],
            "page":c["page"],
            "source":c["source"],
        }
        for c in chunks
    ],
)

print(f"入库完成，当前库中共{collection.count()}条")