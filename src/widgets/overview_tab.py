"""概览 Tab：极致紧凑——单列垂直布局，目标 1/4 原占地。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ai_classifier import AIClassifier, AISuggestion
from ..classifier import Classifier
from ..storage import Storage, day_range
from ..widgets.pending_dialog import PendingDialog

STATE_LABELS = {
    "work": "工作中",
    "fishing": "摸鱼中",
    "neutral": "中立",
    "unknown": "未知",
    "idle": "空闲中",
    "paused": "已暂停",
}
STATE_COLORS = {
    "work": "#2ecc71",
    "fishing": "#e74c3c",
    "neutral": "#3498db",
    "unknown": "#f39c12",
    "idle": "#95a5a6",
    "paused": "#7f8c8d",
}
_BAR_CATEGORIES = ("work", "fishing", "neutral", "idle")
_BAR_LABELS = {
    "work": "工作",
    "fishing": "摸鱼",
    "neutral": "中立",
    "idle": "空闲",
}


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m}m"


class _BarChart(QWidget):
    """今日分类：顶部摘要 + 每类一行 [类名][条][时长 + 占比]。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # 顶部摘要
        self._summary = QLabel("—")
        self._summary.setStyleSheet("font-size: 11px;")
        self._summary.setTextFormat(Qt.RichText)
        root.addWidget(self._summary)

        # 进度条网格
        bars_widget = QWidget()
        grid = QGridLayout(bars_widget)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(1, 1)
        self._rows: dict[str, tuple[QProgressBar, QLabel]] = {}
        for i, cat in enumerate(_BAR_CATEGORIES):
            name = QLabel(_BAR_LABELS[cat])
            name.setFixedWidth(32)
            name.setStyleSheet(
                f"color: {STATE_COLORS[cat]}; font: bold 11px '微软雅黑';"
            )
            bar = QProgressBar()
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            color = STATE_COLORS[cat]
            bar.setStyleSheet(
                "QProgressBar {"
                " background-color: #f4f4f4;"
                " border: none;"
                " border-radius: 5px;"
                "}"
                "QProgressBar::chunk {"
                f" background-color: {color};"
                " border-radius: 5px;"
                "}"
            )
            value = QLabel("—")
            value.setMinimumWidth(86)
            value.setTextFormat(Qt.RichText)
            value.setStyleSheet("font-size: 11px;")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(name, i, 0)
            grid.addWidget(bar, i, 1)
            grid.addWidget(value, i, 2)
            self._rows[cat] = (bar, value)
        root.addWidget(bars_widget)

    def update_totals(self, totals: dict[str, int]) -> None:
        total = sum(totals.get(c, 0) for c in _BAR_CATEGORIES) or 1
        work = totals.get("work", 0)
        fishing = totals.get("fishing", 0)
        active = work + fishing + totals.get("neutral", 0)

        if active <= 0:
            self._summary.setText(
                "<span style='color:#999;'>今日尚无活动数据</span>"
            )
        else:
            ratio = work / active * 100 if active else 0
            self._summary.setText(
                f"<span style='color:{STATE_COLORS['work']}; font-weight:bold;'>"
                f"工作 {_fmt_duration(work)}</span>"
                "  <span style='color:#bbb;'>·</span>  "
                f"<span style='color:{STATE_COLORS['fishing']}; font-weight:bold;'>"
                f"摸鱼 {_fmt_duration(fishing)}</span>"
                "  <span style='color:#bbb;'>·</span>  "
                f"<span style='color:#444;'>工作占比 <b>{ratio:.0f}%</b></span>"
            )

        for cat, (bar, value) in self._rows.items():
            v = int(totals.get(cat, 0))
            bar.setRange(0, max(total, 1))
            bar.setValue(v)
            p = (v / total * 100) if total else 0
            if v > 0:
                value.setText(
                    f"<span style='color:#333; font-weight:bold;'>{_fmt_duration(v)}</span>"
                    f"  <span style='color:#999;'>{p:.0f}%</span>"
                )
            else:
                value.setText("<span style='color:#bbb;'>—</span>")


class OverviewTab(QWidget):
    rules_changed = Signal()
    pause_requested = Signal()
    open_ai_settings_requested = Signal()

    def __init__(
        self,
        storage: Storage,
        classifier: Classifier,
        ai_classifier: AIClassifier,
        sample_interval: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._storage = storage
        self._classifier = classifier
        self._ai = ai_classifier
        self._sample_interval = sample_interval
        self._suggestions: dict[tuple, AISuggestion] = {}
        self._pending_dialog: Optional[PendingDialog] = None

        # --- 顶部一行：状态点 + 按钮 ---
        self._status_label = QLabel("● 等待…")
        self._status_label.setStyleSheet("font: bold 13px '微软雅黑';")
        self._btn_pending = QPushButton("待确定 (0)")
        self._btn_pending.setMaximumHeight(24)
        self._btn_pending.setToolTip("点击查看未匹配规则的活动（含自动规则 / 刷新）")
        self._btn_pending.clicked.connect(self._open_pending_dialog)
        self._btn_pause = QPushButton("暂停 / 恢复")
        self._btn_pause.setMaximumHeight(24)
        self._btn_pause.clicked.connect(self.pause_requested)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addWidget(self._status_label)
        top_row.addStretch(1)
        top_row.addWidget(self._btn_pending)
        top_row.addWidget(self._btn_pause)

        # --- 详情行（一行省略显示） ---
        self._detail_label = QLabel("—")
        self._detail_label.setStyleSheet("font-size: 10px; color: #555;")
        self._detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_label.setWordWrap(False)
        self._detail_label.setMaximumHeight(16)

        # --- 横条形图 ---
        self._bars = _BarChart()

        # --- 整体垂直布局 ---
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        root.addLayout(top_row)
        root.addWidget(self._detail_label)
        # 分隔线
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #e6e6e6;")
        root.addSpacing(2)
        root.addWidget(sep)
        root.addSpacing(2)
        root.addWidget(self._bars)
        root.addStretch(1)

        self._ai.suggestion_ready.connect(self._on_suggestion)

        self.refresh_bars()
        self.refresh_pending_count()

    # --- 入口槽 ---

    def update_status(self, state: str) -> None:
        color = STATE_COLORS.get(state, STATE_COLORS["neutral"])
        label = STATE_LABELS.get(state, state)
        self._status_label.setText(
            f"<span style='color:{color}'>● {label}</span>"
        )

    def update_current_sample(
        self, process: Optional[str], title: Optional[str], url: Optional[str]
    ) -> None:
        # 单行省略显示
        text = (
            (process or "—")
            + (" · " + title if title else "")
            + (" · " + url if url else "")
        )
        if len(text) > 90:
            text = text[:90] + "…"
        self._detail_label.setText(text)

    def set_sample_interval(self, seconds: int) -> None:
        self._sample_interval = seconds
        self.refresh_bars()

    def on_record_inserted(self) -> None:
        self.refresh_bars()
        self.refresh_pending_count()

    # --- 今日条形图 ---

    def refresh_bars(self) -> None:
        start, end = day_range(datetime.now())
        totals = self._storage.aggregate_range(start, end, self._sample_interval)
        self._bars.update_totals(totals)

    # --- 待确定 ---

    def refresh_pending_count(self) -> None:
        n = len(self._storage.pending_unknown(limit=500))
        self._btn_pending.setText(f"待确定 ({n})")
        if n > 0:
            self._btn_pending.setStyleSheet(
                "QPushButton { background-color: #fff4e0; color: #c0392b; font-weight: bold; }"
            )
        else:
            self._btn_pending.setStyleSheet("")

    def _open_pending_dialog(self) -> None:
        if self._pending_dialog is None or not self._pending_dialog.isVisible():
            self._pending_dialog = PendingDialog(
                self._storage,
                self._classifier,
                self._ai,
                self._suggestions,
                parent=self,
            )
            self._pending_dialog.rules_changed.connect(self._on_pending_changed)
            self._pending_dialog.open_ai_settings_requested.connect(
                self.open_ai_settings_requested
            )
            self._pending_dialog.finished.connect(self._on_pending_closed)
            self._pending_dialog.show()
        else:
            self._pending_dialog.raise_()
            self._pending_dialog.activateWindow()

    def _on_pending_changed(self) -> None:
        self.refresh_pending_count()
        self.refresh_bars()
        self.rules_changed.emit()

    def _on_pending_closed(self, _result: int = 0) -> None:
        self._pending_dialog = None
        self.refresh_pending_count()

    # --- AI 建议接入 ---

    def _on_suggestion(self, sug: AISuggestion) -> None:
        key = self._sug_key(sug.process, sug.url, sug.title)
        self._suggestions[key] = sug
        self.refresh_pending_count()

    @staticmethod
    def _sug_key(process: Optional[str], url: Optional[str], title: Optional[str]) -> tuple:
        return ((process or "").lower(), (url or "")[:80] or (title or "")[:80])
