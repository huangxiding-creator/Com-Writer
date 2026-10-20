# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pymupdf
pdf = r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\13 AI输出初稿\黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261012.pdf"
outdir = r"e:\AI-Station\Com-Writer\build\preview_261012"
os.makedirs(outdir, exist_ok=True)
doc = pymupdf.open(pdf)
for i, page in enumerate(doc):
    pix = page.get_pixmap(dpi=110)
    p = os.path.join(outdir, f"page{i+1}.png")
    pix.save(p)
    print(p, pix.width, "x", pix.height)
