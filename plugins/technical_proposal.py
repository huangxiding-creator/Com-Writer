"""技术方案插件 —— 从技术讨论材料生成技术方案文档（Phase 3 预留）。

未来实现：技术方案/施工组织设计/专项方案等。
"""
from __future__ import annotations

from pathlib import Path

from src.core.models import TaskResult
from src.core.plugin_base import WritingPlugin, PluginMeta
from src.utils.logger import get_logger

_log = get_logger("plugin.technical_proposal")


class TechnicalProposalPlugin(WritingPlugin):
    """技术方案生成插件（预留）。"""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="技术方案",
            code="technical_proposal",
            description="从技术讨论材料生成技术方案文档",
        )

    def run(
        self,
        input_path: str | Path | None = None,
        template_path: str | Path | None = None,
        output_path: str | Path | None = None,
        style_reference: str = "",
        **kwargs,
    ) -> TaskResult:
        _log.info("技术方案插件暂未激活，等待用户提供模板")
        return TaskResult(
            success=False,
            error="技术方案插件尚未实现。请在 02 内部写作体裁模板/ 中放入技术方案模板后激活。",
        )


from plugins.registry import register
register("technical_proposal", TechnicalProposalPlugin)
