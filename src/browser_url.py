"""通过 Windows UI Automation 读取浏览器活动标签 URL。

性能注意：UI Automation 较慢（10-100ms），仅对已知浏览器进程调用。
任何异常或超时一律返回 None，主流程不应因此中断。

关于 Chromium 系浏览器（Chrome / Edge / Brave）：
  这类浏览器默认不构建完整无障碍树（性能优化）。我们用 SendMessage(WM_GETOBJECT)
  + 短暂延迟 + 重试，尽量唤醒它们；最稳的办法是浏览器启动参数加
  --force-renderer-accessibility，详见 README。
"""
from __future__ import annotations

import ctypes
import logging
import re
import threading
import time
from queue import Queue
from time import monotonic
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Win32 唤醒 a11y
_WM_GETOBJECT = 0x003D
_OBJID_CLIENT = 0xFFFFFFFC  # -4 as DWORD
_user32 = ctypes.windll.user32


def _nudge_accessibility(hwnd: int) -> None:
    """给 Chromium 系窗口发 WM_GETOBJECT，触发它构建无障碍树。"""
    try:
        _user32.SendMessageW(int(hwnd), _WM_GETOBJECT, 0, _OBJID_CLIENT)
    except Exception:
        pass

# 延迟导入：uiautomation 启动时会初始化 COM，放在模块顶层会拖慢冷启动。
_uia = None
_uia_lock = threading.Lock()


def _get_uia():
    global _uia
    if _uia is None:
        with _uia_lock:
            if _uia is None:
                import uiautomation as uia  # type: ignore
                # 静默 uiautomation 自带的 debug 输出
                try:
                    uia.SetGlobalSearchTimeout(2.0)
                except Exception:
                    pass
                _uia = uia
    return _uia


# Chromium 系（Chrome / Edge / Brave / Vivaldi / Opera）
_CHROMIUM_NAMES = (
    "地址和搜索栏",
    "Address and search bar",
    "地址栏",
    "搜索或键入网址",
    "搜索或在地址栏中输入字词",
    "Search or type a URL",
    "Search or enter web address",
    "Adresse und Suchleiste",
)
_CHROMIUM_AUTOMATION_IDS = ("OmniboxView", "AddressEdit", "url")

# Firefox
_FIREFOX_NAMES = (
    "搜索或输入网址",
    "搜索或输入地址",
    "Search with Google or enter address",
    "Suche oder Adresse eingeben",
)

_URL_LIKE_RE = re.compile(r"^(https?://|about:|chrome:|edge:|brave:|file://)", re.IGNORECASE)


def _looks_like_url(value: str) -> bool:
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    if _URL_LIKE_RE.match(v):
        return True
    # 没协议但首段像域名（含点 + TLD 字符）
    head = v.split("/", 1)[0]
    if "." in head and " " not in head and len(head) < 253:
        return True
    return False


_URL_CANDIDATE_CONTROL_TYPES = {
    "EditControl",
    "ComboBoxControl",
    "DocumentControl",
    "CustomControl",
    "TextControl",
}


# 地址栏控件缓存：hwnd → control（同一窗口反复采样时省去整棵树遍历）
_addr_cache: dict[int, object] = {}
_ADDR_CACHE_MAX = 16


def _cache_put(hwnd: int, ctrl) -> None:
    if len(_addr_cache) >= _ADDR_CACHE_MAX:
        for k in list(_addr_cache.keys())[:4]:
            _addr_cache.pop(k, None)
    _addr_cache[hwnd] = ctrl


def _enum_value_controls(control, max_depth: int = 14):
    """生成器：递归 yield 可能含 URL 的控件（Edit / ComboBox / Custom 等）。"""
    if max_depth < 0:
        return
    try:
        children = control.GetChildren()
    except Exception:
        return
    for c in children:
        try:
            if c.ControlTypeName in _URL_CANDIDATE_CONTROL_TYPES:
                yield c
        except Exception:
            pass
        yield from _enum_value_controls(c, max_depth - 1)


def _safe_get_value(control) -> Optional[str]:
    """读控件的 Value（ValuePattern 优先，再退到 LegacyIAccessible）。"""
    # 1) ValuePattern.Value
    try:
        return control.GetValuePattern().Value
    except Exception:
        pass
    # 2) LegacyIAccessiblePattern.Value
    try:
        return control.GetLegacyIAccessiblePattern().Value
    except Exception:
        pass
    return None


def _find_address_edit(window, names: tuple[str, ...]):
    # 1) 精确 Name 匹配（最快）
    for name in names:
        try:
            edit = window.EditControl(Name=name)
            if edit.Exists(maxSearchSeconds=0.3):
                return edit
        except Exception:
            continue
    # 2) AutomationId 匹配（适用于翻译过的浏览器或新版 Edge）
    for aid in _CHROMIUM_AUTOMATION_IDS:
        try:
            edit = window.EditControl(AutomationId=aid)
            if edit.Exists(maxSearchSeconds=0.3):
                return edit
        except Exception:
            continue
    # 3) Name 模糊匹配：含"地址 / address / 搜索"
    try:
        edit = window.EditControl(searchDepth=20)
        if edit.Exists(maxSearchSeconds=0.3):
            n = (edit.Name or "").lower()
            if any(k in n for k in ("address", "地址", "搜索", "search", "url")):
                return edit
    except Exception:
        pass
    # 4) 兜底：枚举所有 Edit/ComboBox/Custom 控件，挑 Value 像 URL 的
    for ctrl in _enum_value_controls(window, max_depth=14):
        val = _safe_get_value(ctrl)
        if _looks_like_url(val):
            return ctrl
    return None


class _UrlRequest:
    __slots__ = ("fn", "done", "result", "error", "abandoned")

    def __init__(self, fn: Callable[[], Optional[str]]):
        self.fn = fn
        self.done = threading.Event()
        self.result: Optional[str] = None
        self.error: Optional[BaseException] = None
        self.abandoned = False


class _UrlReader:
    """单一常驻 UIA 读取线程 + 串行请求。

    旧实现每次采样都新建线程，UIA 卡死时线程被遗弃、无上限累积。
    现在挂死最多占用一个线程；持续挂死超过 _REPLACE_AFTER 秒才换一个新线程
    （旧僵尸等 UIA 调用返回后自行闲置），URL 读取期间主流程只等 timeout 秒。
    """

    _REPLACE_AFTER = 120.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: Queue = Queue(maxsize=1)
        self._thread: Optional[threading.Thread] = None
        self._busy_since: float = 0.0  # monotonic；0 = 空闲
        self._gen = 0

    def _loop(self, q: Queue, gen: int) -> None:
        # com_ctx 持有引用保持本线程 COM 初始化状态（析构时反初始化）
        com_ctx = None
        try:
            uia = _get_uia()
            com_ctx = uia.UIAutomationInitializerInThread()
        except Exception:
            pass  # comtypes 会在首次调用时按需初始化 COM
        logger.debug("url reader thread started (gen=%d, com=%s)", gen, com_ctx is not None)
        while True:
            req: _UrlRequest = q.get()
            if not req.abandoned:
                try:
                    req.result = req.fn()
                except BaseException as e:
                    req.error = e
            req.done.set()
            with self._lock:
                if self._gen == gen:
                    self._busy_since = 0.0

    def _start_thread_locked(self) -> None:
        self._gen += 1
        self._queue = Queue(maxsize=1)
        self._thread = threading.Thread(
            target=self._loop, args=(self._queue, self._gen),
            daemon=True, name="UrlReader",
        )
        self._thread.start()
        self._busy_since = 0.0

    def read(self, fn: Callable[[], Optional[str]], timeout: float = 3.0) -> Optional[str]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._start_thread_locked()
            elif self._busy_since:
                if monotonic() - self._busy_since > self._REPLACE_AFTER:
                    logger.warning("url reader hung > %.0fs, replacing thread", self._REPLACE_AFTER)
                    self._start_thread_locked()
                else:
                    # 上一个请求还挂在 UIA 调用里，跳过本次采样
                    logger.debug("url reader busy, skipping this sample")
                    return None
            req = _UrlRequest(fn)
            self._busy_since = monotonic()
            self._queue.put_nowait(req)
        if not req.done.wait(timeout):
            req.abandoned = True
            logger.debug("browser url read timed out")
            return None
        if req.error:
            logger.debug("browser url read error: %s", req.error)
            return None
        return req.result


_url_reader = _UrlReader()


def _read_url_with_timeout(fn: Callable[[], Optional[str]], timeout: float = 3.0) -> Optional[str]:
    """在常驻读取线程里执行 fn，超时返回 None。UI Automation 偶发会卡死。"""
    return _url_reader.read(fn, timeout)


def _normalize(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # 地址栏里有时是 "github.com/foo" 这种没协议的形式
    if "://" not in raw and not raw.startswith("about:") and not raw.startswith("chrome:"):
        # 简单判断：第一个 / 之前像域名（含点或 localhost）
        head = raw.split("/", 1)[0]
        if "." in head or head == "localhost":
            raw = "https://" + raw
    return raw


def _get_chromium(hwnd: int) -> Optional[str]:
    uia = _get_uia()
    # 0) 命中缓存就直接读，省去遍历整个 UI 树
    cached = _addr_cache.get(hwnd)
    if cached is not None:
        try:
            val = _safe_get_value(cached)
            if val:
                return val
        except Exception:
            pass
        _addr_cache.pop(hwnd, None)  # 缓存失效，丢掉

    # 1) 先发 WM_GETOBJECT 唤醒 a11y
    _nudge_accessibility(hwnd)
    window = uia.ControlFromHandle(hwnd)
    if window is None:
        return None
    edit = _find_address_edit(window, _CHROMIUM_NAMES)
    if edit is not None:
        val = _safe_get_value(edit)
        if val:
            _cache_put(hwnd, edit)
            return val
    # 2) 第一次没读到 → 再 nudge + 短延迟 + 重试一次
    _nudge_accessibility(hwnd)
    time.sleep(0.25)
    window = uia.ControlFromHandle(hwnd)
    if window is None:
        return None
    edit = _find_address_edit(window, _CHROMIUM_NAMES)
    if edit is None:
        return None
    val = _safe_get_value(edit)
    if val:
        _cache_put(hwnd, edit)
    return val


def _get_firefox(hwnd: int) -> Optional[str]:
    uia = _get_uia()
    cached = _addr_cache.get(hwnd)
    if cached is not None:
        try:
            val = _safe_get_value(cached)
            if val:
                return val
        except Exception:
            pass
        _addr_cache.pop(hwnd, None)
    window = uia.ControlFromHandle(hwnd)
    if window is None:
        return None
    edit = _find_address_edit(window, _FIREFOX_NAMES)
    if edit is None:
        return None
    val = _safe_get_value(edit)
    if val:
        _cache_put(hwnd, edit)
    return val


_DISPATCH = {
    "chrome.exe": _get_chromium,
    "msedge.exe": _get_chromium,
    "brave.exe": _get_chromium,
    "vivaldi.exe": _get_chromium,
    "opera.exe": _get_chromium,
    "firefox.exe": _get_firefox,
}


def get_active_url(hwnd: int, process_name: str) -> Optional[str]:
    fn = _DISPATCH.get(process_name.lower())
    if fn is None:
        return None
    raw = _read_url_with_timeout(lambda: fn(hwnd))
    return _normalize(raw)
