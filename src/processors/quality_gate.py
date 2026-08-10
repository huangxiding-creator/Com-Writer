"""Step 3: 质量自审 —— AI 自检数据一致性与完整性。

检查项：
1. 数据一致性：生成文本中的数字 vs 理解阶段提取的原始数据
2. 完整性：所有议题是否都有对应的部署段落
3. 语气检查：是否有口语残留
不达标 → 返回修正建议，触发返工
"""
from __future__ import annotations

import json
import re

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..core.models import MeetingAnalysis, GeneratedContent, QualityReport
from ..utils.logger import get_logger

_log = get_logger("processor.quality_gate")

_SYSTEM_PROMPT = """你是一位严格的企业公文质量审查专家。你的任务是审查会议纪要的质量，确保内容准确、完整、规范。

审查维度：
1. 数据准确性：所有技术参数（mm/kN/MPa/吨/米/比例等数字）是否与原始数据一致
2. 内容完整性：每个议题是否都有对应的部署段落，决策是否完整
3. 语言规范性：是否存在口语化表达、模糊用词（"大概""可能""也许"）
4. 逻辑清晰性：每段是否有清晰的"问题→决策→要求"结构
5. 格式合规性：是否符合国企公文规范

只输出JSON，不要markdown围栏。"""

_USER_TEMPLATE = """请审查以下会议纪要的正文内容。

原始结构化分析（数据基准）：
{analysis_json}

生成的纪要正文段落：
{paragraphs_json}

请审查并输出：

{{
  "通过": true或false,
  "评分": 0到100的整数,
  "问题列表": [
    "（每个具体问题，如：第二段中"130mm"写成了"1300mm"，数据不一致）"
  ],
  "修正段落": [
    "（如果需要修正，输出修正后的完整段落列表；如果通过则留空数组）"
  ]
}}

判断标准：
- 评分≥80 且无严重数据错误 → 通过=true
- 评分<80 或有关键数据错误 → 通过=false，并在"修正段落"中给出修正版"""


def review(
    llm: MultiLLMClient,
    analysis: MeetingAnalysis,
    content: GeneratedContent,
    prefer_paid: bool = False,
) -> QualityReport:
    """执行质量自审。

    Args:
        llm: LLM 客户端
        analysis: 理解阶段输出（作为数据基准）
        content: 生成阶段输出（待审查）
        prefer_paid: 是否优先付费模型
    Returns:
        QualityReport 审查报告
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
    }, ensure_ascii=False, indent=2)

    paragraphs_json = json.dumps(
        list(content.content_paragraphs), ensure_ascii=False, indent=2
    )

    _log.info("开始质量自审 | %d 段 | 基准数据 %d 条",
              len(content.content_paragraphs), len(all_key_data))

    raw = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_USER_TEMPLATE.format(
            analysis_json=analysis_json,
            paragraphs_json=paragraphs_json,
        ),
        json_mode=True,
        temperature=0.1,
        prefer_paid=prefer_paid,  # 使用传入的参数（默认True由调用方控制）
        max_tokens=8192,
    )

    data = extract_json(raw)  # 稳健JSON提取（LLM可能返回markdown围栏）

    # 合并本地检查问题
    issues = list(quick_issues) + list(data.get("问题列表", []))

    report = QualityReport(
        passed=bool(data.get("通过", False)),
        score=int(data.get("评分", 0)),
        issues=tuple(issues),
        revised_paragraphs=tuple(data.get("修正段落", [])),
    )

    if report.passed:
        _log.info("质量自审通过 | 评分: %d", report.score)
    else:
        _log.warning("质量自审未通过 | 评分: %d | 问题: %s",
                      report.score, "; ".join(report.issues[:3]))

    return report


def _quick_local_check(analysis: MeetingAnalysis, content: GeneratedContent) -> list[str]:
    """不消耗 API 的本地快速检查。"""
    issues: list[str] = []

    # 检查1: 段落数是否匹配议题数 + 开头段
    min_paragraphs = len(analysis.topics) + 1  # 至少: 开头 + 每议题一段
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
            # 提取设计值和实际值中的数字
            for val in [kp.design_value, kp.actual_value]:
                nums = re.findall(r"\d+\.?\d*", val)
                all_numbers_expected.extend(nums)

    # 合并所有段落文本
    full_text = "\n".join(content.content_paragraphs)

    # 检查关键数字是否出现（允许一定的容错）
    missing_numbers = []
    for num in all_numbers_expected:
        if num and len(num) >= 2 and num not in full_text:
            missing_numbers.append(num)

    if missing_numbers:
        unique_missing = list(set(missing_numbers))[:5]  # 只报告前5个
        issues.append(f"关键数据可能缺失：{', '.join(unique_missing)} 等数字未在正文中出现")

    return issues
