"""一次性定稿质量生成 —— 融合所有知识源。

知识来源：
1. 内网全量文章提炼的写作风格参考（24490字）
2. 初稿→定稿差异+定稿范文+参考文档提取的修改模式指南（9600字）
3. 强制数据清单（确保所有技术参数出现）
4. 纯数据注入（补全 LLM 可能遗漏的数字）
5. 确定性后处理（用词替换+结构修复+合规验证）

目标：一次生成即达到定稿水平，无需迭代优化。
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.processors.understander import understand
from src.processors.generator import generate
from src.processors.refiner import _find_missing_data, _inject_missing_data
from src.processors.post_processor import process as post_process, verify as post_verify
from src.readers.docx_reader import read_docx
from src.readers.transcript_parser import parse_transcript
from src.writers.docx_writer import write_minutes
from src.writers.template_engine import analyze_template
from src.core.models import GeneratedContent
from src.utils.logger import get_logger
from src.config.paths import DEFAULT_MINUTES_TEMPLATE, OUTPUT_DIR, REFINE_DIR, RAW_RECORD_DIR

_log = get_logger("scripts.oneshot")

OUTPUT_PATH = OUTPUT_DIR / "meeting_minutes_output.docx"

STYLE_DIR = REFINE_DIR
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"
ANALYSIS_CHECKPOINT = CHECKPOINT_DIR / "phase1_analysis.json"


def main():
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    # ── 加载所有知识源 ──
    _log.info("=" * 60)
    _log.info("加载知识源")
    _log.info("=" * 60)

    # 1. 写作风格参考
    style_ref = _load_text(STYLE_DIR / "writing_style_reference_v2.txt",
                           "细分体裁风格参考")
    # 2. 领导修改模式指南
    revision_guide = _load_text(STYLE_DIR / "revision_guide_definitive.txt",
                                "领导修改模式指南")

    # ── 读取转写稿 ──
    transcript_path = _find_transcript()
    raw_text = read_docx(transcript_path)
    transcript = parse_transcript(raw_text)
    _log.info("转写稿: %d字, %d条发言", transcript.char_count, transcript.entry_count)

    # ── Phase 1: 理解 ──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 1: 理解阶段")
    _log.info("=" * 60)

    analysis = None
    # 优先从 checkpoint 恢复
    if ANALYSIS_CHECKPOINT.exists():
        analysis = _load_checkpoint()
        if analysis:
            _log.info("从 checkpoint 恢复: %d议题, %d数据",
                      len(analysis.topics),
                      sum(len(t.key_data) for t in analysis.topics))

    if analysis is None:
        for attempt in range(1, 4):
            _log.info("理解尝试 %d/3...", attempt)
            try:
                analysis = understand(llm, transcript, prefer_paid=True,
                                      filename_hint=transcript_path.name)
                topics = len(analysis.topics)
                key_data = sum(len(t.key_data) for t in analysis.topics)
                actions = sum(len(t.action_items) for t in analysis.topics)
                _log.info("  结果: %d议题, %d数据, %d行动项", topics, key_data, actions)
                if topics >= 2 and key_data >= 5 and actions >= 3:
                    break
            except Exception as e:
                _log.warning("理解失败: %s", str(e)[:80])

    if analysis is None:
        _log.error("理解阶段失败")
        return

    # 保存 checkpoint
    _save_checkpoint(analysis)

    # ── Phase 2: 一次性生成（注入全部知识）──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 2: 一次性定稿质量生成")
    _log.info("知识源: 风格参考(%d字) + 修改模式(%d字) + 强制数据清单",
              len(style_ref), len(revision_guide))
    _log.info("=" * 60)

    start = time.time()
    content = generate(
        llm, analysis,
        prefer_paid=True,
        style_reference=style_ref,
        revision_guide=revision_guide,
    )
    elapsed = time.time() - start
    _log.info("生成完成 | %d段 | %.0f秒", len(content.content_paragraphs), elapsed)

    # ── Phase 3: 纯数据注入（补全可能遗漏的数字）──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 3: 数据完整性补全")
    _log.info("=" * 60)

    missing = _find_missing_data(analysis, content)
    if missing:
        _log.info("发现 %d 个缺失数据点，执行纯数据注入", len(missing))
        injected = _inject_missing_data(list(content.content_paragraphs), missing)
        content = GeneratedContent(
            title=content.title,
            doc_number=content.doc_number,
            meeting_type=content.meeting_type,
            meeting_topic=content.meeting_topic,
            meeting_date=content.meeting_date,
            meeting_location=content.meeting_location,
            host=content.host,
            participants=content.participants,
            content_paragraphs=tuple(injected),
            compiler=content.compiler,
            model_used=content.model_used,
        )
        # 验证
        still_missing = _find_missing_data(analysis, content)
        if still_missing:
            _log.warning("注入后仍有 %d 个缺失", len(still_missing))
        else:
            _log.info("✅ 所有关键数据已补全")
    else:
        _log.info("✅ 生成文本已包含所有关键数据")

    # ── Phase 4: 确定性后处理（逼近 100% 定稿质量）──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 4: 确定性后处理")
    _log.info("=" * 60)

    pp_result = post_process(content)
    content = pp_result.content
    _log.info("后处理: %d 项修正", pp_result.change_count)

    # ── Phase 5: 合规性验证 ──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 5: 合规性验证")
    _log.info("=" * 60)

    issues = post_verify(content)
    if issues:
        _log.warning("发现 %d 个合规问题:", len(issues))
        for issue in issues:
            _log.warning("  ⚠ %s", issue)
    else:
        _log.info("✅ 全部合规检查通过")

    # ── Phase 6: 输出 Word ──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 6: 输出 Word 文档")
    _log.info("=" * 60)

    template_info = analyze_template(DEFAULT_MINUTES_TEMPLATE)
    final_path = write_minutes(
        content=content,
        template_info=template_info,
        template_path=DEFAULT_MINUTES_TEMPLATE,
        output_path=OUTPUT_PATH,
    )

    _log.info("")
    _log.info("=" * 60)
    _log.info("✅ 一次性定稿质量生成完成！")
    _log.info("输出: %s", final_path)
    _log.info("段落数: %d", len(content.content_paragraphs))
    _log.info("数据完整性: %d/%d",
              sum(len(t.key_data) for t in analysis.topics) - len(_find_missing_data(analysis, content)),
              sum(len(t.key_data) for t in analysis.topics))
    _log.info("=" * 60)

    # 输出文本预览
    _log.info("")
    _log.info("【生成内容预览】")
    for i, para in enumerate(content.content_paragraphs):
        _log.info("  段落%d (%d字): %s...", i + 1, len(para), para[:80])


def _load_text(path: Path, name: str) -> str:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        _log.info("  %s: %d字 (%s)", name, len(text), path.name)
        return text
    _log.warning("  %s: 未找到 (%s)", name, path)
    return ""


def _find_transcript() -> Path:
    # 尝试已知路径
    paths = [
        ROOT / "data/input/transcript.docx",
    ]
    for p in paths:
        if p.exists():
            return p
    # 自动查找
    record_dir = RAW_RECORD_DIR
    docx_files = sorted(record_dir.glob("*.docx"))
    if docx_files:
        return docx_files[-1]
    raise FileNotFoundError("未找到转写稿")


def _load_checkpoint():
    from src.core.models import MeetingAnalysis, Topic, KeyDataPoint, ActionItem
    try:
        data = json.loads(ANALYSIS_CHECKPOINT.read_text(encoding="utf-8"))
        topics = tuple(
            Topic(
                name=t["name"],
                background=t["background"],
                discussion_points=tuple(t["discussion_points"]),
                key_data=tuple(KeyDataPoint(**kp) for kp in t["key_data"]),
                decision=t["decision"],
                action_items=tuple(ActionItem(**ai) for ai in t["action_items"]),
            )
            for t in data["topics"]
        )
        return MeetingAnalysis(
            title=data["title"],
            meeting_type=data["meeting_type"],
            topics=topics,
            participants=tuple(data["participants"]),
            meeting_date=data.get("meeting_date", ""),
            meeting_location=data.get("meeting_location", ""),
            host=data.get("host", ""),
            compiler=data.get("compiler", ""),
        )
    except Exception as e:
        _log.warning("加载 checkpoint 失败: %s", str(e)[:80])
        return None


def _save_checkpoint(analysis) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "title": analysis.title,
        "meeting_type": analysis.meeting_type,
        "meeting_date": analysis.meeting_date,
        "meeting_location": analysis.meeting_location,
        "host": analysis.host,
        "compiler": analysis.compiler,
        "participants": list(analysis.participants),
        "topics": [
            {
                "name": t.name,
                "background": t.background,
                "discussion_points": list(t.discussion_points),
                "key_data": [
                    {"parameter": kp.parameter, "design_value": kp.design_value,
                     "actual_value": kp.actual_value, "remark": kp.remark}
                    for kp in t.key_data
                ],
                "decision": t.decision,
                "action_items": [
                    {"responsible": ai.responsible, "task": ai.task, "deadline": ai.deadline}
                    for ai in t.action_items
                ],
            }
            for t in analysis.topics
        ],
    }
    ANALYSIS_CHECKPOINT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
