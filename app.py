import streamlit as st
import requests

API_URL = "http://127.0.0.1:8000/ask"

st.set_page_config(page_title="施工规范查询助手", page_icon = "🏗️")
st.title("🏗️ 施工规范智能查询")
st.caption("基于 CJJ 1-2008《城镇道路工程施工与质量验收规范》")

question = st.text_input("请输入你的问题", placeholder="例如：基层施工可以用贴薄层修补吗？")

if question:
    with st.spinner("检索中..."):
        try:
            resp= requests.post(
                API_URL,
                json={"question":question},
                timeout=60,
                )
        except requests.exceptions.RequestException as e:
            st.error("无法连接到查询服务，请确认后端已启动。")
            st.caption(f"技术细节：{type(e).__name__}")
            st.stop()

        if resp.status_code !=200:
           st.error(f"查询失败：{resp.json().get('detail', '未知错误')}")
           st.stop()
        
        data = resp.json()
        
        st.markdown("### 回答")

        if data["found"]:
            st.write(data["answer"])
            st.caption(f"依据：{data['source']} 第 {data['clause']} 条，第 {data['page']} 页"
                   f"（检索距离 {data['distance']})")
        else:
            st.warning(data["answer"])
            st.caption(f"（最相似条款距离 {data['distance']}，超过阈值）")
