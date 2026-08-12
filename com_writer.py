#!/usr/bin/env python3
"""企业写手 Com-Writer —— GUI 启动入口。

用法：
  python com_writer.py          # 启动 GUI（默认）
  python com_writer.py --cli    # 命令行模式（保留兼容）

双击运行或 PyInstaller 打包后直接使用。
"""
from __future__ import annotations

import sys


def _ensure_utf8() -> None:
    """Windows 下强制 UTF-8，防止中文乱码。"""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")


def run_gui() -> None:
    """启动 PySide6 GUI。"""
    _ensure_utf8()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtCore import Qt

    # 高 DPI
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Com-Writer")
    app.setApplicationVersion("2.0.0")

    # 全局字体
    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)

    from src.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def run_cli() -> None:
    """命令行模式（兼容旧用法）。"""
    _ensure_utf8()
    from scripts.oneshot_generate import main
    main()


def main() -> None:
    cli_mode = "--cli" in sys.argv
    if cli_mode:
        run_cli()
    else:
        try:
            run_gui()
        except ImportError as e:
            print(f"GUI 依赖缺失: {e}")
            print("请安装: pip install PySide6")
            print("或使用 CLI 模式: python com_writer.py --cli")
            sys.exit(1)


if __name__ == "__main__":
    main()
