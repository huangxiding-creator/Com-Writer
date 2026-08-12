"""生成黄藏寺930并网发电第1次调度会会议纪要。

读取2份转写稿 → LLM理解 → 生成 → 后处理 → 套用工程管理部模板输出
"""
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

from src.config.loader import Config
from src.llm.multi_llm import MultiLLMClient
from src.processors.understander import understand
from src.processors.generator import generate
from src.processors.refiner import _find_missing_data, _inject_missing_data
from src.processors.post_processor import process as post_process, verify as post_verify
from src.readers.docx_reader import read_docx
from src.readers.transcript_parser import parse_transcript
from src.writers.docx_writer import write_minutes
from src.writers.template_engine import analyze_template
from src.core.models import GeneratedContent
from src.utils.logger import get_logger
from src.config.paths import RAW_RECORD_DIR, TEMPLATE_DIR, OUTPUT_DIR, REFINE_DIR

_log = get_logger("scripts.generate_930")

# ── 路径配置 ──
import os
TRANSCRIPT_FILES = sorted([
    f for f in os.listdir(str(RAW_RECORD_DIR))
    if "930" in f and f.endswith(".docx")
])
TEMPLATE_FILE = next(
    f for f in os.listdir(str(TEMPLATE_DIR)) if "工程管理部" in f
)
OUTPUT_PATH = OUTPUT_DIR / "黄藏寺930并网发电第1次调度会会议纪要.docx"


def read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(str(path)) as z:
        content = z.read("word/document.xml").decode("utf-8", errors="ignore")
        t_elems = re.findall(r"<w:t[^>]*>([^<]+)</w:t>", content)
        return "".join(t_elems)


def main():
    cfg = Config(ROOT / "config.ini")
    llm = MultiLLMClient(cfg)

    # ── 读取2份转写稿并合并 ──
    _log.info("=" * 60)
    _log.info("读取转写稿（%d份）", len(TRANSCRIPT_FILES))
    _log.info("=" * 60)

    combined_text = ""
    for fname in TRANSCRIPT_FILES:
        fpath = RAW_RECORD_DIR / fname
        text = read_docx_text(fpath)
        _log.info("  %s: %d字", fname, len(text))
        combined_text += "\n\n" + text

    raw_text = combined_text.strip()
    transcript = parse_transcript(raw_text)
    _log.info("合并转写稿: %d字, %d条发言", transcript.char_count, transcript.entry_count)

    # ── 加载知识源 ──
    style_ref = ""
    v2_path = REFINE_DIR / "writing_style_reference_v2.txt"
    if v2_path.exists():
        style_ref = v2_path.read_text(encoding="utf-8")
        _log.info("风格参考: %d字", len(style_ref))

    revision_guide = ""
    guide_path = REFINE_DIR / "revision_guide_definitive.txt"
    if guide_path.exists():
        revision_guide = guide_path.read_text(encoding="utf-8")
        _log.info("修改模式指南: %d字", len(revision_guide))

    # ── Phase 1: 理解 ──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 1: 理解阶段")
    _log.info("=" * 60)

    analysis = None
    for attempt in range(1, 4):
        _log.info("理解尝试 %d/3...", attempt)
        try:
            analysis = understand(llm, transcript, prefer_paid=True,
                                  filename_hint="黄藏寺930并网发电第1次调度会")
            topics = len(analysis.topics)
            key_data = sum(len(t.key_data) for t in analysis.topics)
            actions = sum(len(t.action_items) for t in analysis.topics)
            _log.info("  结果: %d议题, %d数据, %d行动项", topics, key_data, actions)
            if topics >= 2 and key_data >= 5 and actions >= 3:
                break
        except Exception as e:
            _log.warning("理解失败: %s", str(e)[:80])

    if analysis is None:
        _log.error("理解阶段失败")
        return

    # ── Phase 2: 生成 ──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 2: 生成会议纪要")
    _log.info("=" * 60)

    start = time.time()
    content = generate(
        llm, analysis,
        prefer_paid=True,
        style_reference=style_ref,
        revision_guide=revision_guide,
    )
    elapsed = time.time() - start
    _log.info("生成完成 | %d段 | %.0f秒", len(content.content_paragraphs), elapsed)

    # ── Phase 3: 数据注入 ──
    _log.info("")
    _log.info("Phase 3: 数据完整性补全")

    missing = _find_missing_data(analysis, content)
    if missing:
        _log.info("发现 %d 个缺失数据点，执行注入", len(missing))
        injected = _inject_missing_data(list(content.content_paragraphs), missing)
        content = GeneratedContent(
            title=content.title,
            doc_number=content.doc_number,
            meeting_type=content.meeting_type,
            meeting_topic=content.meeting_topic,
            meeting_date=content.meeting_date,
            meeting_location=content.meeting_location,
            host=content.host,
            participants=content.participants,
            content_paragraphs=tuple(injected),
            compiler=content.compiler,
            model_used=content.model_used,
        )
    else:
        _log.info("✅ 所有关键数据已包含")

    # ── Phase 4: 确定性后处理 ──
    _log.info("")
    _log.info("Phase 4: 确定性后处理")

    pp_result = post_process(content)
    content = pp_result.content
    _log.info("后处理: %d 项修正", pp_result.change_count)

    # ── Phase 5: 合规验证 ──
    _log.info("")
    _log.info("Phase 5: 合规性验证")

    issues = post_verify(content)
    if issues:
        _log.warning("发现 %d 个合规问题:", len(issues))
        for issue in issues:
            _log.warning("  ⚠ %s", issue)
    else:
        _log.info("✅ 全部合规检查通过")

    # ── Phase 6: 输出 Word ──
    _log.info("")
    _log.info("=" * 60)
    _log.info("Phase 6: 输出 Word 文档")
    _log.info("=" * 60)

    template_path = TEMPLATE_DIR / TEMPLATE_FILE
    template_info = analyze_template(template_path)

    # 更新标题
    content = GeneratedContent(
        title="黄藏寺930并网发电第1次调度会会议纪要",
        doc_number=content.doc_number,
        meeting_type=content.meeting_type,
        meeting_topic=content.meeting_topic,
        meeting_date=content.meeting_date,
        meeting_location=content.meeting_location,
        host=content.host,
        participants=content.participants,
        content_paragraphs=content.content_paragraphs,
        compiler=content.compiler,
        model_used=content.model_used,
    )

    final_path = write_minutes(
        content=content,
        template_info=template_info,
        template_path=template_path,
        output_path=OUTPUT_PATH,
    )

    _log.info("")
    _log.info("=" * 60)
    _log.info("✅ 会议纪要生成完成！")
    _log.info("输出: %s", final_path)
    _log.info("段落数: %d", len(content.content_paragraphs))
    _log.info("=" * 60)

    # 输出预览
    _log.info("")
    _log.info("【内容预览】")
    for i, para in enumerate(content.content_paragraphs):
        _log.info("  段落%d (%d字): %s...", i + 1, len(para), para[:100])


if __name__ == "__main__":
    main()
