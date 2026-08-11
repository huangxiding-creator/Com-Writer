"""会议纪要质量迭代优化器 V2 —— 基于 Self-Refine + LLM-as-a-Judge 最佳实践。

V2 核心改进（相比 V1）：
1. 多维度 Rubric 评分：5维×20分，精准定位扣分维度
2. Self-Refine 三段式流程：
   - INIT (第1轮)：完整生成
   - FEEDBACK：多维度 Rubric 审查
   - ITERATE (第2轮+)：针对性修改，非重新生成
3. 收敛检测：连续3轮无提升 → 切换策略或停止
4. 策略切换：停滞时用不同的 temperature 和提示策略突破瓶颈

策略流程：
Phase 1: 理解优化（重试直到数据充分）
Phase 2: INIT 生成 → Rubric 审查 → ITERATE 修正 循环
Phase 3: 输出最佳版本到 Word
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.processors.understander import understand
from src.processors.generator import generate
from src.processors.refiner import refine, _find_missing_data, _inject_missing_data
from src.processors.quality_gate_v2 import review_rubric, RubricReport
from src.readers.docx_reader import read_docx
from src.readers.transcript_parser import parse_transcript
from src.writers.docx_writer import write_minutes
from src.writers.template_engine import analyze_template
from src.core.models import GeneratedContent, MeetingAnalysis, Topic, KeyDataPoint, ActionItem
from src.utils.logger import get_logger
from src.config.paths import DEFAULT_MINUTES_TEMPLATE, OUTPUT_DIR, REFINE_DIR, RAW_RECORD_DIR

_log = get_logger("scripts.optimizer_v2")

MAX_ITERATIONS = 30
TARGET_SCORE = 100
OUTPUT_PATH = OUTPUT_DIR / "meeting_minutes_output.docx"

# 理解阶段质量阈值
MIN_TOPICS = 2
MIN_KEY_DATA = 5
MIN_ACTION_ITEMS = 3

# 收敛检测
CONVERGENCE_PATIENCE = 3  # 连续N轮无提升则触发策略切换

# Checkpoint 路径
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"
ANALYSIS_CHECKPOINT = CHECKPOINT_DIR / "phase1_analysis.json"


def _count_below_max(rubric: RubricReport) -> int:
    """统计未达满分的维度数。"""
    return sum(1 for v in rubric.dimension_scores.values() if v < 20)


def _save_analysis(analysis: MeetingAnalysis) -> None:
    """保存 Phase 1 理解结果到 checkpoint（避免重跑）。"""
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
    _log.info("理解结果已保存 checkpoint: %s", ANALYSIS_CHECKPOINT)


def _load_analysis() -> MeetingAnalysis | None:
    """从 checkpoint 加载 Phase 1 理解结果。"""
    if not ANALYSIS_CHECKPOINT.exists():
        return None
    try:
        data = json.loads(ANALYSIS_CHECKPOINT.read_text(encoding="utf-8"))
        topics = tuple(
            Topic(
                name=t["name"],
                background=t["background"],
                discussion_points=tuple(t["discussion_points"]),
                key_data=tuple(
                    KeyDataPoint(**kp) for kp in t["key_data"]
                ),
                decision=t["decision"],
                action_items=tuple(
                    ActionItem(**ai) for ai in t["action_items"]
                ),
            )
            for t in data["topics"]
        )
        analysis = MeetingAnalysis(
            title=data["title"],
            meeting_type=data["meeting_type"],
            topics=topics,
            participants=tuple(data["participants"]),
            meeting_date=data.get("meeting_date", ""),
            meeting_location=data.get("meeting_location", ""),
            host=data.get("host", ""),
            compiler=data.get("compiler", ""),
        )
        _log.info("从 checkpoint 恢复理解结果: %d议题, %d数据, %d行动项",
                  len(analysis.topics),
                  sum(len(t.key_data) for t in analysis.topics),
                  sum(len(t.action_items) for t in analysis.topics))
        return analysis
    except Exception as e:
        _log.warning("加载 checkpoint 失败: %s", str(e)[:80])
        return None


def main():
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    # 加载风格参考
    style_ref = ""
    v2_path = REFINE_DIR / "writing_style_reference_v2.txt"
    if v2_path.exists():
        style_ref = v2_path.read_text(encoding="utf-8")
        _log.info("已加载细分体裁风格参考: %d 字", len(style_ref))
    else:
        v1_path = REFINE_DIR / "writing_style_reference.txt"
        if v1_path.exists():
            style_ref = v1_path.read_text(encoding="utf-8")
            _log.info("已加载风格参考v1: %d 字", len(style_ref))

    # 加载领导审稿修改模式指南
    revision_guide = ""
    guide_path = REFINE_DIR / "revision_guide_definitive.txt"
    if guide_path.exists():
        revision_guide = guide_path.read_text(encoding="utf-8")
        _log.info("已加载领导修改模式指南: %d 字", len(revision_guide))

    # 读取转写稿
    transcript_path = ROOT / "data/input/transcript.docx"
    if not transcript_path.exists():
        record_dir = RAW_RECORD_DIR
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

    # 优先从 checkpoint 恢复（避免重复调用 API）
    best_analysis = _load_analysis()

    # 如果 checkpoint 已满足质量要求，跳过理解阶段
    if best_analysis:
        _topics = len(best_analysis.topics)
        _data = sum(len(t.key_data) for t in best_analysis.topics)
        _actions = sum(len(t.action_items) for t in best_analysis.topics)
        if _topics >= MIN_TOPICS and _data >= MIN_KEY_DATA and _actions >= MIN_ACTION_ITEMS:
            _log.info("✅ Checkpoint 质量达标 (%d议题/%d数据/%d行动项)，跳过理解阶段",
                      _topics, _data, _actions)
        else:
            _log.info("Checkpoint 质量不足 (%d/%d/%d)，需重新理解",
                      _topics, _data, _actions)
            best_analysis = None

    if best_analysis is None:
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

    # 始终保存 checkpoint（更新为最新最佳）
    _save_analysis(analysis)
    _log.info("理解阶段最终: %d议题, %d关键数据, %d行动项",
              len(analysis.topics),
              sum(len(t.key_data) for t in analysis.topics),
              sum(len(t.action_items) for t in analysis.topics))

    # ── Phase 2: INIT → FEEDBACK → ITERATE 循环 ──────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 2: Self-Refine 迭代优化 (最多 %d 轮, 目标 %d 分)" % (MAX_ITERATIONS, TARGET_SCORE))
    _log.info("策略: 第1轮完整生成(INIT) → 后续轮针对性修改(ITERATE)")
    _log.info("=" * 60)

    best_content = None
    best_score = 0
    best_rubric = None
    history: list[dict] = []
    no_improve_count = 0
    current_content = None
    current_rubric = None

    for iteration in range(1, MAX_ITERATIONS + 1):
        iter_start = time.time()
        _log.info("")
        _log.info("─" * 40)
        _log.info("第 %d/%d 轮 | 策略: %s",
                  iteration, MAX_ITERATIONS,
                  "INIT(完整生成)" if iteration == 1 else "ITERATE(针对性修改)")
        _log.info("─" * 40)

        try:
            if iteration == 1:
                # ★ INIT: 完整生成（Self-Refine 第一步）
                content = generate(llm, analysis, prefer_paid=True,
                                   style_reference=style_ref,
                                   revision_guide=revision_guide)
            else:
                # ★ 检查是否可以使用纯数据注入模式（避免 LLM 振荡）
                inject_base = best_content if best_content else content
                inject_rubric = best_rubric if best_rubric else current_rubric
                use_inject_only = (
                    inject_rubric
                    and inject_rubric.total_score >= 80
                    and _count_below_max(inject_rubric) == 1
                    and inject_rubric.dimension_scores.get("D1_数据准确性", 20) < 20
                )

                if use_inject_only:
                    # ★ 纯数据注入模式：不调用 LLM，直接程序化注入缺失数据
                    _log.info("⚡ 纯数据注入模式（避免LLM振荡）")
                    missing = _find_missing_data(analysis, inject_base)
                    if missing:
                        injected_paragraphs = _inject_missing_data(
                            list(inject_base.content_paragraphs), missing
                        )
                        content = GeneratedContent(
                            title=inject_base.title,
                            doc_number=inject_base.doc_number,
                            meeting_type=inject_base.meeting_type,
                            meeting_topic=inject_base.meeting_topic,
                            meeting_date=inject_base.meeting_date,
                            meeting_location=inject_base.meeting_location,
                            host=inject_base.host,
                            participants=inject_base.participants,
                            content_paragraphs=tuple(injected_paragraphs),
                            compiler=inject_base.compiler,
                            model_used=inject_base.model_used,
                        )
                        _log.info("  程序化注入了 %d 个数据点", len(missing))
                    else:
                        # 本地检查没发现缺失，但仍需 LLM 帮助（Rubric 认为有数据问题）
                        _log.info("  本地检查无缺失，回退到 LLM refine")
                        content = refine(llm, analysis, inject_base,
                                         inject_rubric, prefer_paid=True,
                                         style_reference=style_ref)
                else:
                    # ★ ITERATE: 针对性修改（Self-Refine 第三步）
                    # 策略切换：如果连续停滞，从最佳版本重新开始
                    if no_improve_count >= CONVERGENCE_PATIENCE and best_content:
                        _log.info("⚠ 连续%d轮无提升，从最佳版本重新 refine", no_improve_count)
                        current_content = best_content
                        current_rubric = best_rubric
                        no_improve_count = 0

                    if current_content is None:
                        current_content = best_content or content
                    if current_rubric is None:
                        current_rubric = best_rubric

                    if current_rubric:
                        content = refine(llm, analysis, current_content,
                                         current_rubric, prefer_paid=True,
                                         style_reference=style_ref)
                    else:
                        content = generate(llm, analysis, prefer_paid=True,
                                           style_reference=style_ref,
                                           revision_guide=revision_guide)
        except Exception as e:
            _log.warning("生成/refine 失败: %s", str(e)[:100])
            continue

        # ★ FEEDBACK: 多维度 Rubric 审查（Self-Refine 第二步）
        try:
            rubric = review_rubric(llm, analysis, content, prefer_paid=True)
        except Exception as e:
            _log.warning("Rubric 审查失败: %s", str(e)[:100])
            continue

        score = rubric.total_score
        elapsed = time.time() - iter_start

        # 详细输出维度分数
        dim_scores_str = " | ".join(
            f"{k.split('_')[0]}:{v}" for k, v in rubric.dimension_scores.items()
        )
        _log.info("第 %d 轮结果: 评分 %d/100 | %s | %.0f秒",
                  iteration, score, dim_scores_str, elapsed)

        # 输出扣分项
        for dim, issues in rubric.dimension_issues.items():
            for issue in issues[:2]:
                _log.info("  %s: %s", dim, issue[:100])

        # 记录历史
        history.append({
            "轮次": iteration,
            "评分": score,
            "维度": dict(rubric.dimension_scores),
            "策略": "INIT" if iteration == 1 else "ITERATE",
            "问题数": sum(len(v) for v in rubric.dimension_issues.values()),
        })

        # 判断是否提升
        is_improvement = score > best_score
        if is_improvement:
            best_score = score
            best_content = content
            best_rubric = rubric
            no_improve_count = 0
            _log.info("🎉 新最高分: %d/100", best_score)
        else:
            no_improve_count += 1
            _log.info("未提升 (连续%d轮) | 当前最高: %d", no_improve_count, best_score)

        # 更新当前版本（供下一轮 refine 使用）
        current_content = content
        current_rubric = rubric

        # 达标检查
        if score >= TARGET_SCORE:
            _log.info("🏆 达到目标分数 %d！停止迭代", TARGET_SCORE)
            break

        # 收敛警告
        if no_improve_count >= CONVERGENCE_PATIENCE:
            _log.warning("⚠ 已连续%d轮无提升，下一轮将切换突破策略", no_improve_count)

    # ── Phase 3: 输出最佳版本 ──────────────────────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 3: 输出最佳版本")
    _log.info("=" * 60)

    if best_content is None:
        _log.error("所有迭代均失败")
        return

    # 使用最佳版本（如果 Rubric 提供了更好的修正段落）
    if best_rubric and best_rubric.revised_paragraphs and best_rubric.total_score >= best_score:
        final_content = GeneratedContent(
            title=best_content.title,
            doc_number=best_content.doc_number,
            meeting_type=best_content.meeting_type,
            meeting_topic=best_content.meeting_topic,
            meeting_date=best_content.meeting_date,
            meeting_location=best_content.meeting_location,
            host=best_content.host,
            participants=best_content.participants,
            content_paragraphs=best_rubric.revised_paragraphs,
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
    if best_rubric:
        _log.info("各维度最终分数:")
        for dim, score in best_rubric.dimension_scores.items():
            _log.info("  %s: %d/20", dim, score)
    _log.info("输出: %s", final_path)
    _log.info("")
    _log.info("评分历史:")
    for h in history:
        dim_str = " ".join(f"{k.split('_')[0]}:{v}" for k, v in h.get("维度", {}).items())
        _log.info("  第%2d轮: %3d分 [%s] %s", h["轮次"], h["评分"], h["策略"], dim_str)
    _log.info("=" * 60)

    # 保存历史记录
    history_path = OUTPUT_DIR / "optimization_history_v2.json"
    history_path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log.info("历史记录: %s", history_path)


if __name__ == "__main__":
    main()
