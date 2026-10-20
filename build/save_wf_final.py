# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
# 从 workflow 通知结果落盘（final 字段已由运行时返回）
import re
raw = open(r"C:\Users\91216\AppData\Local\Temp\claude\e--AI-Station\ef6b48f9-becd-4290-9557-772e6fae20d3\tasks\wv6re4g8u.output", encoding="utf-8", errors="replace").read()
m = re.search(r'\{"final":', raw)
if not m:
    # 备选：journal
    jp = r"C:\Users\91216\.claude\projects\e--AI-Station\ef6b48f9-becd-4290-9557-772e6fae20d3\subagents\workflows\wf_b43ac8b5-2dd\journal.jsonl"
    raw = open(jp, encoding="utf-8", errors="replace").read()
    print("from journal, lines:", raw.count(chr(10)))
    sys.exit(0)
data = json.loads(raw[m.start():raw.rindex("}") + 1]) if False else None
# 直接解析整个输出 JSON
try:
    data = json.loads(raw[raw.index("{"):raw.rindex("}")+1])
except Exception as e:
    print("parse err", e); sys.exit(1)
final = data["final"]
json.dump(final, open(r"e:\AI-Station\Com-Writer\build\workflow_final_261012.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("items:", len(final["items"]), "chars:", sum(len(x) for x in final["items"]))
print("placeholders:", len(final["placeholders"]))
