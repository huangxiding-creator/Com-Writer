# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
jp = r"C:\Users\91216\.claude\projects\e--AI-Station\ef6b48f9-becd-4290-9557-772e6fae20d3\subagents\workflows\wf_b43ac8b5-2dd\journal.jsonl"
lines = [json.loads(l) for l in open(jp, encoding="utf-8", errors="replace") if l.strip()]
print("entries:", len(lines), "types:", {l.get("type") for l in lines})
# 找 fix 代理（label 含 fix）的结果
fix = None
for l in lines:
    lbl = str(l.get("label") or l.get("agentLabel") or "")
    if "fix" in lbl.lower() and l.get("type") == "result":
        fix = l
print("fix found:", bool(fix))
if fix:
    val = fix.get("value") or fix.get("result") or fix.get("output")
    print("value keys:", list(val.keys()) if isinstance(val, dict) else type(val))
    if isinstance(val, dict) and "items" in val:
        json.dump(val, open(r"e:\AI-Station\Com-Writer\build\workflow_final_261012.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("saved. items:", len(val["items"]), "chars:", sum(len(x) for x in val["items"]))
    else:
        # 打印一条 result 的结构以定位字段
        print("sample keys:", list(lines[-1].keys()))
