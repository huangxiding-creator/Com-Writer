# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from docx import Document
from docx.shared import Pt
doc = Document(r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\总包部黑河黄藏寺水利枢纽工程专题会（模板A）.docx")
# section info
s = doc.sections[0]
print(f"page: {s.page_width}, {s.page_height}; margins L{s.left_margin} R{s.right_margin} T{s.top_margin} B{s.bottom_margin}")
for i, p in enumerate(doc.paragraphs):
    pf = p.paragraph_format
    runs = [(r.text[:20], r.font.name, r.font.size.pt if r.font.size else None,
             r.font.bold, r.font.element.rPr.rFonts.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia') if r.font.element.rPr is not None and r.font.element.rPr.rFonts is not None else None)
            for r in p.runs if r.text.strip()]
    print(f"[{i}] style={p.style.name} align={pf.alignment} indent_first={pf.first_line_indent} line={pf.line_spacing} | {runs[:3]}")
