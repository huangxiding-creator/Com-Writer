"""DrissionPage 浏览器驱动 —— 反检测参数 + Chrome/Edge 自动发现 + 持久化用户数据。

借鉴 We-AIPO src/browser/driver.py 的成熟设计：
- Chrome 优先，Edge 兜底
- 反检测参数（disable-blink-features 等）
- 持久化 profile（CRC32 端口哈希，保持登录态）
"""
from __future__ import annotations

import os
import zlib
from pathlib import Path
from typing import Optional

from ..utils.logger import get_logger
from ..config.paths import PROJECT_ROOT

_log = get_logger("browser.driver")

_PERSIST_BASE_PORT = 9300

_CANDIDATE_BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

_ANTI_DETECTION_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=IsolateOrigins,site-per-process",
]

_BROWSER_PROFILE_DIR = PROJECT_ROOT / ".browser_profile"


def detect_browser_path() -> Optional[str]:
    """按优先级探测本机已安装的 Chrome / Edge。"""
    for candidate in _CANDIDATE_BROWSER_PATHS:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _stable_port_for(profile: Path) -> int:
    """按 profile 名生成稳定端口（CRC32），保证持久 profile 的登录态/cookie 复用。"""
    return _PERSIST_BASE_PORT + (zlib.crc32(profile.name.encode("utf-8")) % 200)


def build_chromium_options(
    user_data_path: Optional[Path] = None,
    headless: bool = True,
    extra_args: Optional[list] = None,
) -> "ChromiumOptions":
    """构建 ChromiumOptions，含反检测参数。"""
    from DrissionPage import ChromiumOptions

    co = ChromiumOptions()
    browser_path = detect_browser_path()
    if browser_path:
        co.set_browser_path(browser_path)
    else:
        _log.warning("未发现 Chrome/Edge，DrissionPage 将尝试系统默认浏览器")

    if headless:
        co.set_argument("--headless=new")

    for arg in _ANTI_DETECTION_ARGS:
        co.set_argument(arg)
    for arg in (extra_args or []):
        co.set_argument(arg)

    profile = user_data_path or (_BROWSER_PROFILE_DIR / "default")
    profile.mkdir(parents=True, exist_ok=True)
    co.set_user_data_path(str(profile))
    co.set_local_port(_stable_port_for(profile))

    return co


def launch_browser(headless: bool = True):
    """启动 DrissionPage 浏览器实例。

    Returns:
        Chromium 实例
    """
    from DrissionPage import Chromium

    options = build_chromium_options(headless=headless)
    try:
        return Chromium(options)
    except Exception as exc:
        _log.warning("DrissionPage 启动失败，尝试附加已运行实例: %s", str(exc)[:80])
        return Chromium()


def close_browser(browser) -> None:
    """安全关闭浏览器。"""
    try:
        browser.quit()
    except Exception as exc:
        _log.debug("关闭浏览器异常（可忽略）: %s", exc)
