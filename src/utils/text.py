"""文本处理工具 —— 清洗、截断、统计。"""
from __future__ import annotations


def clean_text(text: str) -> str:
    """清洗文本：去除多余空白，保留段落结构。"""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def truncate(text: str, max_chars: int = 50000, suffix: str = "\n\n[...内容过长，已截断...]") -> str:
    """安全截断文本，保留前 max_chars 个字符。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + suffix


def count_chars(text: str) -> int:
    """统计有效字符数（非空白）。"""
    return len(text.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", ""))


def extract_numbers(text: str) -> list[str]:
    """从文本中提取所有数字（含小数）。"""
    import re
    return re.findall(r"\d+\.?\d*", text)
