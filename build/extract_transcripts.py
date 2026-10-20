# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from docx import Document

for path, label in [
    (r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\10 原始记录稿\左岸边坡施工与厂房安装协调会_Transcript_2026-10-12 08_30_40.docx", "会议1 08:30 左岸边坡施工与厂房安装协调会"),
    (r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\10 原始记录稿\左岸边坡锚索方案与现场管理会议_Transcript_2026-10-12 10_24_19.docx", "会议2 10:24 左岸边坡锚索方案与现场管理会议"),
]:
    print(f"\n{'='*70}\n### {label}\n{'='*70}")
    doc = Document(path)
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            print(t)
