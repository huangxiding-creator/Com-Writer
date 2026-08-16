"""GUI 自定义组件 —— 文件夹选择器 + 多选列表 + 可折叠面板。

设计原则：
- 每个组件独立、可复用
- 支持中文路径
- 与业务逻辑零耦合
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QFileDialog, QCheckBox,
    QComboBox, QLineEdit, QGroupBox, QFrame,
)


class FolderSelector(QWidget):
    """文件夹选择器：显示路径 + 浏览按钮。"""

    folder_changed = Signal(str)

    def __init__(self, label: str = "选择文件夹", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._path = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(label)
        self._label.setMinimumWidth(80)
        layout.addWidget(self._label)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("点击右侧按钮选择…")
        self._path_edit.setReadOnly(True)
        layout.addWidget(self._path_edit, stretch=1)

        self._btn = QPushButton("浏览…")
        self._btn.clicked.connect(self._on_browse)
        layout.addWidget(self._btn)

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", self._path or "")
        if path:
            self._path = path
            self._path_edit.setText(path)
            self.folder_changed.emit(path)

    @property
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path
        self._path_edit.setText(path)


class FileMultiSelect(QWidget):
    """文件多选列表：扫描文件夹 → 列表 → 多选。

    用法：
        selector = FileMultiSelect("选择文件")
        selector.scan_folder(Path("some/dir"))
        selected = selector.selected_files  # list[Path]
    """

    selection_changed = Signal()

    def __init__(
        self,
        title: str = "文件列表",
        extensions: tuple[str, ...] = (".docx", ".doc", ".txt", ".pdf"),
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._extensions = extensions
        self._folder = Path(".")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        toolbar = QHBoxLayout()
        self._title_label = QLabel(title)
        toolbar.addWidget(self._title_label)
        toolbar.addStretch()
        self._btn_all = QPushButton("全选")
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none = QPushButton("取消")
        self._btn_none.clicked.connect(self._select_none)
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self._refresh)
        toolbar.addWidget(self._btn_all)
        toolbar.addWidget(self._btn_none)
        toolbar.addWidget(self._btn_refresh)
        layout.addLayout(toolbar)

        # 文件列表
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._list)

        # 状态
        self._status = QLabel("未加载")
        self._status.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._status)

    def scan_folder(self, folder: Path | str) -> None:
        """扫描文件夹并列出所有匹配文件。"""
        folder = Path(folder)
        self._folder = folder
        self._refresh()

    def _refresh(self) -> None:
        self._list.clear()
        folder = self._folder
        if not folder.exists():
            self._status.setText("文件夹不存在")
            return

        files = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in self._extensions
        )
        for f in files:
            item = QListWidgetItem(f.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self._list.addItem(item)

        count = len(files)
        self._status.setText(f"共 {count} 个文件")
        self.selection_changed.emit()

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)
        self.selection_changed.emit()

    def _select_none(self) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.selection_changed.emit()

    @property
    def selected_files(self) -> list[Path]:
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return result

    @property
    def all_files(self) -> list[Path]:
        return [
            Path(self._list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self._list.count())
        ]


class FileSingleSelect(QWidget):
    """文件单选下拉框（用于子体裁/子风格选择）。"""

    selection_changed = Signal(str)

    def __init__(
        self,
        label: str = "选择",
        items: tuple[str, ...] = (),
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(label)
        self._label.setMinimumWidth(80)
        layout.addWidget(self._label)

        self._combo = QComboBox()
        self._combo.addItem("（不选择）", "")
        for item in items:
            self._combo.addItem(item, item)
        self._combo.currentIndexChanged.connect(
            lambda: self.selection_changed.emit(self._combo.currentData())
        )
        layout.addWidget(self._combo, stretch=1)

    @property
    def selected(self) -> str:
        return self._combo.currentData() or ""

    def set_items(self, items: tuple[str, ...]) -> None:
        self._combo.clear()
        self._combo.addItem("（不选择）", "")
        for item in items:
            self._combo.addItem(item, item)


class HorizontalLine(QFrame):
    """水平分割线。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
