"""键鼠空闲检测。Windows 专用：GetLastInputInfo。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


def idle_seconds() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = _kernel32.GetTickCount()
    # GetTickCount 为 32 位毫秒，约 49.7 天回绕一次；按模减避免回绕后差值变负
    return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0


def is_idle(threshold_seconds: float) -> bool:
    return idle_seconds() >= threshold_seconds
