"""系统托盘：精简菜单 + 状态色图标。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .paths import icon_file


STATE_COLORS = {
    "work": QColor("#2ecc71"),
    "fishing": QColor("#e74c3c"),
    "neutral": QColor("#3498db"),
    "unknown": QColor("#f39c12"),
    "idle": QColor("#95a5a6"),
    "paused": QColor("#7f8c8d"),
}
STATE_LABELS = {
    "work": "工作中",
    "fishing": "摸鱼中",
    "neutral": "中立",
    "unknown": "未知",
    "idle": "空闲中",
    "paused": "已暂停",
}


def _colored_icon(color: QColor, size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(0, 0, 0, 80))
    p.drawEllipse(4, 4, size - 8, size - 8)
    p.end()
    return QIcon(pix)


class Tray(QObject):
    show_main = Signal()         # 显示主窗口（首页）
    show_stats = Signal()         # 显示统计窗口
    toggle_floating = Signal()
    toggle_click_through = Signal()
    toggle_pause = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icons = {k: _colored_icon(c) for k, c in STATE_COLORS.items()}
        base = QIcon(str(icon_file())) if icon_file().exists() else self._icons["neutral"]
        self._base_icon = base if not base.isNull() else self._icons["neutral"]

        self._tray = QSystemTrayIcon(self._base_icon, parent)
        self._tray.setToolTip("WorkingorFishing")

        menu = QMenu()
        act_main = QAction("打开主窗口（概览）", menu)
        act_main.triggered.connect(self.show_main)
        menu.addAction(act_main)

        act_stats = QAction("统计（设置·统计）", menu)
        act_stats.triggered.connect(self.show_stats)
        menu.addAction(act_stats)

        self._act_float = QAction("显示/隐藏悬浮窗", menu)
        self._act_float.triggered.connect(self.toggle_floating)
        menu.addAction(self._act_float)

        self._act_through = QAction("悬浮窗鼠标穿透", menu)
        self._act_through.setCheckable(True)
        self._act_through.triggered.connect(self.toggle_click_through)
        menu.addAction(self._act_through)

        menu.addSeparator()
        self._act_pause = QAction("暂停采集", menu)
        self._act_pause.triggered.connect(self.toggle_pause)
        menu.addAction(self._act_pause)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_requested)
        menu.addAction(act_quit)

        self._menu = menu
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_main.emit()

    def update_state(self, state: str) -> None:
        icon = self._icons.get(state, self._base_icon)
        self._tray.setIcon(icon)
        label = STATE_LABELS.get(state, state)
        self._tray.setToolTip(f"WorkingorFishing - {label}")

    def update_paused(self, paused: bool) -> None:
        self._act_pause.setText("恢复采集" if paused else "暂停采集")

    def update_click_through(self, on: bool) -> None:
        self._act_through.setChecked(bool(on))

    def show_message(self, title: str, msg: str) -> None:
        self._tray.showMessage(title, msg, QSystemTrayIcon.Information, 3000)
