"""质量自审 V2 —— 基于 Self-Refine + LLM-as-a-Judge 最佳实践。

核心改进（相比 V1）：
1. 多维度 Rubric 评分：5维×20分=100分，每维度独立打分
2. 结构化反馈：每个扣分项给出具体的"哪里→为什么→怎么改"
3. 分维度修正建议：不只说问题，给出每段的修改方向

5个评分维度：
  D1 数据准确性 (20分)：技术参数与原始数据完全一致
  D2 内容完整性 (20分)：议题覆盖、决策完整、行动项齐全
  D3 语言规范性 (20分)：公文用语、无口语/模糊词
  D4 逻辑清晰性 (20分)：问题→决策→要求结构
  D5 格式合规性 (20分)：国企公文格式标准
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..core.models import MeetingAnalysis, GeneratedContent, QualityReport
from ..utils.logger import get_logger

_log = get_logger("processor.quality_gate_v2")

_SYSTEM_PROMPT = """你是一位极其严格的企业公文质量审查专家，负责审查会议纪要。

★★★ 评分体系：5维度 × 20分 = 100分 ★★★

【D1 数据准确性 20分】
- 所有技术参数（mm/kN/MPa/吨/米/比例/水灰比等数字）必须与"数据基准"完全一致
- 扣分项：遗漏关键数据(-3/个)、数据不一致(-5/个)、单位错误(-3/个)
- 满分要求：所有基准数据均在正文中准确出现，无遗漏无误写

【D2 内容完整性 20分】
- 每个议题必须有独立段落，不可合并
- 每段必须包含：①问题概述 ②关键技术数据 ③决策结论 ④工作要求和责任人
- 扣分项：议题遗漏(-5/个)、缺决策结论(-3/段)、缺行动项(-3/段)、段落过短(-2/段)
- 满分要求：议题全覆盖、决策完整、行动项明确到人

【D3 语言规范性 20分】
- 必须使用规范公文用语，不得出现口语化、模糊化
- 禁用词：大概、可能、也许、差不多、好像是、应该是、然后、那个
- 扣分项：口语残留(-3/处)、模糊词(-2/处)、主观抒情(-3/处)
- 满分要求：语言庄重精炼，祈使句+规范公文用语

【D4 逻辑清晰性 20分】
- 每段须有清晰的"问题→决策→要求"递进结构
- 扣分项：结构混乱(-3/段)、逻辑跳跃(-2/段)、缺少过渡(-2/段)
- 满分要求：每段层次分明，逻辑递进，可追溯可督办

【D5 格式合规性 20分】
- 符合国企公文格式：分条列项、层次标号、称谓规范
- 扣分项：格式不规范(-2/处)、缺少结构标号(-2/处)、称谓不当(-2/处)
- 满分要求：完全符合《党政机关公文处理工作条例》格式标准

★★★ 输出要求 ★★★
对每个维度：给出分数、扣分原因、具体修改建议（指明哪段哪个问题怎么改）。
总分=5维度之和。只有总分=100且所有维度满分时，通过=true。

只输出JSON，不要markdown围栏。"""

_USER_TEMPLATE = """请按5维度Rubric严格审查以下会议纪要。

【数据基准】（所有数字必须准确出现在正文中）：
{analysis_json}

【待审查的纪要正文段落】：
{paragraphs_json}

请输出如下JSON（每个维度独立评分）：

{{
  "通过": true或false,
  "总分": 0到100的整数,
  "维度评分": {{
    "D1_数据准确性": {{
      "分数": 0到20,
      "扣分原因": ["具体原因1", "具体原因2"],
      "修改建议": ["针对原因1的具体修改方法"]
    }},
    "D2_内容完整性": {{
      "分数": 0到20,
      "扣分原因": [],
      "修改建议": []
    }},
    "D3_语言规范性": {{
      "分数": 0到20,
      "扣分原因": [],
      "修改建议": []
    }},
    "D4_逻辑清晰性": {{
      "分数": 0到20,
      "扣分原因": [],
      "修改建议": []
    }},
    "D5_格式合规性": {{
      "分数": 0到20,
      "扣分原因": [],
      "修改建议": []
    }}
  }},
  "修正段落": [
    "（如果需要修正，输出修正后的完整段落列表；总分=100则留空数组）"
  ],
  "总体评价": "一句话总结主要不足"
}}

★★★ 严格标准 ★★★
- 总分=100 当且仅当所有5个维度均为满分20分
- 有任何扣分原因的维度不得给满分
- 修正段落必须针对所有扣分项进行修改"""


@dataclass
class RubricReport:
    """多维度 Rubric 质量报告。"""
    passed: bool
    total_score: int
    dimension_scores: dict[str, int]
    dimension_issues: dict[str, list[str]]
    dimension_suggestions: dict[str, list[str]]
    revised_paragraphs: tuple[str, ...]
    overall_comment: str
    # 兼容旧 QualityReport 接口
    @property
    def score(self) -> int:
        return self.total_score
    @property
    def issues(self) -> tuple[str, ...]:
        result: list[str] = []
        for dim, issues in self.dimension_issues.items():
            for issue in issues:
                result.append(f"[{dim}] {issue}")
        return tuple(result)


def review_rubric(
    llm: MultiLLMClient,
    analysis: MeetingAnalysis,
    content: GeneratedContent,
    prefer_paid: bool = False,
) -> RubricReport:
    """执行多维度 Rubric 质量自审。

    Args:
        llm: LLM 客户端
        analysis: 理解阶段输出（数据基准）
        content: 生成阶段输出（待审查）
        prefer_paid: 是否优先付费模型
    Returns:
        RubricReport 多维度审查报告
    """
    # 先做快速本地检查（不消耗 API）
    quick_issues = _quick_local_check(analysis, content)
    if quick_issues:
        _log.warning("本地快速检查发现 %d 个问题", len(quick_issues))

    # 提取关键数据用于对比
    all_key_data: list[str] = []
    for topic in analysis.topics:
        for kp in topic.key_data:
            all_key_data.append(f"{kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value}")

    analysis_json = json.dumps({
        "关键数据基准": all_key_data,
        "议题数量": len(analysis.topics),
        "议题列表": [t.name for t in analysis.topics],
        "行动项": [
            f"{ai.responsible}: {ai.task} (时限: {ai.deadline})"
            for t in analysis.topics
            for ai in t.action_items
        ],
    }, ensure_ascii=False, indent=2)

    paragraphs_json = json.dumps(
        list(content.content_paragraphs), ensure_ascii=False, indent=2
    )

    _log.info("开始Rubric质量自审 | %d 段 | 基准数据 %d 条",
              len(content.content_paragraphs), len(all_key_data))

    raw = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_USER_TEMPLATE.format(
            analysis_json=analysis_json,
            paragraphs_json=paragraphs_json,
        ),
        json_mode=True,
        temperature=0.1,
        prefer_paid=prefer_paid,
        max_tokens=8192,
    )

    data = extract_json(raw)

    # 解析多维度评分
    dim_scores_raw = data.get("维度评分", {})
    dimension_scores: dict[str, int] = {}
    dimension_issues: dict[str, list[str]] = {}
    dimension_suggestions: dict[str, list[str]] = {}

    for dim_key, dim_data in dim_scores_raw.items():
        if isinstance(dim_data, dict):
            dimension_scores[dim_key] = int(dim_data.get("分数", 0))
            dimension_issues[dim_key] = list(dim_data.get("扣分原因", []))
            dimension_suggestions[dim_key] = list(dim_data.get("修改建议", []))

    # 合并本地检查问题到 D1
    if quick_issues:
        existing = dimension_issues.get("D1_数据准确性", [])
        existing.extend(quick_issues)
        dimension_issues["D1_数据准确性"] = existing

    total = int(data.get("总分", sum(dimension_scores.values())))

    report = RubricReport(
        passed=bool(data.get("通过", False)),
        total_score=total,
        dimension_scores=dimension_scores,
        dimension_issues=dimension_issues,
        dimension_suggestions=dimension_suggestions,
        revised_paragraphs=tuple(data.get("修正段落", [])),
        overall_comment=str(data.get("总体评价", "")),
    )

    # 日志输出
    dim_str = " | ".join(f"{k}: {v}" for k, v in dimension_scores.items())
    if report.passed:
        _log.info("质量自审通过 | 总分: %d | %s", total, dim_str)
    else:
        all_issues = report.issues
        _log.warning("质量自审未通过 | 总分: %d | %s | 问题: %s",
                      total, dim_str,
                      "; ".join(all_issues[:5]) if all_issues else "无具体问题")

    return report


def review(
    llm: MultiLLMClient,
    analysis: MeetingAnalysis,
    content: GeneratedContent,
    prefer_paid: bool = False,
) -> QualityReport:
    """兼容接口：执行 Rubric 审查，返回兼容的 QualityReport。"""
    rubric = review_rubric(llm, analysis, content, prefer_paid)
    return QualityReport(
        passed=rubric.passed,
        score=rubric.total_score,
        issues=rubric.issues,
        revised_paragraphs=rubric.revised_paragraphs,
    )


def _quick_local_check(analysis: MeetingAnalysis, content: GeneratedContent) -> list[str]:
    """不消耗 API 的本地快速检查。"""
    issues: list[str] = []

    # 检查1: 段落数是否匹配议题数 + 开头段
    min_paragraphs = len(analysis.topics) + 1
    if len(content.content_paragraphs) < min_paragraphs:
        issues.append(
            f"段落数不足：期望至少{min_paragraphs}段（1开头+{len(analysis.topics)}议题），"
            f"实际{len(content.content_paragraphs)}段"
        )

    # 检查2: 口语残留
    colloquial_markers = ["然后呢", "那个啥", "他妈", "我操", "啥意思", "咋回事"]
    for para in content.content_paragraphs:
        for marker in colloquial_markers:
            if marker in para:
                issues.append(f"口语残留：段落中包含'{marker}'")
                break

    # 检查3: 关键数据点是否在文本中出现
    all_numbers_expected: list[str] = []
    for topic in analysis.topics:
        for kp in topic.key_data:
            for val in [kp.design_value, kp.actual_value]:
                nums = re.findall(r"\d+\.?\d*", val)
                all_numbers_expected.extend(nums)

    full_text = "\n".join(content.content_paragraphs)

    missing_numbers = []
    for num in all_numbers_expected:
        if num and len(num) >= 2 and num not in full_text:
            missing_numbers.append(num)

    if missing_numbers:
        unique_missing = list(set(missing_numbers))[:5]
        issues.append(f"关键数据可能缺失：{', '.join(unique_missing)} 等数字未在正文中出现")

    # 检查4: 模糊词检测（新增）
    fuzzy_markers = ["大概", "可能", "也许", "差不多", "好像是", "应该是"]
    for para in content.content_paragraphs:
        for marker in fuzzy_markers:
            if marker in para:
                issues.append(f"模糊用词：段落中包含'{marker}'，应删除或替换为确定性表述")

    return issues
