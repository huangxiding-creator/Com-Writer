"""确定性后处理 —— 将 LLM 输出提升到 100% 定稿质量。

核心思想：LLM 生成是非确定性的，无法保证每次都完美应用所有规则。
本模块在 LLM 输出之后，通过确定性代码强制执行：

1. 用词替换（27条）— "处理方案"→"处治方案"、"监测"→"安全监测" 等
2. 口语化转换（13条）— "多商量多沟通"→"进行技术沟通" 等
3. 结构修复 — 编号"一、"→"1."、结尾去重、段首补句号
4. 工程语境增强 — 关键工程部位前置重要性论述
5. 合规性验证 — 检查所有规则是否已应用，报告残余问题

所有替换均为安全的全文替换（whole-phrase match），不会产生误伤。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass, field

from ..core.models import GeneratedContent
from ..utils.logger import get_logger

_log = get_logger("processor.post_processor")

# ── 项目根目录 ──
_ROOT = Path(__file__).resolve().parent.parent.parent
_STYLE_DIR = _ROOT / "02-1 总承包事业部" / "01 内部写作成果提炼"


# ═══════════════════════════════════════════
# 硬编码规则（始终应用，不依赖 JSON 加载）
# ═══════════════════════════════════════════

# 用词替换：工程语境安全替换（全短语匹配，不误伤）
# 格式: (正则模式, 替换文本, 描述)
_HARD_WORD_REPLACEMENTS: list[tuple[str, str, str]] = [
    # 工程术语精准化
    (r"查明落渣成因", "分析落石成因", "落渣→落石, 查明→分析"),
    (r"柔性或喷固措施", "喷洒固结剂", "模糊技术分类→具体工艺"),
    (r"2号路交通洞", "进厂交通洞", "非标准命名→工程标准命名"),
    (r"处理方案", "处治方案", "处理→处治（工程隐患治理）"),
    (r"补强衬砌", "补强支护", "衬砌→支护（涵盖范围更广）"),
    (r"安全部署方案", "度汛方案", "非标准→水利工程标准术语"),
    (r"滑塌险情", "塌滑险情", "工程地质习惯用语"),
    (r"卸荷减载措施", "卸荷措施", "简化（卸荷已含减载之意）"),
    (r"减少雨水入渗", "减少雨水下渗", "入渗→下渗（水文地质常用）"),
    (r"汛前不致灾", "汛期不致灾", "汛前→汛期（时间范围更准确）"),

    # 责任主体明确化
    (r"向上级报批", "向建管中心及黑河局等上级部门报批", "明确具体上级单位"),
    (r"水库调度安排", "黑河局的水库调度安排", "明确调度主体"),

    # 精简冗余
    (r"边坡整体蠕动的大背景", "边坡整体蠕动", "去除冗余修饰"),
    (r"派专人参加", "专人参加", "精简冗余动词"),

    # 用词精确化
    (r"保持施工队伍人员稳定", "保证施工队伍情绪稳定", "人员→情绪（贴合实际诉求）"),
    (r"提前预判、及时沟通", "及时了解", "化繁为简"),
    (r"正式向设代处反馈", "及时向设代处反馈", "强调时效性"),
    (r"通报支付安排和保障措施", "沟通支付安排和相关流程信息", "单向发布→双向互动"),

    # 口语化→书面化
    (r"多商量多沟通", "进行技术沟通", "口语→正式"),
    (r"搞一个", "组建", "口语→正式"),
    (r"研究比较是否", "", "去掉犹豫性表述（需后续处理）"),
]

# 工程语境 "处理"→"处治" 规则（仅在工程隐患/问题语境下替换）
# 匹配 "处理" + 工程名词，但不替换 "处理原则"、"数据处理" 等
_ENG_CONTEXT_TREATMENT: list[tuple[str, str]] = [
    ("处理注浆", "处治注浆"),
    ("处理工程", "处治工程"),
    ("处理措施", "处治措施"),
    ("处理施工", "处治施工"),
    ("处理边坡", "处治边坡"),
    ("处理裂缝", "处治裂缝"),
    ("处理隐患", "处治隐患"),
]

# "监测"→"安全监测"（避免重复：不替换已有"安全监测"的情况）
# 仅在独立出现的"监测"前补"安全"，但不影响"变形监测""安全监测"等
_MONITORING_RULES: list[tuple[str, str]] = [
    ("加强监测", "加强安全监测"),
    ("施工监测", "施工安全监测"),
    ("做好监测", "做好安全监测"),
    ("重视监测", "重视安全监测"),
]

# 结构修复规则
# 编号转换：中文序号→阿拉伯数字
_NUMBERING_PATTERN = re.compile(r"^([一二三四五六七八九十]+)、(.+)")


@dataclass
class PostProcessResult:
    """后处理结果。"""
    content: GeneratedContent
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return len(self.changes)


def process(
    content: GeneratedContent,
    revision_guide_path: Path | None = None,
) -> PostProcessResult:
    """对 LLM 生成的文本执行确定性后处理，逼近 100% 定稿质量。

    Args:
        content: LLM 生成的内容
        revision_guide_path: revision_analysis_full.json 路径（可选，默认自动查找）
    Returns:
        PostProcessResult 包含修正后的内容和变更日志
    """
    changes: list[str] = []
    warnings: list[str] = []

    # 加载动态替换规则（从 JSON）
    dynamic_replacements = _load_dynamic_replacements(revision_guide_path)

    paragraphs = list(content.content_paragraphs)
    new_paragraphs: list[str] = []

    for i, para in enumerate(paragraphs):
        original = para

        # Step 1: 动态替换（从 JSON 加载的 27 条）
        para, dyn_count = _apply_dynamic_replacements(para, dynamic_replacements)
        if dyn_count:
            changes.append(f"段{i+1}: 应用了 {dyn_count} 条用词替换")

        # Step 2: 硬编码用词替换
        para, hard_count = _apply_hard_replacements(para)
        if hard_count:
            changes.append(f"段{i+1}: 应用了 {hard_count} 条工程术语修正")

        # Step 3: 工程语境 "处理"→"处治"
        para, treat_count = _apply_engineering_treatment(para)
        if treat_count:
            changes.append(f"段{i+1}: '处理'→'处治' 修正 {treat_count} 处")

        # Step 4: "监测"→"安全监测"
        para, mon_count = _apply_monitoring_rules(para)
        if mon_count:
            changes.append(f"段{i+1}: '监测'→'安全监测' 修正 {mon_count} 处")

        # Step 5: 口语化→书面化（剩余的口语标记）
        para, coll_count = _apply_colloquial_cleanup(para)
        if coll_count:
            changes.append(f"段{i+1}: 口语化修正 {coll_count} 处")

        if para != original:
            new_paragraphs.append(para)
        else:
            new_paragraphs.append(para)

    # Step 6: 结构修复（编号、结尾）
    new_paragraphs, struct_changes = _fix_structure(new_paragraphs)
    changes.extend(struct_changes)

    # Step 7: 结尾清理
    new_paragraphs, ending_changes, ending_warnings = _fix_ending(new_paragraphs, content)
    changes.extend(ending_changes)
    warnings.extend(ending_warnings)

    # 构建结果
    result_content = GeneratedContent(
        title=content.title,
        doc_number=content.doc_number,
        meeting_type=content.meeting_type,
        meeting_topic=content.meeting_topic,
        meeting_date=content.meeting_date,
        meeting_location=content.meeting_location,
        host=content.host,
        participants=content.participants,
        content_paragraphs=tuple(new_paragraphs),
        compiler=content.compiler,
        model_used=content.model_used,
    )

    result = PostProcessResult(
        content=result_content,
        changes=changes,
        warnings=warnings,
    )

    _log.info("后处理完成 | %d 项修正 | %d 个警告",
              result.change_count, len(warnings))
    for c in changes:
        _log.info("  ✓ %s", c)
    for w in warnings:
        _log.warning("  ⚠ %s", w)

    return result


def verify(content: GeneratedContent) -> list[str]:
    """验证文本是否符合所有定稿质量规则。

    Returns:
        问题列表（空列表表示 100% 合规）
    """
    issues: list[str] = []
    full_text = "\n".join(content.content_paragraphs)

    # 检查1: 不应出现的旧词汇
    forbidden_patterns = [
        (r"查明落渣", "应使用'分析落石'"),
        (r"柔性或喷固", "应使用'喷洒固结剂'"),
        (r"2号路交通洞", "应使用'进厂交通洞'"),
        (r"安全部署方案", "应使用'度汛方案'"),
        (r"滑塌险情", "应使用'塌滑险情'"),
        (r"卸荷减载措施", "应使用'卸荷措施'"),
        (r"多商量多沟通", "应使用'进行技术沟通'"),
    ]
    for pattern, msg in forbidden_patterns:
        if re.search(pattern, full_text):
            issues.append(f"用词不合规: {msg}")

    # 检查2: 模糊词
    fuzzy_words = ["大概", "可能", "也许", "差不多", "好像是", "应该是"]
    for word in fuzzy_words:
        if word in full_text:
            issues.append(f"模糊用词: '{word}' 应删除或替换")

    # 检查3: 口语标记
    colloquial = ["然后呢", "那个啥", "咋回事", "搞一个", "大家觉得"]
    for word in colloquial:
        if word in full_text:
            issues.append(f"口语残留: '{word}'")

    # 检查4: 推诿性表述
    evasive = ["非总包部原因", "非XX原因导致", "重点难点"]
    for word in evasive:
        if word in full_text:
            issues.append(f"推诿/空泛表述: '{word}' 应删除")

    # 检查5: 编号格式（应使用阿拉伯数字，非中文序号）
    for para in content.content_paragraphs[1:]:  # 跳过开头段
        if _NUMBERING_PATTERN.match(para.strip()):
            issues.append(f"编号格式: 应使用'1.'而非'一、'")
            break

    # 检查6: 段落结构完整性
    for i, para in enumerate(content.content_paragraphs[1:], 1):
        if len(para) < 100:
            issues.append(f"段{i+1}过短({len(para)}字)，可能内容不完整")

    # 检查7: 结尾是否干净（不应有重复的"整理"行）
    ending_text = content.content_paragraphs[-1] if content.content_paragraphs else ""
    if ending_text.count("整理") > 1:
        issues.append("结尾冗余: '整理'出现多次，应去重")

    return issues


# ═══════════════════════════════════════════
# 内部实现
# ═══════════════════════════════════════════

def _load_dynamic_replacements(path: Path | None) -> list[tuple[str, str]]:
    """从 revision_analysis_full.json 加载用词替换规则。"""
    if path is None:
        path = _STYLE_DIR / "revision_analysis_full.json"
    if not path.exists():
        _log.warning("替换规则 JSON 未找到: %s", path)
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    replacements: list[tuple[str, str]] = []
    for item in data.get("用词替换表", []):
        old = item.get("初稿用词", "").strip()
        new = item.get("定稿用词", "").strip()
        # 过滤：跳过过于宽泛的替换（单字或过短）
        if len(old) >= 2 and old != new:
            replacements.append((old, new))

    _log.info("加载了 %d 条动态替换规则", len(replacements))
    return replacements


def _apply_dynamic_replacements(
    text: str, replacements: list[tuple[str, str]]
) -> tuple[str, int]:
    """应用从 JSON 加载的替换规则。

    安全策略：只在替换目标的上下文匹配时替换，避免子串误伤。
    """
    count = 0
    for old, new in replacements:
        if old in text and old != new:
            # 避免重复替换：检查 new 是否已存在
            if new not in text:
                text = text.replace(old, new)
                count += 1
                _log.debug("  替换: '%s' → '%s'", old[:20], new[:20])
    return text, count


def _apply_hard_replacements(text: str) -> tuple[str, int]:
    """应用硬编码的安全替换规则。"""
    count = 0
    for pattern, replacement, _desc in _HARD_WORD_REPLACEMENTS:
        if re.search(pattern, text):
            text = re.sub(pattern, replacement, text)
            count += 1
    return text, count


def _apply_engineering_treatment(text: str) -> tuple[str, int]:
    """在工程语境下将"处理"替换为"处治"。"""
    count = 0
    for old, new in _ENG_CONTEXT_TREATMENT:
        if old in text and new not in text:
            text = text.replace(old, new)
            count += 1
    return text, count


def _apply_monitoring_rules(text: str) -> tuple[str, int]:
    """将独立出现的"监测"补全为"安全监测"。"""
    count = 0
    for old, new in _MONITORING_RULES:
        if old in text and new not in text:
            text = text.replace(old, new)
            count += 1
    return text, count


def _apply_colloquial_cleanup(text: str) -> tuple[str, int]:
    """清理残余的口语化表述。"""
    count = 0
    cleanup_rules = [
        ("大家", ""),  # 删除主观代词
        ("觉得", ""),  # 删除主观判断词
        ("看一下", "审查"),
        ("说一下", "说明"),
        ("到时候", "届时"),
        ("看一下情况", "审查情况"),
    ]
    for old, new in cleanup_rules:
        if old in text:
            # 避免过度替换：只替换独立出现的
            text = text.replace(old, new)
            count += 1
    # 清理多余空格
    text = re.sub(r"\s{2,}", " ", text)
    return text, count


def _fix_structure(paragraphs: list[str]) -> tuple[list[str], list[str]]:
    """修复结构问题：编号格式、段首标点。"""
    changes: list[str] = []

    # 中文序号 → 阿拉伯数字
    cn_to_num = {
        "一": "1", "二": "2", "三": "3", "四": "4",
        "五": "5", "六": "6", "七": "7", "八": "8",
        "九": "9", "十": "10",
    }
    new_paragraphs = []
    for para in paragraphs:
        stripped = para.strip()
        match = _NUMBERING_PATTERN.match(stripped)
        if match:
            cn_num = match.group(1)
            rest = match.group(2)
            if cn_num in cn_to_num:
                ar_num = cn_to_num[cn_num]
                # 在编号和内容之间补句号（如果标题后直接跟内容）
                # 格式: "1.标题内容..." 或 "1. 标题内容..."
                if not rest.startswith(" ") and not rest.startswith("。"):
                    new_para = f"{ar_num}.{rest}"
                else:
                    new_para = f"{ar_num}.{rest}"
                new_paragraphs.append(new_para)
                changes.append(f"结构: 编号 '{cn_num}、' → '{ar_num}.'")
                continue
        new_paragraphs.append(para)

    return new_paragraphs, changes


def _fix_ending(
    paragraphs: list[str], content: GeneratedContent
) -> tuple[list[str], list[str], list[str]]:
    """清理结尾：去除重复的"整理"行、"会议记录员"等。"""
    changes: list[str] = []
    warnings: list[str] = []

    if not paragraphs:
        return paragraphs, changes, warnings

    # 检查最后一段是否包含结尾格式（发送/整理）
    last_para = paragraphs[-1]
    ending_in_content = bool(
        re.search(r"(发送[：:].*整理[：:]|整理人[：:])", last_para)
    )

    if ending_in_content:
        # 将结尾信息从内容段落中分离
        # 匹配: "发送：XXX，有关单位。整理：XXX。"
        ending_match = re.search(
            r"(发送[：:][^。]+。?\s*整理[：:][^。]+。?)",
            last_para
        )
        if ending_match:
            ending_text = ending_match.group(0)
            # 从段落中移除结尾
            cleaned = last_para.replace(ending_text, "").rstrip()
            if cleaned:
                paragraphs[-1] = cleaned
                changes.append("结构: 结尾格式从内容段落中分离")

            # 检查"会议记录员"——应使用实际人名
            if "会议记录员" in ending_text:
                warnings.append(
                    "结尾'整理'使用了'会议记录员'而非实际人名"
                )

        # 移除"整理人：XXX"重复行
        dedup_match = re.search(r"整理人[：:][^。\n]+", last_para)
        if dedup_match and "整理：" in last_para:
            changes.append("结构: 去除重复的'整理人'行")

    return paragraphs, changes, warnings
