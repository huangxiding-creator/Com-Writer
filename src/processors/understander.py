"""Step 1: 深度理解 —— 从转写稿提取结构化信息。

AI 读取口语化转写稿，输出 JSON：
- 议题切分（自动识别"第一个问题...第二个问题..."）
- 关键数据提取（130mm/150mm/600kN 等技术参数原值保留）
- 决策与行动项识别
"""
from __future__ import annotations

import json

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..readers.transcript_parser import TranscriptData, format_transcript_for_llm
from ..core.models import (
    KeyDataPoint,
    ActionItem,
    Topic,
    MeetingAnalysis,
)
from ..utils.logger import get_logger
from ..utils.text import truncate

_log = get_logger("processor.understander")

_SYSTEM_PROMPT = """你是一位工程建设领域的资深会议分析专家。你的任务是仔细阅读会议录音转写稿（ASR语音识别输出），提取结构化信息。

注意转写稿的特点：
- 口语化严重，有重复、打断、省略
- 可能有方言导致的误识别（如"锚索"被识别为"毛索"、"管靴"被识别为"管学/管线"）
- 含大量专业技术参数和数据

你的核心职责：
1. 准确理解讨论内容，透过口语化表达抓住技术本质
2. 识别方言误识别并修正为正确术语
3. 所有技术参数和数据必须原值保留，绝不可篡改或遗漏
4. 按议题分类组织信息
5. 准确识别决策结论和行动项

只输出JSON，不要markdown围栏，不要解释。"""

_USER_TEMPLATE = """请分析以下会议转写稿，提取结构化信息。

{transcript}

请输出以下JSON格式（所有字段必须填写）：

{{
  "会议标题": "（从转写稿第一行或内容推断，格式：XX工程XX会议）",
  "会议类别": "（如：专题会/调度会/工作会）",
  "会议时间推断": "（从转写稿内容或文件名推断日期，如无法确定则写"待确认"）",
  "会议地点": "（从内容推断，如无法确定则写"待确认"）",
  "主持人推断": "（主要发言、主导讨论的人）",
  "整理人推断": "（通常是主持人或其团队人员）",
  "参会发言人员": ["人名1", "人名2"],
  "议题列表": [
    {{
      "议题名称": "（简洁概括，如：锚索孔径调整方案）",
      "背景": "（2-3句话说明问题和起因）",
      "讨论要点": [
        "（每条一个讨论要点，具体到技术细节）"
      ],
      "关键数据": [
        {{"参数": "（如：600kN锚索设计孔径）", "设计值": "（如：≥130mm）", "实际值": "（如：122mm）", "备注": "（如：管靴位置内径不足）"}}
      ],
      "决策结论": "（1-2句话，会议最终达成的结论）",
      "行动项": [
        {{"负责人": "（单位或人名）", "任务": "（具体任务描述）", "时限": "（如无法确定则留空）"}}
      ]
    }}
  ]
}}

★★★ 关键要求（务必严格遵守）★★★
1. **议题切分**：仔细识别会议中的不同讨论主题。当发言人明显转向新话题时（如"第二个问题就是..."、"下一个..."），必须切分为新的议题。不要把多个不相关的问题合并为一个议题。
2. **数据完整性**：所有技术数据（mm/kN/MPa/吨/米/百分比/比例等）必须逐个提取，不可遗漏。每个关键参数都应出现在"关键数据"数组中。
3. **数据准确性**：数值必须与原文完全一致，不可四舍五入或近似。
4. **议题按会议讨论顺序排列**。
5. **决策结论要准确反映会议达成的共识**，不要臆测未讨论的内容。
6. **行动项**：包括需要发文件、做实验、测算费用、采购材料等具体任务。
7. 发言人名称使用转写稿中的原始名称。"""


def understand(
    llm: MultiLLMClient,
    transcript: TranscriptData,
    prefer_paid: bool = False,
    filename_hint: str = "",
) -> MeetingAnalysis:
    """执行理解步骤：转写稿 → 结构化分析。

    Args:
        llm: 多模型 LLM 客户端
        transcript: 解析后的转写稿数据
        prefer_paid: 是否优先使用付费模型
        filename_hint: 文件名（含日期信息，辅助推断会议时间）
    Returns:
        MeetingAnalysis 结构化分析结果
    Raises:
        Exception: AI 解析失败
    """
    formatted = format_transcript_for_llm(transcript)
    if filename_hint:
        formatted = f"（文件名提示：{filename_hint}）\n\n{formatted}"
    formatted = truncate(formatted, max_chars=100000)

    _log.info("开始理解阶段 | 转写稿 %d 字 | %d 条发言 | %d 位发言人",
              transcript.char_count, transcript.entry_count, len(transcript.speakers))

    raw = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_USER_TEMPLATE.format(transcript=formatted),
        json_mode=True,
        temperature=0.1,  # 理解阶段需要确定性
        prefer_paid=prefer_paid,
        max_tokens=8192,
    )

    if not raw or not raw.strip():
        raise ValueError("LLM 返回空响应，可能是请求过长或模型超时")

    _log.debug("LLM 返回 %d 字符", len(raw))

    data = extract_json(raw)  # 稳健 JSON 提取

    # 转换为不可变模型
    topics: list[Topic] = []
    for t in data.get("议题列表", []):
        key_data = tuple(
            KeyDataPoint(
                parameter=kp.get("参数", ""),
                design_value=kp.get("设计值", ""),
                actual_value=kp.get("实际值", ""),
                remark=kp.get("备注", ""),
            )
            for kp in t.get("关键数据", [])
        )
        action_items = tuple(
            ActionItem(
                responsible=ai.get("负责人", ""),
                task=ai.get("任务", ""),
                deadline=ai.get("时限", ""),
            )
            for ai in t.get("行动项", [])
        )
        topics.append(Topic(
            name=t.get("议题名称", ""),
            background=t.get("背景", ""),
            discussion_points=tuple(t.get("讨论要点", [])),
            key_data=key_data,
            decision=t.get("决策结论", ""),
            action_items=action_items,
        ))

    # 清理参会人员（过滤非人名条目如"说话人3"）
    raw_participants = data.get("参会发言人员", [])
    participants = tuple(
        p for p in raw_participants
        if p and not p.startswith("说话人") and len(p) <= 10
    )

    analysis = MeetingAnalysis(
        title=data.get("会议标题", transcript.title),
        meeting_type=data.get("会议类别", "专题会"),
        topics=tuple(topics),
        participants=participants,
        meeting_date=data.get("会议时间推断", "待确认"),
        meeting_location=data.get("会议地点", "待确认"),
        host=data.get("主持人推断", ""),
        compiler=data.get("整理人推断", ""),
    )

    _log.info("理解完成 | %d 个议题 | %d 个行动项 | %d 个关键数据点",
              len(analysis.topics),
              sum(len(t.action_items) for t in analysis.topics),
              sum(len(t.key_data) for t in analysis.topics))

    return analysis
