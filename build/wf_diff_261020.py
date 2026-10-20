# -*- coding: utf-8 -*-
"""工作流版回放稿 vs 定稿261020：最优配对相似度 + 规则符合性抽查。"""
import sys, io, re, difflib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from docx import Document

MARKS = tuple(f"{c}、" for c in "一二三四五六七八九十") + ("十一、","十二、","十三、","十四、","十五、")

def items_of(path):
    return [p.text.strip() for p in Document(path).paragraphs if p.text.strip().startswith(MARKS)]

def norm(s):
    return re.sub(r"[\s\[\]（）()：:，。；、·—-]", "", s)

wf = items_of(r"e:\AI-Station\Com-Writer\build\回放稿-工作流版-261012.docx")
fin = items_of(r"e:\AI-Station\Com-Writer\02-1 总承包事业部\05 成果迭代优化\黑河黄藏寺水利枢纽工程专题会会议纪要（定稿）261020.docx")

print(f"工作流版 {len(wf)} 条 / 定稿 {len(fin)} 条\n=== 定稿条 → 工作流版最优配对 ===")
cov = []
for i, f in enumerate(fin):
    nf = norm(f)
    best = max(((difflib.SequenceMatcher(None, nf, norm(w)).ratio(), j) for j, w in enumerate(wf)))
    cov.append(best[0])
    star = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"[i]
    flag = "✓" if best[0] >= 0.75 else ("△" if best[0] >= 0.45 else "✗")
    print(f"定稿{star} {flag} {best[0]:.0%} ← 工作流条{best[1]+1}")
print(f"定稿内容覆盖率(均值): {sum(cov)/len(cov):.0%}  完整命中(≥75%): {sum(1 for c in cov if c>=0.75)}/12\n")

print("=== 工作流版独有条（定稿无 ≥45% 配对） ===")
for j, w in enumerate(wf):
    nw = norm(w)
    best = max(difflib.SequenceMatcher(None, nw, norm(f)).ratio() for f in fin)
    if best < 0.45:
        print(f"条{j+1}({best:.0%}): {w[:52]}...")

print("\n=== 规则符合性抽查 ===")
alltext = "\n".join(wf)
print("个人具名:", len(re.findall(r"哈佳(强调|指出|要求)|齐三红(指出|要求|强调)", alltext)), "处")
print("金额数字:", len(re.findall(r"\d+(万余?元|万元|亿元)", alltext)), "处")
print("占位标记:", len(re.findall(r"［?\[待确认", alltext)), "处")
print("口语词(窝在/握在):", len(re.findall(r"窝在|握在自己手里", alltext)), "处")
