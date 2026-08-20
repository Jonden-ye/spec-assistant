import os
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI
import chromadb
from fastapi import FastAPI, HTTPException

app = FastAPI(title="施工规范查询 API")

THRESHOLD = 0.85

# ---- 输入格式 ----
class AskRequest(BaseModel):
    question: str


# ---- 输出格式 ----
class AskResponse(BaseModel):
    answer: str
    found: bool
    clause: str | None = None
    page: int | None = None
    source: str | None = None
    distance: float


# ---- 启动时初始化（只做一次）----
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_collection(name="cjj1_2008")


@app.get("/")
def root():
    return {"message": "施工规范查询服务已启动"}


@app.post(
    "/ask", 
    response_model=AskResponse,
    responses={
        400:{"description":"请求参数错误"},
        503:{"description":"外部服务不可用"},
    },
    )
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    # 1. 问题向量化
    try:
        q_resp = client.embeddings.create(
            model="qwen3.7-text-embedding",
            input=[req.question],
        )
        q_vector = q_resp.data[0].embedding
    except Exception as e:
        print(f"[embedding 失败] {e}")
        raise HTTPException(status_code=503, detail="检索服务暂时不可用")

    # 2. 检索
    try:
        
        results = collection.query(query_embeddings=[q_vector], n_results=3)
    except Exception as e:
        print(f"[Chroma 查询失败] {e}")
        raise HTTPException(status_code=503, detail="知识库查询失败")
    top_distance = results["distances"][0][0]
    top_meta = results["metadatas"][0][0]

    # 3. 阈值兜底
    if top_distance > THRESHOLD:
        return AskResponse(
            answer="提供的规范条文中未提及该问题。",
            found=False,
            distance=round(top_distance, 4),
        )

    # 4. 组装上下文
    context = "\n\n".join([
        f"【{m['source']} 第{m['clause']}条，第{m['page']}页】\n{d}"
        for d, m in zip(results["documents"][0], results["metadatas"][0])
    ])

    prompt = f"""你是施工规范查询助手。请严格根据以下规范原文回答问题。

规范原文：
{context}

问题：{req.question}

要求：
1. 只能依据上面提供的规范原文回答，禁止使用你自己的知识补充。
2. 如果原文中没有相关规定，必须明确回答"提供的规范条文中未提及"，不得编造。
3. 回答末尾必须标注依据的规范名称、条号和页码。
"""

    # 5. 生成
    try:
        completion = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[生成失败] {e}")
        raise HTTPException(status_code=503,detail="答案生成失败")
    return AskResponse(
        answer=completion.choices[0].message.content,
        found=True,
        clause=top_meta["clause"],
        page=top_meta["page"],
        source=top_meta["source"],
        distance=round(top_distance, 4),
    )