"""数据模型 —— 类型安全的中间数据结构。

使用 frozen dataclass 保证不可变性（用户编码规范要求）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KeyDataPoint:
    """关键数据点（从转写稿提取的技术参数）。"""
    parameter: str        # 参数名（如"锚索孔径"）
    design_value: str     # 设计值（如"≥130mm"）
    actual_value: str     # 实际值（如"122mm"）
    remark: str = ""      # 备注


@dataclass(frozen=True)
class ActionItem:
    """行动项。"""
    responsible: str      # 负责人/单位
    task: str             # 具体任务
    deadline: str = ""    # 时限


@dataclass(frozen=True)
class Topic:
    """会议议题（结构化提取结果）。"""
    name: str                                   # 议题名称
    background: str                             # 背景说明
    discussion_points: tuple[str, ...]          # 讨论要点
    key_data: tuple[KeyDataPoint, ...]          # 关键数据
    decision: str                               # 决策结论
    action_items: tuple[ActionItem, ...]        # 行动项


@dataclass(frozen=True)
class MeetingAnalysis:
    """AI 理解阶段输出 —— 会议结构化分析结果。"""
    title: str                                   # 会议标题
    meeting_type: str                            # 会议类别
    topics: tuple[Topic, ...]                    # 议题列表
    participants: tuple[str, ...]                # 参会人员
    meeting_date: str                            # 会议时间推断
    meeting_location: str = ""                   # 会议地点
    host: str = ""                               # 主持人推断
    compiler: str = ""                           # 整理人推断


@dataclass(frozen=True)
class GeneratedContent:
    """生成阶段输出。"""
    title: str                    # 纪要标题
    doc_number: str               # 文号（如〔2026〕1号）
    meeting_type: str             # 会议类别
    meeting_topic: str            # 会议议题
    meeting_date: str             # 会议时间
    meeting_location: str         # 会议地点
    host: str                     # 主持人
    participants: str             # 参加人员
    content_paragraphs: tuple[str, ...]  # 纪要内容段落
    compiler: str                 # 整理人
    model_used: str = ""          # 使用的模型


@dataclass(frozen=True)
class QualityReport:
    """质量自审报告。"""
    passed: bool                  # 是否通过
    score: int                    # 评分 0-100
    issues: tuple[str, ...]       # 问题列表
    revised_paragraphs: tuple[str, ...] = ()  # 修正后的段落（如有）


@dataclass(frozen=True)
class TaskResult:
    """任务最终结果。"""
    success: bool
    output_path: str = ""
    model_used: str = ""
    quality_score: int = 0
    duration_seconds: float = 0.0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
