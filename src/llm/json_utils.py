"""JSON 提取与容错 —— 从 LLM 输出中稳健提取 JSON。

借鉴 We-AIPO extract_json()：去除 markdown 围栏，正则提取最外层 {}。
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> Any:
    """从 LLM 输出文本中提取 JSON。

    策略：
    1. 去除 ```json / ``` 围栏
    2. 尝试直接 json.loads
    3. 正则提取最外层 {} 或 []
    4. 失败则抛出 ValueError
    """
    if not text or not text.strip():
        raise ValueError("空文本，无法提取 JSON")

    cleaned = text.strip()

    # 去除 markdown 围栏
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
    fence_match = fence_pattern.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # 直接尝试
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 提取最外层 {}
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            fragment = cleaned[start : end + 1]
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                continue

    raise ValueError(f"无法从文本中提取有效 JSON（前200字: {cleaned[:200]}）")


def safe_json_extract(text: str, default: Any = None) -> Any:
    """安全提取 JSON，失败返回默认值。"""
    try:
        return extract_json(text)
    except (ValueError, json.JSONDecodeError):
        return default
