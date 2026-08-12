"""实时录音 + 转写组件 —— 使用 sounddevice 采集音频，Whisper/API 转写。

功能：
1. 点击「开始录音」→ 调用笔记本麦克风持续采集
2. 实时显示转写文本
3. 点击「停止录音」→ 输出完整转写稿文件到指定目录

设计：
- 录音在独立线程中运行，避免阻塞 GUI
- 音频以固定时长（如30秒）分段 → 每段送转写 → 追加到文本区
- 转写引擎优先尝试本地 whisper（如安装），否则用云端 ASR API
"""
from __future__ import annotations

import wave
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from PySide6.QtCore import Signal, QObject, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QFileDialog,
)

from ..utils.logger import get_logger

_log = get_logger("gui.recorder")

# 尝试导入 sounddevice
try:
    import sounddevice as sd
    import numpy as np
    _HAS_SD = True
except Exception:
    _HAS_SD = False
    _log.info("sounddevice 未安装，录音功能将不可用")


# ════════════════════════════════════════════════════════
#  录音工作线程
# ════════════════════════════════════════════════════════

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 30  # 每段 30 秒


class RecordingWorker(QThread):
    """录音工作线程：持续采集 → 分段 → 转写 → 信号通知。"""

    chunk_transcribed = Signal(str)   # 每段转写结果
    status_update = Signal(str)        # 状态文本
    error_occurred = Signal(str)       # 错误消息
    finished_recording = Signal(str)   # 最终文件路径

    def __init__(
        self,
        output_dir: Path,
        transcribe_fn: Optional[Callable[[Path], str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._output_dir = output_dir
        self._transcribe_fn = transcribe_fn or self._default_transcribe
        self._stop_flag = threading.Event()
        self._all_chunks: list[Path] = []

    def run(self) -> None:
        if not _HAS_SD:
            self.error_occurred.emit(
                "sounddevice 未安装。请在终端执行: pip install sounddevice numpy"
            )
            return

        self.status_update.emit("🔴 录音中…")
        self._all_chunks = []

        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=SAMPLE_RATE,  # 1 秒
            )
            stream.start()

            chunk_buffer: list = []
            seconds_collected = 0

            while not self._stop_flag.is_set():
                block, _ = stream.read(SAMPLE_RATE)  # 1秒
                chunk_buffer.append(block)
                seconds_collected += 1

                if seconds_collected >= CHUNK_DURATION:
                    self._process_chunk(chunk_buffer)
                    chunk_buffer = []
                    seconds_collected = 0

            # 处理剩余数据
            if chunk_buffer:
                self._process_chunk(chunk_buffer)

            stream.stop()
            stream.close()

        except Exception as e:
            self.error_occurred.emit(f"录音失败: {e}")
            return

        # 合并所有转写文本 → 写入文件
        self.status_update.emit("📝 保存转写稿…")
        final_path = self._merge_chunks()
        self.finished_recording.emit(str(final_path))

    def _process_chunk(self, buffers: list) -> None:
        """保存一段音频 → 转写 → 信号通知。"""
        audio_data = np.concatenate(buffers)
        chunk_path = self._output_dir / f"_chunk_{len(self._all_chunks):04d}.wav"
        self._write_wav(chunk_path, audio_data)
        self._all_chunks.append(chunk_path)

        self.status_update.emit(f"🔄 转写第 {len(self._all_chunks)} 段…")
        try:
            text = self._transcribe_fn(chunk_path)
            if text:
                self.chunk_transcribed.emit(text + "\n\n")
        except Exception as e:
            _log.warning("转写失败: %s", e)

    def _write_wav(self, path: Path, data: np.ndarray) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(data.tobytes())

    def _merge_chunks(self) -> Path:
        """合并所有分段转写文本 → 输出文件。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self._output_dir / f"录音转写_{timestamp}.txt"

        texts = []
        for chunk_path in self._all_chunks:
            try:
                text = self._transcribe_fn(chunk_path)
                if text:
                    texts.append(text)
            except Exception:
                pass
            # 清理临时文件
            try:
                chunk_path.unlink(missing_ok=True)
            except Exception:
                pass

        output_path.write_text("\n\n".join(texts), encoding="utf-8")
        return output_path

    def stop(self) -> None:
        self._stop_flag.set()

    @staticmethod
    def _default_transcribe(audio_path: Path) -> str:
        """默认转写：尝试 whisper → 空。"""
        try:
            import whisper  # type: ignore
            model = whisper.load_model("base")
            result = model.transcribe(str(audio_path))
            return result.get("text", "").strip()
        except ImportError:
            return f"[需安装 whisper 或配置 ASR API 才能转写: {audio_path.name}]"
        except Exception as e:
            return f"[转写失败: {e}]"


# ════════════════════════════════════════════════════════
#  录音面板组件
# ════════════════════════════════════════════════════════

class RecorderPanel(QWidget):
    """录音 + 实时转写面板。"""

    recording_started = Signal()
    recording_stopped = Signal(str)  # 输出文件路径

    def __init__(self, output_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._output_dir = output_dir
        self._worker: Optional[RecordingWorker] = None

        layout = QVBoxLayout(self)

        # 控制栏
        ctrl = QHBoxLayout()
        self._btn_record = QPushButton("🎙️ 开始录音")
        self._btn_record.setMinimumHeight(44)
        self._btn_record.setStyleSheet(
            "QPushButton { font-size: 16px; font-weight: bold; }"
            "QPushButton:checked { background-color: #ff4444; color: white; }"
        )
        self._btn_record.setCheckable(True)
        self._btn_record.toggled.connect(self._on_toggle)
        ctrl.addWidget(self._btn_record)

        self._btn_save_as = QPushButton("另存为…")
        self._btn_save_as.clicked.connect(self._save_as)
        self._btn_save_as.setEnabled(False)
        ctrl.addWidget(self._btn_save_as)

        layout.addLayout(ctrl)

        # 状态
        self._status = QLabel("就绪。点击「开始录音」启动麦克风采集。")
        self._status.setStyleSheet("color: #666;")
        layout.addWidget(self._status)

        # 转写文本区
        self._text_area = QTextEdit()
        self._text_area.setPlaceholderText("实时转写内容将显示在这里…")
        self._text_area.setReadOnly(True)
        layout.addWidget(self._text_area, stretch=1)

        if not _HAS_SD:
            self._btn_record.setEnabled(False)
            self._status.setText(
                "⚠️ 录音需要 sounddevice 库。请在终端执行:\n"
                "  pip install sounddevice numpy"
            )

    def _on_toggle(self, checked: bool) -> None:
        if checked:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        self._text_area.clear()
        self._btn_record.setText("⏹️ 停止录音")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._worker = RecordingWorker(self._output_dir, parent=self)
        self._worker.chunk_transcribed.connect(self._on_chunk)
        self._worker.status_update.connect(self._on_status)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.finished_recording.connect(self._on_finished)
        self._worker.start()
        self.recording_started.emit()

    def _stop_recording(self) -> None:
        if self._worker:
            self._btn_record.setText("🎙️ 开始录音")
            self._worker.stop()

    def _on_chunk(self, text: str) -> None:
        cursor = self._text_area.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self._text_area.setTextCursor(cursor)

    def _on_status(self, msg: str) -> None:
        self._status.setText(msg)

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"❌ {msg}")
        self._btn_record.setChecked(False)
        self._btn_record.setText("🎙️ 开始录音")

    def _on_finished(self, file_path: str) -> None:
        self._status.setText(f"✅ 转写完成: {Path(file_path).name}")
        self._btn_save_as.setEnabled(True)
        self._final_path = file_path
        self.recording_stopped.emit(file_path)

    def _save_as(self) -> None:
        if not hasattr(self, "_final_path"):
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "保存转写稿", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if dest:
            src = Path(self._final_path)
            if src.exists():
                import shutil
                shutil.copy2(str(src), dest)
                self._status.setText(f"已保存到: {dest}")

    @property
    def output_file(self) -> Optional[str]:
        return getattr(self, "_final_path", None)
