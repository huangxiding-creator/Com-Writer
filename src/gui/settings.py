"""高级设置面板 —— 模型选择、参数调优、提示词编辑。

点击主窗口「设置」按钮弹出的对话框。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox,
    QDoubleSpinBox, QTextEdit, QCheckBox, QPushButton,
    QDialogButtonBox, QTabWidget, QWidget, QGroupBox, QLabel,
)


class SettingsDialog(QDialog):
    """高级设置对话框。"""

    def __init__(self, config_data: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("高级设置")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._config = dict(config_data)  # 拷贝，不可变原则

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_model_tab(), "模型")
        tabs.addTab(self._build_prompt_tab(), "提示词")
        tabs.addTab(self._build_advanced_tab(), "高级")
        layout.addWidget(tabs)

        # 按钮
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ── 模型 Tab ──

    def _build_model_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self._primary_model = QComboBox()
        self._primary_model.addItems(["glm-4-flash", "glm-4-plus", "glm-5.2", "deepseek-chat"])
        self._primary_model.setCurrentText(self._config.get("primary_model", "glm-4-flash"))
        form.addRow("主力模型:", self._primary_model)

        self._paid_model = QComboBox()
        self._paid_model.addItems(["glm-5.2", "glm-4-plus", "deepseek-chat"])
        self._paid_model.setCurrentText(self._config.get("paid_model", "glm-5.2"))
        form.addRow("付费模型:", self._paid_model)

        self._prefer_paid = QCheckBox("优先使用付费模型（高质量模式）")
        self._prefer_paid.setChecked(self._config.get("prefer_paid", True))
        form.addRow("", self._prefer_paid)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setValue(self._config.get("temperature", 0.7))
        form.addRow("温度（创造性）:", self._temperature)

        self._max_retries = QSpinBox()
        self._max_retries.setRange(1, 10)
        self._max_retries.setValue(self._config.get("max_retries", 3))
        form.addRow("最大重试次数:", self._max_retries)

        return tab

    # ── 提示词 Tab ──

    def _build_prompt_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 风格参考
        style_group = QGroupBox("写作风格参考（从学习材料中提炼）")
        style_layout = QVBoxLayout(style_group)
        self._style_ref = QTextEdit()
        self._style_ref.setPlainText(self._config.get("style_reference", ""))
        self._style_ref.setMaximumHeight(160)
        style_layout.addWidget(self._style_ref)
        layout.addWidget(style_group)

        # 修改指南
        guide_group = QGroupBox("修改模式指南（从初稿-定稿对中学习）")
        guide_layout = QVBoxLayout(guide_group)
        self._revision_guide = QTextEdit()
        self._revision_guide.setPlainText(self._config.get("revision_guide", ""))
        guide_layout.addWidget(self._revision_guide)
        layout.addWidget(guide_group)

        return tab

    # ── 高级 Tab ──

    def _build_advanced_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self._auto_push = QCheckBox("生成完成后自动推送到企业微信")
        self._auto_push.setChecked(self._config.get("auto_push_wecom", True))
        form.addRow("", self._auto_push)

        self._auto_verify = QCheckBox("生成完成后自动合规验证")
        self._auto_verify.setChecked(self._config.get("auto_verify", True))
        form.addRow("", self._auto_verify)

        self._timeout = QSpinBox()
        self._timeout.setRange(30, 600)
        self._timeout.setSuffix(" 秒")
        self._timeout.setValue(self._config.get("llm_timeout", 120))
        form.addRow("单次 LLM 超时:", self._timeout)

        info = QLabel(
            "💡 提示：修改设置后点击「确定」保存。\n"
            "风格参考和修改指南可在此直接编辑，将覆盖自动学习的内容。"
        )
        info.setStyleSheet("color: #666; font-size: 11px;")
        form.addRow("", info)

        return tab

    # ── 获取结果 ──

    def get_settings(self) -> dict:
        """返回用户修改后的配置字典。"""
        return {
            "primary_model": self._primary_model.currentText(),
            "paid_model": self._paid_model.currentText(),
            "prefer_paid": self._prefer_paid.isChecked(),
            "temperature": self._temperature.value(),
            "max_retries": self._max_retries.value(),
            "style_reference": self._style_ref.toPlainText(),
            "revision_guide": self._revision_guide.toPlainText(),
            "auto_push_wecom": self._auto_push.isChecked(),
            "auto_verify": self._auto_verify.isChecked(),
            "llm_timeout": self._timeout.value(),
        }
