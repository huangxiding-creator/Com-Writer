"""路径常量 —— 项目内所有路径定义。"""
from __future__ import annotations

from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 配置文件
CONFIG_INI = PROJECT_ROOT / "config.ini"
ENV_FILE = PROJECT_ROOT / ".env"

# 工作目录
BASE_DIR = PROJECT_ROOT / "02-1 总承包事业部"
TEMPLATE_DIR = BASE_DIR / "02 内部写作体裁模板"
RAW_RECORD_DIR = BASE_DIR / "03 原始记录资料"
OUTPUT_DIR = BASE_DIR / "04 自动写作成果"
CRAWL_DIR = BASE_DIR / "00 内网文字材料爬取"
REFINE_DIR = BASE_DIR / "01 内部写作成果提炼"

# 默认模板
DEFAULT_MINUTES_TEMPLATE = TEMPLATE_DIR / "总包部项目专题会会议纪要（模板）.docx"

# 检查点
STATE_FILE = PROJECT_ROOT / ".com-writer-state.json"

# 日志
LOG_DIR = PROJECT_ROOT / "logs"
