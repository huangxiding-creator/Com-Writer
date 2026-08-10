"""Step 2: 正式撰写 —— 将结构化分析转化为正式公文。

按会议纪要模板格式，将 AI 理解的结构化信息转化为：
- 开头综述段（会议背景、总体要求）
- 分条工作部署（每条对应一个议题的决策和行动项）
"""
from __future__ import annotations

import json
from typing import Optional

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..core.models import MeetingAnalysis, GeneratedContent
from ..utils.logger import get_logger

_log = get_logger("processor.generator")

_SYSTEM_PROMPT = """你是一位专业的企业公文写作专家，擅长撰写工程建设领域的正式会议纪要。

★★★ 核心原则：会议纪要写的是"最后达成一致的内容"，不是讨论过程的复述。★★★

═══ 基础要求 ═══
1. **聚焦决策和共识**：每个议题的段落要写清：问题是什么 → 最终决定怎么做 → 谁负责 → 有什么要求
2. **不要写讨论过程**：不需要写"XX提出...""XX认为..."，直接写结论
3. 语言正式、简洁、规范，符合国企/政府部门公文标准
4. 技术参数必须准确引用原始数据（如"130mm""600kN""0.4水灰比"等），不可模糊化
5. 决策要明确、有执行力（"需...""应...""由XX负责..."）
6. 不要使用口语、不要使用"大概""可能""也许"等模糊词
7. 每个议题至少写一个完整段落（200-400字），确保内容充实

═══ 领导审稿修改规则（基于20条模式提炼，必须全部遵循）═══

【规则1：同类合并与结构重组】同一主题的事项合并为一条，散落的"一是、二是、三是"口头并列应整合为"主要包括A、B、C"的书面并列结构，按逻辑递进组织。
  例：支付计划+分包沟通+农民工工资 → 合为一条；"一是…二是…三要…四是要…"→"主要包括A，B，C，D"

【规则2：先人后机构】成立工作组时：先说"由XX牵头+哪些部门参加"，再说"组成XX专班"。参会人员列表先姓名后职务。

【规则3：明确执行主体】每个决策必须明确执行部门。
  例："采用现金支付"→"商财资部尽量采用现金支付"（补充部门+"尽量"适度语气）
  跨部门资金/资源调配必须使用"商XX部"格式，兼顾流程合规与执行效率。

【规则4：条件递进处理问题】问题处理不用命令式，用条件递进。口头讨论中不确定的表述（"研究比较是否…"）要直接转为明确指令或升级反馈机制。
  例："提出建议→如解决不了→及时向后方反馈→组织开展技术协调会"
  例："研究比较是否在洞内进行锚固和灌浆"→直接写"洞内进行锚固和灌浆"

【规则5：隐喻/口语转具体措施】将口语化的比喻、口号、泛指技术说法转化为具体可执行的工程措施和正式用语。
  例："举'全院之智'，动态优化"→"工程院加大设计力量的投入，多邀请内外部专家对方案全力做好优化"
  例："柔性或喷固措施"→"喷洒固结剂"

【规则6：技术表述精准化】使用规范的工程术语，在核心名词前增加限定词以明确范畴。
  例："查明落渣成因"→"分析落石成因"；"监测"→"安全监测"；"处理方案"→"处治方案"
  例："补强衬砌"→"补强支护"；"导水"→"导排水"；"2号路交通洞"→"进厂交通洞"

【规则7：语气强化与冗余删减】适度使用"不折不扣""决战状态"等强指令词传达紧迫感。删除委托背景、历史成绩等非核心信息，直接切入当前任务。
  例："提高思想认识…需全面完成发电目标"→"要不折不扣执行各项工作部署…工作状态要立即转入决战状态"

【规则8：禁用模糊和推诿】
  禁用：重点难点、非XX原因导致、大概、差不多
  替换：人员稳定→情绪稳定、正式反馈→及时反馈、提前预判、及时沟通→及时了解
  删除"非总包部原因导致"等推诿性表述，直接客观陈述问题。

【规则9：口语化→书面化转换】
  "搞一个"→"组建/编制"、"抓紧"→"限期/及时"、"多商量多沟通"→"进行技术沟通"
  删除"大家"等主观代词，"我觉得应该"→直接写决定

【规则10：数据完整】所有技术参数（包括"待确定"的）都必须在正文中出现。

【规则11：开头综述模式】按"时间+主持人+会议名称+听取/讨论内容+总体结论/要求+'纪要如下：'"结构撰写开头。如不知道确切日期/人名，用"近日"等自然语言概括，绝不可使用"XXX""X月X日"占位符。

【规则12：条目编号体系】工作部署/要求类内容用"1.""2.""3."或"（1）（2）（3）"编号；具体项目审查/费用商谈类用"第一项""第二项"大分类，内部可嵌套"（1）"。

【规则13：行动项表述】用祈使句："要+具体动作"、"加强+工作内容"、"确保+目标"、"抓紧"、"进一步"、"争取"，并尽可能明确时间节点和量化标准。

【规则14：问题处理表述】先客观陈述问题/分歧点，然后给出明确的处理原则/解决措施/商定结论，表述果断明确无歧义。

【规则15：结尾格式】公司级会议用"发送：… 整理：…"格式收尾；部门级会议用"部门名称+日期"落款。无多余寒暄。

【规则16：内容增删与重点细化】关键工程部位处理要求要前置补充重要性论述（如"唯一通道""重要保障"），补充关键民生问题（如农民工工资），删除空泛冗余词（如"重点难点"）。

参考公文风格示例（来自定稿）：
"由XX副主任牵头负责，工程管理部、合同采购部、EPC项目部专人参加组成XX工程支付管理专班。"

"有关XX项目工程款的支付商财资部尽量采用现金支付，确保施工单位能及时拿到现金，保障现场材料采购、农民工工资支付和施工正常运转。"

"1.进厂交通洞涉及工程安全，是进厂厂房唯一通道，是发电生产的重要保障，关乎抢险的进度，要高度重视交通洞的裂缝处治。"

只输出JSON，不要markdown围栏，不要解释。"""

_USER_TEMPLATE = """请根据以下结构化会议分析结果，撰写正式的会议纪要正文内容。

会议信息：
- 标题：{title}
- 类别：{meeting_type}
- 议题数量：{topic_count}

结构化分析数据（含决策结论和行动项）：
{analysis_json}

{mandatory_data_check}

请输出以下JSON格式：

{{
  "会议标题": "（完整正式标题，如：XX水利枢纽工程XX专题会会议纪要）",
  "会议类别": "{meeting_type}",
  "会议议题": "（简要议题描述，如有多个议题用顿号分隔）",
  "内容段落": [
    "开头综述段：（100-200字，概括会议时间、地点、主持人、参会人员、会议性质和主要议题，需明确提及议题数量）",
    "1.（对应第一个议题的最终决策和工作部署，200-500字。必须包含：①问题概述 ②所有关键技术参数及设计/实际值 ③达成的共识和决策 ④具体工作要求和责任人）",
    "2.（对应第二个议题，同上格式，200-500字）",
    "...（每个议题都必须有独立段落，不可合并）"
  ]
}}

★★★ 关键要求 ★★★
1. **每个议题必须独立成段**，不可合并多个议题为一段
2. **内容段落数量必须等于"议题数量+1"**（1个开头综述 + N个议题部署）
3. 段落聚焦"最终达成一致的内容"：写决定、写要求、写部署
4. **所有技术参数必须与输入数据完全一致**（mm/kN/MPa等）
   ★★★ 上述"强制数据清单"中的每一项都必须在正文中出现 ★★★
   包括"待确定"的参数也要明确写出（如"XX参数设计值待确定"）
5. 行动项用"由XX负责...""要求XX前完成..."等表述嵌入
6. **开头综述**：如果不知道确切日期/人名/地点，用自然语言概括（如"近日"，"总包部组织召开"），**绝不可使用"XXX""X月X日"等占位符**
7. 开头综述要概括会议性质和主要议题，需明确提及议题数量
8. 正文中议题条目采用"1.""2.""3."阿拉伯数字编号进行结构化（工作部署类会议标准编号方式）"""


def generate(
    llm: MultiLLMClient,
    analysis: MeetingAnalysis,
    prefer_paid: bool = False,
    style_reference: str = "",
    revision_guide: str = "",
) -> GeneratedContent:
    """执行生成步骤：结构化分析 → 正式公文。

    Args:
        llm: 多模型 LLM 客户端
        analysis: 理解阶段的输出
        prefer_paid: 是否优先使用付费模型
        style_reference: 写作风格参考（从内网文章提炼）
        revision_guide: 领导审稿修改模式指南（从初稿→定稿差异提炼）
    Returns:
        GeneratedContent 包含正式文本
    """
    # 将 analysis 序列化为 LLM 友好的 JSON
    analysis_dict = {
        "会议标题": analysis.title,
        "会议类别": analysis.meeting_type,
        "议题列表": [
            {
                "议题名称": t.name,
                "背景": t.background,
                "讨论要点": list(t.discussion_points),
                "关键数据": [
                    {
                        "参数": kp.parameter,
                        "设计值": kp.design_value,
                        "实际值": kp.actual_value,
                        "备注": kp.remark,
                    }
                    for kp in t.key_data
                ],
                "决策结论": t.decision,
                "行动项": [
                    {
                        "负责人": ai.responsible,
                        "任务": ai.task,
                        "时限": ai.deadline,
                    }
                    for ai in t.action_items
                ],
            }
            for t in analysis.topics
        ],
    }

    # 注入风格参考和修改模式指南
    style_hint = ""
    if style_reference:
        style_hint = f"\n\n★★★ 本企业写作风格参考（请在生成中模仿此风格）★★★\n{style_reference}"
    if revision_guide:
        style_hint += f"\n\n★★★ 领导审稿修改模式指南（必须遵循）★★★\n{revision_guide}"

    # ★★★ 构建强制数据清单 ★★★
    data_items: list[str] = []
    for t in analysis.topics:
        for kp in t.key_data:
            data_items.append(
                f"  - {kp.parameter}: 设计={kp.design_value}, 实际={kp.actual_value}"
            )
    mandatory_data_check = (
        "★★★ 强制数据清单（以下每一项都必须在正文中出现）★★★\n"
        + "\n".join(data_items)
    ) if data_items else ""

    _log.info("开始生成阶段 | %d 个议题 | %d 个强制数据点 | 风格参考: %s",
              len(analysis.topics), len(data_items),
              "有" if style_reference else "无")

    raw = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_USER_TEMPLATE.format(
            title=analysis.title,
            meeting_type=analysis.meeting_type,
            topic_count=len(analysis.topics),
            analysis_json=json.dumps(analysis_dict, ensure_ascii=False, indent=2),
            mandatory_data_check=mandatory_data_check,
        ) + style_hint,
        json_mode=True,
        temperature=0.4,
        prefer_paid=prefer_paid,
        max_tokens=8192,
    )

    data = extract_json(raw)

    # 确保内容段落是字符串列表（LLM 可能返回嵌套结构）
    raw_paragraphs = data.get("内容段落", [])
    paragraphs: list[str] = []
    for p in raw_paragraphs:
        if isinstance(p, str):
            paragraphs.append(p.strip())
        elif isinstance(p, dict):
            # 尝试提取文本值
            text = p.get("text", "") or p.get("内容", "") or p.get("段落", "") or ""
            if text:
                paragraphs.append(text.strip())
            else:
                # 取第一个字符串值
                for v in p.values():
                    if isinstance(v, str) and v.strip():
                        paragraphs.append(v.strip())
                        break
        elif isinstance(p, list):
            paragraphs.append(" ".join(str(x) for x in p).strip())

    content = GeneratedContent(
        title=data.get("会议标题", analysis.title),
        doc_number="",  # 由模板填写
        meeting_type=data.get("会议类别", analysis.meeting_type),
        meeting_topic=data.get("会议议题", ""),
        meeting_date=analysis.meeting_date,
        meeting_location=analysis.meeting_location,
        host=analysis.host,
        participants="、".join(analysis.participants),
        content_paragraphs=tuple(paragraphs),
        compiler=analysis.compiler,
    )

    _log.info("生成完成 | 标题: %s | %d 个段落", content.title, len(content.content_paragraphs))

    return content
