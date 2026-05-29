"""自动暂停设置弹窗：到点自动暂停、过点自动恢复，支持多个每天生效的时间段。

接入主窗口的 settings_changed 通道，保存后由 main.on_settings_changed 统一
调用 collector.configure_schedules 重新加载日程。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..settings import AutoPauseSettings, PauseRange, Settings


class AutoPauseDialog(QDialog):
    settings_changed = Signal(Settings)

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("自动暂停设置")
        self.resize(420, 320)
        self._settings = settings
        ap = settings.auto_pause

        self._enabled = QCheckBox("启用自动暂停（到点自动暂停，过点自动恢复）")
        self._enabled.setChecked(ap.enabled)

        # 每行一个时间段，self._rows 保存每行的 (容器widget, start_edit, end_edit, enabled_check)
        self._rows: list[tuple[QWidget, QTimeEdit, QTimeEdit, QCheckBox]] = []
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(4)

        rows_host = QWidget()
        rows_host.setLayout(self._rows_box)

        btn_add = QPushButton("+ 添加时间段")
        btn_add.clicked.connect(lambda: self._add_row())

        hint = QLabel(
            "<span style='color:#888; font-size:11px;'>"
            "时段内不采样、不记录活动，避免误统计（如午餐 12:00–13:00）。"
            "跨午夜时段把结束时间填得比开始时间小，例如 23:50–00:30。"
            "</span>"
        )
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self._enabled)
        root.addWidget(rows_host)
        root.addWidget(btn_add)
        root.addStretch(1)
        root.addWidget(hint)
        root.addWidget(buttons)

        # 载入已有时间段
        for r in ap.ranges:
            self._add_row(r.start, r.end, r.enabled)

    def _add_row(self, start: str = "12:00", end: str = "13:00", enabled: bool = True) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        start_edit = QTimeEdit(QTime.fromString(start, "HH:mm"))
        start_edit.setDisplayFormat("HH:mm")
        end_edit = QTimeEdit(QTime.fromString(end, "HH:mm"))
        end_edit.setDisplayFormat("HH:mm")
        chk = QCheckBox("启用")
        chk.setChecked(enabled)

        btn_del = QPushButton("✕")
        btn_del.setFixedWidth(28)
        btn_del.setToolTip("删除此时间段")
        btn_del.clicked.connect(lambda: self._remove_row(row))

        layout.addWidget(start_edit)
        layout.addWidget(QLabel("至"))
        layout.addWidget(end_edit)
        layout.addSpacing(8)
        layout.addWidget(chk)
        layout.addStretch(1)
        layout.addWidget(btn_del)

        self._rows.append((row, start_edit, end_edit, chk))
        self._rows_box.addWidget(row)

    def _remove_row(self, row: QWidget) -> None:
        self._rows = [r for r in self._rows if r[0] is not row]
        self._rows_box.removeWidget(row)
        row.deleteLater()

    def _on_ok(self) -> None:
        ranges = [
            PauseRange(
                start=start_edit.time().toString("HH:mm"),
                end=end_edit.time().toString("HH:mm"),
                enabled=chk.isChecked(),
            )
            for (_row, start_edit, end_edit, chk) in self._rows
        ]
        self._settings.auto_pause = AutoPauseSettings(
            enabled=self._enabled.isChecked(),
            ranges=ranges,
        )
        self._settings.save()
        self.settings_changed.emit(self._settings)
        self.accept()
