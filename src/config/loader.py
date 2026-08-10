"""INI 配置加载器 —— 支持 .env 密钥注入与 ${VAR} 变量展开。

借鉴 We-AIPO src/config/loader.py 的成熟设计：
- ${VAR} 递归展开
- .env 密钥加载
- 热重载（mtime 检测）
- 类型安全的 getter 方法
"""
from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from . import paths

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand(value: str, env: dict) -> str:
    """递归展开 ${VAR}，支持嵌套。未定义变量保持原样。"""
    previous = None
    while previous != value:
        previous = value
        value = _VAR_PATTERN.sub(lambda m: env.get(m.group(1), m.group(0)), value)
    return value


class Config:
    """只读配置访问器。按需取值，带类型转换与默认值。"""

    def __init__(self, ini_path: Path | None = None, env_path: Path | None = None):
        self.ini_path = ini_path or paths.CONFIG_INI
        load_dotenv(env_path or paths.ENV_FILE, override=False)

        if not self.ini_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在：{self.ini_path}\n请参考 config.ini 模板创建。"
            )

        self._cp = configparser.ConfigParser(
            interpolation=None,
            inline_comment_prefixes=("#", ";"),
        )
        self._cp.read(self.ini_path, encoding="utf-8")
        self._expand_all(os.environ)
        self._mtime = self.ini_path.stat().st_mtime

    def _expand_all(self, env: dict) -> None:
        for section in self._cp.sections():
            for key, value in list(self._cp[section].items()):
                self._cp[section][key] = _expand(value, env)

    # ---- 基本读取 ----

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        return self._cp.get(section, key, fallback=default)

    def require(self, section: str, key: str) -> str:
        value = self.get(section, key)
        if value is None or value.strip() == "":
            raise KeyError(f"缺少必填配置 [{section}] {key}")
        return value

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        raw = self.get(section, key)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        raw = self.get(section, key)
        if raw is None:
            return default
        return raw.strip().lower() in ("true", "1", "yes", "on", "是")

    def get_float(self, section: str, key: str, default: float = 0.0) -> float:
        raw = self.get(section, key)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def get_list(self, section: str, key: str, default: List[str] | None = None) -> List[str]:
        raw = self.get(section, key, "")
        if not raw.strip():
            return default or []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def has_section(self, section: str) -> bool:
        return self._cp.has_section(section)

    def items(self, section: str) -> List[tuple]:
        return list(self._cp.items(section))

    def override(self, section: str, key: str, value: str) -> None:
        """运行时覆盖某个配置项（用于 CLI 参数覆盖）。"""
        if not self._cp.has_section(section):
            self._cp.add_section(section)
        self._cp.set(section, key, value)

    def validate(self, required: List[tuple]) -> List[str]:
        """返回缺失项清单（空表示全部通过）。"""
        missing = []
        for section, key in required:
            value = self.get(section, key)
            if value is None or value.strip() == "" or value.strip().startswith("${"):
                missing.append(f"[{section}] {key}")
        return missing


_singleton: Config | None = None


def get_config() -> Config:
    """全局单例配置。"""
    global _singleton
    if _singleton is None:
        _singleton = Config()
    return _singleton


def reload_config() -> Config:
    """强制重新加载。"""
    global _singleton
    _singleton = Config()
    return _singleton
