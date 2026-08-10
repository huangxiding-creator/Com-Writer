"""工作报告插件 —— 从原始材料生成工作报告（Phase 3 预留）。

未来实现：周报/月报/季度总结等工作报告体裁。
当前为框架预留，等待用户提供模板后激活。
"""
from __future__ import annotations

from pathlib import Path

from src.core.models import TaskResult
from src.core.plugin_base import WritingPlugin, PluginMeta
from src.utils.logger import get_logger

_log = get_logger("plugin.work_report")


class WorkReportPlugin(WritingPlugin):
    """工作报告生成插件（预留）。"""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="工作报告",
            code="work_report",
            description="从工作记录材料生成周报/月报/季度总结",
        )

    def run(
        self,
        input_path: str | Path | None = None,
        template_path: str | Path | None = None,
        output_path: str | Path | None = None,
        style_reference: str = "",
        **kwargs,
    ) -> TaskResult:
        _log.info("工作报告插件暂未激活，等待用户提供模板")
        return TaskResult(
            success=False,
            error="工作报告插件尚未实现。请在 02 内部写作体裁模板/ 中放入工作报告模板后激活。",
        )


from plugins.registry import register
register("work_report", WorkReportPlugin)
