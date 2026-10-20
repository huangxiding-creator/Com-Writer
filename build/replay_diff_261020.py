# -*- coding: utf-8 -*-
"""金标准回放验证：规则版回放稿 vs 定稿261020 逐条比对与缺口归类。"""
import sys, io, re, difflib
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from docx import Document

MARKS = tuple(f"{c}、" for c in "一二三四五六七八九十") + ("十一、","十二、","十三、","十四、","十五、")

def items_of(path):
    doc = Document(path)
    out = []
    for p in doc.paragraphs:
        t = p.text.strip()
        if t.startswith(MARKS):
            out.append(t)
    return out

def norm(s):
    return re.sub(r"\s+", "", s)

replay = items_of(r"e:\AI-Station\Com-Writer\build\回放稿-规则版-261012.docx")
final = items_of(r"e:\AI-Station\Com-Writer\02-1 总承包事业部\05 成果迭代优化\黑河黄藏寺水利枢纽工程专题会会议纪要（定稿）261020.docx")

print(f"回放 {len(replay)} 条 / 定稿 {len(final)} 条")
print(f"回放总字数 {sum(len(x) for x in replay)} / 定稿总字数 {sum(len(x) for x in final)}")

ratios = []
for i in range(max(len(replay), len(final))):
    a = norm(replay[i]) if i < len(replay) else ""
    b = norm(final[i]) if i < len(final) else ""
    r = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 1.0
    ratios.append(r)
    tag = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"[i] if i < 12 else str(i+1)
    print(f"\n条{tag} 相似度 {r:.0%}")
    if r < 0.98:
        sm = difflib.SequenceMatcher(None, a, b)
        for op, a1, a2, b1, b2 in sm.get_opcodes():
            if op == 'equal':
                continue
            da = a[a1:a2][:60]; db = b[b1:b2][:60]
            print(f"  [{op}] 回放「{da}」→ 定稿「{db}」")

overall = sum(ratios)/len(ratios)
print(f"\n=== 逐条相似度均值: {overall:.0%} ===")
