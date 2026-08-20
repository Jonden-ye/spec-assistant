import streamlit as st
from openai import OpenAI
import chromadb

st.set_page_config(page_title="施工规范查询助手", page_icon="🏗️")
st.title("🏗️ 施工规范智能查询")
st.caption("基于 CJJ 1-2008《城镇道路工程施工与质量验收规范")

THRESHOLD = 0.85

@st.cache_resource
def init():
    client = OpenAI(
        api_key=st.secrets["DASHSCOPE_API_KEY"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    chroma_client = chromadb.PersistentClient(path="./chroma_db")
    collection = chroma_client.get_collection(name="cjj1_2008")
    return client, collection

client,collection = init()

question = st.text_input("请输入你的问题", placeholder="例如：基层施工可以用贴薄层修补吗？")

if question:
    with st.spinner("检索中..."):
        try:
            q_resp = client.embeddings.create(
                model="qwen3.7-text-embedding",
                input=[question],
            )
            q_vector = q_resp.data[0].embedding
        except Exception as e:
            st.error("检索服务暂时不可用，请稍后重试。")
            st.caption(f"技术细节：{type(e).__name__}")
            st.stop()

        try:
            results = collection.query(query_embeddings=[q_vector],n_results=3)
        except Exception as e:
            st.error("知识库查询失败。")
            st.caption(f"技术细节：{type(e).__name__}")
            st.stop()

        top_distance = results["distances"][0][0]
        top_meta = results["metadatas"][0][0]

    st.markdown("### 回答")

    if top_distance>THRESHOLD:
        st.warning("提供的规范条文中未提及该问题。")
        st.caption(f"（最相似条款距离 {top_distance:.4f}，超过阈值 {THRESHOLD}）")
        st.stop()

    context = "\n\n".join([
        f"【{m['source']} 第{m['clause']}条，第{m['page']}页】\n{d}"
        for d, m in zip(results["documents"][0], results["metadatas"][0])
    ])

    prompt = f"""你是施工规范查询助手。请严格根据以下规范原文回答问题。

规范原文：
{context}

问题：{question}

要求：
1. 只能依据上面提供的规范原文回答，禁止使用你自己的知识补充。
2. 如果原文中没有相关规定，必须明确回答"提供的规范条文中未提及"，不得编造。
3. 回答末尾必须标注依据的规范名称、条号和页码。
"""

    with st.spinner("生成中..."):
        try:
            completion = client.chat.completions.create(
                model="qwen-plus",
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            st.error("答案生成失败，请稍后重试。")
            st.caption(f"技术细节：{type(e).__name__}")
            st.stop()
            
    st.write(completion.choices[0].message.content)
    st.caption(f"依据：{top_meta['source']} 第 {top_meta['clause']} 条，第{top_meta['page']} 页"
               f"(检索距离{top_distance:.4f})")