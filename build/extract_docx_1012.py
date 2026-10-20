# -*- coding: utf-8 -*-
"""Extract docx content: paragraphs with styles, tables, for template/transcripts/minutes."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from docx import Document

def dump(path, label, max_len=None):
    print(f"\n{'='*70}\n### {label}\n{path}\n{'='*70}")
    doc = Document(path)
    # iterate body elements in order
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    idx_p, idx_t = 0, 0
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            p = Paragraph(child, doc)
            style = p.style.name if p.style else '?'
            text = p.text.strip()
            if text:
                print(f"[P:{style}] {text}")
            idx_p += 1
        elif tag == 'tbl':
            t = Table(child, doc)
            print(f"[TABLE {len(t.rows)}x{len(t.columns)}]")
            for r in t.rows[:60]:
                cells = [c.text.strip().replace('\n','⏎') for c in r.cells]
                print("  | " + " | ".join(cells))
            if len(t.rows) > 60:
                print(f"  ... ({len(t.rows)-60} more rows)")
            idx_t += 1
    print(f"--- total paragraphs={idx_p}, tables={idx_t}")

dump(r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\总包部黑河黄藏寺水利枢纽工程专题会（模板A）.docx", "模板A")
