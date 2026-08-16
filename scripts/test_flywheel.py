#!/usr/bin/env python3
"""质量飞轮端到端验证 —— 用真实转写稿跑完整管线（含六层飞轮）。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.loader import get_config
from src.pipeline.auto_pipeline import PipelineInput, run_pipeline
from src.config.paths import RAW_RECORD_DIR, TEMPLATE_DIR
from src.workspace.manager import WorkspaceManager


def main() -> None:
    ws = WorkspaceManager().active

    # 输入：真实会议转写稿
    transcripts = sorted(
        f for f in ws.record_dir.glob("*.docx")
        if "锚索" in f.name or "锚杆" in f.name
    )
    if not transcripts:
        transcripts = sorted(f for f in ws.record_dir.glob("*.docx"))[:1]
    if not transcripts:
        print("无输入转写稿")
        return

    # 模板：会议纪要模板
    templates = sorted(ws.template_dir.glob("*会议纪要*.docx"))

    output_dir = ws.output_dir / "质量飞轮验证"
    output_dir.mkdir(parents=True, exist_ok=True)

    pipe_input = PipelineInput(
        input_files=[transcripts[0]],
        template_files=templates[:1],
        output_dir=output_dir,
        sub_genre="会议纪要",
        sub_style="专题会",
        workspace=ws,
        prefer_paid=True,
        enable_flywheel=True,
    )

    cfg = get_config()
    print(f"输入: {transcripts[0].name}")
    print(f"模板: {templates[0].name if templates else '无'}")
    print(f"输出: {output_dir}")
    print("=" * 60)

    def on_progress(msg: str, cur: int, total: int) -> None:
        print(f"  [{cur}/{total}] {msg}")

    t0 = time.time()
    result = run_pipeline(cfg, pipe_input, on_progress=on_progress)

    print("=" * 60)
    print(f"success:        {result.success}")
    print(f"error:          {result.error or '(无)'}")
    print(f"质量评分:       {result.quality_score}/100")
    print(f"指纹审计:       {'通过' if result.fingerprint_passed else '未通过'}")
    print(f"迭代轮数:       {result.refine_rounds}")
    print(f"注入范例数:     {result.exemplar_count}")
    print(f"输出文件:       {[str(f) for f in result.output_files]}")
    print(f"总耗时:         {time.time() - t0:.1f}s")
    if result.quality_issues:
        print(f"质量提醒({len(result.quality_issues)}): ")
        for q in result.quality_issues[:5]:
            print(f"  - {q}")


if __name__ == "__main__":
    main()
