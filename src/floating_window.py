"""桌面悬浮窗：上层彩色状态条 + 下层独立透明时长（绕过 windowOpacity 的毛玻璃感）。"""
from __future__ import annotations

import ctypes
from datetime import datetime
from time import monotonic
from typing import Optional

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from .paths import APP_DISPLAY_NAME, floating_image
from .settings import Settings
from .storage import Storage, day_range
from .tray import STATE_COLORS, STATE_LABELS

# --- Win32 鼠标穿透 ---
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.GetDC.restype = ctypes.c_void_p
_user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.GetPixel.restype = ctypes.c_uint
_gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]


def _win_set_click_through(hwnd: int, on: bool) -> None:
    try:
        ex = _user32.GetWindowLongW(int(hwnd), _GWL_EXSTYLE)
        if on:
            new_ex = ex | _WS_EX_LAYERED | _WS_EX_TRANSPARENT
        else:
            new_ex = (ex | _WS_EX_LAYERED) & ~_WS_EX_TRANSPARENT
        if new_ex != ex:
            _user32.SetWindowLongW(int(hwnd), _GWL_EXSTYLE, new_ex)
    except Exception:
        pass


def _get_pixel(x: int, y: int) -> Optional[int]:
    try:
        hdc = _user32.GetDC(None)
        if not hdc:
            return None
        try:
            v = _gdi32.GetPixel(hdc, x, y)
        finally:
            _user32.ReleaseDC(None, hdc)
        if v == 0xFFFFFFFF:
            return None
        return int(v)
    except Exception:
        return None


def _luminance(colorref: int) -> float:
    r = colorref & 0xFF
    g = (colorref >> 8) & 0xFF
    b = (colorref >> 16) & 0xFF
    return 0.299 * r + 0.587 * g + 0.114 * b


def _fmt_short(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m}m"


class _InfoOverlay(QWidget):
    """独立顶层透明窗口，只画一行时长文字。

    - 拥有自己的 setWindowOpacity(1.0)，不受主悬浮窗 windowOpacity 影响
    - 始终鼠标穿透：never intercepts input
    - 跟随主悬浮窗位置（由 FloatingWindow 调度 reposition）
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(1.0)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        # 确保 OS 层也穿透
        _win_set_click_through(int(self.winId()), True)

    def set_content(self, text: str, color: str) -> None:
        self._label.setText(text)
        self._label.setStyleSheet(
            "QLabel {"
            " background: transparent;"
            " border: none;"
            " padding: 0px;"
            f" color: {color};"
            " font: bold 12px '微软雅黑';"
            "}"
        )


class FloatingWindow(QWidget):
    request_show_main = Signal()
    request_show_stats = Signal()
    request_toggle_pause = Signal()
    request_toggle_click_through = Signal()
    request_quit = Signal()

    INFO_HEIGHT = 18
    INFO_GAP = 2

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._storage = storage
        self._sample_interval = settings.sample_interval_seconds
        self._current_state = "neutral"
        self._today_totals: dict[str, int] = {}
        self._last_query: float = 0.0
        self._drag_offset: Optional[QPoint] = None
        self._adaptive_cache: str = "white"

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(APP_DISPLAY_NAME)

        # 文字主题：彩色圆角条 + 文字
        # 图片主题：用 assets/floating/float_<state>.png 渲染
        self._state_label = QLabel("中立", self)
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setScaledContents(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._state_label)

        # 独立的时长 overlay
        self._info = _InfoOverlay()

        # 缓存 pixmap：状态 → QPixmap，避免每秒重新读盘
        self._pixmap_cache: dict[str, QPixmap] = {}

        self._apply_theme_size()
        self._apply_state("neutral")
        self.setWindowOpacity(settings.floating_window.opacity)
        self.move(settings.floating_window.x, settings.floating_window.y)

        # 不显示秒钟，状态变化由 update_state 信号驱动；只用定时器刷新时长缓存
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(5000)
        self._tick()

    # --- show / hide / move / resize 触发 overlay 同步 ---

    def showEvent(self, e) -> None:
        super().showEvent(e)
        _win_set_click_through(int(self.winId()), self._settings.floating_window.click_through)
        self._reposition_info()
        self._tick()

    def hideEvent(self, e) -> None:
        self._info.hide()
        super().hideEvent(e)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self._reposition_info()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reposition_info()

    def _reposition_info(self) -> None:
        if not self.isVisible():
            return
        global_bl = self.mapToGlobal(self.rect().bottomLeft())
        self._info.resize(self.width(), self.INFO_HEIGHT)
        self._info.move(global_bl.x(), global_bl.y() + self.INFO_GAP)

    # --- 尺寸 / 主题 ---

    def _apply_theme_size(self) -> None:
        """根据当前主题应用尺寸。"""
        fw = self._settings.floating_window
        if fw.theme == "image":
            sz = max(48, int(fw.image_size))
            self._state_label.setFixedSize(sz, sz)
            self.setFixedSize(sz, sz)
        else:
            w = max(40, int(fw.width))
            h = max(20, int(fw.height))
            self._state_label.setFixedSize(w, h)
            self.setFixedSize(w, h)
        self._reposition_info()

    # --- 状态 ---

    def update_state(self, state: str) -> None:
        self._current_state = state
        self._apply_state(state)
        self._tick()

    def _apply_state(self, state: str) -> None:
        if self._settings.floating_window.theme == "image":
            self._apply_image_state(state)
        else:
            self._apply_text_state(state)

    def _apply_text_state(self, state: str) -> None:
        color = STATE_COLORS.get(state, STATE_COLORS["neutral"])
        text = STATE_LABELS.get(state, state)
        r, g, b = color.red(), color.green(), color.blue()
        # QLabel 的 text 与 pixmap 互斥：必须先清残留图片，再设文字（setText 须最后调用，
        # 否则 setPixmap(空) 会把刚设好的文字一并清掉 → 文字主题文字消失）
        self._state_label.setPixmap(QPixmap())
        self._state_label.setText(text)
        self._state_label.setStyleSheet(
            "QLabel {"
            f" background-color: rgba({r}, {g}, {b}, 220);"
            " color: white;"
            " border-radius: 8px;"
            " font: bold 13px '微软雅黑';"
            " padding: 2px;"
            "}"
        )

    def _apply_image_state(self, state: str) -> None:
        pix = self._load_pixmap(state)
        sz = self._state_label.size()
        if pix.isNull():
            # 图片缺失 → 退回文字
            self._apply_text_state(state)
            return
        scaled = pix.scaled(
            sz.width(), sz.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self._state_label.setText("")
        self._state_label.setStyleSheet("background-color: transparent;")
        self._state_label.setPixmap(scaled)

    def _load_pixmap(self, state: str) -> QPixmap:
        if state in self._pixmap_cache:
            return self._pixmap_cache[state]
        path = floating_image(state)
        pix = QPixmap(str(path)) if path.exists() else QPixmap()
        self._pixmap_cache[state] = pix
        return pix

    # --- 心跳 ---

    def _tick(self) -> None:
        now = datetime.now()
        if monotonic() - self._last_query >= 30 or not self._today_totals:
            try:
                start, end = day_range(now)
                self._today_totals = self._storage.aggregate_range(
                    start, end, self._sample_interval
                )
            except Exception:
                pass
            self._last_query = monotonic()

        state = self._current_state
        if state == "work":
            text = _fmt_short(self._today_totals.get("work", 0))
        elif state == "fishing":
            text = _fmt_short(self._today_totals.get("fishing", 0))
        else:
            text = ""

        if text and self.isVisible():
            color = self._resolved_color()
            self._info.set_content(text, color)
            if not self._info.isVisible():
                self._reposition_info()
                self._info.show()
                self._reposition_info()
        else:
            self._info.hide()

    def _resolved_color(self) -> str:
        choice = self._settings.floating_window.font_color
        if choice in ("white", "black"):
            return choice
        sampled = self._sample_adaptive_color()
        if sampled is not None:
            self._adaptive_cache = sampled
        return self._adaptive_cache

    def _sample_adaptive_color(self) -> Optional[str]:
        if not self.isVisible():
            return None
        # 时长 overlay 的屏幕区域
        global_bl = self.mapToGlobal(self.rect().bottomLeft())
        x0, y0 = global_bl.x(), global_bl.y() + self.INFO_GAP
        w, h = self.width(), self.INFO_HEIGHT
        if w <= 0 or h <= 0:
            return None
        # 采左右两端 & 上下边缘，避开正中央的文字
        samples: list[float] = []
        for dx, dy in (
            (2, h // 2),
            (max(2, w // 8), h // 2),
            (w - 3, h // 2),
            (w - max(2, w // 8), h // 2),
            (w // 2, 1),
            (w // 2, h - 2),
        ):
            v = _get_pixel(x0 + dx, y0 + dy)
            if v is not None:
                samples.append(_luminance(v))
        if not samples:
            return None
        avg = sum(samples) / len(samples)
        return "black" if avg > 140 else "white"

    def on_record_inserted(self) -> None:
        self._last_query = 0.0

    def set_sample_interval(self, seconds: int) -> None:
        self._sample_interval = seconds
        self._last_query = 0.0

    # --- 设置 / 穿透 ---

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._pixmap_cache.clear()  # 主题切换时清缓存
        self._apply_theme_size()
        self._apply_state(self._current_state)
        self.setWindowOpacity(settings.floating_window.opacity)
        if settings.floating_window.enabled:
            if not self.isVisible():
                self.show()
        else:
            self.hide()
        if self.isVisible():
            _win_set_click_through(int(self.winId()), settings.floating_window.click_through)
            self._reposition_info()
        self._tick()

    def _apply_click_through(self, on: bool) -> None:
        self._settings.floating_window.click_through = bool(on)
        if self.isVisible():
            _win_set_click_through(int(self.winId()), bool(on))

    def is_click_through(self) -> bool:
        return bool(self._settings.floating_window.click_through)

    def close_overlay(self) -> None:
        """退出程序时清理 overlay 顶层窗口。"""
        try:
            self._info.close()
            self._info.deleteLater()
        except Exception:
            pass

    # --- 拖拽 ---

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._settings.floating_window.x = self.x()
            self._settings.floating_window.y = self.y()
            self._settings.save()
            e.accept()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.request_show_main.emit()

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        a_main = QAction("打开主窗口", menu)
        a_main.triggered.connect(self.request_show_main)
        menu.addAction(a_main)
        a_stats = QAction("统计", menu)
        a_stats.triggered.connect(self.request_show_stats)
        menu.addAction(a_stats)
        a_pause = QAction("暂停/恢复采集", menu)
        a_pause.triggered.connect(self.request_toggle_pause)
        menu.addAction(a_pause)
        a_through = QAction("鼠标穿透", menu)
        a_through.setCheckable(True)
        a_through.setChecked(self.is_click_through())
        a_through.triggered.connect(self.request_toggle_click_through)
        menu.addAction(a_through)
        menu.addSeparator()
        a_hide = QAction("隐藏悬浮窗", menu)
        a_hide.triggered.connect(self.hide)
        menu.addAction(a_hide)
        menu.addSeparator()
        a_quit = QAction("退出程序", menu)
        a_quit.triggered.connect(self.request_quit)
        menu.addAction(a_quit)
        menu.exec(e.globalPos())
