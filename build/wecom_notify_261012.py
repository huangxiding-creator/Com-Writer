# -*- coding: utf-8 -*-
"""会议纪要完成通知 → 企业微信群机器人（读 config/wecom.secret.ini，直连不走代理）。"""
import sys, io, os, json, configparser
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests

cfg = configparser.ConfigParser()
cfg.read(r"e:\AI-Station\config\wecom.secret.ini", encoding="utf-8")
webhook = cfg.get("wecom", "webhook", fallback="").strip()
if not webhook:
    raise SystemExit("no webhook configured")

content = (
    "**【黄藏寺专题会会议纪要已完成】**\n"
    "> 时间：2026年10月12日上午（两场：左岸边坡施工与厂房安装协调会、锚索方案与现场管理会）\n"
    "> 主持：哈佳（总包部主任）；齐三红（总包部副主任）参会提意见\n\n"
    "纪要共14条，已重点反映哈佳、齐三红意见：\n"
    "• 哈佳：锚索试验限期出成果、安能约谈本周内完成、结算以实测地形图为准、现场管控要点表格化、3号机组按周调度\n"
    "• 齐三红：锚固段约6米不小于5.5倍孔径、不能一味加长；超量认可与减量并举（“优化不能全是增加钱”）\n\n"
    "成果文件（Word 8页 + PDF预览）：\n"
    "13 AI输出初稿/黑河黄藏寺水利枢纽工程专题会会议纪要（初稿）261012.docx\n"
    "模板A版式，待审核定稿编号。"
)
sess = requests.Session()
sess.trust_env = False  # 企微API绝不走代理（SOP红线）
r = sess.post(webhook, json={"msgtype": "markdown", "markdown": {"content": content}}, timeout=10)
print("HTTP", r.status_code, r.json())
