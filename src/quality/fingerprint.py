"""数字指纹审计 —— 企业公文数字防篡改的确定性校验。

核心思想：企业公文中的数字（金额/日期/参数/文号）一旦出错即致命。
LLM 生成可能产生三类数字错误：
1. 遗漏：原文有的关键数字没写进成果
2. 篡改：数字被错误转录（130mm → 1300mm）
3. 幻觉：凭空编造原文没有的数字

本模块 100% 确定性（纯正则，零 API 成本）：
- extract_fingerprints(text): 提取所有数字及其上下文
- audit(source, generated): 三类错误全面审计
- audit_report_to_chinese(report): 转中文报告（注入 refine prompt）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..utils.logger import get_logger

_log = get_logger("quality.fingerprint")


# ════════════════════════════════════════════════════════
#  数字提取
# ════════════════════════════════════════════════════════

# 数量/金额/参数：130mm、600kN、4317.28万元、2181mm、31972.13万元、37.2万立方米
# 值组前的负向后顾 (?<![\d月]) 排除日期片段（"8月12日"中的"12日"不算时长）
_NUM_WITH_UNIT = re.compile(
    r"(?<![\d月])(\d+(?:\.\d+)?)\s*(万元|亿元|元|m³|立方米|万立方米|mm|cm|km|kN|KN|"
    r"MPa|Mpa|kV|KV|MW|kW|吨|万t|公里|‰|%|℃|个月|天|日|年|次|台|套|批|家|人|名|组|孔|段|层)"
)
# 日期：2026年8月13日、8月12日、2025-12-25、9.30、9月30日
# 「9.30」式须 (?<![\d.]) 防止把「4317.28」中的「17.28」误判为日期
_DATE = re.compile(
    r"(\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日|"
    r"(?<![\d.])[12]\d\.\d{1,2}(?![\d.])|\d{1,2}月底|\d{1,2}月底前)"
)
# 文号：XX建管[2025]25号、办（建设函〔2025〕790号）
_DOC_NUMBER = re.compile(
    r"([一-龥]{2,10}[\[〔(]\d{4}[\]〕)]\d+号)"
)
# 排序词（非数据，排除）：第一位、第2章 等
_ORDINAL = re.compile(r"第[一二三四五六七八九十\d]+")

# 无单位裸数字（参数值）：仅保留 3 位以上且不在排除列表的
_BARE_NUM = re.compile(r"(?<![\d.])(\d{2,6}(?:\.\d+)?)(?![\d.%月日号年])")

# 幻觉排除：编号类（1. 2. 3.）、百分比上下文已处理
_LIST_NUMBER = re.compile(r"^\s*\d{1,2}[\.、]\s")


@dataclass(frozen=True)
class Fingerprint:
    """一个数字指纹。"""
    value: str        # 数值文本（如 "4317.28"）
    unit: str         # 单位（如 "万元"），日期/文号为固定标签
    context: str      # 前后各10字符上下文
    kind: str         # num / date / doc_number / bare


def extract_fingerprints(text: str) -> list[Fingerprint]:
    """提取文本中所有数字指纹。"""
    fps: list[Fingerprint] = []
    seen_spans: list[tuple[int, int]] = []

    def _ctx(pos_start: int, pos_end: int) -> str:
        return text[max(0, pos_start - 10):pos_end + 10].replace("\n", " ")

    def _skip(pos: int) -> bool:
        return any(s <= pos < e for s, e in seen_spans)

    # 1. 日期（最具体，优先占位，避免"8月12日"被拆成"12日"时长）
    for m in _DATE.finditer(text):
        if _skip(m.start()):
            continue
        fps.append(Fingerprint(m.group(1), "日期", _ctx(m.start(), m.end()), "date"))
        seen_spans.append((m.start(), m.end()))

    # 2. 文号
    for m in _DOC_NUMBER.finditer(text):
        if _skip(m.start()):
            continue
        fps.append(Fingerprint(m.group(1), "文号", _ctx(m.start(), m.end()), "doc_number"))
        seen_spans.append((m.start(), m.end()))

    # 3. 带单位数字
    for m in _NUM_WITH_UNIT.finditer(text):
        if _skip(m.start()):
            continue
        # 排除纯序号（"1个月"这类保留，"第3次"由 ORDINAL 覆盖）
        prefix = text[max(0, m.start() - 2):m.start()]
        if _ORDINAL.search(prefix):
            continue
        fps.append(Fingerprint(m.group(1), m.group(2), _ctx(m.start(), m.end()), "num"))
        seen_spans.append((m.start(), m.end()))

    # 4. 裸数字（≥3位整数部分，排除列表编号）
    for m in _BARE_NUM.finditer(text):
        if _skip(m.start()):
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_prefix = text[line_start:m.start()]
        if _LIST_NUMBER.match(line_prefix):
            continue
        # 排除年份单独出现（2026年 已被日期捕获）
        fps.append(Fingerprint(m.group(1), "", _ctx(m.start(), m.end()), "bare"))

    return fps


# ════════════════════════════════════════════════════════
#  审计
# ════════════════════════════════════════════════════════

@dataclass
class AuditIssue:
    """一个审计问题。"""
    error_type: str    # missing / corrupted / hallucinated
    detail: str        # 中文描述


@dataclass
class FingerprintReport:
    """数字指纹审计报告。"""
    passed: bool
    source_count: int
    generated_count: int
    issues: list[AuditIssue] = field(default_factory=list)

    @property
    def score(self) -> int:
        """数字质量分：100 - 问题扣分。"""
        penalty = sum(
            15 if i.error_type == "corrupted" else 10
            for i in self.issues
        )
        return max(0, 100 - penalty)


def audit(
    source_text: str,
    generated_text: str,
    required_values: Optional[set[str]] = None,
) -> FingerprintReport:
    """对照原文审计生成文本的数字质量。

    Args:
        source_text: 原始材料全文（转写稿/参考文档）
        generated_text: 生成成果全文
        required_values: 必须包含的数值白名单（来自结构化理解的 key_data）。
            提供时，遗漏检查只针对白名单内的数字 —— 转写稿中的口语闲聊
            数字（"7吨8吨是吧"）不构成遗漏。篡改/幻觉检查始终全局执行。
    """
    src_fps = extract_fingerprints(source_text)
    gen_fps = extract_fingerprints(generated_text)

    # 建索引：value+unit → context 列表
    # 文号只按「[年份]编号号」核心比对（前缀单位名写法不一）
    def _doc_core(fp_value: str) -> str:
        m = re.search(r"[\[〔(]\d{4}[\]〕)]\d+号", fp_value)
        return m.group(0) if m else fp_value

    def _index(fps: list[Fingerprint]) -> dict[str, list[Fingerprint]]:
        idx: dict[str, list[Fingerprint]] = {}
        for fp in fps:
            if fp.kind == "doc_number":
                key = f"{_doc_core(fp.value)}|{fp.kind}"
            else:
                key = f"{fp.value}|{fp.unit}|{fp.kind}"
            idx.setdefault(key, []).append(fp)
        return idx

    src_idx = _index(src_fps)
    gen_idx = _index(gen_fps)

    issues: list[AuditIssue] = []

    # ── 检查1: 遗漏（原文关键数字未出现在成果中）──
    # 只审计高价值指纹：带单位的数字、日期、文号
    # required_values 提供时仅审计白名单（key_data），过滤口语噪音
    # 白名单条目: "值|单位"（数字对）或完整日期/文号字符串
    def _required(fp: Fingerprint) -> bool:
        if required_values is None:
            return True
        if fp.kind == "num":
            return f"{fp.value}|{fp.unit}" in required_values
        return fp.value in required_values

    for key, fps in src_idx.items():
        fp = fps[0]
        if fp.kind == "bare":
            continue  # 裸数字噪音大，不作为遗漏依据
        if not _required(fp):
            continue
        if key not in gen_idx:
            # 相近值存在则可能是格式差异（43.5 vs 43.50）
            if not _has_close_match(fp, gen_fps):
                issues.append(AuditIssue(
                    "missing",
                    f"原文关键数据「{fp.value}{fp.unit}」（上下文: …{fp.context}…）"
                    f"未出现在生成成果中",
                ))

    # ── 检查2: 篡改（原文的数字在成果中变成了别的值）──
    # 策略：成果中的 num 类指纹，若 value 在原文不存在但存在"相近"值，
    # 且单位相同 → 疑似篡改
    src_values = {fp.value for fp in src_fps}
    for fp in gen_fps:
        if fp.kind != "num":
            continue
        if fp.value in src_values:
            continue
        # 0) 数字换位（4317.28→4317.82：同单位同数字集不同顺序）—— 最典型的转录错误
        transposed = _find_transposed(fp, src_fps)
        if transposed is not None:
            issues.append(AuditIssue(
                "corrupted",
                f"疑似数字换位：成果中「{fp.value}{fp.unit}」，原文为"
                f"「{transposed.value}{transposed.unit}」（上下文: …{fp.context}…），请核对",
            ))
            continue
        close = _find_closest(fp, src_fps)
        if close is not None:
            issues.append(AuditIssue(
                "corrupted",
                f"疑似数字篡改：成果中「{fp.value}{fp.unit}」，原文最接近为"
                f"「{close.value}{close.unit}」（上下文: …{fp.context}…），请核对",
            ))
            continue
        # 数字位数偏移（130→1300，掉位/加位是 LLM 典型转录错误）
        shifted = _find_digit_shift(fp, src_fps)
        if shifted is not None:
            issues.append(AuditIssue(
                "corrupted",
                f"疑似数字位数错误：成果中「{fp.value}{fp.unit}」，原文为"
                f"「{shifted.value}{shifted.unit}」（上下文: …{fp.context}…），请核对位数",
            ))

    # ── 检查3: 幻觉（成果中的金额类数字原文完全没有）──
    for fp in gen_fps:
        if fp.unit not in ("万元", "亿元"):
            continue
        if fp.value in src_values:
            continue
        if not _has_close_match(fp, src_fps):
            issues.append(AuditIssue(
                "hallucinated",
                f"疑似凭空金额：「{fp.value}{fp.unit}」（上下文: …{fp.context}…）"
                f"在原文中未找到对应来源",
            ))

    # 幻觉类严重度低于篡改，排序：corrupted > missing > hallucinated
    priority = {"corrupted": 0, "missing": 1, "hallucinated": 2}
    issues.sort(key=lambda i: priority[i.error_type])

    report = FingerprintReport(
        passed=len(issues) == 0,
        source_count=len(src_fps),
        generated_count=len(gen_fps),
        issues=issues,
    )
    _log.info(
        "数字指纹审计: 原文%d个指纹, 成果%d个指纹, 问题%d个 → %s",
        report.source_count, report.generated_count, len(issues),
        "通过" if report.passed else f"评分{report.score}",
    )
    return report


def _has_close_match(target: Fingerprint, pool: list[Fingerprint]) -> bool:
    """同单位下存在小数值差异（<1%）的匹配（视为格式差异）。"""
    try:
        tv = float(target.value)
    except ValueError:
        return False
    for fp in pool:
        if fp.unit != target.unit or fp.kind != target.kind:
            continue
        try:
            pv = float(fp.value)
        except ValueError:
            continue
        if pv == 0 and tv == 0:
            return True
        denom = max(abs(pv), abs(tv), 1e-9)
        if abs(tv - pv) / denom < 0.01:
            return True
    return False


def _find_closest(target: Fingerprint, pool: list[Fingerprint]) -> Optional[Fingerprint]:
    """找同单位、数字相近（1%-50% 数值差异）的指纹（疑似转录篡改）。"""
    try:
        tv = float(target.value)
    except ValueError:
        return None
    best: Optional[Fingerprint] = None
    best_ratio = float("inf")
    for fp in pool:
        if fp.kind != "num" or fp.unit != target.unit:
            continue
        try:
            pv = float(fp.value)
        except ValueError:
            continue
        denom = max(abs(pv), abs(tv), 1e-9)
        ratio = abs(tv - pv) / denom
        # 相近但不同：1% ~ 50% 差异视为疑似篡改（10倍差异另由位数检查覆盖）
        if 0.01 < ratio <= 0.5 and ratio < best_ratio:
            best = fp
            best_ratio = ratio
    return best


def _find_transposed(target: Fingerprint, pool: list[Fingerprint]) -> Optional[Fingerprint]:
    """检测数字换位：同单位、数字字符多重集相同但顺序不同（4317.28↔4317.82）。"""
    tv_digits = sorted(target.value)
    for fp in pool:
        if fp.kind != "num" or fp.unit != target.unit or fp.value == target.value:
            continue
        if len(fp.value) != len(target.value):
            continue
        if sorted(fp.value) == tv_digits:
            return fp
    return None


def _find_digit_shift(target: Fingerprint, pool: list[Fingerprint]) -> Optional[Fingerprint]:
    """检测位数偏移：value 与原文同单位值互为前缀（130↔1300，4317↔43172）。"""
    tv = target.value.lstrip("0") or "0"
    if "." in tv:
        tv_int, tv_frac = tv.split(".", 1)
    else:
        tv_int, tv_frac = tv, ""
    for fp in pool:
        if fp.kind != "num" or fp.unit != target.unit:
            continue
        pv = fp.value.lstrip("0") or "0"
        if "." in pv:
            pv_int, pv_frac = pv.split(".", 1)
        else:
            pv_int, pv_frac = pv, ""
        if tv_frac or pv_frac:
            continue  # 含小数的不做位数判断（避免误报）
        if tv_int == pv_int:
            continue
        # 一方是另一方的前缀（尾部加/减一位数字）
        if len(tv_int) != len(pv_int) and (
            tv_int.startswith(pv_int) or pv_int.startswith(tv_int)
        ):
            return fp
    return None


def audit_report_to_chinese(report: FingerprintReport, max_items: int = 12) -> str:
    """转中文清单（注入 Self-Refine prompt，截断防超长）。"""
    if report.passed:
        return "数字指纹审计通过：所有关键数字与原文一致。"
    issues = report.issues[:max_items]
    lines = [
        f"数字指纹审计发现 {len(report.issues)} 个问题"
        + ("（仅列前{}项）".format(max_items) if len(report.issues) > max_items else "")
        + "（必须逐项修复）：",
    ]
    for i, issue in enumerate(issues, 1):
        lines.append(f"  {i}. [{issue.error_type}] {issue.detail}")
    return "\n".join(lines)
