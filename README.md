@"
# 施工规范智能查询系统

基于 CJJ 1-2008《城镇道路工程施工与质量验收规范》的 RAG 问答系统。

**在线体验：** https://cjj-spec-assistant.streamlit.app

## 它解决什么问题

施工现场查规范，翻纸质书慢，问 AI 又不敢信——因为没有出处。
本系统的每个答案都标注**规范名称 + 条号 + 页码**，可追溯到原文。
规范里没有的问题，会明确回答"未提及"，不编造。

## 技术要点

**按条款结构切分**
全书 234 页切分为 477 条。按 X.Y.Z 条款编号切，而非固定长度——
规范是按"条"组织的，固定长度切分会破坏语义完整性。

**排除条文说明**
国标后半部分的「条文说明」使用与正文完全相同的条号，
不排除会导致主键冲突和检索污染。本系统定位正文范围为第 11–187 页。

**修复两类误切**
- 表号误判：8.1.7-2、10.1.1—1 等被当成新条款
- 换行引用：原文"应符合表 5.2.7 规定"因排版换行，5.2.7 落到行首
重复条号从 15 降至 0。

**两层幻觉兜底**
- 代码层：检索距离 > 0.85 直接拒答，不调用大模型
- 提示词层：硬约束"只能依据提供的原文回答"
阈值 0.85 由 31 题评估集的距离分布确定，是零误杀的最大值。

**评估集**
31 题分六类：精确术语、口语转述、数字表格、跨章节、规范外兜底、易混淆。
其中 5 题是规范里没有的，必须拒答——只测正例的话，
一个永远瞎编的系统也能拿高分。

检索召回率 85%（22/26），规范外问题兜底成功率 70%。

## 技术栈

Python / FastAPI / Streamlit / Chroma / Docker
Embedding: qwen3.7-text-embedding (1024 维)
LLM: qwen-plus

## 项目结构

\`\`\`
chunker.py          PDF 解析与条款切分
build_db.py         向量化并写入 Chroma
api.py              FastAPI 服务（含阈值兜底、状态码分级）
app.py              Streamlit 前端（调用 API）
app_cloud.py        云端部署版本（逻辑内联）
eval_questions.py   31 题评估集
run_eval.py         检索评估脚本
Dockerfile          容器化配置
\`\`\`

## 本地运行

\`\`\`bash
pip install -r requirements.txt

# 设置 API key
# Windows: [Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", "sk-xxx", "User")

python build_db.py              # 构建向量库
uvicorn api:app --reload        # 启动后端
streamlit run app.py            # 启动前端
\`\`\`

## Docker

\`\`\`bash
docker build -t spec-assistant .
docker run -d -p 8000:8000 \\
  -e DASHSCOPE_API_KEY="\$env:DASHSCOPE_API_KEY" \\
  -v "\${PWD}/chroma_db:/app/chroma_db" \\
  spec-assistant
\`\`\`

## 已知局限

- **表格处理**：二维表提取后行列关系丢失，数值类问题准确率低
- **混合检索**：BM25 + RRF 已实现，但评估显示召回率下降（85% → 62%），暂未启用
- **术语鸿沟**：用户说"井盖"，规范写"井框"，检索命中率受影响

## 语料说明

规范 PDF 因版权原因未包含在仓库中。
"@ | Out-File -FilePath README.md -Encoding utf8
