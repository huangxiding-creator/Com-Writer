"""插件注册表 —— 管理所有可用的写作体裁插件。

用法：
    from plugins.registry import get_plugin, list_plugins

    # 列出所有插件
    for code, cls in list_plugins().items():
        print(f"{cls.meta.name}: {cls.meta.description}")

    # 获取并运行插件
    plugin_cls = get_plugin("meeting_minutes")
    plugin = plugin_cls(llm, cfg)
    result = plugin.run("transcript.docx")
"""
from __future__ import annotations

from typing import Optional

from src.core.plugin_base import WritingPlugin, PluginMeta, _REGISTRY


def register(name: str, plugin_class: type[WritingPlugin]) -> None:
    """手动注册插件。"""
    _REGISTRY[name] = plugin_class


def get_plugin(name: str) -> Optional[type[WritingPlugin]]:
    """获取插件类。"""
    return _REGISTRY.get(name)


def list_plugins() -> dict[str, type[WritingPlugin]]:
    """列出所有已注册插件。"""
    return dict(_REGISTRY)


def get_plugin_info() -> list[dict]:
    """获取所有插件的元信息（用于展示）。"""
    info: list[dict] = []
    for code, cls in _REGISTRY.items():
        try:
            # 创建临时实例获取 meta
            instance = object.__new__(cls)
            meta = cls.meta.fget(instance)  # type: ignore
            info.append({
                "code": code,
                "name": meta.name,
                "description": meta.description,
                "version": meta.version,
            })
        except Exception:
            info.append({"code": code, "name": code, "description": "", "version": "?"})
    return info
