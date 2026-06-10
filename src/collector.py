"""前台窗口采样：常驻工作线程定时抓 hwnd → 进程名 / 标题 / 浏览器 URL，分类后入库。

线程模型：采样跑在独立 daemon 线程（避免 UIA 慢调用阻塞 GUI 主线程），
信号从工作线程 emit，Qt 自动以 queued connection 投递回主线程；
Storage 自带锁、Classifier 只读原子换引用的规则列表，均线程安全。
日程检查（自动暂停）仍用主线程 QTimer，只翻转标志位。
"""
from __future__ import annotations

import logging
import threading
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

    # _unknown_seen 封顶：超过即清空重新发现（防动态标题导致集合无限增长）
    _UNKNOWN_SEEN_MAX = 5000

    def __init__(self, storage: Storage, classifier: Classifier, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._classifier = classifier
        # 采样工作线程
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._kick = threading.Event()  # 置位 = 立即采样一次（绕过 interval 等待）
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
        self._unknown_lock = threading.Lock()
        # 前台进程名缓存：(pid, Process, name)，同一前台应用连续采样省去重复打开进程
        self._proc_cache: Optional[tuple[int, psutil.Process, str]] = None

    # --- 控制 ---

    def configure(self, interval_seconds: int, idle_threshold_seconds: int) -> None:
        self._interval_s = max(1, int(interval_seconds))
        self._idle_threshold_s = max(10, int(idle_threshold_seconds))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self._kick.set()
            return
        self._stop_event.clear()
        self._kick.set()  # 启动即采一次
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="Collector"
        )
        self._thread.start()

    def stop(self) -> None:
        # 注意：不停 _sched_timer——日程检查独立于采样（只翻标志位），
        # 否则 WebDAV 同步 / 导入路径 stop→start 后自动暂停日程会悄悄失效。
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._kick.set()
            self._thread.join(timeout=5.0)
        self._thread = None

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
            self._emit_state("paused")
        else:
            self._kick.set()  # 恢复后立即采一次

    def clear_unknown_seen(self) -> None:
        """规则有变更或用户确认了一些 unknown 后，清掉去重表让 collector 重新发现。"""
        with self._unknown_lock:
            self._unknown_seen.clear()

    @property
    def sample_interval(self) -> int:
        return self._interval_s

    # --- 采样 ---

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._kick.wait(timeout=self._interval_s):
                self._kick.clear()
            if self._stop_event.is_set():
                break
            if self._is_paused():
                continue
            try:
                self._sample_once()
            except Exception:
                logger.exception("collector sample failed")

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
            proc_name = self._process_name(pid) if pid else None
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            proc_name = None

        url: Optional[str] = None
        is_browser = bool(proc_name and proc_name.lower() in _BROWSER_PROCS)
        if is_browser:
            url = browser_url.get_active_url(hwnd, proc_name)
            if not url:
                logger.debug(
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
            with self._unknown_lock:
                if len(self._unknown_seen) >= self._UNKNOWN_SEEN_MAX:
                    self._unknown_seen.clear()
                is_new = key not in self._unknown_seen
                if is_new:
                    self._unknown_seen.add(key)
            if is_new:
                self.unknown_sample.emit(
                    {
                        "process": proc_name,
                        "title": title,
                        "url": url,
                    }
                )

    def _process_name(self, pid: int) -> Optional[str]:
        cached = self._proc_cache
        if cached and cached[0] == pid and cached[1].is_running():
            return cached[2]
        p = psutil.Process(pid)
        name = p.name()
        self._proc_cache = (pid, p, name)
        return name

    def _emit_state(self, state: str) -> None:
        if state != self._last_state:
            self._last_state = state
            self.state_changed.emit(state)
