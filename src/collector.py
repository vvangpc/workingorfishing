"""前台窗口采样：定时抓 hwnd → 进程名 / 标题 / 浏览器 URL，分类后入库。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import psutil
import win32gui
import win32process
from PySide6.QtCore import QObject, QTimer, Signal

from . import browser_url, idle
from .classifier import Classifier
from .settings import AutoPauseSettings
from .storage import Storage

logger = logging.getLogger(__name__)

def _parse_hhmm(value: str) -> Optional[int]:
    """\"HH:MM\" → 当天分钟数；解析失败返回 None。"""
    try:
        h, m = str(value).split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


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
        # 暂停分两层：手动（托盘/按钮）+ 日程（自动暂停时段），有效暂停 = 二者之一为真
        self._manual_paused = False
        self._schedule_paused = False
        # 日程检查定时器：每 30s 判断当前时间是否落入暂停时段
        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self._check_schedule)
        self._auto_pause_enabled = False
        self._pause_ranges: list[tuple[int, int]] = []  # 已解析为分钟数的区间
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
        if not self._is_paused():
            self._timer.start(self._interval_s * 1000)
            QTimer.singleShot(0, self._tick)

    def stop(self) -> None:
        self._timer.stop()
        self._sched_timer.stop()

    def _is_paused(self) -> bool:
        return self._manual_paused or self._schedule_paused

    def set_paused(self, paused: bool) -> None:
        self._manual_paused = paused
        self._refresh_active()

    def configure_schedules(self, auto_pause: AutoPauseSettings) -> None:
        """根据自动暂停设置（重新）配置日程定时器。"""
        self._auto_pause_enabled = bool(auto_pause.enabled)
        self._pause_ranges = []
        for r in auto_pause.ranges:
            if not getattr(r, "enabled", True):
                continue
            start = _parse_hhmm(r.start)
            end = _parse_hhmm(r.end)
            if start is None or end is None or start == end:
                continue
            self._pause_ranges.append((start, end))

        if self._auto_pause_enabled and self._pause_ranges:
            self._sched_timer.start(30_000)
            self._check_schedule()  # 立即评估一次
        else:
            self._sched_timer.stop()
            self._schedule_paused = False
            self._refresh_active()

    def _check_schedule(self) -> None:
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        in_range = any(
            (start <= now_min < end) if start < end
            else (now_min >= start or now_min < end)  # 跨午夜
            for start, end in self._pause_ranges
        )
        if in_range != self._schedule_paused:
            self._schedule_paused = in_range
            self._refresh_active()

    def _refresh_active(self) -> None:
        if self._is_paused():
            self._timer.stop()
            self._emit_state("paused")
        elif not self._timer.isActive():
            self._timer.start(self._interval_s * 1000)
            QTimer.singleShot(0, self._tick)

    def clear_unknown_seen(self) -> None:
        """规则有变更或用户确认了一些 unknown 后，清掉去重表让 collector 重新发现。"""
        self._unknown_seen.clear()

    @property
    def sample_interval(self) -> int:
        return self._interval_s

    # --- 采样 ---

    def _tick(self) -> None:
        if self._is_paused():
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
                interval=self._interval_s,
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
            interval=self._interval_s,
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
