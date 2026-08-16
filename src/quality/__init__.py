"""quality 包 —— 六层质量飞轮。

L1 exemplar:    黄金范例注入（few-shot）
L2 dual_review: 双盲交叉评审（内容官+格式官）
L3 fingerprint: 数字指纹审计（确定性防篡改）
L5 self_refine: 反馈驱动的定向修改闭环
L6 learn_hook:  修改定稿自动学习入库
"""
from .fingerprint import (
    Fingerprint,
    FingerprintReport,
    AuditIssue,
    extract_fingerprints,
    audit,
    audit_report_to_chinese,
)
from .dual_review import (
    ReviewIssue,
    DualReviewReport,
    dual_review,
)
from .exemplar import (
    Exemplar,
    ExemplarLibrary,
    build_fewshot_block,
    detect_genre,
)
from .self_refine import refine_with_feedback, run_refine_loop, RefineLoopResult
from .learn_hook import learn_pair, persist_rules, scan_iteration_dir

__all__ = [
    # L3
    "Fingerprint", "FingerprintReport", "AuditIssue",
    "extract_fingerprints", "audit", "audit_report_to_chinese",
    # L2
    "ReviewIssue", "DualReviewReport", "dual_review",
    # L1
    "Exemplar", "ExemplarLibrary", "build_fewshot_block", "detect_genre",
    # L5
    "refine_with_feedback", "run_refine_loop", "RefineLoopResult",
    # L6
    "learn_pair", "persist_rules", "scan_iteration_dir",
]
