# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import win32com.client as wc
docx = r"e:\AI-Station\Com-Writer\02-2 黄藏寺EPC项目\13 AI输出初稿\黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261012.docx"
pdf = docx[:-5] + ".pdf"
word = wc.DispatchEx("Word.Application")
word.Visible = False
try:
    d = word.Documents.Open(docx, ReadOnly=True)
    d.SaveAs2(pdf, FileFormat=17)
    pages = d.ComputeStatistics(2)  # wdStatisticPages
    d.Close(False)
    print(f"PDF saved: {pdf}")
    print(f"pages: {pages}, size: {os.path.getsize(pdf)}")
finally:
    word.Quit()
