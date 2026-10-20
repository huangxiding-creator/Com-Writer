# -*- coding: utf-8 -*-
"""提取历史初稿→定稿配对文本"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from docx import Document

BASE = r"e:\AI-Station\Com-Writer\02-1 总承包事业部\05 成果迭代优化"
PAIRS = [
    ("20260306 黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）.docx", "20260306 黑河黄藏寺水利枢纽工程专题会会议纪要（定稿）260309.docx", "2603"),
    ("20260622 黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）.docx", "20260622 黑河黄藏寺水利枢纽工程专题会会议纪要（定稿）0624.docx", "2606"),
    ("20260809黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）260810A.docx", "20260809黑河黄藏寺水利枢纽工程专题会会议纪要（定稿）260810B.docx", "2608"),
    ("GCGLB-HZS-004黑河黄藏寺水利枢纽工程专题会会议纪要（锚索扩孔与灌浆）初稿260810.docx", "GCGLB-HZS-004黑河黄藏寺水利枢纽工程专题会会议纪要（锚索扩孔与灌浆）定稿260812.docx", "gcglb4"),
    ("GCGLB-HZS-005黄藏寺930并网发电第1次调度会会议纪要（初稿）260812.docx", "GCGLB-HZS-005黄藏寺930并网发电第1次调度会会议纪要（定稿）260812.docx", "gcglb5"),
]
import os
for src, dst, tag in PAIRS:
    for fn, mark in [(src, "初稿"), (dst, "定稿")]:
        p = os.path.join(BASE, fn)
        print(f"\n{'='*72}\n### {tag} {mark}: {fn}\n{'='*72}")
        try:
            doc = Document(p)
            for para in doc.paragraphs:
                t = para.text.strip()
                if t:
                    print(t)
        except Exception as e:
            print("ERR:", e)
