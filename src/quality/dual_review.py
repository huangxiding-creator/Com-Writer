"""双盲交叉评审 —— 两个独立视角 + 交叉模型，破除 LLM 自我偏好。

Layer 2 设计（针对 LLM-as-judge 的 self-preference bias）：
- 评审A「内容官」：对照原文审查数据一致性、事实完整性（用另一家模型）
- 评审B「格式官」：对照体裁规范审查结构、语言、风格
- 两官独立打分，输出结构化问题清单，供 Self-Refine 定向修复

交叉模型策略：
- 生成用 GLM → 评审用 DeepSeek（反之亦然）
- 若只配了一个厂商，则用「角色隔离」prompt 强制视角分离（次优）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..utils.logger import get_logger

_log = get_logger("quality.dual_review")


# ════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════

@dataclass
class ReviewIssue:
    """一个评审问题。"""
    reviewer: str      # content / format
    dimension: str     # 维度名
    severity: str      # critical / major / minor
    location: str      # 位置描述
    description: str   # 问题描述
    fix_hint: str      # 修复建议


@dataclass
class DualReviewReport:
    """双盲评审报告。"""
    content_score: int          # 内容官评分 0-100
    format_score: int           # 格式官评分 0-100
    issues: list[ReviewIssue] = field(default_factory=list)
    raw_feedback: str = ""      # 原始反馈（注入 refine）

    @property
    def overall(self) -> int:
        return (self.content_score + self.format_score) // 2

    @property
    def passed(self) -> bool:
        return self.overall >= 85 and not self.critical_issues

    @property
    def critical_issues(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "critical"]


# ════════════════════════════════════════════════════════
#  评审官 A: 内容官（数据一致性 / 事实完整性）
# ════════════════════════════════════════════════════════

_CONTENT_OFFICER_SYSTEM = """你是一位铁面无私的公文内容审查官。你与撰写者无任何利害关系，唯一职责是对照原始材料逐项核对成果文件的**事实准确性**。

审查维度（只看内容，不管格式）：
1. 数据准确性：每个数字（金额/日期/参数/文号）是否与原始材料一致
2. 事实完整性：原始材料中的关键事项、决策、行动项是否有遗漏
3. 逻辑一致性：时间线是否自洽，前后表述是否矛盾
4. 无中生有：是否有原始材料不存在的事实、承诺、结论

★ 判分纪律（从严）★
- 任何一个关键数字错误 = critical
- 遗漏关键决策或行动项 = major
- 表述含糊但事实正确 = minor

只输出JSON。"""

_CONTENT_OFFICER_TEMPLATE = """【原始材料】（事实的唯一来源）：
{source_excerpt}

【待审成果】：
{generated_text}

请逐项核对，输出：

{{
  "评分": 0到100,
  "问题列表": [
    {{"维度": "数据准确性|事实完整性|逻辑一致性|无中生有",
      "严重度": "critical|major|minor",
      "位置": "问题所在段落/句子",
      "描述": "具体问题",
      "修复建议": "怎么改"}}
  ]
}}"""


# ════════════════════════════════════════════════════════
#  评审官 B: 格式官（体裁规范 / 语言风格）
# ════════════════════════════════════════════════════════

_FORMAT_OFFICER_SYSTEM = """你是一位资深的国企公文格式审查官。你不关心内容对错，只审查**体裁规范和语言质量**。

审查维度（只看格式和语言）：
1. 结构规范：标题/称呼/正文层次/结尾用语/落款是否符合该体裁法定格式
2. 语言书面度：是否残留口语、模糊词（大概/可能/也许）、冗余修饰
3. 句式力度：是否使用该体裁的标准句式（如请示的"妥否，请批示"）
4. 段落质量：段落长度是否充实（不空洞）、引导句是否规范

★ 判分纪律 ★
- 缺失法定格式要素（如请示无"妥否，请批示"）= critical
- 口语/模糊词残留 = major
- 表述可再精炼 = minor

只输出JSON。"""

_FORMAT_OFFICER_TEMPLATE = """【体裁】：{genre}
【该体裁规范要点】：
{skill_rules}

【待审成果】：
{generated_text}

请审查格式和语言，输出：

{{
  "评分": 0到100,
  "问题列表": [
    {{"维度": "结构规范|语言书面度|句式力度|段落质量",
      "严重度": "critical|major|minor",
      "位置": "问题所在段落/句子",
      "描述": "具体问题",
      "修复建议": "怎么改"}}
  ]
}}"""


# ════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════

def dual_review(
    llm: MultiLLMClient,
    source_text: str,
    generated_text: str,
    genre: str = "",
    skill_rules: str = "",
    prefer_cross_model: bool = True,
) -> DualReviewReport:
    """执行双盲交叉评审。

    Args:
        llm: 多模型客户端
        source_text: 原始材料（事实基准）
        generated_text: 待审成果
        genre: 体裁（如"请示件"）
        skill_rules: 该体裁的写作规则文本（注入格式官）
        prefer_cross_model: 尽量用另一家模型评审
    """
    report = DualReviewReport(content_score=0, format_score=0)

    # 内容官：交叉模型优先
    try:
        raw_a = llm.chat(
            system_prompt=_CONTENT_OFFICER_SYSTEM,
            user_prompt=_CONTENT_OFFICER_TEMPLATE.format(
                source_excerpt=_excerpt(source_text, 6000),
                generated_text=_excerpt(generated_text, 6000),
            ),
            json_mode=True,
            temperature=0.1,
            prefer_paid=False,
            prefer="deepseek" if prefer_cross_model else "",
            max_tokens=4096,
        )
        data_a = extract_json(raw_a)
        report.content_score = int(data_a.get("评分", 0))
        _parse_issues(data_a, "content", report.issues)
    except Exception as e:
        _log.warning("内容官评审失败: %s", str(e)[:100])
        report.content_score = 60  # 保守分，不阻塞

    # 格式官
    try:
        raw_b = llm.chat(
            system_prompt=_FORMAT_OFFICER_SYSTEM,
            user_prompt=_FORMAT_OFFICER_TEMPLATE.format(
                genre=genre or "通用公文",
                skill_rules=skill_rules or "（无特定规则，按通用国企公文标准）",
                generated_text=_excerpt(generated_text, 6000),
            ),
            json_mode=True,
            temperature=0.1,
            prefer_paid=False,
            max_tokens=4096,
        )
        data_b = extract_json(raw_b)
        report.format_score = int(data_b.get("评分", 0))
        _parse_issues(data_b, "format", report.issues)
    except Exception as e:
        _log.warning("格式官评审失败: %s", str(e)[:100])
        report.format_score = 60

    # 汇总反馈文本（供 Self-Refine）
    report.raw_feedback = _build_feedback(report)

    _log.info(
        "双盲评审: 内容官%d分, 格式官%d分, 综合%d分, %s",
        report.content_score, report.format_score, report.overall,
        "通过" if report.passed else "需迭代修复",
    )
    return report


def _excerpt(text: str, limit: int) -> str:
    """超长文本截断（保留头尾）。"""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n……（中略）……\n" + text[-half:]


def _parse_issues(data: dict, reviewer: str, out: list[ReviewIssue]) -> None:
    for item in data.get("问题列表", []):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("严重度", "minor")).lower()
        if severity not in ("critical", "major", "minor"):
            severity = "minor"
        out.append(ReviewIssue(
            reviewer=reviewer,
            dimension=str(item.get("维度", "")),
            severity=severity,
            location=str(item.get("位置", ""))[:80],
            description=str(item.get("描述", "")),
            fix_hint=str(item.get("修复建议", "")),
        ))


def _build_feedback(report: DualReviewReport) -> str:
    """汇总双官反馈为中文清单。"""
    if not report.issues:
        return "双盲评审未发现问题。"
    lines = [
        f"双盲评审（内容官{report.content_score}分/格式官{report.format_score}分）"
        f"发现 {len(report.issues)} 个问题，请逐项修复：",
    ]
    for i, issue in enumerate(report.issues, 1):
        officer = "内容官" if issue.reviewer == "content" else "格式官"
        lines.append(
            f"  {i}. [{officer}/{issue.severity}] {issue.dimension}: "
            f"{issue.description}"
            + (f"（位置: {issue.location}）" if issue.location else "")
            + (f" → 修复: {issue.fix_hint}" if issue.fix_hint else "")
        )
    return "\n".join(lines)
