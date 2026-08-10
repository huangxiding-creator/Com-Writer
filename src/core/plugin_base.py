"""写作体裁插件基类 —— 所有写作类型继承此接口。

Com-Writer 的核心设计是"输入→处理→输出"管线，
不同写作体裁（会议纪要/工作报告/技术方案...）作为插件实现。

实现新体裁只需：
1. 继承 WritingPlugin
2. 实现 build_prompts() 和 format_output()
3. 注册到 plugins/registry.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import TaskResult
from ..llm.multi_llm import MultiLLMClient


@dataclass(frozen=True)
class PluginMeta:
    """插件元信息。"""
    name: str           # 插件名称（如"会议纪要"）
    code: str           # 唯一标识（如"meeting_minutes"）
    description: str    # 简短描述
    version: str = "1.0"


class WritingPlugin(ABC):
    """写作体裁插件抽象基类。"""

    def __init__(self, llm: MultiLLMClient, cfg):
        self._llm = llm
        self._cfg = cfg

    @property
    @abstractmethod
    def meta(self) -> PluginMeta:
        """插件元信息。"""
        ...

    @abstractmethod
    def run(
        self,
        input_path: str | Path,
        template_path: str | Path | None = None,
        output_path: str | Path | None = None,
        style_reference: str = "",
        **kwargs,
    ) -> TaskResult:
        """执行写作任务。

        Args:
            input_path: 输入文件路径
            template_path: 模板路径
            output_path: 输出路径
            style_reference: 写作风格参考
        Returns:
            TaskResult
        """
        ...


# ---- 插件注册表 ----

_REGISTRY: dict[str, type[WritingPlugin]] = {}


def register_plugin(plugin_class: type[WritingPlugin]) -> type[WritingPlugin]:
    """注册插件（装饰器用法）。"""
    # 临时实例化获取 meta（不执行 __init__）
    code = plugin_class.__name__
    _REGISTRY[code] = plugin_class
    return plugin_class


def get_plugin(code: str) -> type[WritingPlugin] | None:
    """按 code 获取插件类。"""
    return _REGISTRY.get(code)


def list_plugins() -> dict[str, type[WritingPlugin]]:
    """列出所有已注册插件。"""
    return dict(_REGISTRY)
