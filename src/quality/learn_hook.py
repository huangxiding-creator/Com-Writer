"""L6 修改学习钩子 —— 定稿归档后自动 diff 初稿，提取新规则入库。

用法（管线外触发，如用户把人工修改后的定稿放回目录后）：
    python -m src.quality.learn_hook            # 扫描全部新增对
    python -m src.quality.learn_hook <初稿> <定稿>  # 指定一对

学习产物（追加，不覆盖）：
- 01 内部写作成果提炼/revision_guide_definitive.txt（追加新模式）
- 01 内部写作成果提炼/learned_rules.jsonl（机器可读规则流）
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from ..config.paths import REFINE_DIR, ITERATION_DIR
from ..utils.logger import get_logger

_log = get_logger("quality.learn_hook")

LEARNED_RULES_JSONL = REFINE_DIR / "learned_rules.jsonl"
GUIDE_PATH = REFINE_DIR / "revision_guide_definitive.txt"


def learn_pair(draft_text: str, final_text: str, source_name: str = "") -> list[dict]:
    """对一对初稿/定稿做 diff，提取可复用规则。

    提取策略（确定性，零 API 成本）：
    1. 词级替换：初稿短语 → 定稿短语（长度≥2，出现频次高）
    2. 删除模式：初稿有、定稿无的口语/冗余片段
    """
    rules: list[dict] = []
    sm = difflib.SequenceMatcher(None, draft_text, final_text)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        old = draft_text[i1:i2].strip()
        new = final_text[j1:j2].strip()
        # 过滤噪音：太短无意义，太长可能是结构重排
        if not (2 <= len(old) <= 30 and 1 <= len(new) <= 40):
            continue
        # 跳过纯数字变化（可能是数据更新，不是写作模式）
        if re.fullmatch(r"[\d.%]+", old) and re.fullmatch(r"[\d.%]+", new):
            continue
        # 跳过包含换行的
        if "\n" in old or "\n" in new:
            continue

        kind = _classify(old, new)
        rules.append({
            "type": kind,
            "old": old,
            "new": new,
            "source": source_name,
            "learned_at": datetime.now().isoformat(timespec="seconds"),
        })

    return rules


def _classify(old: str, new: str) -> str:
    """分类修改类型。"""
    if len(new) < len(old) * 0.7:
        return "精简"
    if len(new) > len(old) * 1.3:
        return "补充"
    return "替换"


def persist_rules(rules: list[dict]) -> int:
    """规则持久化：JSONL 追加 + 指南追加。"""
    if not rules:
        return 0

    # 去重：老规则里已有的跳过
    existing: set[tuple[str, str]] = set()
    if LEARNED_RULES_JSONL.exists():
        for line in LEARNED_RULES_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                existing.add((r["old"], r["new"]))
            except Exception:
                continue

    fresh = [r for r in rules if (r["old"], r["new"]) not in existing]
    if not fresh:
        _log.info("全部 %d 条规则已存在，跳过", len(rules))
        return 0

    # JSONL 追加
    with LEARNED_RULES_JSONL.open("a", encoding="utf-8") as fp:
        for r in fresh:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 指南追加（人类可读）
    if REFINE_DIR.exists():
        block = [f"\n\n═══ {datetime.now():%Y-%m-%d} 自动学习新增规则（{len(fresh)}条）═══"]
        for r in fresh:
            if r["type"] == "精简" and not r["new"]:
                block.append(f"- 删除「{r['old']}」")
            else:
                block.append(f"- 「{r['old']}」→「{r['new']}」（{r['type']}）")
        GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GUIDE_PATH.open("a", encoding="utf-8") as fp:
            fp.write("\n".join(block))

    _log.info("L6 学习完成: 新增 %d 条规则 → %s", len(fresh), LEARNED_RULES_JSONL.name)
    return len(fresh)


def scan_iteration_dir() -> list[dict]:
    """扫描 05 成果迭代优化 目录，配对新出现的初稿/定稿对。"""
    pairs: list[tuple[Path, Path]] = []
    if not ITERATION_DIR.exists():
        return []

    for sub in sorted(ITERATION_DIR.iterdir()):
        if not sub.is_dir():
            continue
        files = sorted(sub.glob("*.docx"))
        drafts = [f for f in files if "初稿" in f.name]
        finals = [f for f in files if any(k in f.name for k in ("定稿", "终稿", "修改稿"))]
        if drafts and finals:
            pairs.append((drafts[0], finals[-1]))  # 取最新定稿

    all_rules: list[dict] = []
    from ..pipeline.auto_pipeline import _read_docx_text
    for draft_path, final_path in pairs:
        draft = _read_docx_text(draft_path)
        final = _read_docx_text(final_path)
        if not draft or not final:
            continue
        rules = learn_pair(draft, final, source_name=final_path.name)
        all_rules.extend(rules)
        _log.info("配对学习: %s vs %s → %d 条候选规则",
                  draft_path.name, final_path.name, len(rules))
    return all_rules


def main() -> None:
    if len(sys.argv) >= 3:
        # 指定文件模式
        draft = Path(sys.argv[1]).read_text(encoding="utf-8")
        final = Path(sys.argv[2]).read_text(encoding="utf-8")
        rules = learn_pair(draft, final, source_name=Path(sys.argv[2]).name)
    else:
        rules = scan_iteration_dir()
    count = persist_rules(rules)
    print(f"学习完成: 提取 {len(rules)} 条候选，新增入库 {count} 条")


if __name__ == "__main__":
    main()
