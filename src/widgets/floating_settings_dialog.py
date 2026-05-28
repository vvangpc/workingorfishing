"""悬浮窗设置弹窗：宽 / 高 / 透明度 / 时长字体颜色。

接入主窗口的 settings_changed 通道，保存后由 main.on_settings_changed 统一刷新悬浮窗。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..settings import Settings

_COLOR_CHOICES = [
    ("白色", "white"),
    ("黑色", "black"),
    ("自适应（根据下方背景）", "auto"),
]

_THEME_CHOICES = [
    ("文字（彩色圆角条）", "text"),
    ("图片（assets/floating/）", "image"),
]


class FloatingSettingsDialog(QDialog):
    settings_changed = Signal(Settings)

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("悬浮窗设置")
        self.resize(360, 260)
        self._settings = settings
        fw = settings.floating_window

        form = QFormLayout()

        self._enabled = QCheckBox("显示桌面悬浮窗")
        self._enabled.setChecked(fw.enabled)
        form.addRow("", self._enabled)

        self._theme = QComboBox()
        for label, val in _THEME_CHOICES:
            self._theme.addItem(label, val)
        idx = self._theme.findData(fw.theme)
        if idx >= 0:
            self._theme.setCurrentIndex(idx)
        self._theme.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("主题", self._theme)

        self._image_size = QSpinBox()
        self._image_size.setRange(48, 400)
        self._image_size.setSuffix(" px")
        self._image_size.setValue(fw.image_size)
        form.addRow("图片主题尺寸", self._image_size)

        self._width = QSpinBox()
        self._width.setRange(40, 400)
        self._width.setSuffix(" px")
        self._width.setValue(fw.width)
        form.addRow("宽度", self._width)

        self._height = QSpinBox()
        self._height.setRange(20, 120)
        self._height.setSuffix(" px")
        self._height.setValue(fw.height)
        form.addRow("高度", self._height)

        opacity_row = QHBoxLayout()
        self._opacity = QSlider(Qt.Horizontal)
        self._opacity.setRange(30, 100)
        self._opacity.setValue(int(fw.opacity * 100))
        self._opacity_label = QLabel(f"{self._opacity.value()}%")
        self._opacity_label.setFixedWidth(40)
        self._opacity.valueChanged.connect(
            lambda v: self._opacity_label.setText(f"{v}%")
        )
        opacity_row.addWidget(self._opacity, 1)
        opacity_row.addWidget(self._opacity_label)
        form.addRow("透明度", opacity_row)

        self._color = QComboBox()
        for label, val in _COLOR_CHOICES:
            self._color.addItem(label, val)
        idx = self._color.findData(fw.font_color)
        if idx >= 0:
            self._color.setCurrentIndex(idx)
        form.addRow("时长字体颜色", self._color)

        self._click_through = QCheckBox("鼠标穿透（点击直接穿过到桌面 / 下方窗口）")
        self._click_through.setChecked(fw.click_through)
        form.addRow("", self._click_through)

        hint = QLabel(
            "<span style='color:#888; font-size:11px;'>"
            "字体颜色仅影响悬浮窗下方时长。自适应会每秒采样下方桌面背景亮度，自动切换黑 / 白。"
            "</span>"
        )
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        # 初始联动
        self._on_theme_changed()

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(hint)
        root.addStretch(1)
        root.addWidget(buttons)

    def _on_theme_changed(self) -> None:
        is_text = self._theme.currentData() == "text"
        # 文字主题的宽/高、字体颜色仅在文字模式下有意义
        for w in (self._width, self._height, self._color):
            w.setEnabled(is_text)
        self._image_size.setEnabled(not is_text)

    def _on_ok(self) -> None:
        fw = self._settings.floating_window
        fw.enabled = self._enabled.isChecked()
        fw.theme = self._theme.currentData() or "text"
        fw.image_size = self._image_size.value()
        fw.width = self._width.value()
        fw.height = self._height.value()
        fw.opacity = self._opacity.value() / 100.0
        fw.font_color = self._color.currentData() or "white"
        fw.click_through = self._click_through.isChecked()
        self._settings.save()
        self.settings_changed.emit(self._settings)
        self.accept()
