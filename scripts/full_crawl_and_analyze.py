"""全量补爬 + 多体裁分析 + 重新生成会议纪要。

用户要求：
  "请务必抓取到所有内容"
  "不要遗漏"
  "请全面分析提炼总包部各种体裁的写作风格"
  "每个大的体裁要进一步细分"
  "尽量分出最多的写作种类"
  "根据最新原始资料，重新生成会议纪要"
"""
import json
import sys
import time
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.processors.multi_genre_analyzer import analyze_all_genres, format_synthesis_for_prompt
from src.utils.logger import get_logger

_log = get_logger("scripts.full_crawl")

# ── 配置 ──────────────────────────────────────────────
CRAWL_DIR = ROOT / "02-1 总承包事业部" / "00 内网文字材料爬取"
STYLE_OUTPUT_DIR = ROOT / "02-1 总承包事业部" / "01 内部写作成果提炼"
STYLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def count_crawled() -> dict:
    """统计当前各分类已爬取的文章数。"""
    stats = {}
    for cat_dir in sorted(CRAWL_DIR.iterdir()):
        if not cat_dir.is_dir():
            continue
        txt_files = [f for f in cat_dir.glob("*.txt") if f.name != "index.txt"]
        total_chars = 0
        for f in txt_files:
            try:
                text = f.read_text(encoding="utf-8")
                parts = text.split("=" * 60, 1)
                body = parts[1].strip() if len(parts) > 1 else text
                total_chars += len(body)
            except Exception:
                pass
        stats[cat_dir.name] = {
            "count": len(txt_files),
            "chars": total_chars,
        }
    return stats


def run_dynamic_crawl(cfg: Config) -> int:
    """运行动态爬虫补全所有分类。"""
    from src.browser.dynamic_crawler import DynamicCrawler

    _log.info("=" * 60)
    _log.info("动态 JS 爬取阶段（DrissionPage）")
    _log.info("=" * 60)

    crawler = DynamicCrawler(cfg)
    total = crawler.crawl_category_pages(max_per_category=5000)

    _log.info("动态爬取完成: 新增 %d 篇", total)
    return total


def save_analysis(synthesis: dict) -> Path:
    """保存分析结果到 JSON 文件。"""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filepath = STYLE_OUTPUT_DIR / f"multi_genre_analysis_{timestamp}.json"
    filepath.write_text(
        json.dumps(synthesis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log.info("分析结果已保存: %s", filepath)
    return filepath


def main():
    # 加载配置（Config.__init__ 会自动加载 .env）
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    # ── Step 1: 统计当前爬取状况 ──────────────────────
    _log.info("=" * 60)
    _log.info("Step 1: 检查当前爬取状况")
    _log.info("=" * 60)

    before = count_crawled()
    total_before = sum(s["count"] for s in before.values())
    _log.info("当前总文章数: %d", total_before)
    for cat, s in sorted(before.items()):
        _log.info("  %-25s %3d篇  %8d字", cat, s["count"], s["chars"])

    # ── Step 2: 动态补爬 ──────────────────────────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Step 2: 动态 JS 补爬（解除所有限制）")
    _log.info("=" * 60)

    try:
        new_count = run_dynamic_crawl(cfg)
    except Exception as exc:
        _log.error("动态爬取失败: %s", str(exc)[:200])
        new_count = 0

    # 统计爬取后
    after = count_crawled()
    total_after = sum(s["count"] for s in after.values())
    _log.info("")
    _log.info("补爬后总文章数: %d（新增 %d）", total_after, total_after - total_before)
    for cat, s in sorted(after.items()):
        delta = s["count"] - before.get(cat, {"count": 0})["count"]
        marker = f" (+{delta})" if delta > 0 else ""
        _log.info("  %-25s %3d篇  %8d字%s", cat, s["count"], s["chars"], marker)

    if total_after == 0:
        _log.error("无任何爬取内容，退出")
        return

    # ── Step 3: 多体裁深度分析 ────────────────────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Step 3: 多体裁写作风格深度分析")
    _log.info("=" * 60)

    synthesis = analyze_all_genres(
        llm,
        crawl_dir=CRAWL_DIR,
        max_per_genre=20,
        max_chars_per_article=3000,
    )

    if synthesis:
        save_analysis(synthesis)
        style_ref = format_synthesis_for_prompt(synthesis)
        _log.info("综合风格指南长度: %d 字", len(style_ref))

        # 保存格式化的风格参考文本
        style_txt = STYLE_OUTPUT_DIR / "writing_style_reference.txt"
        style_txt.write_text(style_ref, encoding="utf-8")
        _log.info("风格参考文本已保存: %s", style_txt)
    else:
        _log.error("多体裁分析失败")
        return

    # ── Step 4: 用新风格重新生成会议纪要 ──────────────
    _log.info("")
    _log.info("=" * 60)
    _log.info("Step 4: 用最新风格参考重新生成会议纪要")
    _log.info("=" * 60)

    from src.core.orchestrator import Orchestrator
    orchestrator = Orchestrator(cfg)

    # 查找最新转写稿
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
        _log.info("输出: %s", result.output_path)
        _log.info("质量评分: %d/100", result.quality_score)
        _log.info("耗时: %.1f秒", result.duration_seconds)
        _log.info("=" * 60)
    else:
        _log.error("会议纪要生成失败: %s", result.error)


if __name__ == "__main__":
    main()
