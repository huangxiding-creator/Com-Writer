# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
jp = r"C:\Users\91216\.claude\projects\e--AI-Station\ef6b48f9-becd-4290-9557-772e6fae20d3\subagents\workflows\wf_b43ac8b5-2dd\journal.jsonl"
lines = [json.loads(l) for l in open(jp, encoding="utf-8", errors="replace") if l.strip()]
for l in lines:
    if l.get("type") == "result":
        keys = [k for k in l.keys() if k not in ("type",)]
        # 概览每条 result 的可识别特征
        v = l.get("result") if "result" in l else l.get("value")
        desc = ""
        if isinstance(v, dict):
            desc = ",".join(list(v.keys())[:6])
        elif isinstance(v, str):
            desc = v[:40].replace("\n", " ")
        print(f"keys={keys} val=[{desc}]")
