"""会议纪要质量迭代优化器 —— 目标 100 分，最多 30 轮。

策略：
1. 理解阶段：重试直到提取充分（议题≥2, 关键数据≥5, 行动项≥3）
2. 生成阶段：每轮将上一轮质检问题反馈，针对性修正
3. 质检阶段：严格评分，收集问题列表
4. 保留历史最高分版本
5. 达到 100 分或 30 轮后停止
"""
import json
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.processors.understander import understand
from src.processors.generator import generate
from src.processors.quality_gate import review
from src.readers.docx_reader import read_docx
from src.readers.transcript_parser import parse_transcript
from src.writers.docx_writer import write_minutes
from src.writers.template_engine import analyze_template
from src.core.models import GeneratedContent
from src.utils.logger import get_logger
from src.config.paths import DEFAULT_MINUTES_TEMPLATE, OUTPUT_DIR

_log = get_logger("scripts.optimizer")

MAX_ITERATIONS = 30
TARGET_SCORE = 100
OUTPUT_PATH = OUTPUT_DIR / "meeting_minutes_output.docx"

# 理解阶段质量阈值
MIN_TOPICS = 2
MIN_KEY_DATA = 5
MIN_ACTION_ITEMS = 3


def main():
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    # 加载风格参考
    style_ref = ""
    v2_path = ROOT / "02-1 总承包事业部/01 内部写作成果提炼/writing_style_reference_v2.txt"
    if v2_path.exists():
        style_ref = v2_path.read_text(encoding="utf-8")
        _log.info("已加载细分体裁风格参考: %d 字", len(style_ref))

    # 读取转写稿
    transcript_path = ROOT / "data/input/transcript.docx"
    if not transcript_path.exists():
        # 自动查找
        record_dir = ROOT / "02-1 总承包事业部/03 原始记录资料"
        docx_files = sorted(record_dir.glob("*.docx"))
        if docx_files:
            transcript_path = docx_files[-1]

    raw_text = read_docx(transcript_path)
    transcript = parse_transcript(raw_text)
    _log.info("转写稿: %d字, %d条发言, 发言人: %s",
              transcript.char_count, transcript.entry_count,
              ", ".join(transcript.speakers))

    # ── Phase 1: 理解阶段（重试直到质量达标）──────────────
    _log.info("=" * 60)
    _log.info("Phase 1: 理解阶段优化")
    _log.info("=" * 60)

    best_analysis = None
    for attempt in range(1, 6):
        _log.info("理解尝试 %d/5...", attempt)
        try:
            analysis = understand(llm, transcript, prefer_paid=True,
                                  filename_hint=transcript_path.name)
            topics = len(analysis.topics)
            key_data = sum(len(t.key_data) for t in analysis.topics)
            actions = sum(len(t.action_items) for t in analysis.topics)
            _log.info("  结果: %d议题, %d关键数据, %d行动项", topics, key_data, actions)

            if best_analysis is None or (topics + key_data + actions) > (
                len(best_analysis.topics) +
                sum(len(t.key_data) for t in best_analysis.topics) +
                sum(len(t.action_items) for t in best_analysis.topics)
            ):
                best_analysis = analysis

            if topics >= MIN_TOPICS and key_data >= MIN_KEY_DATA and actions >= MIN_ACTION_ITEMS:
                _log.info("✅ 理解质量达标")
                break
        except Exception as e:
            _log.warning("理解尝试 %d 失败: %s", attempt, str(e)[:80])

    if best_analysis is None:
        _log.error("理解阶段完全失败")
        return

    analysis = best_analysis
    _log.info("理解阶段最终结果: %d议题, %d关键数据, %d行动项",
              len(analysis.topics),
              sum(len(t.key_data) for t in analysis.topics),
              sum(len(t.action_items) for t in analysis.topics))

    # ── Phase 2: 生成-质控迭代 ──────────────────────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 2: 生成-质控迭代优化 (最多 %d 轮, 目标 %d 分)" % (MAX_ITERATIONS, TARGET_SCORE))
    _log.info("=" * 60)

    best_content = None
    best_score = 0
    best_report = None
    history: list[dict] = []
    accumulated_feedback = ""

    for iteration in range(1, MAX_ITERATIONS + 1):
        iter_start = time.time()
        _log.info("")
        _log.info("─" * 40)
        _log.info("第 %d/%d 轮迭代", iteration, MAX_ITERATIONS)
        _log.info("─" * 40)

        # 构建本轮的风格参考（含历史反馈）
        style_this_round = style_ref
        if accumulated_feedback:
            style_this_round = (
                style_ref +
                f"\n\n★★★ 历史迭代反馈（请针对性修正以下问题）★★★\n{accumulated_feedback}"
            )

        # Step A: 生成（注入历史反馈）
        try:
            content = generate(llm, analysis, prefer_paid=True,
                               style_reference=style_this_round)
        except Exception as e:
            _log.warning("生成失败: %s", str(e)[:100])
            continue

        # Step B: 质检
        try:
            report = review(llm, analysis, content, prefer_paid=True)
        except Exception as e:
            _log.warning("质检失败: %s", str(e)[:100])
            continue

        score = report.score
        issues = list(report.issues)
        elapsed = time.time() - iter_start

        _log.info("第 %d 轮结果: 评分 %d/100 | %d 个问题 | %.0f秒",
                  iteration, score, len(issues), elapsed)

        if issues:
            for i, issue in enumerate(issues[:5]):
                _log.info("  问题%d: %s", i + 1, issue[:120])

        # 记录历史
        history.append({
            "轮次": iteration,
            "评分": score,
            "问题数": len(issues),
            "问题": [str(i)[:100] for i in issues[:5]],
        })

        # 更新最佳
        if score > best_score:
            best_score = score
            best_content = content
            best_report = report
            _log.info("🎉 新最高分: %d/100", best_score)

        # 达标检查
        if score >= TARGET_SCORE:
            _log.info("🏆 达到目标分数 %d！停止迭代", TARGET_SCORE)
            break

        # Step C: 构建下一轮反馈
        feedback_parts = []
        if issues:
            feedback_parts.append(f"第{iteration}轮评分{score}分，需要修正以下问题:")
            for issue in issues:
                feedback_parts.append(f"  - {issue}")

        # 如果质检给出了修正段落，用修正后的内容作为下轮参考
        if report.revised_paragraphs:
            feedback_parts.append("\n质检修正版本（在此基础上继续提升）:")
            for i, p in enumerate(report.revised_paragraphs):
                feedback_parts.append(f"  段落{i+1}: {p[:300]}...")
            # 更新当前 content 为修正版本（供下轮在修正基础上继续优化）
            content = GeneratedContent(
                title=content.title,
                doc_number=content.doc_number,
                meeting_type=content.meeting_type,
                meeting_topic=content.meeting_topic,
                meeting_date=content.meeting_date,
                meeting_location=content.meeting_location,
                host=content.host,
                participants=content.participants,
                content_paragraphs=report.revised_paragraphs,
                compiler=content.compiler,
                model_used=content.model_used,
            )

        new_feedback = "\n".join(feedback_parts)
        if new_feedback:
            accumulated_feedback = new_feedback + "\n\n" + accumulated_feedback
            if len(accumulated_feedback) > 8000:
                accumulated_feedback = accumulated_feedback[:8000]

    # ── Phase 3: 输出最佳版本 ──────────────────────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 3: 输出最佳版本")
    _log.info("=" * 60)

    if best_content is None:
        _log.error("所有迭代均失败")
        return

    # 使用修正段落的最终版本
    if best_report and best_report.revised_paragraphs and best_report.score >= 80:
        final_content = GeneratedContent(
            title=best_content.title,
            doc_number=best_content.doc_number,
            meeting_type=best_content.meeting_type,
            meeting_topic=best_content.meeting_topic,
            meeting_date=best_content.meeting_date,
            meeting_location=best_content.meeting_location,
            host=best_content.host,
            participants=best_content.participants,
            content_paragraphs=best_report.revised_paragraphs,
            compiler=best_content.compiler,
            model_used=best_content.model_used,
        )
    else:
        final_content = best_content

    # 写 Word
    template_info = analyze_template(DEFAULT_MINUTES_TEMPLATE)
    final_path = write_minutes(
        content=final_content,
        template_info=template_info,
        template_path=DEFAULT_MINUTES_TEMPLATE,
        output_path=OUTPUT_PATH,
    )

    _log.info("")
    _log.info("=" * 60)
    _log.info("✅ 迭代优化完成！")
    _log.info("总轮数: %d", len(history))
    _log.info("最终评分: %d/100", best_score)
    _log.info("输出: %s", final_path)
    _log.info("")
    _log.info("评分历史:")
    for h in history:
        _log.info("  第%2d轮: %3d分 (%d个问题)", h["轮次"], h["评分"], h["问题数"])
    _log.info("=" * 60)

    # 保存历史记录
    history_path = OUTPUT_DIR / "optimization_history.json"
    history_path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
