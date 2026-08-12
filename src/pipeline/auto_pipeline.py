"""全自动写作管线 —— 一键执行：学习→理解→生成→后处理→验证→输出→推送。

用户只需提供：
1. 学习源（URL 或本地文件夹路径，可选）
2. 模板文件列表（多选，可选）
3. 输入文件列表（多选）
4. 输出目录
5. 子风格（单选，可选）
6. 子体裁分类（单选，可选）

管线自动完成全部步骤，通过回调实时报告进度。
"""
from __future__ import annotations

import time
import shutil
import zipfile
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..config.loader import Config
from ..config.paths import REFINE_DIR, PROJECT_ROOT
from ..skills import SkillRegistry
from ..llm.multi_llm import MultiLLMClient
from ..processors.understander import understand
from ..processors.generator import generate
from ..processors.refiner import _find_missing_data, _inject_missing_data
from ..processors.post_processor import process as post_process, verify as post_verify
from ..readers.docx_reader import read_docx
from ..readers.transcript_parser import parse_transcript
from ..writers.docx_writer import write_minutes
from ..writers.template_engine import analyze_template
from ..core.models import GeneratedContent
from ..utils.logger import get_logger
from ..workspace.manager import Workspace

_log = get_logger("pipeline.auto")

ProgressCallback = Callable[[str, int, int], None]
"""回调签名: (步骤描述, 当前步骤号, 总步骤数)"""


@dataclass
class PipelineInput:
    """管线输入参数。"""
    input_files: list[Path] = field(default_factory=list)
    template_files: list[Path] = field(default_factory=list)
    output_dir: Path = field(default_factory=lambda: Path("."))
    sub_genre: str = ""             # 子体裁（如 "会议纪要", "函件", "通知"）
    sub_style: str = ""             # 子风格（如 "专题会", "调度会" ）
    workspace: Optional[Workspace] = None
    style_reference: str = ""       # 预加载的写作风格参考
    revision_guide: str = ""        # 预加载的修改模式指南
    prefer_paid: bool = True


@dataclass
class PipelineResult:
    """管线输出结果。"""
    success: bool = False
    output_files: list[Path] = field(default_factory=list)
    error: str = ""
    duration: float = 0.0
    quality_issues: list[str] = field(default_factory=list)


def _read_docx_text(path: Path) -> str:
    """读取 docx 纯文本（含文本框）。"""
    try:
        with zipfile.ZipFile(str(path)) as z:
            content = z.read("word/document.xml").decode("utf-8", errors="ignore")
            t_elems = re.findall(r"<w:t[^>]*>([^<]+)</w:t>", content)
            return "".join(t_elems)
    except Exception:
        return read_docx(path)


def run_pipeline(
    cfg: Config,
    pipe_input: PipelineInput,
    on_progress: Optional[ProgressCallback] = None,
) -> PipelineResult:
    """执行全自动写作管线。

    Args:
        cfg: 配置对象
        pipe_input: 管线输入参数
        on_progress: 进度回调
    Returns:
        PipelineResult
    """
    result = PipelineResult()
    start_time = time.time()
    llm = MultiLLMClient(cfg)

    total_steps = 6
    current_step = 0

    def _report(msg: str) -> None:
        nonlocal current_step
        current_step += 1
        if on_progress:
            on_progress(msg, current_step, total_steps)
        _log.info("步骤 %d/%d: %s", current_step, total_steps, msg)

    try:
        # ── Skill 匹配 ──
        skill_registry = SkillRegistry()
        matched_skill = skill_registry.match(
            genre=pipe_input.sub_genre,
            sub_style=pipe_input.sub_style,
        )
        skill_prompt = ""
        if matched_skill:
            skill_prompt = matched_skill.to_prompt_section()
            _log.info("匹配 Skill: %s", matched_skill.name)

        # 合并 style_reference
        effective_style = pipe_input.style_reference
        if skill_prompt:
            effective_style = skill_prompt + "\n\n" + effective_style if effective_style else skill_prompt

        # ── Step 1: 读取输入文件 ──
        _report("读取原始资料")
        combined_text = ""
        for fpath in pipe_input.input_files:
            if fpath.suffix == ".docx":
                combined_text += "\n\n" + _read_docx_text(fpath)
            elif fpath.suffix == ".txt":
                combined_text += "\n\n" + fpath.read_text(encoding="utf-8")
            elif fpath.suffix == ".pdf":
                try:
                    import fitz
                    doc = fitz.open(str(fpath))
                    combined_text += "\n\n" + "".join(p.get_text() for p in doc)
                except Exception:
                    pass
            else:
                combined_text += "\n\n" + _read_docx_text(fpath)

        combined_text = combined_text.strip()
        if not combined_text:
            result.error = "原始资料内容为空"
            result.duration = time.time() - start_time
            return result

        _log.info("合并输入: %d字, %d个文件", len(combined_text), len(pipe_input.input_files))

        # ── Step 2: AI理解 ──
        _report("AI 深度理解会议内容")
        transcript = parse_transcript(combined_text)
        analysis = None
        for attempt in range(1, 4):
            try:
                analysis = understand(
                    llm, transcript,
                    prefer_paid=pipe_input.prefer_paid,
                    filename_hint=pipe_input.input_files[0].name if pipe_input.input_files else "",
                )
                topics = len(analysis.topics)
                key_data = sum(len(t.key_data) for t in analysis.topics)
                actions = sum(len(t.action_items) for t in analysis.topics)
                _log.info("理解结果: %d议题, %d数据, %d行动项", topics, key_data, actions)
                if topics >= 2 and actions >= 2:
                    break
            except Exception as e:
                _log.warning("理解尝试 %d 失败: %s", attempt, str(e)[:80])

        if analysis is None:
            result.error = "AI 理解阶段失败"
            result.duration = time.time() - start_time
            return result

        # ── Step 3: 生成（按模板循环）──
        templates = pipe_input.template_files if pipe_input.template_files else [None]
        total_templates = len(templates)

        for idx, template_path in enumerate(templates):
            tpl_name = template_path.name if template_path else "默认格式"
            if total_templates > 1:
                _report(f"生成文档 ({idx + 1}/{total_templates}): {tpl_name}")
            else:
                _report("AI 正式撰写文档")

            # LLM生成
            content = generate(
                llm, analysis,
                prefer_paid=pipe_input.prefer_paid,
                style_reference=effective_style,
                revision_guide=pipe_input.revision_guide,
            )

            # 数据注入
            missing = _find_missing_data(analysis, content)
            if missing:
                injected = _inject_missing_data(list(content.content_paragraphs), missing)
                content = GeneratedContent(
                    title=content.title, doc_number=content.doc_number,
                    meeting_type=content.meeting_type, meeting_topic=content.meeting_topic,
                    meeting_date=content.meeting_date, meeting_location=content.meeting_location,
                    host=content.host, participants=content.participants,
                    content_paragraphs=tuple(injected),
                    compiler=content.compiler, model_used=content.model_used,
                )

            # 后处理
            pp_result = post_process(content)
            content = pp_result.content

            # 合规验证
            issues = post_verify(content)
            result.quality_issues.extend(issues)

            # 输出 Word
            pipe_input.output_dir.mkdir(parents=True, exist_ok=True)
            safe_title = content.title.replace("/", "_").replace("\\", "_")[:50]
            if template_path:
                # 套模板
                template_info = analyze_template(template_path)
                output_name = f"{safe_title}.docx"
                output_path = pipe_input.output_dir / output_name
                write_minutes(
                    content=content,
                    template_info=template_info,
                    template_path=template_path,
                    output_path=output_path,
                )
            else:
                # 无模板：纯文本输出
                output_name = f"{safe_title}.docx"
                output_path = pipe_input.output_dir / output_name
                _write_plain_docx(content, output_path)

            result.output_files.append(output_path)
            _log.info("输出: %s", output_path)

        # ── Step 4: 后处理与验证报告 ──
        _report("确定性后处理 + 合规验证")
        # (已在循环中完成，此处汇总)

        # ── Step 5: 企业微信推送 ──
        _report("推送成果到企业微信")
        try:
            _push_to_wecom(cfg, result.output_files)
        except Exception as e:
            _log.warning("企业微信推送失败: %s", str(e)[:80])

        # ── Step 6: 完成 ──
        _report("全部完成")
        result.success = True

    except Exception as e:
        result.error = str(e)
        _log.error("管线异常: %s", str(e))

    result.duration = time.time() - start_time
    return result


def _write_plain_docx(content: GeneratedContent, output_path: Path) -> None:
    """无模板时，生成简单格式 docx。"""
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # 标题
    h = doc.add_heading(content.title, level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # 正文
    for para in content.content_paragraphs:
        p = doc.add_paragraph(para)
    doc.save(str(output_path))


def _push_to_wecom(cfg: Config, files: list[Path]) -> None:
    """推送成果到企业微信。"""
    webhook = cfg.get("通知", "企微webhook", "")
    if not webhook or not webhook.startswith("http"):
        _log.info("未配置企业微信 webhook，跳过推送")
        return

    from ..notify.wecom import WeComNotifier
    notifier = WeComNotifier(webhook)
    file_names = ", ".join(f.name for f in files)
    notifier.send_text(
        f"✅ Com-Writer 写作完成\n"
        f"📄 生成文件: {file_names}\n"
        f"📂 输出目录: {files[0].parent if files else '未知'}"
    )
