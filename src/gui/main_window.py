"""Com-Writer 主窗口 —— 四区域选择 + 一键写作 + 实时进度。

布局结构（从上到下）：
1. 工作目录选择（下拉框 + 管理）
2. 左右分栏:
   - 左栏: 学习源 URL / 模板多选
   - 右栏: 原始资料多选 / 录音面板
3. 底栏: 子体裁单选 + 输出目录 + 开始按钮
4. 进度栏 + 日志
5. 设置按钮（右上角）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QProgressBar, QTextEdit, QComboBox,
    QTabWidget, QFileDialog, QMessageBox, QGroupBox, QLineEdit,
    QSplitter, QFrame,
)

from ..config.paths import (
    PROJECT_ROOT, BASE_DIR, TEMPLATE_DIR, RAW_RECORD_DIR, OUTPUT_DIR,
    REFINE_DIR, CRAWL_DIR,
)
from ..workspace.manager import WorkspaceManager, Workspace
from .widgets import FolderSelector, FileMultiSelect, FileSingleSelect, HorizontalLine
from .settings import SettingsDialog
from .recorder import RecorderPanel
from ..pipeline.auto_pipeline import PipelineInput, PipelineResult, run_pipeline
from ..utils.logger import get_logger

_log = get_logger("gui.main")

# 子体裁选项
SUB_GENRES = (
    "会议纪要", "正式函件", "通知通报", "工作汇报",
    "新闻资讯", "经验总结", "专题报告", "技术方案",
)

# 子风格选项（根据子体裁动态调整）
SUB_STYLES: dict[str, tuple[str, ...]] = {
    "会议纪要": ("调度会", "专题会", "周例会", "月度会", "年度会", "动员会"),
    "正式函件": ("请示函", "回复函", "通知函", "商洽函"),
    "通知通报": ("工作通知", "安全通报", "表彰通报", "情况通报"),
    "新闻资讯": ("项目动态", "技术创新", "党群建设", "企业形象"),
    "工作汇报": ("周报", "月报", "季报", "年报", "专项汇报"),
}


# ════════════════════════════════════════════════════════
#  管线执行线程
# ════════════════════════════════════════════════════════

class PipelineWorker(QThread):
    """在后台执行写作管线。"""

    progress = Signal(str, int, int)  # (描述, 当前, 总数)
    finished_ok = Signal(object)      # PipelineResult
    failed = Signal(str)

    def __init__(self, config_obj, pipe_input: PipelineInput, parent=None) -> None:
        super().__init__(parent)
        self._config = config_obj
        self._pipe_input = pipe_input

    def run(self) -> None:
        try:
            result = run_pipeline(
                self._config,
                self._pipe_input,
                on_progress=lambda msg, cur, total: self.progress.emit(msg, cur, total),
            )
            if result.success:
                self.finished_ok.emit(result)
            else:
                self.failed.emit(result.error or "未知错误")
        except Exception as e:
            self.failed.emit(str(e))


# ════════════════════════════════════════════════════════
#  主窗口
# ════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """Com-Writer 主界面。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("企业写手 Com-Writer v2.0")
        self.setMinimumSize(960, 720)

        # 状态
        self._ws_manager = WorkspaceManager()
        self._settings_data: dict = {}
        self._worker: Optional[PipelineWorker] = None
        self._config_obj = None

        # 加载配置
        self._load_config()

        # 构建 UI
        self._build_ui()
        self._load_initial_workspace()

    # ── 配置加载 ──

    def _load_config(self) -> None:
        try:
            from ..config.loader import get_config
            self._config_obj = get_config()
            # 加载风格参考和修改指南
            style_path = REFINE_DIR / "style_reference.txt"
            guide_path = REFINE_DIR / "revision_guide_definitive.txt"
            self._settings_data = {
                "primary_model": self._config_obj.get("模型", "主力模型", "glm-4-flash"),
                "paid_model": self._config_obj.get("模型", "付费模型", "glm-5.2"),
                "prefer_paid": self._config_obj.get_bool("模型", "优先付费", True),
                "temperature": self._config_obj.get_float("模型", "温度", 0.7),
                "max_retries": self._config_obj.get_int("模型", "重试次数", 3),
                "style_reference": style_path.read_text(encoding="utf-8") if style_path.exists() else "",
                "revision_guide": guide_path.read_text(encoding="utf-8") if guide_path.exists() else "",
                "auto_push_wecom": True,
                "auto_verify": True,
                "llm_timeout": 120,
            }
        except Exception as e:
            _log.warning("加载配置失败: %s", e)
            self._settings_data = {}

    # ── UI 构建 ──

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)

        # ── 顶部栏 ──
        top = QHBoxLayout()
        title = QLabel("✍️ 企业写手 Com-Writer")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2c3e50;")
        top.addWidget(title)
        top.addStretch()

        # 工作目录下拉
        top.addWidget(QLabel("工作目录:"))
        self._ws_combo = QComboBox()
        self._ws_combo.setMinimumWidth(200)
        self._ws_combo.currentTextChanged.connect(self._on_workspace_changed)
        top.addWidget(self._ws_combo)

        self._btn_add_ws = QPushButton("➕ 新建")
        self._btn_add_ws.clicked.connect(self._add_workspace)
        top.addWidget(self._btn_add_ws)

        self._btn_settings = QPushButton("⚙️ 设置")
        self._btn_settings.clicked.connect(self._open_settings)
        top.addWidget(self._btn_settings)
        main_layout.addLayout(top)

        main_layout.addWidget(HorizontalLine())

        # ── 核心区域（Tab: 文件选择 vs 录音）──
        tabs = QTabWidget()

        # Tab 1: 文件选择模式
        file_tab = self._build_file_tab()
        tabs.addTab(file_tab, "📁 选择文件")

        # Tab 2: 录音模式
        record_tab = self._build_record_tab()
        tabs.addTab(record_tab, "🎙️ 现场录音")

        main_layout.addWidget(tabs, stretch=1)

        # ── 底部控制区 ──
        bottom = QGridLayout()

        # 子体裁
        bottom.addWidget(QLabel("体裁:"), 0, 0)
        self._genre_combo = FileSingleSelect("体裁", SUB_GENRES)
        self._genre_combo.selection_changed.connect(self._on_genre_changed)
        bottom.addWidget(self._genre_combo, 0, 1)

        # 子风格
        bottom.addWidget(QLabel("子风格:"), 0, 2)
        self._style_combo = FileSingleSelect("子风格", ())
        bottom.addWidget(self._style_combo, 0, 3)

        # 输出目录
        bottom.addWidget(QLabel("输出到:"), 1, 0)
        self._output_selector = FolderSelector("输出目录")
        self._output_selector.set_path(str(OUTPUT_DIR))
        bottom.addWidget(self._output_selector, 1, 1, 1, 3)

        main_layout.addLayout(bottom)

        # ── 开始按钮 ──
        self._btn_start = QPushButton("🚀 开始写作")
        self._btn_start.setMinimumHeight(52)
        self._btn_start.setStyleSheet("""
            QPushButton {
                font-size: 18px;
                font-weight: bold;
                background-color: #3498db;
                color: white;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #2980b9; }
            QPushButton:disabled { background-color: #bdc3c7; }
        """)
        self._btn_start.clicked.connect(self._on_start)
        main_layout.addWidget(self._btn_start)

        # ── 进度栏 ──
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        main_layout.addWidget(self._progress)

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("color: #666;")
        main_layout.addWidget(self._progress_label)

        # ── 日志区 ──
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(140)
        self._log_area.setStyleSheet(
            "QTextEdit { font-family: 'Consolas', 'Monaco', monospace; font-size: 11px; }"
        )
        log_layout.addWidget(self._log_area)
        main_layout.addWidget(log_group)

    def _build_file_tab(self) -> QWidget:
        """构建文件选择 Tab：左栏模板 + 右栏原始资料。"""
        tab = QWidget()
        layout = QHBoxLayout(tab)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：学习源 + 模板
        left = QWidget()
        left_layout = QVBoxLayout(left)

        # 学习源 URL
        url_group = QGroupBox("学习源（可选）")
        url_layout = QVBoxLayout(url_group)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("输入内网/外网 URL，系统将自动学习写作风格…")
        url_layout.addWidget(self._url_edit)
        left_layout.addWidget(url_group)

        # 模板多选
        tpl_group = QGroupBox("模板（多选，可选）")
        tpl_layout = QVBoxLayout(tpl_group)
        self._template_selector = FileMultiSelect("模板文件", extensions=(".docx",))
        tpl_layout.addWidget(self._template_selector)
        left_layout.addWidget(tpl_group)

        splitter.addWidget(left)

        # 右栏：原始资料多选
        right = QWidget()
        right_layout = QVBoxLayout(right)
        input_group = QGroupBox("原始资料（多选）")
        input_layout = QVBoxLayout(input_group)
        self._input_selector = FileMultiSelect(
            "原始文件", extensions=(".docx", ".doc", ".txt", ".pdf")
        )
        input_layout.addWidget(self._input_selector)
        right_layout.addWidget(input_group, stretch=1)

        splitter.addWidget(right)
        splitter.setSizes([400, 500])

        layout.addWidget(splitter)
        return tab

    def _build_record_tab(self) -> QWidget:
        """构建录音 Tab。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self._recorder = RecorderPanel(RAW_RECORD_DIR)
        self._recorder.recording_stopped.connect(self._on_recording_done)
        layout.addWidget(self._recorder)
        return tab

    # ── 工作目录管理 ──

    def _load_initial_workspace(self) -> None:
        self._ws_combo.clear()
        for ws in self._ws_manager.all_workspaces:
            self._ws_combo.addItem(ws.name)
        active = self._ws_manager.active
        self._ws_combo.setCurrentText(active.name)
        self._refresh_selectors(active)

    def _on_workspace_changed(self, name: str) -> None:
        try:
            ws = self._ws_manager.switch(name)
            self._refresh_selectors(ws)
        except KeyError:
            pass

    def _refresh_selectors(self, ws: Workspace) -> None:
        self._template_selector.scan_folder(ws.template_dir)
        self._input_selector.scan_folder(ws.record_dir)
        self._output_selector.set_path(str(ws.output_dir))

    def _add_workspace(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择或创建工作目录")
        if not path:
            return
        p = Path(path)
        name = p.name
        try:
            ws = self._ws_manager.add(name, str(p))
            self._ws_combo.clear()
            for w in self._ws_manager.all_workspaces:
                self._ws_combo.addItem(w.name)
            self._ws_combo.setCurrentText(name)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"创建工作目录失败:\n{e}")

    # ── 子体裁联动 ──

    def _on_genre_changed(self, genre: str) -> None:
        styles = SUB_STYLES.get(genre, ())
        self._style_combo.set_items(styles)

    # ── 设置对话框 ──

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings_data, self)
        if dlg.exec():
            self._settings_data = dlg.get_settings()
            self._log("设置已更新")

    # ── 录音完成 ──

    def _on_recording_done(self, file_path: str) -> None:
        self._log(f"录音转写完成: {Path(file_path).name}")
        # 切换到文件选择 Tab，并刷新输入列表
        self._input_selector.scan_folder(self._ws_manager.active.record_dir)

    # ── 开始写作 ──

    def _on_start(self) -> None:
        # 收集输入
        input_files = self._input_selector.selected_files
        template_files = self._template_selector.selected_files
        output_dir = Path(self._output_selector.path) if self._output_selector.path else OUTPUT_DIR

        if not input_files:
            # 尝试用录音输出
            rec_file = self._recorder.output_file
            if rec_file:
                input_files = [Path(rec_file)]
            else:
                QMessageBox.warning(self, "提示", "请先选择原始资料文件或进行录音。")
                return

        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        # 构建 PipelineInput
        pipe_input = PipelineInput(
            input_files=input_files,
            template_files=template_files,
            output_dir=output_dir,
            sub_genre=self._genre_combo.selected,
            sub_style=self._style_combo.selected,
            workspace=self._ws_manager.active,
            style_reference=self._settings_data.get("style_reference", ""),
            revision_guide=self._settings_data.get("revision_guide", ""),
            prefer_paid=self._settings_data.get("prefer_paid", True),
        )

        # 禁用按钮
        self._btn_start.setEnabled(False)
        self._btn_start.setText("⏳ 写作中…")
        self._progress.setVisible(True)
        self._progress.setValue(0)

        # 启动线程
        self._worker = PipelineWorker(self._config_obj, pipe_input, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, msg: str, cur: int, total: int) -> None:
        pct = int(cur / total * 100) if total > 0 else 0
        self._progress.setValue(pct)
        self._progress_label.setText(f"步骤 {cur}/{total}: {msg}")
        self._log(f"[{cur}/{total}] {msg}")

    def _on_finished(self, result: PipelineResult) -> None:
        self._btn_start.setEnabled(True)
        self._btn_start.setText("🚀 开始写作")
        self._progress.setValue(100)
        self._progress_label.setText(f"✅ 完成！耗时 {result.duration:.1f}s")

        files = "\n".join(f"  📄 {Path(f).name}" for f in result.output_files)
        msg = f"写作完成！\n耗时: {result.duration:.1f}秒\n\n输出文件:\n{files}"
        if result.quality_issues:
            msg += f"\n\n⚠️ 质量提醒 ({len(result.quality_issues)} 项):\n"
            msg += "\n".join(f"  • {issue}" for issue in result.quality_issues[:5])
        QMessageBox.information(self, "完成", msg)
        self._log(f"✅ 管线完成，输出 {len(result.output_files)} 个文件")

    def _on_failed(self, error: str) -> None:
        self._btn_start.setEnabled(True)
        self._btn_start.setText("🚀 开始写作")
        self._progress.setVisible(False)
        self._progress_label.setText(f"❌ 失败: {error}")
        QMessageBox.critical(self, "失败", f"写作失败:\n{error}")
        self._log(f"❌ 管线失败: {error}")

    # ── 日志 ──

    def _log(self, msg: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_area.append(f"[{ts}] {msg}")
