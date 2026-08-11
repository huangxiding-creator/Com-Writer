"""全栏目子体裁细分分析 + 重新生成会议纪要。

用户要求："所有栏目都要进行子体裁细分研究"
          "不要把栏目当作一个整体体裁处理分析，每个栏目要细分进行分析"
          "每个大的体裁要进一步细分"
          "尽量分出最多的写作种类"
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.processors.subgenre_analyzer import analyze_all_subgenres
from src.utils.logger import get_logger
from src.config.paths import REFINE_DIR as STYLE_OUTPUT_DIR, CRAWL_DIR

_log = get_logger("scripts.subgenre")


def main():
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    _log.info("=" * 60)
    _log.info("全栏目子体裁细分分析")
    _log.info("用户要求: 每个栏目都要细分，不要整体处理")
    _log.info("=" * 60)

    start = time.time()

    # 全部分析
    all_results = analyze_all_subgenres(
        llm,
        crawl_dir=CRAWL_DIR,
        max_per_subgenre=10,
        max_chars_per_article=2500,
    )

    elapsed = time.time() - start

    # 统计
    total_subgenres = sum(len(v) for v in all_results.values())
    _log.info("")
    _log.info("=" * 60)
    _log.info("子体裁分析完成!")
    _log.info("栏目数: %d | 子体裁总数: %d | 耗时: %.0f秒 (%.1f分钟)",
              len(all_results), total_subgenres, elapsed, elapsed / 60)
    _log.info("=" * 60)

    # 详细输出
    for genre, subs in all_results.items():
        _log.info("  %s: %d 个子体裁", genre, len(subs))
        for sub in subs:
            sub_name = sub.get("子体裁名称", "?")
            _log.info("    - %s", sub_name)

    # 保存完整分析结果
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = STYLE_OUTPUT_DIR / f"subgenre_analysis_{timestamp}.json"
    json_path.write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log.info("分析结果已保存: %s", json_path)

    # 构建增强的风格参考文本
    style_ref = _build_enhanced_style_ref(all_results)
    txt_path = STYLE_OUTPUT_DIR / "writing_style_reference_v2.txt"
    txt_path.write_text(style_ref, encoding="utf-8")
    _log.info("增强风格参考已保存: %s (%d 字)", txt_path, len(style_ref))

    # 重新生成会议纪要
    _log.info("")
    _log.info("=" * 60)
    _log.info("用细分体裁风格重新生成会议纪要")
    _log.info("=" * 60)

    from src.core.orchestrator import Orchestrator
    orchestrator = Orchestrator(cfg)

    transcript = orchestrator.find_latest_transcript()
    if not transcript:
        _log.error("未找到转写稿")
        return

    result = orchestrator.run_meeting_minutes(
        transcript_path=transcript,
        style_reference=style_ref,
    )

    if result.success:
        _log.info("")
        _log.info("=" * 60)
        _log.info("✅ 全流程完成！")
        _log.info("子体裁分析: %d 栏目 / %d 子体裁", len(all_results), total_subgenres)
        _log.info("输出: %s", result.output_path)
        _log.info("质量评分: %d/100", result.quality_score)
        _log.info("耗时: %.1f秒", result.duration_seconds)
        _log.info("=" * 60)
    else:
        _log.error("会议纪要生成失败: %s", result.error)


def _build_enhanced_style_ref(all_results: dict[str, list[dict]]) -> str:
    """将全栏目子体裁分析结果构建为增强版风格参考。"""
    parts: list[str] = []

    parts.append("【企业写作风格总纲（基于全栏目细分体裁分析）】")
    parts.append("以下风格指南基于对内网全部16个栏目的细分体裁逐一研究得出。\n")

    for genre_name, sub_analyses in all_results.items():
        parts.append(f"\n{'='*40}")
        parts.append(f"【栏目：{genre_name}】（{len(sub_analyses)} 个子体裁）")
        parts.append("=" * 40)

        for sub in sub_analyses:
            sub_name = sub.get("子体裁名称", "?")
            positioning = sub.get("子体裁定位", "")
            title_pattern = sub.get("标题规律", "")
            structure = sub.get("结构范式", "")
            language = sub.get("语言特色", [])
            data_usage = sub.get("数据使用", "")
            key_points = sub.get("写作要点", "")

            parts.append(f"\n  ◆ {sub_name}")
            if positioning:
                parts.append(f"    定位: {positioning}")
            if title_pattern:
                parts.append(f"    标题: {title_pattern}")
            if structure:
                parts.append(f"    结构: {structure}")
            if isinstance(language, list) and language:
                parts.append(f"    语言: {'; '.join(str(l) for l in language[:5])}")
            if data_usage:
                parts.append(f"    数据: {data_usage}")
            if key_points:
                parts.append(f"    要点: {key_points}")

    # 会议纪要专项提取
    minutes_subs = all_results.get("会议纪要", [])
    if minutes_subs:
        parts.append(f"\n{'='*40}")
        parts.append("【会议纪要专项指南（融合所有子体裁）】")
        parts.append("=" * 40)
        for sub in minutes_subs:
            sub_name = sub.get("子体裁名称", "")
            key_points = sub.get("写作要点", "")
            if key_points:
                parts.append(f"\n{sub_name}:\n{key_points}")

    return "\n".join(parts)


if __name__ == "__main__":
    main()
