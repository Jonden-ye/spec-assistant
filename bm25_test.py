import re
import jieba
from rank_bm25 import BM25Okapi
from chunker import load_chunks

def tokenize(text):
    print("### tokenize 被调用了 ###") 
    """分词并过滤掉无意义的token"""
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


chunks = load_chunks()
print(f"载入{len(chunks)}条")

#-----分词 ----
#BM25 需要的是“分好词的列表”，不是原始字符串
tokenized = [tokenize(c["text"]) for c in chunks] 

print("\n 分词示例(7.1.5那条)：")
for c , t in zip(chunks,tokenized):
    if c["clause"] == "7.1.5":
        print(f"原文：{c['text']}")
        print(f"分词：{'/'.join(t)}")
        break

#-------建索引 ------
bm25 = BM25Okapi(tokenized)


# -------试检索 -------
for question in["基层能用贴薄层的方法修补吗？",
                 "路面井盖怎么做到车经过的时候不响？",
                 "快速路主干路与次干路支路的平整度标准有何区别？"]:
    q_tokens = tokenize(question) 
    scores = bm25.get_scores(q_tokens)

    #取分数最高的前3
    ranked = sorted(enumerate(scores),key=lambda x:x[1],reverse=True)[:3]

    print(f"\n==={question} ===")
    print(f"分词：{'/'.join(q_tokens)}")
    for idx, score in ranked:
        print(f" [{score:.3f}] {chunks[idx]['clause']}  {chunks[idx]['text'][:30]}")