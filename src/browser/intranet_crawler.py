"""通用整站爬虫 —— 深度递归爬取任意网站全部内容。

通用设计：
1. 自动检测站点前缀（从起始 URL 提取路径根，不硬编码）
2. BFS 广度优先遍历整站所有同域链接
3. URL 去重、分类存储、文档下载
4. 支持任意 HTTP 站点（内网/外网均可）

特性：
- full_site=True: 递归遍历整站所有页面
- full_site=False: 仅爬取起始页上的文章链接
- 自动跳过静态资源/媒体文件
- 礼貌延迟（避免压垮服务器）
- 增量爬取（已下载的文件跳过）
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs

import requests

from ..config.loader import Config
from ..config.paths import CRAWL_DIR
from ..utils.logger import get_logger

_log = get_logger("browser.crawler")

# 静态资源/媒体文件后缀（跳过）
_DOC_EXTENSIONS = {".docx", ".doc", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx"}
_MEDIA_EXTENSIONS = {".mp4", ".mp3", ".avi", ".mov", ".wmv", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".bmp"}
_SKIP_EXTENSIONS = {".css", ".js", ".ico", ".woff", ".woff2", ".ttf", ".eot"}

# 导航页面的特征（内容太短的页面视为导航页）
_MIN_CONTENT_CHARS = 200


@dataclass(frozen=True)
class CrawledPage:
    """爬取的页面/文件。"""
    url: str
    title: str
    category: str
    content: str
    date_hint: str = ""
    filename: str = ""
    is_document: bool = False
    content_length: int = 0  # 正文字符数（用于质量过滤）


class IntranetCrawler:
    """通用整站爬虫 —— 适应任意网站。"""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._base_url = cfg.get("内网爬取", "地址", "").rstrip("/?")
        self._token = cfg.get("内网爬取", "token", "")
        self._output_dir = Path(cfg.get("内网爬取", "输出目录", str(CRAWL_DIR)))
        self._delay = 0.5  # 请求间隔（秒）
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        self._visited: set[str] = set()
        self._queue: deque[str] = deque()
        self._site_prefix: str = ""  # 自动检测的站点前缀

    def crawl(
        self,
        max_pages: int = 0,
        full_site: bool = True,
        categories: Optional[list[str]] = None,
    ) -> list[CrawledPage]:
        """爬取网站。

        Args:
            max_pages: 最大页面数（0=不限制，全量爬取）
            full_site: True=递归整站爬取; False=仅起始页文章
            categories: 限制爬取的类别目录（None=全部）
        Returns:
            爬取到的页面/文件列表
        """
        if not self._base_url:
            _log.warning("爬取地址未配置，跳过爬取")
            return []

        self._output_dir.mkdir(parents=True, exist_ok=True)
        start_url = self._build_url()

        # ★ 自动检测站点前缀（从起始 URL 提取，不硬编码）
        self._site_prefix = self._detect_site_prefix(start_url)
        _log.info("站点前缀检测: %s", self._site_prefix or "(整域)")

        self._visited.clear()
        self._queue.clear()
        self._queue.append(start_url)

        results: list[CrawledPage] = []
        count = 0

        _log.info("开始%s爬取: %s", "整站" if full_site else "文章", start_url)

        while self._queue:
            if max_pages > 0 and count >= max_pages:
                _log.info("达到最大页面数 %d，停止爬取", max_pages)
                break

            url = self._queue.popleft()

            # URL 去重
            url_key = self._url_key(url)
            if url_key in self._visited:
                continue
            self._visited.add(url_key)

            count += 1
            _log.info("[%d] 爬取: %s", count, url[:100])

            try:
                page = self._fetch_page(url, full_site, categories)

                if page:
                    results.append(page)
                    self._save_page(page)

            except Exception as exc:
                _log.warning("爬取失败: %s | %s", url[:80], str(exc)[:100])

            time.sleep(self._delay)

        # 统计
        content_pages = [p for p in results if p.content_length >= _MIN_CONTENT_CHARS]
        nav_pages = [p for p in results if p.content_length < _MIN_CONTENT_CHARS]
        docs = [p for p in results if p.is_document]

        _log.info("爬取完成: 共访问 %d 个URL | 有效内容页: %d | 导航页: %d | 文档: %d",
                  len(self._visited), len(content_pages), len(nav_pages), len(docs))
        return results

    def _detect_site_prefix(self, url: str) -> str:
        """从起始 URL 自动检测站点前缀路径。

        策略：取 URL 路径的第一个目录段作为站点根。
        例如 http://your-host/pub/zcbsyb/index.html → /pub/zcbsyb/
        """
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]

        if len(path_parts) <= 1:
            return ""  # 根域名，不限制路径

        # 取前两级目录作为站点前缀（如 /pub/zcbsyb/）
        # 如果只有一级目录，取一级
        depth = min(2, len(path_parts))
        prefix = "/" + "/".join(path_parts[:depth]) + "/"
        return prefix

    def _fetch_page(
        self, url: str, extract_links: bool, categories: Optional[list[str]]
    ) -> Optional[CrawledPage]:
        """获取单个页面或文件。"""
        # 判断是否为文档文件
        path = urlparse(url).path.lower()
        ext = Path(path).suffix

        # 跳过媒体和静态资源
        if ext in _SKIP_EXTENSIONS or ext in _MEDIA_EXTENSIONS:
            return None

        # 文档文件：下载
        if ext in _DOC_EXTENSIONS:
            return self._download_document(url)

        # HTML 页面：获取并提取内容
        try:
            resp = self._session.get(url, timeout=15)
            resp.encoding = self._detect_encoding(resp)
            html = resp.text
        except Exception as exc:
            _log.warning("获取页面失败: %s | %s", url[:80], str(exc)[:80])
            return None

        # ★ 整站模式：先从原始 HTML 提取链接（在内容清洗之前）
        if extract_links:
            new_links = self._extract_internal_links(html, url)
            for link in new_links:
                link_key = self._url_key(link)
                if link_key not in self._visited:
                    self._queue.append(link)
            if new_links:
                _log.debug("发现 %d 个新链接", len(new_links))

        # 提取标题
        title_match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        title = title_match.group(1).strip() if title_match else "未命名"
        title = title.replace("&nbsp;", " ").strip()

        # 提取正文
        content = self._extract_content(html)
        content_length = len(content)

        if not content or content_length < _MIN_CONTENT_CHARS:
            # 导航页/索引页：保留链接信息但标记为短内容
            content = self._extract_all_text(html)
            content_length = len(content)

        # 分类
        category = self._get_category(url)

        # 类别过滤
        if categories and category not in categories:
            return None

        # 日期提示
        date_hint = self._extract_date(url)

        filename = self._make_filename(url, category, date_hint, ext=".txt")

        return CrawledPage(
            url=url,
            title=title,
            category=category,
            content=content,
            date_hint=date_hint,
            filename=filename,
            is_document=False,
            content_length=content_length,
        )

    def _detect_encoding(self, resp: requests.Response) -> str:
        """自动检测响应编码。"""
        # 先检查 Content-Type header
        ct = resp.headers.get("Content-Type", "")
        if "charset=" in ct:
            charset = ct.split("charset=")[-1].strip().split(";")[0]
            if charset:
                return charset
        # 检查 HTML meta 标签
        if resp.content[:500].find(b"charset=") != -1:
            meta = resp.content[:500].decode("ascii", errors="ignore")
            match = re.search(r'charset=["\']?([\w-]+)', meta, re.I)
            if match:
                return match.group(1)
        # 默认 UTF-8（现代网站多数用 UTF-8）
        return "utf-8"

    def _download_document(self, url: str) -> Optional[CrawledPage]:
        """下载文档文件（.docx/.pdf 等）。"""
        try:
            resp = self._session.get(url, timeout=30)
            if resp.status_code != 200:
                return None

            path = urlparse(url).path
            ext = Path(path).suffix
            category = self._get_category(url)
            date_hint = self._extract_date(url)
            filename = self._make_filename(url, category, date_hint, ext=ext)

            # 保存二进制文件
            filepath = self._output_dir / category / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(resp.content)

            _log.info("下载文档: %s (%d bytes)", filename, len(resp.content))

            return CrawledPage(
                url=url,
                title=filename,
                category=category,
                content=f"[文档已下载: {filename}]",
                date_hint=date_hint,
                filename=filename,
                is_document=True,
                content_length=len(resp.content),
            )
        except Exception as exc:
            _log.warning("文档下载失败: %s | %s", url[:80], str(exc)[:80])
            return None

    def _extract_content(self, html: str) -> str:
        """从 HTML 提取正文文本。"""
        # 移除 script/style/comment
        clean = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
        clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.I)
        clean = re.sub(r"<!--.*?-->", "", clean, flags=re.DOTALL)

        # 尝试提取内容区域（通用模式匹配）
        content_patterns = [
            r'<div[^>]*class="[^"]*(?:content|article[_-]?(?:body|content|text)|'
            r'main[_-]?content|detail[_-]?content|news[_-]?content|title-content|'
            r'TRS_Editor|zoom|article)[^"]*"[^>]*>'
            r'(.*?)</div>',
            r'<div[^>]*class="[^"]*(?:text|body|nr_content|zw)[^"]*"[^>]*>(.*?)</div>',
        ]

        for pattern in content_patterns:
            matches = re.findall(pattern, clean, re.DOTALL | re.I)
            if matches:
                # 取最长的匹配（通常是正文）
                longest = max(matches, key=len)
                clean = longest
                break

        # 去标签
        text = re.sub(r"<[^>]+>", "\n", clean)
        # HTML 实体
        entities = {
            "&nbsp;": " ", "&ldquo;": """, "&rdquo;": """,
            "&amp;": "&", "&lt;": "<", "&gt;": ">",
            "&middot;": "·", "&hellip;": "…",
            "&mdash;": "—", "&ndash;": "–",
            "&times;": "×", "&divide;": "÷",
        }
        for entity, char in entities.items():
            text = text.replace(entity, char)
        # 清理空白
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        content_lines = [line for line in lines if len(line) > 5]

        return "\n".join(content_lines)

    def _extract_all_text(self, html: str) -> str:
        """提取页面所有可见文本（用于索引页/导航页）。"""
        clean = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
        clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", "\n", clean)
        text = text.replace("&nbsp;", " ")
        lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) > 5]
        return "\n".join(lines[:50])  # 索引页只保留前50行

    def _extract_internal_links(self, html: str, base_url: str) -> list[str]:
        """从 HTML 中提取同站链接（基于自动检测的站点前缀）。"""
        links: list[str] = []
        seen: set[str] = set()

        # 基准 URL 的域
        base_parsed = urlparse(base_url)
        base_domain = base_parsed.netloc

        # 匹配所有 href
        for match in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
            href = match.group(1).strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # 构建完整 URL
            full_url = urljoin(base_url, href)

            parsed = urlparse(full_url)

            # 只保留同域链接
            if parsed.netloc != base_domain:
                continue

            # 如果有站点前缀，只保留前缀下的链接
            if self._site_prefix and self._site_prefix not in parsed.path:
                continue

            # 跳过静态资源
            ext = Path(parsed.path).suffix.lower()
            if ext in _SKIP_EXTENSIONS or ext in _MEDIA_EXTENSIONS:
                continue

            # 添加 token（如果配置了）
            if self._token and "token=" not in full_url:
                full_url += f"{'&' if '?' in full_url else '?'}token={self._token}"

            if full_url not in seen and full_url not in self._visited:
                seen.add(full_url)
                links.append(full_url)

        return links

    def _build_url(self) -> str:
        """构建带 token 的 URL。"""
        url = self._base_url
        if self._token:
            sep = "&" if "?" in url else "?"
            if "token=" not in url:
                url += f"{sep}token={self._token}"
        return url

    def _url_key(self, url: str) -> str:
        """URL 去重 key（去掉 token 参数的差异）。"""
        parsed = urlparse(url)
        return f"{parsed.netloc}{parsed.path}"

    def _get_category(self, url: str) -> str:
        """从 URL 提取分类目录。"""
        parsed = urlparse(url)
        parts = parsed.path.split("/")

        # 如果有站点前缀，从前缀之后取分类
        if self._site_prefix:
            prefix_parts = [p for p in self._site_prefix.split("/") if p]
            if prefix_parts:
                try:
                    # 找到前缀最后一部分在 URL 中的位置
                    last_prefix = prefix_parts[-1]
                    idx = parts.index(last_prefix)
                    if idx + 1 < len(parts):
                        return parts[idx + 1]
                except ValueError:
                    pass

        # 通用策略：取路径中有意义的目录段
        meaningful = [p for p in parts if p and not p.endswith(".html") and len(p) > 2]
        if len(meaningful) > 1:
            return meaningful[1]
        return "other"

    def _extract_date(self, url: str) -> str:
        """从 URL 路径提取日期。"""
        # 匹配 YYYYMM/YYYYMMDD 模式
        match = re.search(r"/(\d{6})/(\d{8})/", url)
        if match:
            d = match.group(2)
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return ""

    def _make_filename(self, url: str, category: str, date_hint: str, ext: str) -> str:
        """生成文件名。"""
        parsed = urlparse(url)
        stem = Path(parsed.path).stem

        if date_hint:
            return f"{date_hint}_{stem}{ext}"
        return f"{stem}{ext}"

    def _save_page(self, page: CrawledPage) -> None:
        """保存页面到文件。"""
        if page.is_document:
            return  # 文档已在 _download_document 中保存

        filepath = self._output_dir / page.category / page.filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # 增量：跳过已存在
        if filepath.exists():
            return

        header = f"标题: {page.title}\n"
        header += f"分类: {page.category}\n"
        if page.date_hint:
            header += f"日期: {page.date_hint}\n"
        header += f"来源: {page.url}\n"
        header += f"字数: {page.content_length}\n"
        header += f"{'='*60}\n\n"

        filepath.write_text(header + page.content, encoding="utf-8")
