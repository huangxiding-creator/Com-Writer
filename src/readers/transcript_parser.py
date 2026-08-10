"""转写稿解析器 —— 解析 ASR 输出的会议录音转写稿。

解析格式（常见 ASR 输出）：
    发言人名 HH:MM        （或 HH:MM:SS）
    发言内容...

    发言人名 HH:MM
    发言内容...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TranscriptEntry:
    """单条转写记录。"""
    speaker: str
    timestamp: str
    content: str


@dataclass(frozen=True)
class TranscriptData:
    """解析后的转写稿数据。"""
    title: str
    entries: tuple[TranscriptEntry, ...]
    raw_text: str
    speakers: tuple[str, ...] = field(default_factory=tuple)
    total_duration_hint: str = ""

    @property
    def char_count(self) -> int:
        return len(self.raw_text)

    @property
    def entry_count(self) -> int:
        return len(self.entries)


# 发言人行: "发言人姓名 00:00" 或 "说话人3 01:23:47"
_SPEAKER_PATTERN = re.compile(
    r"^(.+?)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$"
)


def parse_transcript(text: str) -> TranscriptData:
    """解析转写稿文本。

    Args:
        text: 转写稿全文
    Returns:
        TranscriptData 结构化数据
    """
    lines = text.strip().splitlines()

    # 第一行通常是标题
    title = lines[0].strip() if lines else "未命名会议"

    entries: list[TranscriptEntry] = []
    current_speaker = ""
    current_timestamp = ""
    current_lines: list[str] = []
    speakers: list[str] = []

    def _flush_entry():
        nonlocal current_speaker, current_timestamp, current_lines
        if current_speaker and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                entries.append(TranscriptEntry(
                    speaker=current_speaker,
                    timestamp=current_timestamp,
                    content=content,
                ))
        current_speaker = ""
        current_timestamp = ""
        current_lines = []

    for line in lines[1:]:  # 跳过标题行
        line = line.strip()
        if not line:
            continue

        match = _SPEAKER_PATTERN.match(line)
        if match:
            _flush_entry()
            current_speaker = match.group(1).strip()
            current_timestamp = match.group(2).strip()
            if current_speaker not in speakers:
                speakers.append(current_speaker)
        else:
            if current_speaker:
                current_lines.append(line)
            elif not entries:
                # 标题后的额外文本，附加到标题
                title = f"{title}\n{line}".strip()

    _flush_entry()

    # 推断总时长
    duration_hint = ""
    if entries:
        duration_hint = entries[-1].timestamp

    return TranscriptData(
        title=title,
        entries=tuple(entries),
        raw_text=text,
        speakers=tuple(speakers),
        total_duration_hint=duration_hint,
    )


def format_transcript_for_llm(data: TranscriptData) -> str:
    """将转写稿格式化为 LLM 易读的文本。

    格式：[发言人 时间]\n内容\n
    """
    parts = [f"会议标题：{data.title}"]
    if data.speakers:
        parts.append(f"参会发言人员：{', '.join(data.speakers)}")
    parts.append(f"时长参考（最后时间戳）：{data.total_duration_hint}")
    parts.append("")
    parts.append("---")
    parts.append("")

    for entry in data.entries:
        parts.append(f"[{entry.speaker} {entry.timestamp}]")
        parts.append(entry.content)
        parts.append("")

    return "\n".join(parts)
