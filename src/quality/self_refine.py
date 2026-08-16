"""通用 Self-Refine 迭代器 —— 基于结构化反馈的定向修改（体裁无关）。

Layer 5：评分不达标时，把「数字指纹审计 + 双盲评审」的问题清单
注入修改 prompt，让 LLM 只修问题、不动已合格部分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..llm.multi_llm import MultiLLMClient
from ..utils.logger import get_logger

_log = get_logger("quality.self_refine")

_SYSTEM_PROMPT = """你是一位企业公文修改专家。你的任务是**针对性修改**文档中的具体问题。

★★★ 修改铁律 ★★★
1. **最小改动**：只修复问题清单中指出的问题，未涉及的句子保持原样
2. **数字零错**：涉及数字的修复，必须使用审计报告中给出的原文数值，禁止四舍五入或改写
3. **结构不变**：段落划分、标题、落款格式保持不变
4. **只修不辩**：不要解释，直接输出修改后的全文"""


def refine_with_feedback(
    llm: MultiLLMClient,
    current_text: str,
    feedback: str,
    prefer_paid: bool = True,
) -> str:
    """基于反馈清单执行一轮定向修改，返回修改后全文。"""
    user_prompt = f"""【当前文档全文】：

{current_text}

【审计与评审问题清单】（逐项修复，一项不漏）：

{feedback}

请输出修复后的**完整文档全文**（保持原有段落结构），不要输出任何解释。"""

    result = llm.chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.2,
        prefer_paid=prefer_paid,
        max_tokens=8192,
    )
    # 剥掉可能的 markdown 围栏
    text = result.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("\n"):
            text = text[1:]
        # 去掉可能的语言标注行
        first_nl = text.find("\n")
        if first_nl > 0 and text[:first_nl].strip() in ("text", "markdown", "txt"):
            text = text[first_nl + 1:]
    return text.strip()


@dataclass
class RefineLoopResult:
    """Self-Refine 循环结果。"""
    final_text: str
    iterations: int
    scores: list[int] = field(default_factory=list)
    audit_passed: bool = False
    review_passed: bool = False


def run_refine_loop(
    llm: MultiLLMClient,
    initial_text: str,
    source_text: str,
    genre: str,
    skill_rules: str,
    max_rounds: int = 3,
    target_score: int = 85,
    prefer_paid: bool = True,
    required_values: Optional[set] = None,
    on_round: Optional[Callable[[int, str], None]] = None,
) -> RefineLoopResult:
    """执行「审计→评审→修改」闭环直到达标或耗尽轮次。

    Args:
        llm: LLM 客户端
        initial_text: 初始生成全文
        source_text: 原始材料（事实基准）
        genre: 体裁
        skill_rules: 体裁写作规则
        max_rounds: 最大迭代轮数
        target_score: 双盲评审综合分阈值
        required_values: 必须包含的数值白名单（结构化理解 key_data）
        on_round: 每轮回调 (轮次, 摘要)
    """
    from .fingerprint import audit, audit_report_to_chinese
    from .dual_review import dual_review

    current = initial_text
    result = RefineLoopResult(final_text=current, iterations=0)
    prev_signature: Optional[tuple] = None  # 无进展检测: (指纹问题数, 评审分)

    for round_no in range(1, max_rounds + 1):
        result.iterations = round_no

        # L3 数字指纹（确定性）
        fp_report = audit(source_text, current, required_values=required_values)
        result.audit_passed = fp_report.passed

        # L2 双盲评审
        review = dual_review(
            llm, source_text, current,
            genre=genre, skill_rules=skill_rules,
        )
        result.review_passed = review.passed
        result.scores.append(review.overall)

        summary = (
            f"轮次{round_no}: 指纹{'✓' if fp_report.passed else '✗'}"
            f"({len(fp_report.issues)}问题), "
            f"评审{review.overall}分(内容{review.content_score}/格式{review.format_score})"
        )
        if on_round:
            on_round(round_no, summary)
        _log.info("Self-Refine %s", summary)

        # 达标判定
        if fp_report.passed and review.passed:
            _log.info("第 %d 轮即达标，停止迭代", round_no)
            break

        # 无进展早停：连续两轮 (问题数, 评分) 相同 → 再迭代也无收益
        signature = (len(fp_report.issues), review.overall)
        if signature == prev_signature:
            _log.warning("连续两轮无进展（%s），提前停止迭代", signature)
            break
        prev_signature = signature

        # 最后一轮不再修改
        if round_no == max_rounds:
            break

        # L5 定向修改
        feedback = audit_report_to_chinese(fp_report) + "\n\n" + review.raw_feedback
        try:
            revised = refine_with_feedback(
                llm, current, feedback, prefer_paid=prefer_paid,
            )
            if len(revised) > len(initial_text) * 0.5:  # 合理性护栏
                current = revised
                result.final_text = current
            else:
                _log.warning("修改稿长度异常(%d字)，丢弃本轮修改", len(revised))
                break
        except Exception as e:
            _log.warning("Self-Refine 修改失败: %s", str(e)[:100])
            break

    return result
