"""DrissionPage 动态爬虫 —— 爬取 JavaScript 动态加载的内网文章列表。

内网很多分类页面使用 JavaScript 动态加载文章列表，普通 HTTP 只能获取索引骨架。
此模块用 DrissionPage 渲染 JS 获取文章 URL，再用 HTTP 逐篇爬取（文章页是静态 HTML）。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from ..config.loader import Config
from ..config.paths import CRAWL_DIR
from ..utils.logger import get_logger

_log = get_logger("browser.dynamic_crawler")

_BASE_DOMAIN = os.environ.get("INTRANET_HOST", "your-intranet-host")

# 全部分类（按优先级排序，会议纪要类最重要）
ALL_CATEGORIES = [
    "hyjy_zcbsyb",   # 会议纪要 97篇
    "ldjh_zcb",       # 领导讲话 20篇
    "gzzd_zcb",       # 规章制度 11篇
    "qwgk_zcb",       # 企务公开 48篇
    "xwdt_zcb",       # 新闻动态 2323篇
    "zytz_zcb",       # 重要通知 445篇
    "whyd_zcb",       # 委河韵动 125篇
    "aqsc_zcb",       # 安全生产 162篇
    "dqwh_zcb",       # 党风廉政 144篇
    "gwhb_zcb",       # 国企文化 434篇
    "tpxw_zcb",       # 图片新闻 8篇
    "gbmxmzyfzrdt_zcb",  # 领导周工作安排 115篇
    "xmpbxxb_zcb",    # 项目周报 25篇
    "xmscdtjdb_zcb",  # 项目生产动态 68篇
    "xmsjgldtb_zcb",  # 项目设计管理 56篇
    "sybykqtjb_zcb",  # 月考勤统计 76篇
]


class DynamicCrawler:
    """DrissionPage 动态爬虫。"""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._base_url = cfg.get("内网爬取", "地址", "").rstrip("/?")
        self._token = cfg.get("内网爬取", "token", "")
        self._output_dir = Path(cfg.get("内网爬取", "输出目录", str(CRAWL_DIR)))
        self._delay = 0.3
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        self._browser = None

    def crawl_category_pages(
        self,
        categories: Optional[list[str]] = None,
        max_per_category: int = 5000,
    ) -> int:
        """用 DrissionPage 渲染分类页，提取文章链接，再逐篇爬取。

        Args:
            categories: 要爬取的分类列表（None=全部）
            max_per_category: 每个分类最大文章数（5000=实际不限）
        Returns:
            新增文章数
        """
        cats = categories or ALL_CATEGORIES
        total_new = 0

        try:
            from ..browser.driver import launch_browser

            self._browser = launch_browser(headless=True)
            _log.info("DrissionPage 浏览器启动成功")

            for cat in cats:
                try:
                    new_count = self._crawl_one_category(cat, max_per_category)
                    total_new += new_count
                except Exception as exc:
                    _log.warning("分类 %s 爬取失败: %s", cat, str(exc)[:100])

        except Exception as exc:
            _log.error("动态爬取初始化失败: %s", str(exc)[:200])
            return 0
        finally:
            if self._browser:
                try:
                    self._browser.quit()
                except Exception:
                    pass

        _log.info("动态爬取完成，共获取 %d 篇新文章", total_new)
        return total_new

    def _crawl_one_category(self, category: str, max_articles: int) -> int:
        """爬取单个分类的所有文章。"""
        index_url = self._build_category_url(category)
        _log.info("动态爬取分类: %s", category)

        # Step 1: 用 DrissionPage 渲染分类页，提取文章链接
        article_links = self._extract_article_links_dynamic(index_url, category)
        _log.info("分类 %s: 发现 %d 篇文章链接", category, len(article_links))

        if not article_links:
            return 0

        # Step 2: 逐篇用 HTTP 爬取文章内容（文章页是静态 HTML）
        cat_dir = self._output_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        new_count = 0
        for i, url in enumerate(article_links[:max_articles]):
            filename = self._make_filename(url, category)
            filepath = cat_dir / filename
            if filepath.exists():
                continue

            try:
                content = self._fetch_article(url)
                if content and len(content) >= 100:
                    self._save_article(filepath, url, category, content)
                    new_count += 1
                    if new_count % 20 == 0:
                        _log.info("分类 %s: 已爬取 %d 篇...", category, new_count)
                time.sleep(self._delay)
            except Exception:
                pass

        _log.info("分类 %s: 新增 %d 篇文章", category, new_count)
        return new_count

    def _extract_article_links_dynamic(self, url: str, category: str) -> list[str]:
        """用 DrissionPage 渲染页面，提取所有文章链接（含翻页）。

        支持的分页机制：
        - Layui laypage（.layui-laypage-next 按钮）
        - 传统"下一页"文字链接
        - .next class 按钮
        """
        links: list[str] = []
        seen: set[str] = set()

        def _extract_links_from_html(html: str) -> list[str]:
            """从 HTML 提取文章链接。"""
            found: list[str] = []
            for match in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
                href = match.group(1).strip()
                if not href or href.startswith("#") or "javascript:" in href:
                    continue
                if href.endswith("index.html") or href == "/" or href.endswith("/"):
                    continue

                full_url = urljoin(url, href)
                parsed = urlparse(full_url)

                if parsed.netloc != _BASE_DOMAIN:
                    continue
                if not parsed.path.endswith(".html"):
                    continue

                if self._token and "token=" not in full_url:
                    full_url += f"{'&' if '?' in full_url else '?'}token={self._token}"

                if full_url not in seen:
                    seen.add(full_url)
                    found.append(full_url)
            return found

        try:
            page = self._browser.latest_tab
            page.get(url)
            time.sleep(3)

            # 第一页链接
            links.extend(_extract_links_from_html(page.html))

            # 翻页：Layui laypage 为主，多策略兼容
            max_pages = 500  # 安全上限
            for page_num in range(2, max_pages + 2):
                clicked = False

                # 策略1: Layui laypage 下一页按钮（最可靠）
                try:
                    next_btn = page.ele('.layui-laypage-next', timeout=2)
                    if next_btn:
                        cls = next_btn.attrs.get('class', '')
                        if 'disabled' in cls or 'layui-disabled' in cls:
                            _log.debug("分类 %s: 到达最后一页 (%d)", category, page_num - 1)
                            break
                        next_btn.click()
                        clicked = True
                except Exception:
                    pass

                # 策略2: 传统"下一页"文字链接
                if not clicked:
                    try:
                        next_btn = page.ele(f'//a[contains(text(),"下一页")]', timeout=1)
                        if next_btn:
                            cls = next_btn.attrs.get('class', '')
                            if 'disabled' in cls:
                                break
                            next_btn.click()
                            clicked = True
                    except Exception:
                        pass

                # 策略3: .next class
                if not clicked:
                    try:
                        next_btn = page.ele('.next', timeout=1)
                        if next_btn:
                            next_btn.click()
                            clicked = True
                    except Exception:
                        pass

                if not clicked:
                    break

                time.sleep(2)

                # 提取新页面链接
                before = len(links)
                new_found = _extract_links_from_html(page.html)
                links.extend(new_found)

                if len(links) == before:
                    # 没有新链接 = 到底了
                    break

                if page_num % 20 == 0:
                    _log.info("分类 %s: 已翻 %d 页, 累计 %d 篇...", category, page_num, len(links))

        except Exception as exc:
            _log.warning("DrissionPage 渲染失败: %s | %s", url[:60], str(exc)[:80])

        # 过滤：只保留属于当前分类的链接
        if links:
            filtered = [l for l in links if f"/{category}/" in l or f"/{category}" in l]
            if filtered:
                return filtered
            _log.warning("分类 %s 宽松匹配 %d 篇", category, len(links))

        return links

    def _fetch_article(self, url: str) -> Optional[str]:
        """用 HTTP 获取文章内容（文章页是静态 HTML）。"""
        try:
            resp = self._session.get(url, timeout=15)
            resp.encoding = "utf-8"
            return self._extract_content_bs4(resp.text)
        except Exception:
            return None

    def _extract_content_bs4(self, html: str) -> str:
        """借鉴 IdeaDig: 用 BeautifulSoup + lxml 做稳健内容提取。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        # 去噪：移除 script/style/nav/menu/footer 等
        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas",
                          "form", "button"]):
            tag.decompose()

        # 移除导航/页脚等噪声元素
        noise_keywords = ["nav", "menu", "footer", "header", "sidebar", "breadcrumb",
                          "pagination", "copyright", "toolbar", "login", "modal"]
        for el in soup.find_all(True):
            attrs = el.attrs
            if not isinstance(attrs, dict):
                continue
            cls = " ".join(attrs.get("class", []) or []).lower()
            el_id = str(attrs.get("id") or "").lower()
            marker = f"{cls} {el_id}"
            if any(k in marker for k in noise_keywords):
                el.decompose()

        # 定位正文区域
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(attrs={"class": re.compile(r"content|main|article|zoom|TRS_Editor|nr_content|title-content", re.I)})
            or soup.body
            or soup
        )

        # 提取纯文本
        text = main.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) > 3]

        return "\n".join(lines)

    def _build_category_url(self, category: str) -> str:
        """构建分类索引页 URL。"""
        base = self._base_url.split("?")[0].rstrip("/")
        url = f"{base}/{category}/index.html"
        if self._token:
            url += f"?token={self._token}"
        return url

    def _make_filename(self, url: str, category: str) -> str:
        """生成文件名。"""
        parsed = urlparse(url)
        stem = Path(parsed.path).stem
        match = re.search(r"/(\d{6})/(\d{8})/", url)
        if match:
            d = match.group(2)
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}_{stem}.txt"
        return f"{stem}.txt"

    def _save_article(self, filepath: Path, url: str, category: str, content: str) -> None:
        """保存文章到文件。"""
        header = f"来源: {url}\n"
        header += f"分类: {category}\n"
        header += f"字数: {len(content)}\n"
        header += f"{'='*60}\n\n"
        filepath.write_text(header + content, encoding="utf-8")
