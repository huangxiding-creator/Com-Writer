"""会议纪要插件 —— 封装会议纪要生成的完整流程。

Com-Writer 的第一个生产级插件，演示了完整的写作管线。
"""
from __future__ import annotations

from pathlib import Path

from src.config.loader import Config, get_config
from src.config.paths import DEFAULT_MINUTES_TEMPLATE, OUTPUT_DIR, RAW_RECORD_DIR
from src.core.orchestrator import Orchestrator
from src.core.models import TaskResult
from src.core.plugin_base import WritingPlugin, PluginMeta
from src.utils.logger import get_logger

_log = get_logger("plugin.meeting_minutes")


class MeetingMinutesPlugin(WritingPlugin):
    """会议纪要生成插件。"""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="会议纪要",
            code="meeting_minutes",
            description="从会议录音转写稿自动生成正式会议纪要",
        )

    def run(
        self,
        input_path: str | Path | None = None,
        template_path: str | Path | None = None,
        output_path: str | Path | None = None,
        style_reference: str = "",
        crawl_first: bool = False,
        max_crawl_articles: int = 20,
        **kwargs,
    ) -> TaskResult:
        """运行会议纪要生成。"""
        orchestrator = Orchestrator(self._cfg)

        if crawl_first:
            return orchestrator.run_with_style(
                transcript_path=input_path,
                template_path=template_path,
                crawl_first=True,
                max_crawl_articles=max_crawl_articles,
            )

        if input_path is None:
            record_dir = self._cfg.get("会议纪要", "原始记录目录", str(RAW_RECORD_DIR))
            latest = orchestrator.find_latest_transcript(record_dir)
            if latest is None:
                return TaskResult(success=False, error=f"未找到转写稿: {record_dir}")
            input_path = latest

        if template_path is None:
            template_path = self._cfg.get("会议纪要", "模板路径", str(DEFAULT_MINUTES_TEMPLATE))

        if output_path is None:
            out_dir = self._cfg.get("会议纪要", "输出目录", str(OUTPUT_DIR))
            Path(out_dir).mkdir(parents=True, exist_ok=True)

        return orchestrator.run_meeting_minutes(
            transcript_path=input_path,
            template_path=template_path,
            output_path=output_path,
            style_reference=style_reference,
        )


# 注册插件
from plugins.registry import register
register("meeting_minutes", MeetingMinutesPlugin)


# ---- 兼容旧接口 ----

def run(
    transcript_path: str | Path | None = None,
    template_path: str | Path | None = None,
    output_path: str | Path | None = None,
    doc_number: str = "",
    cfg: Config | None = None,
    crawl_first: bool = False,
    max_crawl_articles: int = 20,
) -> TaskResult:
    """运行会议纪要生成（兼容旧接口）。"""
    cfg = cfg or get_config()
    from src.llm.multi_llm import create_llm
    llm = create_llm(cfg)
    plugin = MeetingMinutesPlugin(llm, cfg)
    return plugin.run(
        input_path=transcript_path,
        template_path=template_path,
        output_path=output_path,
        crawl_first=crawl_first,
        max_crawl_articles=max_crawl_articles,
    )
