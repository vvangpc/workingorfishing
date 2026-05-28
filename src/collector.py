"""前台窗口采样：定时抓 hwnd → 进程名 / 标题 / 浏览器 URL，分类后入库。"""
from __future__ import annotations

import logging
from typing import Optional

import psutil
import win32gui
import win32process
from PySide6.QtCore import QObject, QTimer, Signal

from . import browser_url, idle
from .classifier import Classifier
from .storage import Storage

logger = logging.getLogger(__name__)

_BROWSER_PROCS = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
}


class Collector(QObject):
    # 当前类别（work/fishing/neutral/unknown/idle/paused）
    state_changed = Signal(str)
    # 出现一个未命中规则的样本，payload: {process, title, url, ts}
    unknown_sample = Signal(dict)
    # 已经入库一条记录（任意类别），UI 可据此刷新统计
    record_inserted = Signal()

    def __init__(self, storage: Storage, classifier: Classifier, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._classifier = classifier
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._interval_s = 10
        self._idle_threshold_s = 300
        self._paused = False
        self._last_state: Optional[str] = None
        # 去重：避免同一 (process, title-or-url-key) 反复发未确认事件
        self._unknown_seen: set[tuple[str, str]] = set()

    # --- 控制 ---

    def configure(self, interval_seconds: int, idle_threshold_seconds: int) -> None:
        self._interval_s = max(1, int(interval_seconds))
        self._idle_threshold_s = max(10, int(idle_threshold_seconds))
        if self._timer.isActive():
            self._timer.start(self._interval_s * 1000)

    def start(self) -> None:
        if not self._paused:
            self._timer.start(self._interval_s * 1000)
            QTimer.singleShot(0, self._tick)

    def stop(self) -> None:
        self._timer.stop()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self._timer.stop()
            self._emit_state("paused")
        else:
            self.start()

    def clear_unknown_seen(self) -> None:
        """规则有变更或用户确认了一些 unknown 后，清掉去重表让 collector 重新发现。"""
        self._unknown_seen.clear()

    @property
    def sample_interval(self) -> int:
        return self._interval_s

    # --- 采样 ---

    def _tick(self) -> None:
        if self._paused:
            return
        try:
            self._sample_once()
        except Exception:
            logger.exception("collector tick failed")

    def _sample_once(self) -> None:
        if idle.is_idle(self._idle_threshold_s):
            self._storage.insert(
                process_name=None,
                window_title=None,
                url=None,
                category="idle",
                is_idle=True,
            )
            self._emit_state("idle")
            self.record_inserted.emit()
            return

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            title = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = psutil.Process(pid).name() if pid else None
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            proc_name = None

        url: Optional[str] = None
        is_browser = bool(proc_name and proc_name.lower() in _BROWSER_PROCS)
        if is_browser:
            url = browser_url.get_active_url(hwnd, proc_name)
            if not url:
                logger.info(
                    "browser %s: URL capture failed, falling back to title (hwnd=%s, title=%r)",
                    proc_name, hwnd, (title or "")[:80],
                )

        # 优先级：URL → 标题 → 进程兜底（浏览器进程通常是 neutral）
        category, rule_id = self._classifier.classify(proc_name, title, url)
        self._storage.insert(
            process_name=proc_name,
            window_title=title,
            url=url,
            category=category,
            is_idle=False,
            rule_id=rule_id,
        )
        self._emit_state(category)
        self.record_inserted.emit()

        if category == "unknown":
            key = (
                (proc_name or "").lower(),
                # URL 优先做去重 key，否则用标题前缀
                (url or "")[:80] or (title or "")[:80],
            )
            if key not in self._unknown_seen:
                self._unknown_seen.add(key)
                self.unknown_sample.emit(
                    {
                        "process": proc_name,
                        "title": title,
                        "url": url,
                    }
                )

    def _emit_state(self, state: str) -> None:
        if state != self._last_state:
            self._last_state = state
            self.state_changed.emit(state)
