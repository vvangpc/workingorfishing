"""数据导出 / 导入（zip）+ WebDAV 同步（裸 HTTP，不引入额外库）。"""
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 同步的三个文件
SYNC_FILES = ("activity.db", "settings.yaml", "rules.yaml")


# --- 本地导出 / 导入 ---

def export_to_zip(
    zip_path: Path,
    db_path: Path,
    settings_path: Path,
    rules_path: Path,
) -> list[str]:
    src_map = {
        "activity.db": db_path,
        "settings.yaml": settings_path,
        "rules.yaml": rules_path,
    }
    included: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, p in src_map.items():
            if p.exists():
                z.write(p, arcname=name)
                included.append(name)
    return included


def import_from_zip(
    zip_path: Path,
    db_path: Path,
    settings_path: Path,
    rules_path: Path,
) -> list[str]:
    """调用方必须先关闭 Storage 连接，然后再 reopen。"""
    dst_map = {
        "activity.db": db_path,
        "settings.yaml": settings_path,
        "rules.yaml": rules_path,
    }
    extracted: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as z:
        names = set(z.namelist())
        for name, dst in dst_map.items():
            if name not in names:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(dst, "wb") as f:
                shutil.copyfileobj(src, f)
            extracted.append(name)
    return extracted


# --- WebDAV ---

class WebDAVClient:
    """极简 WebDAV 客户端：PUT / GET / PROPFIND。不依赖第三方 WebDAV 库。"""

    def __init__(self, url: str, username: str, password: str, timeout: float = 30.0):
        if not url:
            raise ValueError("WebDAV URL 为空")
        self.url = url.rstrip("/") + "/"
        self.auth = (username, password)
        self.timeout = timeout

    def test(self) -> tuple[bool, str]:
        try:
            r = httpx.request(
                "PROPFIND",
                self.url,
                auth=self.auth,
                headers={"Depth": "0"},
                timeout=self.timeout,
            )
            if r.status_code in (200, 207):
                return True, f"连接成功 (HTTP {r.status_code})"
            if r.status_code == 401:
                return False, "认证失败 (HTTP 401)"
            if r.status_code == 404:
                return False, "路径不存在 (HTTP 404)"
            return False, f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            return False, f"网络错误: {e}"
        except Exception as e:
            return False, str(e)

    def upload(self, remote_name: str, local_path: Path) -> tuple[bool, str]:
        if not local_path.exists():
            return False, f"{remote_name}: 本地不存在"
        try:
            with open(local_path, "rb") as f:
                content = f.read()
            r = httpx.put(
                self.url + remote_name,
                content=content,
                auth=self.auth,
                timeout=self.timeout,
            )
            if r.status_code in (200, 201, 204):
                return True, f"{remote_name}: 上传 OK ({len(content)} bytes)"
            return False, f"{remote_name}: HTTP {r.status_code}"
        except Exception as e:
            return False, f"{remote_name}: {e}"

    def download(self, remote_name: str, local_path: Path) -> tuple[bool, str]:
        try:
            r = httpx.get(
                self.url + remote_name,
                auth=self.auth,
                timeout=self.timeout,
            )
            if r.status_code == 200:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(r.content)
                return True, f"{remote_name}: 下载 OK ({len(r.content)} bytes)"
            if r.status_code == 404:
                return False, f"{remote_name}: 远端不存在 (404)"
            return False, f"{remote_name}: HTTP {r.status_code}"
        except Exception as e:
            return False, f"{remote_name}: {e}"


def webdav_push(
    client: WebDAVClient,
    db_path: Path,
    settings_path: Path,
    rules_path: Path,
) -> tuple[bool, list[str]]:
    messages: list[str] = []
    all_ok = True
    for name, p in (
        ("activity.db", db_path),
        ("settings.yaml", settings_path),
        ("rules.yaml", rules_path),
    ):
        if not p.exists():
            messages.append(f"{name}: 本地不存在，跳过")
            continue
        ok, msg = client.upload(name, p)
        messages.append(msg)
        if not ok:
            all_ok = False
    return all_ok, messages


def webdav_pull(
    client: WebDAVClient,
    db_path: Path,
    settings_path: Path,
    rules_path: Path,
) -> tuple[bool, list[str]]:
    """调用方必须先关闭 Storage 连接，然后再 reopen。"""
    messages: list[str] = []
    all_ok = True
    for name, p in (
        ("activity.db", db_path),
        ("settings.yaml", settings_path),
        ("rules.yaml", rules_path),
    ):
        ok, msg = client.download(name, p)
        messages.append(msg)
        if not ok and "404" not in msg:
            all_ok = False
    return all_ok, messages
