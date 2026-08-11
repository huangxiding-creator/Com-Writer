"""全量初稿→定稿差异分析 V2 —— 处理所有文档对，积累修改模式。

处理：
- 3对初稿→定稿（20260306, 20260622, 20260809）
- 4份定稿范文（20251219, 20260211, 20260525, 20260602）
- 使用XML提取解决文本框文档的读取问题
"""
import sys
import json
import re
import zipfile
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.readers.docx_reader import read_docx
from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.llm.json_utils import extract_json
from src.utils.logger import get_logger
from src.config.paths import ITERATION_DIR as BASE_DIR, REFINE_DIR as OUTPUT_DIR

_log = get_logger("scripts.revision_v2")


def read_docx_robust(path: Path) -> str:
    """读取docx，支持文本框内的内容。"""
    # 先用常规方法
    text = read_docx(path)
    if len(text) > 50:
        return text
    # 常规方法失败，从XML提取
    try:
        with zipfile.ZipFile(str(path)) as z:
            texts = []
            for fname in z.namelist():
                if fname.endswith('.xml'):
                    content = z.read(fname).decode('utf-8', errors='ignore')
                    t_elems = re.findall(r'<w:t[^>]*>([^<]+)</w:t>', content)
                    if t_elems:
                        texts.extend(t_elems)
            return ''.join(texts)
    except Exception:
        return text


def main():
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    # 分类所有文档
    all_files = sorted(BASE_DIR.glob("*.docx"))
    pairs: list[tuple[str, Path, Path]] = []
    finals_only: list[Path] = []
    reference_docs: list[Path] = []  # 无"初稿"/"定稿"标记的参考文档

    # 匹配初稿→定稿对
    drafts = [f for f in all_files if "初稿" in f.name]
    for draft in drafts:
        prefix = draft.name.split("初稿")[0].strip()
        for f in all_files:
            if ("定稿" in f.name or "最终稿" in f.name) and f.name.split("定稿" if "定稿" in f.name else "最终稿")[0].strip().startswith(prefix[:8]):
                pairs.append((prefix[:8], draft, f))
                break

    # 收集定稿范文（不在配对中的定稿/最终稿）
    paired_finals = {p[2].name for p in pairs}
    for f in all_files:
        if f.name in paired_finals or "初稿" in f.name:
            continue
        if "定稿" in f.name or "最终稿" in f.name:
            finals_only.append(f)
        else:
            reference_docs.append(f)

    _log.info("文档分类完成:")
    _log.info("  初稿→定稿对: %d", len(pairs))
    _log.info("  定稿范文: %d", len(finals_only))
    _log.info("  参考文档: %d", len(reference_docs))

    # ── Phase A: 逐对分析 ──
    all_patterns: list[dict] = []
    all_replacements: list[dict] = []
    all_colloquial: list[dict] = []

    for date_tag, draft_path, final_path in pairs:
        _log.info("\n" + "=" * 60)
        _log.info("分析初稿→定稿对: %s", date_tag)
        _log.info("  初稿: %s", draft_path.name)
        _log.info("  定稿: %s", final_path.name)
        _log.info("=" * 60)

        draft = read_docx_robust(draft_path)
        final = read_docx_robust(final_path)

        _log.info("  初稿: %d字 → 定稿: %d字 (变化: %+d字)",
                  len(draft), len(final), len(final) - len(draft))

        # 本地 diff
        draft_lines = [l for l in draft.split("\n") if l.strip()]
        final_lines = [l for l in final.split("\n") if l.strip()]
        diff = difflib.unified_diff(draft_lines, final_lines, lineterm="")
        changes = len([l for l in diff if l.startswith("+") or l.startswith("-")])
        _log.info("  差异行数: %d", changes)

        # LLM 深度分析
        analysis = _llm_analyze(llm, draft, final, date_tag)

        all_patterns.extend(analysis.get("修改模式", []))
        all_replacements.extend(analysis.get("用词替换表", []))
        all_colloquial.extend(analysis.get("口语化转换规则", []))

    # ── Phase B: 定稿范文 + 参考文档 风格分析 ──
    all_style_docs = list(finals_only) + list(reference_docs)
    _log.info("\n" + "=" * 60)
    _log.info("分析定稿范文+参考文档 (%d份)", len(all_style_docs))
    _log.info("=" * 60)

    final_texts = []
    for f in all_style_docs:
        text = read_docx_robust(f)
        if len(text) > 50:
            final_texts.append(text)
            _log.info("  %s: %d字", f.name, len(text))

    if final_texts:
        # 分批分析（避免 prompt 过长）
        batch_size = 4
        for batch_start in range(0, len(final_texts), batch_size):
            batch = final_texts[batch_start:batch_start + batch_size]
            _log.info("  分析批次 %d-%d / %d", batch_start + 1, min(batch_start + batch_size, len(final_texts)), len(final_texts))
            style_analysis = _llm_analyze_style(llm, batch)
            new_patterns = style_analysis.get("风格模式", [])
            all_patterns.extend(new_patterns)
            _log.info("  → 提取 %d 个风格模式", len(new_patterns))

    # ── Phase C: 去重 + 汇总 ──
    _log.info("\n" + "=" * 60)
    _log.info("汇总去重")
    _log.info("=" * 60)

    # 按模式名称去重，合并同类
    seen_patterns = {}
    for p in all_patterns:
        name = p.get("模式名称", "")
        if name in seen_patterns:
            # 合并示例
            existing = seen_patterns[name]
            existing_examples = existing.get("示例", "")
            new_example = p.get("示例", "")
            if new_example and new_example not in existing_examples:
                existing["示例"] = existing_examples + "；" + new_example
        else:
            seen_patterns[name] = p

    # 按用词去重
    seen_replacements = {}
    for r in all_replacements:
        key = r.get("初稿用词", "") + "→" + r.get("定稿用词", "")
        if key not in seen_replacements:
            seen_replacements[key] = r

    # 按口语去重
    seen_colloquial = {}
    for c in all_colloquial:
        key = c.get("口头说法", "")
        if key not in seen_colloquial:
            seen_colloquial[key] = c

    unique_patterns = list(seen_patterns.values())
    unique_replacements = list(seen_replacements.values())
    unique_colloquial = list(seen_colloquial.values())

    _log.info("修改模式: %d (去重后)", len(unique_patterns))
    _log.info("用词替换: %d (去重后)", len(unique_replacements))
    _log.info("口语转换: %d (去重后)", len(unique_colloquial))

    # ── Phase D: 构建最终指南 ──
    guide = _build_comprehensive_guide(
        unique_patterns, unique_replacements, unique_colloquial,
        num_pairs=len(pairs), num_finals=len(finals_only), num_refs=len(reference_docs),
    )
    guide_path = OUTPUT_DIR / "revision_guide_definitive.txt"
    guide_path.write_text(guide, encoding="utf-8")
    _log.info("\n修改模式指南已更新: %s (%d字)", guide_path, len(guide))

    # 保存 JSON
    json_path = OUTPUT_DIR / "revision_analysis_full.json"
    json_path.write_text(
        json.dumps({
            "修改模式": unique_patterns,
            "用词替换表": unique_replacements,
            "口语化转换": unique_colloquial,
            "分析文档对数": len(pairs),
            "分析范文数": len(final_texts),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log.info("详细分析: %s", json_path)

    # ── 输出统计 ──
    _log.info("\n" + "=" * 60)
    _log.info("✅ 全量分析完成！")
    _log.info("  初稿→定稿对: %d", len(pairs))
    _log.info("  定稿范文: %d", len(final_texts))
    _log.info("  修改模式: %d", len(unique_patterns))
    _log.info("  用词替换: %d", len(unique_replacements))
    _log.info("  口语转换: %d", len(unique_colloquial))
    _log.info("=" * 60)


def _llm_analyze(llm, draft, final, tag):
    system = """你是企业公文写作专家。对比会议纪要初稿和定稿，提取领导修改模式。

要求：
1. 找出所有类型的修改：用词替换、结构重组、内容增删、语气调整、口语化转换
2. 每个模式必须有具体示例和可执行规则
3. 特别注意：录音口头说法→书面正式表述的转换
4. 识别隐喻/口语→具体措施/正式用语的转换

只输出JSON。"""

    user = f"""对比分析以下初稿和定稿，提取所有修改模式。

【初稿{tag}】：
{draft}

【定稿{tag}】：
{final}

输出JSON：
{{
  "修改模式": [
    {{"模式名称": "", "说明": "", "示例": "初稿XXX→定稿XXX", "写作规则": "当遇到XX时，应该XX"}}
  ],
  "用词替换表": [
    {{"初稿用词": "", "定稿用词": "", "替换原因": ""}}
  ],
  "口语化转换规则": [
    {{"口头说法": "", "书面表述": "", "转换说明": ""}}
  ]
}}"""

    raw = llm.chat(system_prompt=system, user_prompt=user,
                   json_mode=True, temperature=0.1, prefer_paid=True, max_tokens=8192)
    return extract_json(raw)


def _llm_analyze_style(llm, final_texts):
    """分析定稿范文的写作风格模式。"""
    system = """你是企业公文写作专家。分析多份定稿会议纪要，提取共同写作风格模式。

关注：
1. 开头综述的写作模式
2. 条目编号方式（一、 / 1. / （1）/ 第一项）
3. 行动项表述方式
4. 问题处理的表述模式
5. 结尾格式

只输出JSON。"""

    combined = "\n\n---\n\n".join(final_texts)
    user = f"""分析以下{len(final_texts)}份定稿会议纪要的共同写作风格。

【定稿合集】：
{combined[:8000]}

输出JSON：
{{
  "风格模式": [
    {{"模式名称": "", "说明": "", "示例": "", "写作规则": "当遇到XX时，应该XX"}}
  ]
}}"""

    raw = llm.chat(system_prompt=system, user_prompt=user,
                   json_mode=True, temperature=0.1, prefer_paid=True, max_tokens=8192)
    return extract_json(raw)


def _build_comprehensive_guide(patterns, replacements, colloquial, num_pairs=0, num_finals=0, num_refs=0):
    parts = []
    parts.append("【领导审稿修改模式指南（基于多份初稿→定稿+定稿范文+参考文档全量分析）】")
    parts.append(f"分析来源：{num_pairs}对初稿→定稿 + {num_finals}份定稿范文 + {num_refs}份参考文档")
    parts.append(f"修改模式：{len(patterns)}条 | 用词替换：{len(replacements)}条 | 口语转换：{len(colloquial)}条")
    parts.append("")

    if patterns:
        parts.append("=" * 50)
        parts.append("【核心修改模式】")
        for i, p in enumerate(patterns):
            name = p.get("模式名称", "")
            desc = p.get("说明", "")
            example = p.get("示例", "")
            rule = p.get("写作规则", "")
            parts.append(f"\n{i+1}. 【{name}】")
            if desc:
                parts.append(f"   {desc}")
            if example:
                parts.append(f"   示例: {example}")
            if rule:
                parts.append(f"   ★规则: {rule}")

    if replacements:
        parts.append(f"\n{'='*50}")
        parts.append("【用词替换表】")
        for r in replacements:
            old = r.get("初稿用词", "")
            new = r.get("定稿用词", "")
            reason = r.get("替换原因", "")
            parts.append(f"  '{old}' → '{new}'（{reason}）")

    if colloquial:
        parts.append(f"\n{'='*50}")
        parts.append("【口语化→书面化转换规则】")
        for c in colloquial:
            spoken = c.get("口头说法", "")
            written = c.get("书面表述", "")
            note = c.get("转换说明", "")
            parts.append(f"  '{spoken}' → '{written}'（{note}）")

    parts.append(f"\n{'='*50}")
    parts.append("【一次性定稿质量生成原则】")
    parts.append("1. 【结构】同类合并为一条，按逻辑递进组织")
    parts.append("2. 【表述】隐喻→具体措施（'举全院之智'→'工程院加大投入，多邀请专家'）")
    parts.append("3. 【人员】先人后机构，姓名在前职务在后")
    parts.append("4. 【责任】必须明确执行部门+具体措施")
    parts.append("5. 【语气】适度留有余地（'尽量'），条件递进（'如解决不了→升级'）")
    parts.append("6. 【用词】精确、务实，禁模糊推诿")
    parts.append("7. 【口语】口头说法必须转为正式书面语")
    parts.append("8. 【数据】所有技术参数必须准确出现")
    parts.append("9. 【专业】泛称→具体技术名（'柔性措施'→'喷洒固结剂'）")
    parts.append("10.【沟通】'多商量多沟通'→'进行技术沟通'")

    return "\n".join(parts)


if __name__ == "__main__":
    main()
