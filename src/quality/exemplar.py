"""黄金范例库 —— 从定稿范文中抽取优质段落做 few-shot 注入。

核心思想（Layer 1，最强质量杠杆）：
LLM 遵循抽象规则的能力 << 模仿真实范例的能力。
把真实的定稿段落作为 few-shot 注入生成 prompt，
生成质量会有数量级的提升（show, don't tell）。

范例来源：
1. workspace 的 05 成果迭代优化（初稿-定稿对中的定稿）
2. workspace 的 01 内部写作成果提炼
3. 06/07 等参考文档目录

按体裁自动分类索引，检索时返回与当前主题最相关的 K 段。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..utils.logger import get_logger
from ..workspace.manager import WorkspaceManager

_log = get_logger("quality.exemplar")


# ════════════════════════════════════════════════════════
#  体裁识别
# ════════════════════════════════════════════════════════

_GENRE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "请示件": ("的请示", "妥否", "请批示", "公司领导"),
    "会议纪要": ("会议纪要", "会议认为", "会议指出", "会议要求", "会议强调", "纪要如下"),
    "正式函件": ("的函", "贵单位", "贵司", "特此函"),
    "通知通报": ("的通知", "的通报", "现将有关"),
}


def detect_genre(text: str) -> str:
    """根据文本特征识别体裁。"""
    scores = {
        genre: sum(1 for kw in kws if kw in text)
        for genre, kws in _GENRE_KEYWORDS.items()
    }
    best = max(scores, key=lambda g: scores[g])
    return best if scores[best] > 0 else ""


# ════════════════════════════════════════════════════════
#  范例段落
# ════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Exemplar:
    """一段黄金范例。"""
    genre: str        # 体裁
    source: str       # 来源文件名
    paragraph: str    # 范例正文段落


_MIN_PARA_LEN = 80    # 太短无学习价值
_MAX_PARA_LEN = 500   # 太长稀释 prompt


def _split_paragraphs(text: str) -> list[str]:
    """按 docx 提取的连续文本切分段落。"""
    paras = [p.strip() for p in re.split(r"[\n\r]+", text) if p.strip()]
    if len(paras) <= 1 and len(text) > 400:
        # docx 提取常丢失换行：按句号密度切分
        sentences = re.split(r"(?<=[。！？])", text)
        paras, buf = [], ""
        for s in sentences:
            buf += s
            if len(buf) >= 200:
                paras.append(buf)
                buf = ""
        if buf:
            paras.append(buf)
    return paras


def _is_golden(paragraph: str, genre: str) -> bool:
    """判断段落是否为黄金范例（体裁特征 + 长度 + 书面度）。"""
    if not (_MIN_PARA_LEN <= len(paragraph) <= _MAX_PARA_LEN):
        return False
    # 口语/噪音过滤
    if re.search(r"[？?]{2,}|说话人|然后呢|那个啥|嗯[，。]", paragraph):
        return False
    # 体裁特征分
    kws = _GENRE_KEYWORDS.get(genre, ())
    hits = sum(1 for kw in kws if kw in paragraph)
    if genre == "会议纪要":
        return hits >= 1
    # 其他体裁：段首引导词或含体裁关键词
    return hits >= 1 or paragraph.startswith(("根据", "鉴于", "目前", "为", "关于"))


class ExemplarLibrary:
    """黄金范例库：扫描 workspace 定稿 → 按体裁索引 → 相关性检索。"""

    def __init__(self, workspace=None) -> None:
        self._by_genre: dict[str, list[Exemplar]] = {}
        self._load(workspace)

    def _load(self, workspace) -> None:
        try:
            if workspace is None:
                workspace = WorkspaceManager().active
        except Exception as e:
            _log.warning("workspace 加载失败: %s", e)
            return

        scan_dirs: list[Path] = []
        # 05 成果迭代优化（定稿） + 06/07 参考目录
        base = workspace.path
        if base.exists():
            for child in sorted(base.iterdir()):
                name = str(child.name)
                if not child.is_dir():
                    continue
                if name.startswith(("05", "06", "07", "01")):
                    scan_dirs.append(child)

        for d in scan_dirs:
            for f in sorted(d.rglob("*.docx")):
                if f.name.startswith(("~$", "$")):
                    continue
                # 只取"定稿/修改稿/最终"或 07 模板学习目录的文件
                fname = f.name
                is_final = any(kw in fname for kw in ("定稿", "修改稿", "最终", "终稿"))
                in_learning_dir = str(d.name).startswith(("07",))
                if not (is_final or in_learning_dir):
                    continue
                text = self._read_docx(f)
                if not text:
                    continue
                genre = detect_genre(text)
                if not genre:
                    continue
                for para in _split_paragraphs(text):
                    if _is_golden(para, genre):
                        self._by_genre.setdefault(genre, []).append(
                            Exemplar(genre, fname, para)
                        )

        total = sum(len(v) for v in self._by_genre.values())
        _log.info(
            "范例库加载完成: %d 个体裁, 共 %d 段范例 %s",
            len(self._by_genre), total,
            {g: len(v) for g, v in self._by_genre.items()},
        )

    @staticmethod
    def _read_docx(path: Path) -> str:
        """轻量 docx 文本提取（保留段落边界，兼容损坏文件）。"""
        try:
            import zipfile
            with zipfile.ZipFile(str(path)) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
                # 段落边界 → 换行（避免标题/称呼/正文粘连）
                parts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>|(</w:p>)", xml)
                return "".join(t if t else "\n" for t, _p in parts)
        except Exception:
            return ""

    # ── 检索 ──

    def retrieve(self, genre: str, context: str = "", k: int = 3) -> list[Exemplar]:
        """检索与上下文最相关的 K 段范例（词重叠打分）。"""
        pool = self._by_genre.get(genre, [])
        if not pool:
            # 体裁名模糊匹配（如「请示件」vs 库内「请示件」）
            for g, v in self._by_genre.items():
                if genre in g or g in genre:
                    pool = v
                    break
        if not pool:
            return []

        if not context:
            return pool[:k]

        ctx_chars = set(re.findall(r"[一-龥]", context))
        def _score(ex: Exemplar) -> tuple[int, int]:
            para_chars = set(re.findall(r"[一-龥]", ex.paragraph))
            overlap = len(ctx_chars & para_chars)
            # 打破平局：较长段落信息量更大
            return (overlap, min(len(ex.paragraph), 400))

        ranked = sorted(pool, key=_score, reverse=True)
        # 去重：相似度极高的段落只保留一个
        result: list[Exemplar] = []
        for ex in ranked:
            if any(_similarity(ex.paragraph, r.paragraph) > 0.7 for r in result):
                continue
            result.append(ex)
            if len(result) >= k:
                break
        return result

    def stats(self) -> dict[str, int]:
        return {g: len(v) for g, v in self._by_genre.items()}


def _similarity(a: str, b: str) -> float:
    """字符 Jaccard 相似度（快速去重用）。"""
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def build_fewshot_block(exemplars: list[Exemplar], genre: str) -> str:
    """把范例列表转成 prompt 注入块。"""
    if not exemplars:
        return ""
    lines = [f"★★★ {genre} 黄金范例（本单位真实定稿，写作风格和详实度必须对齐）★★★"]
    for i, ex in enumerate(exemplars, 1):
        lines.append(f"【范例{i}】（来源: {ex.source}）")
        lines.append(ex.paragraph)
        lines.append("")
    lines.append("要求：你的输出在语言风格、数据密度、句式结构上必须达到上述范例的水平。")
    return "\n".join(lines)
