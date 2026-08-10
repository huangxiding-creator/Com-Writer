"""本地文件夹扫描器 —— 递归扫描本地文档，支持多种格式。

用户要求："用户给定本地一个文件夹（不仅仅是网址），你也能进行同样的分析提炼"
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..utils.logger import get_logger
from .docx_reader import read_docx

_log = get_logger("readers.folder_scanner")

SUPPORTED_EXTENSIONS = {".docx", ".doc", ".txt", ".md", ".pdf"}


@dataclass(frozen=True)
class ScannedFile:
    """扫描到的文件信息。"""
    path: Path
    extension: str
    size_bytes: int
    category: str  # 按扩展名分类


def scan_folder(
    folder_path: str | Path,
    extensions: list[str] | None = None,
    recursive: bool = True,
) -> list[ScannedFile]:
    """递归扫描本地文件夹。

    Args:
        folder_path: 文件夹路径
        extensions: 允许的扩展名列表（None 用默认）
        recursive: 是否递归子目录
    Returns:
        扫描到的文件列表
    """
    folder = Path(folder_path)
    if not folder.exists():
        _log.warning("文件夹不存在: %s", folder)
        return []

    allowed = set(ext.lower() for ext in (extensions or [".docx", ".txt", ".md"]))
    if not allowed:
        allowed = SUPPORTED_EXTENSIONS

    results: list[ScannedFile] = []

    glob_method = folder.rglob if recursive else folder.glob
    for filepath in glob_method("*"):
        if not filepath.is_file():
            continue
        ext = filepath.suffix.lower()
        if ext not in allowed:
            continue
        try:
            stat = filepath.stat()
            results.append(ScannedFile(
                path=filepath,
                extension=ext,
                size_bytes=stat.st_size,
                category=ext.lstrip("."),
            ))
        except OSError:
            continue

    _log.info("扫描完成: %s | %d 个文件 | 递归=%s", folder, len(results), recursive)
    return results


def read_file_content(filepath: str | Path) -> str:
    """根据文件类型读取内容。

    Args:
        filepath: 文件路径
    Returns:
        文本内容
    """
    path = Path(filepath)
    ext = path.suffix.lower()

    if ext in (".docx",):
        return read_docx(path)
    elif ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        # PDF 读取需要额外依赖，暂用简单方式
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(path))
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            return text
        except ImportError:
            _log.warning("PDF 读取需要安装 PyMuPDF: pip install PyMuPDF")
            return ""
    else:
        _log.warning("不支持的文件格式: %s", ext)
        return ""
