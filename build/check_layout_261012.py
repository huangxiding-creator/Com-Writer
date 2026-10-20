# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pymupdf
pdf = r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\13 AI输出初稿\黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261012.pdf"
doc = pymupdf.open(pdf)
# 第1页：标题/红头/字段版式
page = doc[0]
print("=== 第1页 span（字体 | 字号 | 颜色 | 文本） ===")
for b in page.get_text("dict")["blocks"]:
    for l in b.get("lines", []):
        for s in l["spans"]:
            t = s["text"].strip()
            if t:
                print(f"{s['font'][:22]:24} {s['size']:5.1f} #{s['color']:06x} x={int(s['bbox'][0]):4} | {t[:42]}")
# 图片对象（红头图形?）
imgs = page.get_images()
print(f"第1页图片对象: {len(imgs)}")
# 最后一页：发送/整理人
last = doc[-1]
print("=== 第8页 span ===")
for b in last.get_text("dict")["blocks"]:
    for l in b.get("lines", []):
        for s in l["spans"]:
            t = s["text"].strip()
            if t:
                print(f"{s['font'][:22]:24} {s['size']:5.1f} | {t[:42]}")
