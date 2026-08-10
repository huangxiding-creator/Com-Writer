#!/usr/bin/env python3
"""企业写手 Com-Writer — CLI 入口。

用法：
    # 自动查找最新记录生成会议纪要
    python run.py

    # 指定转写稿文件
    python run.py -i "03 原始记录资料/某会议.docx"

    # 指定输出路径和文号
    python run.py -o "output/会议纪要.docx" -n "〔2026〕2号"

    # 使用付费模型（质量优先）
    python run.py --quality

    # 先爬取内网提炼风格，再生成会议纪要
    python run.py --crawl

    # 列出所有可用插件
    python run.py --list-plugins

    # 调试模式（详细日志）
    python run.py --debug
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _print_banner(version: str) -> None:
    print("=" * 60)
    print(f"  企业写手 Com-Writer v{version}")
    print("  通用企业写作自动化工具")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="企业写手 Com-Writer — 企业内部写作自动化工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py                                    # 自动查找最新记录生成会议纪要
  python run.py -i "某会议.docx"                   # 指定输入文件
  python run.py -i "某会议.docx" -o "输出.docx"     # 指定输入和输出
  python run.py -i "某会议.docx" --quality          # 质量优先（使用付费模型）
  python run.py --crawl                             # 爬取内网→提炼风格→生成纪要
  python run.py --list-plugins                      # 列出可用插件
""",
    )

    parser.add_argument(
        "-i", "--input",
        help="输入文件路径（转写稿 .docx/.txt），不指定则自动查找最新记录",
    )
    parser.add_argument(
        "-o", "--output",
        help="输出文件路径，不指定则自动命名到输出目录",
    )
    parser.add_argument(
        "-t", "--template",
        help="模板文件路径，不指定则使用配置文件中的默认模板",
    )
    parser.add_argument(
        "-n", "--number",
        default="",
        help="文号（如 〔2026〕2号）",
    )
    parser.add_argument(
        "-p", "--plugin",
        default="meeting_minutes",
        help="写作体裁插件（默认 meeting_minutes）",
    )
    parser.add_argument(
        "--quality",
        action="store_true",
        help="质量优先模式（使用 GLM-5.2 付费模型）",
    )
    parser.add_argument(
        "--crawl",
        action="store_true",
        help="先爬取内网文章，提炼写作风格后再生成会议纪要",
    )
    parser.add_argument(
        "--max-crawl",
        type=int,
        default=20,
        help="爬取文章上限（默认20，0=整站全量爬取）",
    )
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="列出所有可用插件后退出",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Com-Writer v1.0.0",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="调试模式（详细日志）",
    )

    args = parser.parse_args()

    # 加载配置
    from src.config.loader import get_config
    from src.utils.logger import get_logger

    cfg = get_config()

    # 覆盖配置
    if args.quality:
        cfg.override("会议纪要", "使用付费模型", "true")
    if args.debug:
        cfg.override("系统", "日志级别", "DEBUG")

    log = get_logger("main", cfg.get("系统", "日志级别", "INFO"))

    # --list-plugins: 列出插件后退出
    if args.list_plugins:
        _print_banner("1.0.0")
        # 导入所有插件以触发注册
        import plugins.meeting_minutes  # noqa: F401
        import plugins.work_report  # noqa: F401
        import plugins.technical_proposal  # noqa: F401
        from plugins.registry import get_plugin_info

        print("\n可用插件:")
        print("-" * 60)
        for info in get_plugin_info():
            status = "✅" if info["name"] == "会议纪要" else "📋"
            print(f"  {status} {info['name']} ({info['code']})")
            print(f"     {info['description']}")
            print(f"     版本: {info['version']}")
        print("-" * 60)
        return 0

    _print_banner("1.0.0")

    # 执行写作任务
    if args.plugin == "meeting_minutes":
        from plugins.meeting_minutes import run as run_minutes

        result = run_minutes(
            transcript_path=args.input,
            template_path=args.template,
            output_path=args.output,
            doc_number=args.number,
            cfg=cfg,
            crawl_first=args.crawl,
            max_crawl_articles=args.max_crawl,
        )
    else:
        # 通用插件调用
        import plugins.meeting_minutes  # noqa: F401
        import plugins.work_report  # noqa: F401
        import plugins.technical_proposal  # noqa: F401
        from plugins.registry import get_plugin
        from src.llm.multi_llm import create_llm

        plugin_cls = get_plugin(args.plugin)
        if plugin_cls is None:
            print(f"  ❌ 未知插件: {args.plugin}")
            print(f"  使用 --list-plugins 查看可用插件")
            return 1

        llm = create_llm(cfg)
        plugin = plugin_cls(llm, cfg)
        result = plugin.run(
            input_path=args.input,
            template_path=args.template,
            output_path=args.output,
        )

    if result.success:
        print(f"\n{'=' * 60}")
        print(f"  ✅ 写作任务完成！")
        print(f"  📂 输出: {result.output_path}")
        print(f"  📊 质量评分: {result.quality_score}/100")
        print(f"  ⏱️ 耗时: {result.duration_seconds:.1f}秒")
        print(f"{'=' * 60}\n")
        return 0
    else:
        print(f"\n{'=' * 60}")
        print(f"  ❌ 生成失败: {result.error}")
        print(f"{'=' * 60}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
