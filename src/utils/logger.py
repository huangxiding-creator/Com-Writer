"""日志管理 —— UTF-8 兼容 Windows，文件+控制台双输出。

借鉴 AIResearch.LogManager 的 Windows UTF-8 处理模式。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..config.paths import LOG_DIR

_loggers: dict[str, logging.Logger] = {}


def _ensure_utf8_stdout() -> None:
    """Windows 下强制 stdout 为 UTF-8，防止中文乱码。"""
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")


def get_logger(name: str = "com-writer", level: str = "INFO") -> logging.Logger:
    """获取/创建日志器。同名日志器只初始化一次。"""
    if name in _loggers:
        return _loggers[name]

    _ensure_utf8_stdout()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 控制台
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # 文件（使用项目根目录下的 logs/）
    log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "com-writer.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.propagate = False
    _loggers[name] = logger
    return logger
