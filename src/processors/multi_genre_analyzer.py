"""多体裁写作风格分析器 —— 细致分类研究每种文体的写作方法。

用户要求："请尽量细致地进行分类，分类进行研究"
          "每个大的体裁要进一步细分"
          "尽量分出最多的写作种类"

将内网内容按体裁分类，对每种体裁独立分析写作方法论，
最后汇总为企业级写作风格指南。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..utils.logger import get_logger
from ..utils.text import truncate
from ..config.paths import CRAWL_DIR

_log = get_logger("processor.multi_genre")

# 体裁中文名映射
GENRE_NAMES = {
    "hyjy_zcbsyb": "会议纪要",
    "ldjh_zcb": "领导讲话",
    "gzzd_zcb": "规章制度",
    "qwgk_zcb": "企务公开",
    "xwdt_zcb": "新闻动态",
    "zytz_zcb": "重要通知",
    "whyd_zcb": "委河韵动/文化",
    "aqsc_zcb": "安全生产",
    "dqwh_zcb": "党风廉政",
    "gwhb_zcb": "国企文化",
    "tpxw_zcb": "图片新闻",
    "gbmxmzyfzrdt_zcb": "领导周工作安排",
    "xmpbxxb_zcb": "项目周报",
    "xmscdtjdb_zcb": "项目生产动态",
    "xmsjgldtb_zcb": "项目设计管理",
    "sybykqtjb_zcb": "月考勤统计",
    "zzjg_zcb": "组织机构/人事",
}

# 分析 prompt（要求细分体裁）
_ANALYZE_PROMPT = """你是企业公文写作分析专家。请深入分析以下{genre_name}类型的文章，提炼该体裁的写作方法论。

文章样本（{n}篇，共{total_chars}字）：
{samples}

请输出以下JSON格式（针对"{genre_name}"这一特定体裁的写作规范）：

{{
  "体裁名称": "{genre_name}",
  "体裁定位": "（这一体裁在企业中的功能和用途）",

  "结构模板": {{
    "开头模式": ["（该体裁常见的开头方式和格式）"],
    "主体结构": "（如何组织核心内容，如：分条列项/时间顺序/逻辑递进）",
    "结尾方式": ["（如何收尾）"],
    "段落长度": "（典型段落长度和层次）"
  }},

  "语言特征": {{
    "正式词汇": ["（高频正式词汇和专业术语）"],
    "典型句式": ["（该体裁特有的句型模板）"],
    "禁用表达": ["（该体裁中不应出现的表达）"]
  }},

  "格式规范": {{
    "标题格式": "（标题的规范写法）",
    "日期时间": "（日期时间的表述方式）",
    "数字数据": "（数字和数据的引用方式）",
    "称谓规范": "（人称和职务的表述方式）"
  }},

  "写作要点": "（300字以内，该体裁写作的核心要点和方法论，供AI直接模仿）"
}}"""

# 汇总 prompt
_SYNTHESIS_PROMPT = """你是企业写作风格首席专家。以下是对企业各体裁写作风格的分项分析结果。
请综合所有体裁的分析，输出一份统一的企业写作风格指南。

各体裁分析结果：
{genre_analyses}

请输出以下JSON格式：

{{
  "企业写作总体风格": "（一句话概括）",

  "通用写作原则": [
    "（适用于所有体裁的核心原则，如：数据说话、决策导向等）"
  ],

  "各体裁要点速查": {{
    "会议纪要": "（3-5条要点，从分析中提炼）",
    "领导讲话": "（3-5条要点）",
    "新闻动态": "（3-5条要点）",
    "规章制度": "（3-5条要点）",
    "重要通知": "（3-5条要点）"
  }},

  "通用词汇库": {{
    "高频正式词汇": ["（跨体裁通用的高频正式词汇）"],
    "特色搭配": ["（该企业特有的词汇搭配）"],
    "权威表达": ["（体现权威性的表达方式）"]
  }},

  "格式通用规范": {{
    "数字表述": "（数字的规范表述方式）",
    "时间表述": "（时间的规范表述方式）",
    "称谓规范": "（称谓的规范用法）",
    "段落组织": "（段落的组织原则）"
  }},

  "会议纪要专项指南": "（500字以内的详细指南，专门针对会议纪要这一体裁，融合所有分析中与会议纪要相关的内容。包括：开头综述怎么写、议题部署怎么展开、行动项怎么表述、结尾怎么收束、语气怎么把握等）"
}}"""


def analyze_all_genres(
    llm: MultiLLMClient,
    crawl_dir: Path | None = None,
    max_per_genre: int = 25,
    max_chars_per_article: int = 3000,
) -> dict:
    """对所有体裁进行细致分类分析，返回综合风格指南。

    Args:
        llm: LLM 客户端
        crawl_dir: 爬取内容目录
        max_per_genre: 每个体裁最多分析文章数
        max_chars_per_article: 每篇文章最大字符数
    Returns:
        综合写作风格指南 dict
    """
    search_dir = crawl_dir or CRAWL_DIR
    genre_analyses: list[dict] = []

    # 按体裁逐个分析
    for cat_code, cat_name in GENRE_NAMES.items():
        cat_dir = search_dir / cat_code
        if not cat_dir.exists():
            continue

        # 收集该体裁的文章
        articles = _collect_articles(cat_dir, max_per_genre, max_chars_per_article)
        if not articles:
            continue

        _log.info("分析体裁: %s（%d篇，%d字）", cat_name, articles["count"], articles["total_chars"])

        try:
            analysis = _analyze_one_genre(llm, cat_name, articles)
            genre_analyses.append(analysis)
        except Exception as exc:
            _log.warning("体裁 %s 分析失败: %s", cat_name, str(exc)[:80])

    if not genre_analyses:
        _log.error("无体裁可分析")
        return {}

    _log.info("完成 %d 个体裁分析，开始综合汇总...", len(genre_analyses))

    # 综合汇总
    synthesis = _synthesize(llm, genre_analyses)

    return synthesis


def _collect_articles(cat_dir: Path, max_articles: int, max_chars: int) -> dict:
    """收集某一体裁的文章内容，按内容长度降序排列。"""
    files_data: list[tuple[int, str]] = []
    for f in cat_dir.glob("*.txt"):
        if f.name == "index.txt":
            continue
        try:
            text = f.read_text(encoding="utf-8")
            parts = text.split("=" * 60, 1)
            body = parts[1].strip() if len(parts) > 1 else ""
            if len(body) >= 200:
                files_data.append((len(body), body))
        except Exception:
            continue

    # 按长度降序排列，取前 max_articles 篇
    files_data.sort(key=lambda x: -x[0])
    selected = files_data[:max_articles]

    samples = []
    total = 0
    for body_len, body in selected:
        truncated = truncate(body, max_chars)
        samples.append(truncated)
        total += len(truncated)

    return {
        "samples": "\n---\n".join(samples),
        "count": len(selected),
        "total_chars": total,
    }


def _analyze_one_genre(llm: MultiLLMClient, genre_name: str, articles: dict) -> dict:
    """分析单个体裁的写作风格。"""
    raw = llm.chat(
        system_prompt="你是企业公文写作分析专家。只输出JSON，不要markdown围栏。",
        user_prompt=_ANALYZE_PROMPT.format(
            genre_name=genre_name,
            n=articles["count"],
            total_chars=articles["total_chars"],
            samples=truncate(articles["samples"], max_chars=40000),
        ),
        json_mode=True,
        temperature=0.3,
        prefer_paid=True,
        max_tokens=4096,
    )

    if not raw or not raw.strip():
        return {"体裁名称": genre_name, "分析": "失败"}

    return extract_json(raw)


def _synthesize(llm: MultiLLMClient, genre_analyses: list[dict]) -> dict:
    """综合所有体裁分析，生成统一风格指南。"""
    analyses_text = json.dumps(genre_analyses, ensure_ascii=False, indent=2)
    analyses_text = truncate(analyses_text, max_chars=50000)

    raw = llm.chat(
        system_prompt="你是企业写作风格首席专家。只输出JSON，不要markdown围栏。",
        user_prompt=_SYNTHESIS_PROMPT.format(genre_analyses=analyses_text),
        json_mode=True,
        temperature=0.3,
        prefer_paid=True,
        max_tokens=8192,
    )

    if not raw or not raw.strip():
        return {}

    return extract_json(raw)


def format_synthesis_for_prompt(synthesis: dict) -> str:
    """将综合风格指南格式化为可注入生成器的文本。"""
    parts: list[str] = []

    # 总体风格
    overall = synthesis.get("企业写作总体风格", "")
    if overall:
        parts.append(f"【企业写作总体风格】{overall}")

    # 通用原则
    principles = synthesis.get("通用写作原则", [])
    if principles:
        parts.append("【通用写作原则】\n" + "\n".join(f"  • {p}" for p in principles))

    # 各体裁要点
    genre_tips = synthesis.get("各体裁要点速查", {})
    if isinstance(genre_tips, dict):
        for genre, tips in genre_tips.items():
            if tips:
                parts.append(f"【{genre}要点】{tips}")

    # 通用词汇
    vocab = synthesis.get("通用词汇库", {})
    if isinstance(vocab, dict):
        formal_words = vocab.get("高频正式词汇", [])
        if formal_words:
            parts.append("【推荐用词】" + "、".join(formal_words[:25]))
        collocations = vocab.get("特色搭配", [])
        if collocations:
            parts.append("【特色搭配】" + "；".join(collocations[:10]))

    # 格式规范
    fmt = synthesis.get("格式通用规范", {})
    if isinstance(fmt, dict):
        for key, val in fmt.items():
            if val:
                parts.append(f"【{key}】{val}")

    # 会议纪要专项指南（最重要）
    minutes_guide = synthesis.get("会议纪要专项指南", "")
    if minutes_guide:
        parts.append(f"【会议纪要专项指南】\n{minutes_guide}")

    return "\n\n".join(parts) if parts else ""
