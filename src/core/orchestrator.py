"""管线编排器 —— 协调 AI 处理全流程。

借鉴 We-AIPO ServiceContainer + pipeline 模式：
1. 构建共享服务（LLM、通知器、配置）
2. 执行 4 步管线：理解 → 生成 → 质控 → 输出
3. 检查点恢复：中断后从断点继续
4. 全程通知关键节点

用法：
    orchestrator = Orchestrator(cfg)
    result = orchestrator.run_meeting_minutes(transcript_path)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from ..config.loader import Config
from ..config.paths import DEFAULT_MINUTES_TEMPLATE, RAW_RECORD_DIR, OUTPUT_DIR, PROJECT_ROOT
from ..llm.multi_llm import MultiLLMClient
from ..notify.wecom import WeComNotifier, create_notifier
from ..readers.docx_reader import read_docx
from ..readers.transcript_parser import parse_transcript
from ..processors.understander import understand
from ..processors.generator import generate
from ..processors.quality_gate import review
from ..writers.docx_writer import write_minutes
from ..writers.template_engine import analyze_template
from .models import GeneratedContent, QualityReport, TaskResult
from .checkpoint import save_state, load_task_state, clear_task_state
from ..utils.logger import get_logger

_log = get_logger("core.orchestrator")


class Orchestrator:
    """管线编排器 —— 协调 AI 写作全流程。"""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._llm = MultiLLMClient(cfg)

        webhook = cfg.get("通知", "企微webhook", "")
        self._notifier: Optional[WeComNotifier] = create_notifier(webhook)

        self._quality_rounds = cfg.get_int("会议纪要", "质量自审轮次", 3)
        self._quality_threshold = cfg.get_int("会议纪要", "质量阈值", 80)
        self._prefer_paid = cfg.get_bool("会议纪要", "使用付费模型", False)

        # 通知开关
        self._notify_milestones = cfg.get_bool("通知", "关键节点通知", True)
        self._notify_completion = cfg.get_bool("通知", "完成通知", True)
        self._notify_error = cfg.get_bool("通知", "失败告警", True)

    def run_meeting_minutes(
        self,
        transcript_path: str | Path,
        template_path: str | Path | None = None,
        output_path: str | Path | None = None,
        doc_number: str = "",
        style_reference: str = "",
        revision_guide: str = "",
    ) -> TaskResult:
        """执行完整的会议纪要生成流程。

        Args:
            transcript_path: 转写稿文件路径
            template_path: 模板路径（None 用默认）
            output_path: 输出路径（None 自动命名）
            doc_number: 文号
            style_reference: 写作风格参考（从内网文章提炼）
            revision_guide: 领导审稿修改模式指南
        Returns:
            TaskResult
        """
        start_time = time.time()
        transcript_path = Path(transcript_path)
        template_path = Path(template_path) if template_path else DEFAULT_MINUTES_TEMPLATE
        task_id = f"meeting_minutes:{transcript_path.stem}"

        task_name = f"会议纪要生成 — {transcript_path.stem}"
        _log.info("="*60)
        _log.info("任务启动: %s", task_name)
        _log.info("输入: %s", transcript_path)
        _log.info("模板: %s", template_path)
        _log.info("="*60)

        if self._notify_milestones and self._notifier:
            self._notifier.send_milestone(
                "🚀 任务启动",
                f"**{task_name}**\n输入: `{transcript_path.name}`",
            )

        try:
            # Step 0: 检查点恢复
            state = load_task_state(task_id)
            analysis_data = None
            if state and state.get("phase") in ("generation", "quality", "writing"):
                _log.info("检测到检查点，从 %s 阶段恢复", state["phase"])
                analysis_data = state.get("data", {}).get("analysis")

            # Step 1: 读取转写稿
            raw_text = read_docx(transcript_path)
            transcript = parse_transcript(raw_text)

            _log.info("转写稿读取完成 | %d 字 | %d 条发言 | 发言人: %s",
                      transcript.char_count,
                      transcript.entry_count,
                      ", ".join(transcript.speakers))

            # Step 2: AI 深度理解（★ 关键步骤：始终用付费模型确保质量）
            _log.info("理解阶段使用付费模型确保质量（关键步骤）")
            analysis = understand(self._llm, transcript, prefer_paid=True,
                                  filename_hint=transcript_path.name)
            save_state(task_id, "generation", {"analysis": True})

            if self._notify_milestones and self._notifier:
                topic_summary = "\n".join(
                    f"  {i+1}. {t.name}（{len(t.action_items)}个行动项）"
                    for i, t in enumerate(analysis.topics)
                )
                self._notifier.send_milestone(
                    "📊 理解完成",
                    f"识别到 **{len(analysis.topics)}** 个议题:\n{topic_summary}",
                )

            # Step 3: 正式撰写（注入写作风格参考+修改模式指南）
            content = generate(self._llm, analysis, self._prefer_paid,
                             style_reference, revision_guide=revision_guide)
            save_state(task_id, "quality", {"analysis": True, "content": True})

            # Step 4: 质量自审 + 返工
            quality_report: QualityReport | None = None
            for round_num in range(1, self._quality_rounds + 1):
                _log.info("质量自审 第%d轮 (共%d轮)", round_num, self._quality_rounds)

                quality_report = review(self._llm, analysis, content, self._prefer_paid)

                if quality_report.passed and quality_report.score >= self._quality_threshold:
                    _log.info("质量自审通过 (第%d轮, 评分%d)", round_num, quality_report.score)
                    break

                # 使用修正段落（如果有）
                if quality_report.revised_paragraphs:
                    _log.info("使用AI修正后的段落 (第%d轮)", round_num)
                    content = GeneratedContent(
                        title=content.title,
                        doc_number=content.doc_number,
                        meeting_type=content.meeting_type,
                        meeting_topic=content.meeting_topic,
                        meeting_date=content.meeting_date,
                        meeting_location=content.meeting_location,
                        host=content.host,
                        participants=content.participants,
                        content_paragraphs=quality_report.revised_paragraphs,
                        compiler=content.compiler,
                        model_used=content.model_used,
                    )
                else:
                    _log.warning("第%d轮未通过，且无修正段落", round_num)
                    if round_num < self._quality_rounds:
                        # 重新生成
                        content = generate(self._llm, analysis, prefer_paid=True)
                    else:
                        _log.warning("质量自审 %d 轮后仍未达标，使用当前版本输出",
                                      self._quality_rounds)

            save_state(task_id, "writing", {"analysis": True, "content": True})

            # Step 5: 输出 Word 文档
            template_info = analyze_template(template_path)
            final_path = write_minutes(
                content=content,
                template_info=template_info,
                template_path=template_path,
                output_path=output_path,
                doc_number=doc_number,
            )

            duration = time.time() - start_time
            quality_score = quality_report.score if quality_report else 0

            # 清除检查点
            clear_task_state(task_id)

            _log.info("="*60)
            _log.info("✅ 任务完成: %s", task_name)
            _log.info("输出: %s", final_path)
            _log.info("耗时: %.1f秒 | 质量评分: %d", duration, quality_score)
            _log.info("="*60)

            # 发送完成通知
            if self._notify_completion and self._notifier:
                self._notifier.send_completion(
                    task_name=task_name,
                    output_path=final_path,
                    duration=duration,
                    quality_score=quality_score,
                )

            return TaskResult(
                success=True,
                output_path=final_path,
                quality_score=quality_score,
                duration_seconds=duration,
                extra={"topics": len(analysis.topics)},
            )

        except Exception as exc:
            duration = time.time() - start_time
            error_msg = str(exc)
            _log.error("任务失败: %s", error_msg, exc_info=True)

            if self._notify_error and self._notifier:
                self._notifier.send_error(task_name, error_msg)

            return TaskResult(
                success=False,
                duration_seconds=duration,
                error=error_msg,
            )

    def find_latest_transcript(self, record_dir: str | Path | None = None) -> Path | None:
        """查找原始记录目录中最新的转写稿文件。

        Args:
            record_dir: 原始记录目录（None 用配置默认值）
        Returns:
            最新文件的路径，无文件返回 None
        """
        search_dir = Path(record_dir) if record_dir else RAW_RECORD_DIR
        if not search_dir.exists():
            _log.warning("原始记录目录不存在: %s", search_dir)
            return None

        # 支持的文件类型
        patterns = ["*.docx", "*.doc", "*.txt"]
        files: list[Path] = []
        for pattern in patterns:
            files.extend(search_dir.glob(pattern))

        if not files:
            _log.warning("原始记录目录无文件: %s", search_dir)
            return None

        # 按修改时间排序，取最新
        latest = max(files, key=lambda f: f.stat().st_mtime)
        _log.info("找到最新记录: %s", latest.name)
        return latest

    def crawl_and_extract_style(
        self,
        max_articles: int = 30,
    ) -> str:
        """爬取网站文章并系统性提炼写作方法论。

        用户要求："一定要把总包部内网所有资料研究清楚了，提炼出写作方法论后再去编写会议纪要"
                  "请务必抓取到所有内容"

        两阶段爬取：
        1. 静态 BFS 整站爬取（获取所有静态 HTML 页面）
        2. 动态 JS 爬取（DrissionPage 渲染 JS 动态加载的文章列表）

        Returns:
            格式化的风格方法论参考文本（供 generate() 使用），失败返回空字符串
        """
        from ..browser.intranet_crawler import IntranetCrawler
        from ..processors.style_extractor import extract_style, format_style_for_prompt

        # Step 1a: 静态整站爬取
        if self._notify_milestones and self._notifier:
            self._notifier.send_milestone("🕷️ 开始整站爬取", "阶段1: 静态 BFS 全量爬取...")

        crawler = IntranetCrawler(self._cfg)
        static_articles = crawler.crawl(full_site=True, max_pages=0)
        _log.info("静态爬取完成: %d 篇", len(static_articles))

        # Step 1b: 动态 JS 爬取（补全 JS 动态加载的文章）
        if self._notify_milestones and self._notifier:
            self._notifier.send_milestone("🕷️ 阶段2: 动态 JS 爬取", "DrissionPage 渲染全部分类...")

        dynamic_new = 0
        try:
            from ..browser.dynamic_crawler import DynamicCrawler
            dyn_crawler = DynamicCrawler(self._cfg)
            dynamic_new = dyn_crawler.crawl_category_pages(max_per_category=5000)
            _log.info("动态爬取完成: 新增 %d 篇", dynamic_new)
        except Exception as exc:
            _log.warning("动态爬取失败（不影响主流程）: %s", str(exc)[:100])

        total_articles = len(static_articles) + dynamic_new
        if total_articles == 0:
            _log.warning("未爬取到任何文章，跳过风格提取")
            return ""

        if self._notify_milestones and self._notifier:
            self._notifier.send_milestone(
                "🕷️ 爬取完成",
                f"静态 {len(static_articles)} 篇 + 动态 {dynamic_new} 篇 = **{total_articles} 篇**\n"
                f"开始深度提炼写作方法论...",
            )

        # Step 2: 系统性提炼写作方法论（优先分析内容丰富的文章）
        style = extract_style(self._llm, max_articles=max_articles)

        if not style:
            _log.warning("风格提取失败")
            return ""

        # Step 3: 格式化为 prompt 可用文本
        style_ref = format_style_for_prompt(style)

        if self._notify_milestones and self._notifier:
            methodology = style.get("写作方法论总结", "")
            summary = methodology[:100] + "..." if len(methodology) > 100 else methodology
            self._notifier.send_milestone(
                "✍️ 写作方法论提炼完成",
                f"已系统性提炼企业写作方法论\n\n> {summary}",
            )

        _log.info("写作方法论提炼完成 | 输出长度: %d 字", len(style_ref))
        return style_ref

    def run_with_style(
        self,
        transcript_path: str | Path | None = None,
        template_path: str | Path | None = None,
        crawl_first: bool = True,
        max_crawl_articles: int = 30,
    ) -> TaskResult:
        """完整流程：爬取内网 → 提炼风格 → 生成会议纪要。

        用户要求："内网爬虫提炼写作精髓后再生成会议纪要"
        """
        # Step 0: 查找最新记录
        if transcript_path is None:
            transcript_path = self.find_latest_transcript()
            if transcript_path is None:
                return TaskResult(success=False, error="未找到转写稿文件")

        # Step 1: 爬取内网 + 提炼风格（可选）
        style_ref = ""
        if crawl_first:
            _log.info("="*60)
            _log.info("Phase 2: 内网爬取 + 写作风格提炼")
            _log.info("="*60)
            style_ref = self.crawl_and_extract_style(max_crawl_articles)

        # 加载修改模式指南（如果存在）
        revision_guide = ""
        guide_path = PROJECT_ROOT / "02-1 总承包事业部" / "01 内部写作成果提炼" / "revision_guide_definitive.txt"
        if guide_path.exists():
            revision_guide = guide_path.read_text(encoding="utf-8")
            _log.info("已加载修改模式指南: %d 字", len(revision_guide))

        # Step 2: 用风格参考+修改模式生成会议纪要
        _log.info("="*60)
        _log.info("Phase 1: 会议纪要生成（含风格参考+修改模式）")
        _log.info("="*60)

        return self.run_meeting_minutes(
            transcript_path=transcript_path,
            template_path=template_path,
            style_reference=style_ref,
            revision_guide=revision_guide,
        )
