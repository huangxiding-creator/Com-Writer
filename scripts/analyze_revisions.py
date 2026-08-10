"""初稿→定稿差异深度分析 —— 提取领导修改模式。

用户要求："认真揣摩这些材料的变化，最终一定要实现一次性完成跟定稿材料一模一样的高质量成果"

分析维度：
1. 结构变化：条目合并/拆分/重排
2. 用词调整：精确化、规范化、去口语化
3. 内容增删：信息补充、冗余删除
4. 语气调整：命令→协商、绝对→条件递进
5. 责任明确化：补充执行部门/人员
"""
import sys
import json
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.readers.docx_reader import read_docx
from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.llm.json_utils import extract_json
from src.utils.logger import get_logger

_log = get_logger("scripts.revision_analysis")

BASE_DIR = ROOT / "02-1 总承包事业部" / "05 成果迭代优化"
OUTPUT_DIR = ROOT / "02-1 总承包事业部" / "01 内部写作成果提炼"


def main():
    # 读取初稿和定稿
    drafts = sorted(BASE_DIR.glob("*初稿*.docx"))
    finals = sorted(BASE_DIR.glob("*定稿*.docx"))

    if not drafts or not finals:
        _log.error("未找到初稿或定稿文件")
        return

    draft_text = read_docx(drafts[0])
    final_text = read_docx(finals[0])

    _log.info("初稿: %s (%d字)", drafts[0].name, len(draft_text))
    _log.info("定稿: %s (%d字)", finals[0].name, len(final_text))

    # 逐行 diff
    draft_lines = draft_text.split("\n")
    final_lines = final_text.split("\n")

    _log.info("\n" + "=" * 60)
    _log.info("差异分析（unified diff）")
    _log.info("=" * 60)

    diff = list(difflib.unified_diff(
        draft_lines, final_lines,
        fromfile="初稿", tofile="定稿",
        lineterm=""
    ))

    changes: list[dict] = []
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            changes.append({"type": "增加", "content": line[1:]})
            _log.info("  [+] %s", line[1:120])
        elif line.startswith("-") and not line.startswith("---"):
            changes.append({"type": "删除", "content": line[1:]})
            _log.info("  [-] %s", line[1:120])

    # 使用 LLM 进行深度分析
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    _log.info("\n" + "=" * 60)
    _log.info("LLM 深度差异分析")
    _log.info("=" * 60)

    analysis = _llm_analyze_revisions(llm, draft_text, final_text)

    # 输出分析结果
    _log.info("\n" + "=" * 60)
    _log.info("领导修改模式总结")
    _log.info("=" * 60)

    patterns = analysis.get("修改模式", [])
    for i, pattern in enumerate(patterns):
        _log.info("\n模式%d: %s", i + 1, pattern.get("模式名称", ""))
        _log.info("  说明: %s", pattern.get("说明", ""))
        _log.info("  示例: %s", pattern.get("示例", ""))
        _log.info("  规则: %s", pattern.get("写作规则", ""))

    # 生成修改指南
    guide = _build_revision_guide(analysis)
    guide_path = OUTPUT_DIR / "revision_guide.txt"
    guide_path.write_text(guide, encoding="utf-8")
    _log.info("\n修改指南已保存: %s (%d字)", guide_path, len(guide))

    # 保存 JSON 分析结果
    json_path = OUTPUT_DIR / "revision_analysis.json"
    json_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log.info("详细分析: %s", json_path)


def _llm_analyze_revisions(llm: MultiLLMClient, draft: str, final: str) -> dict:
    """使用 LLM 深度分析初稿→定稿的修改模式。"""
    system_prompt = """你是一位企业公文写作专家，擅长分析领导对公文的修改意图和模式。

你的任务是对比初稿和定稿，提取出所有可复用的"修改模式"——即领导在审稿时反复使用的修改原则和方法。

分析维度：
1. **结构整合**：哪些条目被合并了？为什么？（同一主题应放在一起）
2. **用词调整**：哪些词语被替换了？（口语化→书面化、模糊→精确、绝对→适度）
3. **内容增删**：什么内容被新增了？什么被删除了？为什么？
4. **语气调整**：命令语气→条件递进？推诿语言→担当表述？
5. **责任明确化**：哪些地方补充了执行部门/人员/时限？
6. **口语化转换**：录音口头说法→纸面正式表述的转换规则

输出要求：
- 每个修改模式必须有：模式名称、说明、具体示例、可执行的写作规则
- 写作规则必须是"当遇到XX情况时，应该XX"的格式
- 确保这些规则可以直接注入到AI写作系统中

只输出JSON。"""

    user_prompt = f"""请对比分析以下会议纪要的初稿和定稿，提取所有修改模式。

【初稿】：
{draft}

【定稿】：
{final}

请输出如下JSON：
{{
  "总体评价": "一句话总结初稿到定稿的核心变化方向",
  "条目变化": [
    {{
      "初稿条目": "条目编号和主题",
      "定稿处理": "保留/合并/删除/改写",
      "变化说明": "具体改了什么"
    }}
  ],
  "修改模式": [
    {{
      "模式名称": "如：同类合并",
      "说明": "这个模式是什么",
      "示例": "初稿XXX → 定稿XXX",
      "写作规则": "当遇到XX时，应该XX"
    }}
  ],
  "用词替换表": [
    {{
      "初稿用词": "XXX",
      "定稿用词": "XXX",
      "替换原因": "XXX"
    }}
  ],
  "口语化转换规则": [
    {{
      "口头说法": "XXX",
      "书面表述": "XXX",
      "转换说明": "XXX"
    }}
  ]
}}"""

    raw = llm.chat(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        json_mode=True,
        temperature=0.1,
        prefer_paid=True,
        max_tokens=8192,
    )

    return extract_json(raw)


def _build_revision_guide(analysis: dict) -> str:
    """将分析结果构建为可注入生成器的修改指南。"""
    parts: list[str] = []

    parts.append("【领导审稿修改模式指南（基于初稿→定稿差异分析）】")
    parts.append("以下规则提取自实际审稿修改，在生成会议纪要时必须遵循。\n")

    # 总体评价
    overall = analysis.get("总体评价", "")
    if overall:
        parts.append(f"总体方向：{overall}\n")

    # 修改模式
    patterns = analysis.get("修改模式", [])
    if patterns:
        parts.append("=" * 40)
        parts.append("【核心修改模式】")
        for i, p in enumerate(patterns):
            name = p.get("模式名称", "")
            desc = p.get("说明", "")
            example = p.get("示例", "")
            rule = p.get("写作规则", "")
            parts.append(f"\n{i+1}. {name}")
            parts.append(f"   说明: {desc}")
            parts.append(f"   示例: {example}")
            parts.append(f"   ★规则: {rule}")

    # 用词替换表
    replacements = analysis.get("用词替换表", [])
    if replacements:
        parts.append(f"\n{'='*40}")
        parts.append("【用词替换规则】")
        for r in replacements:
            old = r.get("初稿用词", "")
            new = r.get("定稿用词", "")
            reason = r.get("替换原因", "")
            parts.append(f"  {old} → {new}（{reason}）")

    # 口语化转换
    colloquial = analysis.get("口语化转换规则", [])
    if colloquial:
        parts.append(f"\n{'='*40}")
        parts.append("【口语化→书面化转换规则】")
        for c in colloquial:
            spoken = c.get("口头说法", "")
            written = c.get("书面表述", "")
            note = c.get("转换说明", "")
            parts.append(f"  '{spoken}' → '{written}'（{note}）")

    # 条目变化
    items = analysis.get("条目变化", [])
    if items:
        parts.append(f"\n{'='*40}")
        parts.append("【条目变化分析】")
        for item in items:
            draft_item = item.get("初稿条目", "")
            handling = item.get("定稿处理", "")
            note = item.get("变化说明", "")
            parts.append(f"  初稿[{draft_item}] → 定稿[{handling}]: {note}")

    return "\n".join(parts)


if __name__ == "__main__":
    main()
