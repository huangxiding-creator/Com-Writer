# -*- coding: utf-8 -*-
"""提取复盘四件套：哈主任修改记录Summary + AI稿261012 + 用户稿261013C + 定稿261020"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from docx import Document

def dump(path, label):
    print(f"\n{'='*72}\n### {label}\n{'='*72}")
    doc = Document(path)
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            print(t)

dump(r"e:\AI-Station\Com-Writer\02-1 总承包事业部\05 成果迭代优化\10-20 哈主任当面修改会议纪要_Summary_2026-10-20 08_04_18.docx", "哈主任修改意见 Summary")
dump(r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\13 AI输出初稿\黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261012.docx", "AI初稿 261012")
dump(r"e:\AI-Station\Com-Writer\02-1 总承包事业部\05 成果迭代优化\黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261013C.docx", "用户初稿 261013C")
dump(r"e:\AI-Station\Com-Writer\02-1 总承包事业部\05 成果迭代优化\黑河黄藏寺水利枢纽工程专题会会议纪要（定稿）261020.docx", "定稿 261020")
