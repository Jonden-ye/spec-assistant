import pdfplumber
import re

re_clause = re.compile(r"^(\d+\.\d+\.\d+)(?![\d—-])")
CONTINUE_WORDS = ("的","规定","要求","确定","选取")


def load_chunks(pdf_path="道路规范.pdf",start=10,end=187):
    """读PDF，返回切好的chunk 列表"""
    seen = set()

    def match_clause(line):
        m = re_clause.match(line)
        if not m:
            return None
        rest = line[m.end():].strip()
        if rest.startswith(CONTINUE_WORDS):
            return None
        clause = m.group(1)
        if clause in seen:
            return None
        seen.add(clause)
        return clause

    def make_chunk(clause,page,lines):
        return{
            "clause":clause,
            "chapter":clause.split(".")[0],
            "section":".".join(clause.split(".")[:2]),
            "page":page,
            "source":"CJJ 1-2008 城镇道路工程施工与质量验收规范",
            "text":"\n".join(lines),
        }

    lines_with_page = []
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(start,end):
            text = pdf.pages[i].extract_text()
            for line in text.split("\n"):
                lines_with_page.append((i+1,line))

    chunks = []
    current_lines = []
    current_clause = None
    current_page =None

    for page_num,line in lines_with_page:
        clause = match_clause(line)
        if clause:
            if current_clause:
                chunks.append(make_chunk(current_clause,current_page,current_lines))
            current_clause = clause
            current_page = page_num
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_clause:
        chunks.append(make_chunk(current_clause,current_page,current_lines))

    return chunks

#直接运行这个文件时，跑一下统计：
if __name__ =="__main__":
    chunks = load_chunks()
    print(f"共{len(chunks)}条")