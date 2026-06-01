"""AI 今日点评设置弹窗：选择风格 / 自定义提示词 / 立即预览。

接入主窗口的 settings_changed 通道，保存后由 main.on_settings_changed 统一刷新概览点评。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import commentary
from ..ai_classifier import AIClassifier
from ..settings import CommentarySettings, Settings
from ..storage import Storage, day_range

_CUSTOM_KEY = "custom"

_INPUT_QSS = """
QComboBox, QPlainTextEdit {
    padding: 5px 8px;
    border: 1px solid #d0d6d9;
    border-radius: 4px;
    background: white;
}
QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #3498db; }
"""
_PRIMARY_BTN_QSS = (
    "QPushButton {"
    " padding: 6px 16px; border-radius: 4px;"
    " background-color: #3498db; color: white; border: none; font-weight: bold;"
    "}"
    "QPushButton:hover { background-color: #2980b9; }"
)


class CommentaryDialog(QDialog):
    settings_changed = Signal(Settings)

    def __init__(
        self,
        settings: Settings,
        ai: AIClassifier,
        storage: Storage,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("AI 今日点评设置")
        self.resize(460, 420)
        self.setStyleSheet(_INPUT_QSS)
        self._settings = settings
        self._ai = ai
        self._storage = storage

        cm = settings.commentary

        # --- 风格选择 ---
        self._style = QComboBox()
        for key, (label, _prompt) in commentary.COMMENTARY_STYLES.items():
            self._style.addItem(label, key)
        self._style.addItem("自定义", _CUSTOM_KEY)
        idx = self._style.findData(cm.style)
        self._style.setCurrentIndex(idx if idx >= 0 else 0)
        self._style.currentIndexChanged.connect(self._on_style_changed)

        # --- 提示词编辑区 ---
        self._prompt = QPlainTextEdit()
        self._prompt.setMinimumHeight(140)
        # 自定义初始内容：已存的 custom_prompt，否则以当前预设为起点
        self._custom_cache = cm.custom_prompt or commentary.preset_prompt(
            cm.style if cm.style != _CUSTOM_KEY else commentary.DEFAULT_STYLE
        )

        hint = QLabel(
            "可用占位符：{work_pct} {fishing_pct} {neutral_pct} {idle_pct}"
            "（及 {work_min} 等分钟数）。选择「自定义」可自由编辑。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")

        # --- 预览 ---
        preview_row = QHBoxLayout()
        self._btn_preview = QPushButton("立即预览")
        self._btn_preview.setStyleSheet(_PRIMARY_BTN_QSS)
        self._btn_preview.clicked.connect(self._on_preview)
        preview_row.addWidget(self._btn_preview)
        preview_row.addStretch(1)

        self._preview_label = QLabel("—")
        self._preview_label.setWordWrap(True)
        self._preview_label.setTextFormat(Qt.PlainText)
        self._preview_label.setStyleSheet(
            "background: #f7f9fc; border: 1px solid #e3e8ef; border-radius: 8px;"
            " padding: 8px 10px; color: #2c3e50; font: 13px '微软雅黑';"
        )
        self._preview_label.setMinimumHeight(48)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("点评风格"))
        root.addWidget(self._style)
        root.addWidget(QLabel("提示词"))
        root.addWidget(self._prompt, 1)
        root.addWidget(hint)
        root.addLayout(preview_row)
        root.addWidget(self._preview_label)
        root.addWidget(buttons)

        self._ai.commentary_preview_ready.connect(self._on_preview_ready)
        self._on_style_changed()  # 初始填充提示词

    # --- 交互 ---

    def _current_is_custom(self) -> bool:
        return self._style.currentData() == _CUSTOM_KEY

    def _on_style_changed(self) -> None:
        if self._current_is_custom():
            self._prompt.setReadOnly(False)
            self._prompt.setPlainText(self._custom_cache)
        else:
            self._prompt.setReadOnly(True)
            self._prompt.setPlainText(
                commentary.preset_prompt(self._style.currentData())
            )

    def _collect(self) -> CommentarySettings:
        if self._current_is_custom():
            return CommentarySettings(
                style=_CUSTOM_KEY,
                custom_prompt=self._prompt.toPlainText().strip(),
            )
        return CommentarySettings(
            style=self._style.currentData(),
            custom_prompt=self._custom_cache,
        )

    def _on_preview(self) -> None:
        if not self._ai.is_ready:
            self._preview_label.setText(
                "请先在「设置 → 工具 → AI 判断」中启用并配置 api_key。"
            )
            return
        start, end = day_range(datetime.now())
        totals = self._storage.aggregate_range(
            start, end, self._settings.sample_interval_seconds
        )
        prompt = commentary.build_prompt(self._collect(), totals)
        self._preview_label.setText("正在生成预览…")
        self._ai.preview_commentary(prompt)

    def _on_preview_ready(self, text: str) -> None:
        text = (text or "").strip()
        self._preview_label.setText(text or "预览失败，请检查 AI 配置后重试")

    def _on_ok(self) -> None:
        # 记住自定义文本，便于下次切回
        if self._current_is_custom():
            self._custom_cache = self._prompt.toPlainText().strip()
        cm = self._collect()
        self._settings.commentary.style = cm.style
        self._settings.commentary.custom_prompt = cm.custom_prompt
        self._settings.save()
        self.settings_changed.emit(self._settings)
        self.accept()
