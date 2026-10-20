import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pymupdf
doc = pymupdf.open(r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\13 AI输出初稿\黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261012.pdf")
for i, page in enumerate(doc):
    spans = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    spans.append((s["bbox"][1], s["text"].strip()))
    spans.sort()
    print(f"p{i+1} 顶部: {spans[0][1][:20] if spans else ''} | 底部: {spans[-1][1][:30] if spans else ''}")
