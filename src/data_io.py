"""数据导出 / 导入（zip）+ WebDAV 同步（裸 HTTP，不引入额外库）。"""
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

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
    """极简 WebDAV 客户端：PUT / GET / PROPFIND / MKCOL。不依赖第三方 WebDAV 库。

    适配坚果云：坚果云 `/dav/` 根目录不允许 PUT；必须建子目录后再上传。
    本客户端默认在用户给的 URL 下追加 `WorkingorFishing/` 子目录，
    并在首次上传前自动 MKCOL 该子目录。
    """

    SUB_FOLDER = "WorkingorFishing"

    def __init__(self, url: str, username: str, password: str, timeout: float = 30.0):
        if not url:
            raise ValueError("WebDAV URL 为空")
        base = url.rstrip("/") + "/"
        # 如果用户给的 URL 已经以 WorkingorFishing/ 结尾就尊重；否则自动追加
        lower = base.lower().rstrip("/")
        if lower.endswith("/" + self.SUB_FOLDER.lower()):
            self.url = base
        else:
            self.url = base + self.SUB_FOLDER + "/"
        self.auth = (username, password)
        self.timeout = timeout
        self._dir_ensured = False

    def _ensure_dir(self) -> tuple[bool, str]:
        """对目标子目录做 MKCOL。已存在 (405/301) 也算成功。"""
        if self._dir_ensured:
            return True, "OK"
        try:
            r = httpx.request(
                "MKCOL", self.url, auth=self.auth, timeout=self.timeout,
            )
            # 201 Created / 200 OK：创建成功
            # 405 Method Not Allowed / 301 Moved Permanently：目录已存在
            if r.status_code in (200, 201, 204, 301, 405):
                self._dir_ensured = True
                return True, f"目录就绪 (HTTP {r.status_code})"
            if r.status_code == 401:
                return False, "认证失败 (HTTP 401)"
            return False, f"MKCOL 失败 HTTP {r.status_code}"
        except Exception as e:
            return False, f"MKCOL 异常: {e}"

    def test(self) -> tuple[bool, str]:
        try:
            r = httpx.request(
                "PROPFIND",
                self.url.rsplit("/", 2)[0] + "/",  # 用父级测试，避免子目录未建时直接 404
                auth=self.auth,
                headers={"Depth": "0"},
                timeout=self.timeout,
            )
            if r.status_code in (200, 207):
                # 顺手把子目录建好
                ok, msg = self._ensure_dir()
                return True, f"连接成功 (HTTP {r.status_code})，{msg}"
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
        # 推送前确保目录存在
        ok, msg = self._ensure_dir()
        if not ok:
            return False, f"{remote_name}: {msg}"
        try:
            size = local_path.stat().st_size
            target = self.url + remote_name
            # 传文件对象流式上传（httpx 对可 seek 文件自动设 Content-Length），
            # 避免把整个 activity.db（可达上百 MB）读进内存
            with open(local_path, "rb") as f:
                r = httpx.put(
                    target, content=f,
                    auth=self.auth, timeout=self.timeout,
                )
                if r.status_code in (200, 201, 204):
                    return True, f"{remote_name}: 上传 OK ({size} bytes)"
                # 404 兜底：再尝试一次 MKCOL 然后重 PUT
                if r.status_code == 404:
                    self._dir_ensured = False
                    self._ensure_dir()
                    f.seek(0)
                    r = httpx.put(target, content=f, auth=self.auth, timeout=self.timeout)
                    if r.status_code in (200, 201, 204):
                        return True, f"{remote_name}: 上传 OK ({size} bytes)"
            return False, f"{remote_name}: HTTP {r.status_code}"
        except Exception as e:
            return False, f"{remote_name}: {e}"

    def download(self, remote_name: str, local_path: Path) -> tuple[bool, str]:
        try:
            # 流式下载分块落盘，避免大文件整个读进内存
            with httpx.stream(
                "GET",
                self.url + remote_name,
                auth=self.auth,
                timeout=self.timeout,
            ) as r:
                if r.status_code == 200:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with open(local_path, "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
                            written += len(chunk)
                    return True, f"{remote_name}: 下载 OK ({written} bytes)"
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


def webdav_download_to_dir(
    client: WebDAVClient, dst_dir: Path, names: tuple[str, ...] = SYNC_FILES
) -> tuple[dict[str, Path], list[str]]:
    """把远端文件下到临时目录，返回 ({name: 本地路径}, 消息列表)。
    远端不存在 (404) 视为正常跳过。合并同步用。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    got: dict[str, Path] = {}
    messages: list[str] = []
    for name in names:
        dst = dst_dir / name
        ok, msg = client.download(name, dst)
        messages.append(msg)
        if ok:
            got[name] = dst
    return got, messages


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
