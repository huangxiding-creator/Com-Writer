"""通用写作精髓提取器 —— 从任意文本资料中提炼写作风格方法论。

通用设计：
1. 可从任意目录读取文本文件（爬取的网页、本地文档、历史成果等）
2. 按内容质量排序，优先分析内容丰富的文章
3. 多维度提炼：用词、句式、结构、语气、格式
4. 输出可复用的"风格方法论参考"注入生成器

用户要求："一定要把总包部内网所有资料研究清楚了，提炼出写作方法论后再去编写会议纪要"
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..utils.logger import get_logger
from ..utils.text import truncate
from ..config.paths import CRAWL_DIR

_log = get_logger("processor.style_extractor")

# 内容质量过滤
_MIN_ARTICLE_CHARS = 500  # 少于此字数视为导航页，跳过

_SYSTEM_PROMPT = """你是企业公文写作风格分析专家。你的任务是深入分析多篇企业内部文章，系统性地提炼出该企业的写作方法论（而非简单的词汇罗列）。

你不仅要发现表面的用词偏好，更要挖掘深层次的写作规律：
1. **思维方式**：该企业如何切入问题、如何组织论证、如何给出结论
2. **话语体系**：特色术语、高频搭配、权威引用方式
3. **结构范式**：文章的典型组织方式、段落展开模式、逻辑链条
4. **表达风格**：语气基调、修辞手法、感情色彩
5. **格式规范**：数字表述、时间表述、量词使用、称谓规范

只输出JSON，不要markdown围栏，不要解释。"""

_USER_TEMPLATE = """请深入分析以下企业内部文章样本，系统性地提炼该企业的写作方法论。

文章样本（共{n}篇，{total_chars}字）：
{samples}

请输出以下JSON格式（每项要具体、可操作、可模仿）：

{{
  "企业名称": "（从文章内容推断）",
  "核心文风定位": "（一句话概括，如：权威务实、数据驱动、决策导向）",

  "思维方式特征": [
    "（该企业如何切入问题、组织论证、给出结论的模式，每条一个具体特征）"
  ],

  "话语体系": {{
    "高频正式词汇": ["（该企业高频使用的正式词汇/专业术语）"],
    "特色搭配": ["（如'扎实推进''统筹谋划''系统推进'等典型搭配）"],
    "禁用口语": ["（该企业文风中不会出现的口语化表达，用于排除）"]
  }},

  "结构范式": {{
    "典型开头方式": ["（文章/段落常见的开头模式，如'会议指出...''XX牵头组织...'）"],
    "论证展开模式": ["（如何展开一个论点或工作部署）"],
    "结尾收束方式": ["（如何结尾，如强调要求、明确时限等）"],
    "段落长度偏好": "（如：200-400字/段，层次分明）"
  }},

  "表达风格": {{
    "语气基调": "（如：权威务实、积极向上、严谨规范）",
    "常用修辞": ["（如排比、对偶、数字概括等）"],
    "数据引用方式": "（如何引用技术参数、统计数据）"
  }},

  "写作方法论总结": "（500字以内，系统总结该企业的写作方法论，包括：如何选题立意、如何组织结构、如何遣词造句、如何收尾定调。供AI写作助手直接模仿使用）"
}}"""


def extract_style(
    llm: MultiLLMClient,
    articles_dir: Path | None = None,
    max_articles: int = 30,
    max_chars_per_article: int = 3000,
) -> Optional[dict]:
    """从文本资料中系统性提炼写作风格方法论。

    Args:
        llm: LLM 客户端
        articles_dir: 文本资料目录（None 用默认爬取目录）
        max_articles: 分析的最大文章数
        max_chars_per_article: 每篇文章截取的最大字符数
    Returns:
        风格方法论 dict，失败返回 None
    """
    search_dir = articles_dir or CRAWL_DIR
    if not search_dir.exists():
        _log.warning("资料目录不存在: %s", search_dir)
        return None

    # 收集文章文件
    article_files = sorted(search_dir.rglob("*.txt"))
    if not article_files:
        _log.warning("资料目录无文本文件: %s", search_dir)
        return None

    # ★ 按内容质量排序：读取所有文件，按正文长度降序排列
    # 这样优先分析内容丰富的文章，跳过导航页/索引页
    articles_data: list[tuple[int, Path, str]] = []
    for filepath in article_files:
        try:
            text = filepath.read_text(encoding="utf-8")
            # 提取正文部分（跳过头部元数据）
            parts = text.split("=" * 60, 1)
            body = parts[1].strip() if len(parts) > 1 else text
            body_len = len(body)
            if body_len >= _MIN_ARTICLE_CHARS:
                articles_data.append((body_len, filepath, body))
        except Exception:
            continue

    if not articles_data:
        _log.warning("无有效文章内容（所有文件都太短）")
        return None

    # 按内容长度降序排列（长文章优先）
    articles_data.sort(key=lambda x: x[0], reverse=True)

    # 选取前 max_articles 篇
    selected = articles_data[:max_articles]

    _log.info("写作风格分析 | 候选文章: %d 篇 | 选用: %d 篇 | 总可用字数: %d",
              len(articles_data), len(selected), sum(d[0] for d in selected))

    # 读取并拼接文章内容
    samples: list[str] = []
    total_chars = 0
    for body_len, filepath, body in selected:
        truncated_body = truncate(body, max_chars_per_article)
        samples.append(f"【文章: {filepath.stem}（{body_len}字）】\n{truncated_body}\n")
        total_chars += len(truncated_body)

    if not samples:
        _log.warning("无法读取任何文章内容")
        return None

    combined = "\n---\n".join(samples)
    combined = truncate(combined, max_chars=60000)  # 限制总输入长度

    _log.info("开始提炼写作方法论 | 样本: %d 篇 | 输入 %d 字",
              len(selected), len(combined))

    raw = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_USER_TEMPLATE.format(
            n=len(selected),
            total_chars=total_chars,
            samples=combined,
        ),
        json_mode=True,
        temperature=0.3,
        prefer_paid=True,  # 风格分析需要高质量
        max_tokens=8192,  # 需要更大输出空间用于详细方法论
    )

    if not raw or not raw.strip():
        _log.warning("LLM 返回空响应，风格提取失败")
        return None

    style = extract_json(raw)
    _log.info("写作方法论提炼完成 | 企业: %s | 文风: %s",
              style.get("企业名称", "未知"),
              style.get("核心文风定位", "未知"))

    return style


def format_style_for_prompt(style: dict) -> str:
    """将风格方法论格式化为可注入生成器 prompt 的文本。

    输出结构化的方法论参考，让生成器能深度模仿。
    """
    parts: list[str] = []

    # 核心定位
    core = style.get("核心文风定位", "")
    if core:
        parts.append(f"【文风定位】{core}")

    # 思维方式
    thinking = style.get("思维方式特征", [])
    if thinking:
        parts.append("【思维方式】" + "；".join(thinking[:5]))

    # 话语体系
    discourse = style.get("话语体系", {})
    if isinstance(discourse, dict):
        vocab = discourse.get("高频正式词汇", [])
        if vocab:
            parts.append("【推荐用词】" + "、".join(vocab[:20]))
        collocations = discourse.get("特色搭配", [])
        if collocations:
            parts.append("【特色搭配】" + "；".join(collocations[:8]))

    # 结构范式
    structure = style.get("结构范式", {})
    if isinstance(structure, dict):
        openings = structure.get("典型开头方式", [])
        if openings:
            parts.append("【段落开头】" + "；".join(openings[:5]))
        patterns = structure.get("论证展开模式", [])
        if patterns:
            parts.append("【展开模式】" + "；".join(patterns[:3]))

    # 表达风格
    expression = style.get("表达风格", {})
    if isinstance(expression, dict):
        tone = expression.get("语气基调", "")
        if tone:
            parts.append(f"【语气基调】{tone}")
        data_style = expression.get("数据引用方式", "")
        if data_style:
            parts.append(f"【数据引用】{data_style}")

    # 写作方法论总结
    methodology = style.get("写作方法论总结", "")
    if methodology:
        parts.append(f"【方法论总结】\n{methodology}")

    return "\n".join(parts) if parts else ""
