"""设置文件 + Windows 注册表自启 + 窗口几何持久化辅助。"""
from __future__ import annotations

import base64
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QWidget

from .paths import settings_file

logger = logging.getLogger(__name__)

_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "WorkingorFishing"

DEFAULT_AI_PROMPT = (
    "你是一个活动分类助手。判断以下桌面活动属于「work」、「fishing」还是「neutral」。\n"
    "- work：编码、技术文档、写作、设计、办公、邮件、技术学习、工作沟通\n"
    "- fishing：娱乐视频、社交媒体、游戏、购物、闲逛\n"
    "- neutral：无法明确归类、系统页面、桌面切换\n\n"
    "进程: {process}\n"
    "窗口标题: {title}\n"
    "URL: {url}\n\n"
    "只返回 JSON（不要 markdown 围栏），格式：\n"
    "{{\n"
    '  "category": "work" | "fishing" | "neutral",\n'
    '  "reason": "一句话理由",\n'
    '  "suggested_rule": {{\n'
    '    "process": "进程名或 null",\n'
    '    "title_regex": "标题正则或 null",\n'
    '    "url_regex": "URL 正则或 null"\n'
    "  }}\n"
    "}}\n"
)


@dataclass
class FloatingSettings:
    enabled: bool = True
    x: int = 100
    y: int = 100
    opacity: float = 0.85
    click_through: bool = False
    width: int = 80              # 状态条宽
    height: int = 40             # 状态条高
    font_color: str = "white"    # white / black / auto（自适应桌面背景）


@dataclass
class AISettings:
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    batch_size: int = 5
    interval_seconds: int = 60
    prompt_template: str = DEFAULT_AI_PROMPT


@dataclass
class WebDAVSettings:
    url: str = ""
    username: str = ""
    password: str = ""
    last_push: float = 0.0
    last_pull: float = 0.0


@dataclass
class Settings:
    sample_interval_seconds: int = 10
    idle_threshold_seconds: int = 300
    autostart: bool = False
    paused: bool = False
    floating_window: FloatingSettings = field(default_factory=FloatingSettings)
    ai: AISettings = field(default_factory=AISettings)
    webdav: WebDAVSettings = field(default_factory=WebDAVSettings)
    # 窗口几何：name → base64(saveGeometry)
    window_geometry: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        path = settings_file()
        if not path.exists():
            s = cls()
            s.save()
            return s
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.exception("failed to load settings, using defaults")
            return cls()

        fw_data = data.get("floating_window") or {}
        fw = FloatingSettings(
            enabled=bool(fw_data.get("enabled", True)),
            x=int(fw_data.get("x", 100)),
            y=int(fw_data.get("y", 100)),
            opacity=float(fw_data.get("opacity", 0.85)),
            click_through=bool(fw_data.get("click_through", False)),
            width=int(fw_data.get("width", 80)),
            height=int(fw_data.get("height", 40)),
            font_color=str(fw_data.get("font_color") or "white"),
        )

        ai_data = data.get("ai") or {}
        ai = AISettings(
            enabled=bool(ai_data.get("enabled", False)),
            base_url=str(ai_data.get("base_url", "https://api.openai.com/v1")),
            api_key=str(ai_data.get("api_key", "")),
            model=str(ai_data.get("model", "gpt-4o-mini")),
            temperature=float(ai_data.get("temperature", 0.2)),
            batch_size=int(ai_data.get("batch_size", 5)),
            interval_seconds=int(ai_data.get("interval_seconds", 60)),
            prompt_template=str(ai_data.get("prompt_template") or DEFAULT_AI_PROMPT),
        )

        geom = data.get("window_geometry") or {}
        if not isinstance(geom, dict):
            geom = {}

        wd_data = data.get("webdav") or {}
        webdav = WebDAVSettings(
            url=str(wd_data.get("url", "")),
            username=str(wd_data.get("username", "")),
            password=str(wd_data.get("password", "")),
            last_push=float(wd_data.get("last_push", 0.0)),
            last_pull=float(wd_data.get("last_pull", 0.0)),
        )

        return cls(
            sample_interval_seconds=int(data.get("sample_interval_seconds", 10)),
            idle_threshold_seconds=int(data.get("idle_threshold_seconds", 300)),
            autostart=bool(data.get("autostart", False)),
            paused=bool(data.get("paused", False)),
            floating_window=fw,
            ai=ai,
            webdav=webdav,
            window_geometry=geom,
        )

    def apply_from_file(self) -> None:
        """从 settings.yaml 重新读取，原地更新当前实例的所有字段。

        用于数据导入 / WebDAV 拉取后，避免外部持有的引用变野指针。"""
        fresh = Settings.load()
        self.sample_interval_seconds = fresh.sample_interval_seconds
        self.idle_threshold_seconds = fresh.idle_threshold_seconds
        self.autostart = fresh.autostart
        self.paused = fresh.paused
        # 子 dataclass 原地 mutate
        self.floating_window.__dict__.update(fresh.floating_window.__dict__)
        self.ai.__dict__.update(fresh.ai.__dict__)
        self.webdav.__dict__.update(fresh.webdav.__dict__)
        self.window_geometry.clear()
        self.window_geometry.update(fresh.window_geometry)

    def save(self) -> None:
        path = settings_file()
        data: dict[str, Any] = asdict(self)
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


# --- 窗口几何 ---

def save_geometry(settings: Settings, name: str, widget: QWidget) -> None:
    """把 widget 的 saveGeometry() 编码后写入 settings.window_geometry[name]，并落盘。"""
    try:
        ba = bytes(widget.saveGeometry())
        settings.window_geometry[name] = base64.b64encode(ba).decode("ascii")
        settings.save()
    except Exception:
        logger.exception("save_geometry(%s) failed", name)


def restore_geometry(settings: Settings, name: str, widget: QWidget) -> bool:
    s = settings.window_geometry.get(name)
    if not s:
        return False
    try:
        ba = QByteArray(base64.b64decode(s))
        return bool(widget.restoreGeometry(ba))
    except Exception:
        logger.exception("restore_geometry(%s) failed", name)
        return False


# --- 自启 ---

def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    py_exec = str(pyw if pyw.exists() else sys.executable)
    main_py = Path(__file__).resolve().parent / "main.py"
    return f'"{py_exec}" "{main_py}"'


def set_autostart(enabled: bool) -> None:
    import winreg
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _AUTOSTART_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _AUTOSTART_NAME)
            except FileNotFoundError:
                pass


def get_autostart() -> bool:
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _AUTOSTART_NAME)
            return True
    except FileNotFoundError:
        return False
