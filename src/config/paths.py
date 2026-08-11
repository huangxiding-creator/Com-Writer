"""路径常量 —— 项目内所有路径定义。

工作目录通过环境变量 ``COM_WRITER_WORKSPACE`` 配置（见 ``.env``），
默认为 ``workspace``，避免在源码中出现企业内部目录名。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 配置文件
CONFIG_INI = PROJECT_ROOT / "config.ini"
ENV_FILE = PROJECT_ROOT / ".env"

# 提前加载 .env，使 COM_WRITER_WORKSPACE 可用
load_dotenv(ENV_FILE, override=False)

# 工作目录（通过 .env 配置，默认 workspace）
WORKSPACE_NAME = os.environ.get("COM_WRITER_WORKSPACE", "workspace")
BASE_DIR = PROJECT_ROOT / WORKSPACE_NAME
TEMPLATE_DIR = BASE_DIR / "02 内部写作体裁模板"
RAW_RECORD_DIR = BASE_DIR / "03 原始记录资料"
OUTPUT_DIR = BASE_DIR / "04 自动写作成果"
CRAWL_DIR = BASE_DIR / "00 内网文字材料爬取"
REFINE_DIR = BASE_DIR / "01 内部写作成果提炼"
ITERATION_DIR = BASE_DIR / "05 成果迭代优化"

# 默认模板（实际路径请在 config.ini [会议纪要] 模板路径 中配置）
DEFAULT_MINUTES_TEMPLATE = TEMPLATE_DIR / "meeting_minutes_template.docx"

# 检查点
STATE_FILE = PROJECT_ROOT / ".com-writer-state.json"

# 日志
LOG_DIR = PROJECT_ROOT / "logs"
