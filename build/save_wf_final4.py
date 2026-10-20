# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
jp = r"C:\Users\91216\.claude\projects\e--AI-Station\ef6b48f9-becd-4290-9557-772e6fae20d3\subagents\workflows\wf_b43ac8b5-2dd\journal.jsonl"
lines = [json.loads(l) for l in open(jp, encoding="utf-8", errors="replace") if l.strip()]
results = [l for l in lines if l.get("type") == "result"]
final = results[-1]["result"]
assert "items" in final
json.dump(final, open(r"e:\AI-Station\Com-Writer\build\workflow_final_261012.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved. items:", len(final["items"]), "chars:", sum(len(x) for x in final["items"]), "placeholders:", len(final.get("placeholders", [])))
