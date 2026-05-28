"""统一的路径解析：开发模式、便携模式、安装模式。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """可执行文件（或入口脚本）所在目录。"""
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _is_portable() -> bool:
    """打包成 exe 且同目录可写视为便携模式。开发模式始终走项目目录。"""
    if not _frozen():
        return True
    probe = app_dir() / ".write_probe"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def data_root() -> Path:
    """数据/配置根目录。便携模式与 exe 同级，安装模式落 %APPDATA%。"""
    if _is_portable():
        return app_dir()
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "WorkingorFishing"


def config_dir() -> Path:
    p = data_root() / "config"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = data_root() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def assets_dir() -> Path:
    """打包后 PyInstaller 把 assets 解压到 _MEIPASS；开发模式走仓库 assets/。"""
    if _frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def bundled_default_rules() -> Path:
    if _frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "default_rules.yaml"
    return Path(__file__).resolve().parent / "default_rules.yaml"


def settings_file() -> Path:
    return config_dir() / "settings.yaml"


def rules_file() -> Path:
    return config_dir() / "rules.yaml"


def db_file() -> Path:
    return data_dir() / "activity.db"


def icon_file() -> Path:
    return assets_dir() / "icon.ico"


def floating_image(state: str) -> Path:
    """悬浮窗图片主题：assets/floating/float_<state>.png"""
    # 状态归一：work/fishing/neutral/idle/paused，paused 复用 idle
    s = state if state in ("work", "fishing", "neutral", "idle") else "neutral"
    if state == "paused":
        s = "idle"
    return assets_dir() / "floating" / f"float_{s}.png"
