"""Self-Refine ITERATE 步骤 —— 基于结构化反馈的针对性修改。

核心思想（来自 Self-Refine NeurIPS 2023）：
- 不重新生成全文，而是在现有文本基础上做针对性修正
- 保留已得分的内容，只修改被扣分的部分
- 每个修改必须对应一个具体的扣分项
- **关键改进**：计算当前文本中缺失的具体数据点，作为强制清单注入 prompt

输入：当前段落 + Rubric 维度评分 + 扣分原因 + 修改建议
输出：修正后的段落（保持未扣分部分不变）
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..core.models import MeetingAnalysis, GeneratedContent
from .quality_gate_v2 import RubricReport
from ..utils.logger import get_logger

_log = get_logger("processor.refiner")

_SYSTEM_PROMPT = """你是一位企业公文修改专家。你的任务是**针对性修改**会议纪要中的具体问题。

★★★ 修改原则（Self-Refine）★★★
1. **最小改动**：只修改被指出的问题，不要动已经正确的部分
2. **精准修正**：每个修改必须对应一个具体的扣分项
3. **保持结构**：段落数量、整体框架保持不变
4. **提升不降质**：修改后的版本在对应维度必须达到满分
5. **数据完整**：★★★ 强制数据清单中的每一个数据点都必须出现在修正后的文本中 ★★★

常见修改类型：
- 数据补充：将缺失的技术参数、数字补充到对应段落中
- 内容补充：为缺少决策/行动项的段落补充内容
- 语言润色：删除口语/模糊词，替换为规范公文用语
- 结构优化：重新组织段落内的逻辑顺序
- 格式调整：添加/修正分条列项标号

只输出JSON，不要markdown围栏。"""

_USER_TEMPLATE = """请根据以下结构化反馈，对会议纪要进行**针对性修改**。

【当前会议纪要段落】：
{paragraphs_json}

【数据基准】（所有数据必须出现在正文中）：
{analysis_json}

★★★ 强制数据清单（以下数据当前缺失，必须在修正后的文本中出现）★★★
{missing_data_list}

【Rubric 评分结果】：
总分 {total_score}/100
{rubric_json}

【各维度扣分原因和修改建议】：
{feedback_detail}

★★★ 修改要求 ★★★
1. **数据修正（最高优先级）**：强制数据清单中的每个数据点必须出现在修正后的文本中
   - 将缺失的数字自然地融入对应议题的段落中
   - 例如：如果"套管外径168mm"缺失，在锚索孔径段落中加入"套管外径168mm"的表述
2. 针对每个扣分原因进行修改
3. 未被扣分的维度/段落保持原样
4. 确保修正后所有维度均能达到满分20分

请输出修改后的完整段落列表：

{{
  "修改说明": [
    "（逐条说明你做了哪些修改，对应哪个扣分项）"
  ],
  "修正段落": [
    "（修改后的完整段落列表，数量与原始一致）"
  ]
}}"""


def _find_missing_data(analysis: MeetingAnalysis, content: GeneratedContent) -> list[str]:
    """计算当前文本中缺失的关键数据点（检查参数名+数字+"待确定"三重维度）。

    Returns:
        缺失数据描述列表
    """
    full_text = "\n".join(content.content_paragraphs)
    missing: list[str] = []

    for topic in analysis.topics:
        for kp in topic.key_data:
            has_numbers = bool(re.search(r"\d", kp.design_value + kp.actual_value))

            # 检查参数名是否出现
            param_key_words = re.findall(r"[一-龥]{2,}", kp.parameter)
            param_present = any(w in full_text for w in param_key_words) if param_key_words else True

            # 检查"待确定"是否需要明示
            has_pending = "待确定" in kp.design_value or "待确定" in kp.actual_value
            pending_mentioned = "待确定" in full_text or "尚未确定" in full_text or "需进一步" in full_text

            if not has_numbers:
                # 无数字数据点：检查参数名和"待确定"状态
                if not param_present:
                    missing.append(
                        f"  ✗ {kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value} "
                        f"→ 参数名未在正文中提及（需补充相关说明）"
                    )
                elif has_pending and not pending_mentioned:
                    missing.append(
                        f"  ✗ {kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value} "
                        f"→ 需明确提及设计值/实际值待确定"
                    )
                continue

            # 有数字的数据点：检查数字
            all_nums: list[str] = []
            for val in [kp.design_value, kp.actual_value]:
                nums = re.findall(r"\d+\.?\d*", val)
                all_nums.extend(nums)

            missing_nums = [n for n in all_nums if len(n) >= 2 and n not in full_text]
            missing_nums = list(dict.fromkeys(missing_nums))

            if missing_nums and not param_present:
                missing.append(
                    f"  ✗ {kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value} "
                    f"→ 参数名和数字({', '.join(missing_nums)})均缺失"
                )
            elif missing_nums:
                missing.append(
                    f"  ✗ {kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value} "
                    f"→ 缺失数字: {', '.join(missing_nums)}"
                )
            elif not param_present:
                missing.append(
                    f"  ✗ {kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value} "
                    f"→ 参数名未在正文中提及"
                )
            # Note: "待确定" mention issues are handled by LLM refiner, not pure injection,
            # to avoid data duplication from programmatic injection.

    return missing


def refine(
    llm: MultiLLMClient,
    analysis: MeetingAnalysis,
    content: GeneratedContent,
    rubric: RubricReport,
    prefer_paid: bool = False,
    style_reference: str = "",
) -> GeneratedContent:
    """Self-Refine ITERATE 步骤：基于 Rubric 反馈做针对性修改。

    Args:
        llm: LLM 客户端
        analysis: 理解阶段输出（数据基准）
        content: 当前生成的文本（待修改）
        rubric: Rubric 质量报告（含扣分原因和建议）
        prefer_paid: 是否优先付费模型
        style_reference: 写作风格参考
    Returns:
        GeneratedContent 修改后的文本
    """
    # 如果 Rubric 已经提供了修正段落，在修正版本基础上继续优化
    base_paragraphs = list(content.content_paragraphs)
    if rubric.revised_paragraphs and len(rubric.revised_paragraphs) >= len(base_paragraphs):
        base_paragraphs = list(rubric.revised_paragraphs)

    # ★★★ 核心改进：计算缺失的具体数据点 ★★★
    missing_data = _find_missing_data(analysis, content)
    if missing_data:
        missing_data_list = "\n".join(missing_data)
        _log.warning("发现 %d 个缺失数据点:\n%s", len(missing_data), missing_data_list)
    else:
        missing_data_list = "（无缺失——所有基准数据均已出现）"

    # 构建详细的维度反馈
    feedback_parts: list[str] = []
    for dim, issues in rubric.dimension_issues.items():
        if not issues:
            continue
        score = rubric.dimension_scores.get(dim, 0)
        suggestions = rubric.dimension_suggestions.get(dim, [])
        feedback_parts.append(f"\n【{dim}】当前{score}/20分")
        for i, issue in enumerate(issues):
            feedback_parts.append(f"  扣分{i+1}: {issue}")
            if i < len(suggestions):
                feedback_parts.append(f"  建议{i+1}: {suggestions[i]}")

    if not feedback_parts and rubric.total_score >= 95:
        _log.info("评分已≥95且无具体扣分项，执行精细润色")
        feedback_parts.append(
            "\n【精细润色】当前总分%.0f/100，距离满分还有%d分。"
            "请仔细审查每个段落，找出任何不够完美的地方：" %
            (rubric.total_score, 100 - rubric.total_score)
        )
        feedback_parts.append("  - 检查是否有数据表述可以更精确")
        feedback_parts.append("  - 检查是否有公文用语可以更规范")
        feedback_parts.append("  - 检查是否有逻辑结构可以更清晰")
        feedback_parts.append("  - 检查是否有行动项表述可以更明确")

    feedback_detail = "\n".join(feedback_parts)

    # 数据基准（简化版，只含关键数据）
    all_key_data: list[str] = []
    for topic in analysis.topics:
        for kp in topic.key_data:
            all_key_data.append(f"{kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value}")

    analysis_json = json.dumps({
        "关键数据基准": all_key_data,
        "议题列表": [t.name for t in analysis.topics],
        "行动项": [
            f"{ai.responsible}: {ai.task} (时限: {ai.deadline})"
            for t in analysis.topics
            for ai in t.action_items
        ],
    }, ensure_ascii=False, indent=2)

    paragraphs_json = json.dumps(base_paragraphs, ensure_ascii=False, indent=2)

    rubric_json = json.dumps({
        "总分": rubric.total_score,
        "各维度分数": rubric.dimension_scores,
        "总体评价": rubric.overall_comment,
    }, ensure_ascii=False, indent=2)

    # 注入风格参考（截断以避免超长 prompt）
    style_hint = ""
    if style_reference:
        style_hint = f"\n\n★★★ 写作风格参考 ★★★\n{style_reference[:5000]}"

    _log.info("开始Refine修改 | 当前%d分 | 缺失数据%d个 | 扣分维度: %s",
              rubric.total_score,
              len(missing_data),
              ", ".join(k for k, v in rubric.dimension_scores.items() if v < 20) or "无")

    raw = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_USER_TEMPLATE.format(
            paragraphs_json=paragraphs_json,
            analysis_json=analysis_json,
            missing_data_list=missing_data_list,
            total_score=rubric.total_score,
            rubric_json=rubric_json,
            feedback_detail=feedback_detail,
        ) + style_hint,
        json_mode=True,
        temperature=0.3,
        prefer_paid=prefer_paid,
        max_tokens=8192,
    )

    data = extract_json(raw)

    # 提取修正段落
    raw_paragraphs = data.get("修正段落", [])
    paragraphs: list[str] = []
    for p in raw_paragraphs:
        if isinstance(p, str):
            paragraphs.append(p.strip())
        elif isinstance(p, dict):
            text = p.get("text", "") or p.get("内容", "") or ""
            if text:
                paragraphs.append(text.strip())

    if not paragraphs:
        _log.warning("Refine未返回有效段落，保留原文")
        paragraphs = base_paragraphs

    # 验证修正后的文本是否包含了之前缺失的数据
    if missing_data:
        new_text = "\n".join(paragraphs)
        still_missing = _find_missing_data(
            analysis,
            GeneratedContent(
                title=content.title,
                doc_number=content.doc_number,
                meeting_type=content.meeting_type,
                meeting_topic=content.meeting_topic,
                meeting_date=content.meeting_date,
                meeting_location=content.meeting_location,
                host=content.host,
                participants=content.participants,
                content_paragraphs=tuple(paragraphs),
                compiler=content.compiler,
            ),
        )
        if still_missing:
            _log.warning("修正后仍有 %d 个数据点缺失", len(still_missing))
            # 手动注入缺失数据到最相关的段落
            paragraphs = _inject_missing_data(paragraphs, still_missing)
        else:
            _log.info("✅ 所有关键数据已补全")

    # 修改说明日志
    modifications = data.get("修改说明", [])
    for i, mod in enumerate(modifications[:5]):
        _log.info("  修改%d: %s", i + 1, str(mod)[:120])

    return GeneratedContent(
        title=content.title,
        doc_number=content.doc_number,
        meeting_type=content.meeting_type,
        meeting_topic=content.meeting_topic,
        meeting_date=content.meeting_date,
        meeting_location=content.meeting_location,
        host=content.host,
        participants=content.participants,
        content_paragraphs=tuple(paragraphs),
        compiler=content.compiler,
        model_used=content.model_used,
    )


def _inject_missing_data(paragraphs: list[str], missing_items: list[str]) -> list[str]:
    """当 LLM 未能补全数据时，手动将缺失数据注入最相关的段落。

    策略：将缺失数据描述以自然语言形式注入到最相关的段落中。
    """
    if not missing_items or len(paragraphs) < 2:
        return paragraphs

    result = list(paragraphs)

    for item in missing_items:
        # 解析 "  ✗ 参数: 设计=X, 实际=Y → ..."
        match = re.match(r"\s*✗\s*(.+?):\s*设计=(.+?),\s*实际=(.+?)(?:\s*→|$)", item)
        if not match:
            continue
        param, design, actual = match.groups()

        # 始终包含设计值和实际值（即使是"待确定"也需明示）
        data_phrase = f"{param}设计{design}、实际{actual}"

        # 找到最相关的段落
        best_idx = _find_best_paragraph(result, param)
        if best_idx < 0:
            best_idx = 1  # 默认第二段

        # 避免重复注入
        if data_phrase in result[best_idx]:
            continue

        # 注入到段落末尾
        if result[best_idx].endswith("。"):
            result[best_idx] = result[best_idx][:-1] + "。" + data_phrase + "。"
        else:
            result[best_idx] = result[best_idx] + "。" + data_phrase + "。"
        _log.info("  手动注入[%s]到第%d段", param, best_idx + 1)

    return result


def _find_best_paragraph(paragraphs: list[str], param_name: str) -> int:
    """找到与参数名最相关的段落索引。

    通过关键词匹配找到最佳注入位置。
    """
    param_words = re.findall(r"[一-龥]{2,}", param_name)
    if not param_words:
        return 1

    best_idx = -1
    best_score = 0
    for i, para in enumerate(paragraphs):
        if i == 0:
            continue  # 跳过开头段
        score = sum(1 for w in param_words if w in para)
        # 也检查参数中的英文/数字关键词
        eng_words = re.findall(r"[a-zA-Z0-9]+", param_name)
        for ew in eng_words:
            if ew.lower() in para.lower():
                score += 1
        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx if best_score > 0 else 1
