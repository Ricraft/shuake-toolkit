# encoding=utf-8
"""统一启动器自我更新：检查 GitHub Release、校验下载并生成进程外应用脚本。

设计要点（对应自更新可行性分析的结论）：

* 运行中的启动器**绝不覆盖自己**。本模块只下载、校验并把新版本解压到
  ``<base>/.update-staging/<version>/``，随后生成一个进程外的 ``应用更新.cmd``，
  由用户在退出启动器之后自行运行。
* 替换白名单与 ``scripts/build_launcher_release.py`` 的发布契约保持一致：
  ``统一启动器.py`` / ``requirements.txt`` / ``src`` / ``web``。``Yatori/``、
  ``Autovisor/``、``data/``、``logs/`` 与所有用户配置文件都不在替换范围内。
* 校验优先使用 GitHub Release 资产的 ``digest``（``sha256:<hex>``），缺失时回退
  下载 ``launcher-manifest.json`` 取 ``sha256`` + ``size`` 双重校验；两者都拿不到
  则拒绝安装。
* 版本比较复用 :class:`src.core_manager.CoreManager` 已有的语义版本实现，避免
  出现第二份版本解析逻辑（该模块本身不做任何修改）。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime

try:
    from .core_manager import CoreManager
except ImportError:  # pragma: no cover - direct maintenance execution
    from core_manager import CoreManager


LAUNCHER_REPO = "Ricraft/shuake-toolkit"
LAUNCHER_RELEASES_API_URL = (
    f"https://api.github.com/repos/{LAUNCHER_REPO}/releases?per_page=20"
)

# 发布侧契约：launcher-<version>.zip + launcher-manifest.json
LAUNCHER_ASSET_PREFIX = "launcher-"
LAUNCHER_ASSET_SUFFIX = ".zip"
LAUNCHER_MANIFEST_NAME = "launcher-manifest.json"

# 与 scripts/build_launcher_release.py 的 WHITELIST / manifest.replaceWhitelist 一致。
REPLACE_WHITELIST = ("统一启动器.py", "requirements.txt", "src", "web")

# 永不参与替换；仅用于生成脚本中的说明文字与回归断言。
PRESERVED_PATHS = (
    "Yatori",
    "Autovisor",
    "data",
    "logs",
    "configs.ini",
    "qb_config.json",
    "launcher_preferences.json",
)

UPDATE_STAGING_DIR_NAME = ".update-staging"
UPDATE_BACKUP_DIR_NAME = ".update-backup"
APPLY_SCRIPT_NAME = "应用更新.cmd"

# 更新包内必须具备的文件，用于替换前的暂存校验。
# 必须包含首屏入口页：替换后的校验只能证明目标文件存在，而旧版本的入口页
# 本来就存在，缺少它的残缺包会静默退化成新 app.js + 旧 HTML 的混合安装。
REQUIRED_STAGED_FILES = (
    "统一启动器.py",
    os.path.join("src", "web_action_service.py"),
    os.path.join("web", "app.js"),
    os.path.join("web", "现代启动器_UI_预览.html"),
)

USER_AGENT = "shuake-toolkit-launcher-updater/1.0"
_SHA256_HEX_RE = re.compile(r"[0-9a-fA-F]{64}")
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SSL_CONTEXT = ssl.create_default_context()


def _run_in_background(target):
    threading.Thread(target=target, daemon=True).start()


# --------------------------------------------------------------------------- #
# 版本与摘要工具
# --------------------------------------------------------------------------- #

def parse_launcher_version(value):
    """Parse a version through core_manager's semantic parser (None if invalid)."""
    parser = getattr(CoreManager, "_parse_yatori_version", None)
    if not callable(parser):
        return None
    try:
        return parser(value)
    except Exception:  # pragma: no cover - defensive
        return None


def compare_launcher_versions(left, right):
    """Semantic comparison; returns 1/0/-1, or None when unparseable.

    Delegates to ``CoreManager._compare_yatori_versions`` so that the launcher
    update shares exactly one version implementation with core updates.
    """
    comparator = getattr(CoreManager, "_compare_yatori_versions", None)
    if not callable(comparator):
        return None
    try:
        return comparator(left, right)
    except Exception:  # pragma: no cover - defensive
        return None


def normalize_sha256_digest(value):
    """Normalize GitHub's ``sha256:<hex>`` asset digest."""
    if not isinstance(value, str):
        return None
    algorithm, separator, digest = value.strip().partition(":")
    if separator != ":" or algorithm.lower() != "sha256":
        return None
    if not _SHA256_HEX_RE.fullmatch(digest):
        return None
    return digest.lower()


def normalize_sha256_hex(value):
    """Accept a bare 64-char hex sha256 (launcher-manifest.json form)."""
    if not isinstance(value, str):
        return None
    digest = value.strip()
    if not _SHA256_HEX_RE.fullmatch(digest):
        return None
    return digest.lower()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract_zip(zip_ref, destination):
    """Extract a downloaded archive without allowing path traversal."""
    destination = os.path.abspath(destination)
    for member in zip_ref.infolist():
        target = os.path.abspath(os.path.join(destination, member.filename))
        if os.path.commonpath((destination, target)) != destination:
            raise ValueError(f"压缩包包含越界路径: {member.filename}")
    zip_ref.extractall(destination)


def format_release_notes(body, *, max_lines=14, max_chars=900):
    """Condense a GitHub Release body for the confirmation dialog."""
    text = str(body or "").strip()
    if not text:
        return ""
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    summary = "\n".join(lines).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."
    return summary


def safe_version_dirname(version):
    return _UNSAFE_FILENAME_RE.sub("_", str(version or "").strip()).strip("_") or "unknown"


def select_latest_release(releases):
    """Newest non-draft release whose tag is a parseable version."""
    best = None
    best_version = None
    best_published = ""
    for release in releases or []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name") or "").strip()
        if not tag or parse_launcher_version(tag) is None:
            continue
        published = str(release.get("published_at") or "")
        if best is None:
            best, best_version, best_published = release, tag, published
            continue
        comparison = compare_launcher_versions(tag, best_version)
        if comparison is None:
            continue
        if comparison > 0 or (comparison == 0 and published > best_published):
            best, best_version, best_published = release, tag, published
    return best


def _usable_asset(asset):
    url = asset.get("browser_download_url")
    return isinstance(url, str) and bool(url.strip())


def select_release_asset(release, version):
    """Prefer ``launcher-<version>.zip``, then any ``launcher-*.zip``."""
    assets = [
        asset
        for asset in (release.get("assets") or [])
        if isinstance(asset, dict)
    ]
    preferred = f"{LAUNCHER_ASSET_PREFIX}{version}{LAUNCHER_ASSET_SUFFIX}"
    for asset in assets:
        if str(asset.get("name") or "") == preferred and _usable_asset(asset):
            return asset
    for asset in assets:
        name = str(asset.get("name") or "")
        if (
            name.startswith(LAUNCHER_ASSET_PREFIX)
            and name.endswith(LAUNCHER_ASSET_SUFFIX)
            and _usable_asset(asset)
        ):
            return asset
    return None


def find_manifest_asset(release):
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "") != LAUNCHER_MANIFEST_NAME:
            continue
        if _usable_asset(asset):
            return asset
    return None


def build_apply_script(
    *,
    version,
    current_version,
    launcher_pid=0,
    payload_dir_name=None,
):
    """Build the out-of-process apply script (CRLF, UTF-8).

    Every path is quoted; ``ROOT`` is derived from ``%~dp0..`` so the script
    keeps working when the launcher directory contains spaces or CJK
    characters and even if the whole folder is moved after staging.
    """
    payload_name = payload_dir_name or safe_version_dirname(version)
    try:
        pid_text = str(int(launcher_pid or 0))
    except (TypeError, ValueError):
        pid_text = "0"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal enableextensions enabledelayedexpansion",
        "rem ==============================================================",
        "rem  统一启动器更新脚本",
        f"rem  版本: {current_version} 到 {version}",
        "rem  替换白名单: 统一启动器.py requirements.txt src web",
        "rem  Yatori Autovisor data logs 与全部用户配置文件不会被修改",
        "rem  先备份到 .update-backup，替换后校验关键文件，失败自动回滚",
        "rem ==============================================================",
        "",
        'for %%I in ("%~dp0..") do set "ROOT=%%~fI"',
        f'set "PAYLOAD=%~dp0{payload_name}"',
        f'set "BACKUP=%ROOT%\\{UPDATE_BACKUP_DIR_NAME}\\{stamp}"',
        f'set "VERSION={version}"',
        f'set "CURRENT={current_version}"',
        f'set "LAUNCHER_PID={pid_text}"',
        'set "FAILED=0"',
        "",
        "echo.",
        'echo [统一启动器更新] 当前版本 "%CURRENT%" 到 "%VERSION%"',
        'echo 安装目录: "%ROOT%"',
        "echo.",
        "",
        "rem ---- 等待启动器退出，避免替换正在使用的文件 ----",
        "set /a WAITED=0",
        ":wait_launcher",
        'if "%LAUNCHER_PID%"=="0" goto ready',
        'tasklist /FI "PID eq %LAUNCHER_PID%" /NH 2>nul | findstr /R "[0-9]" >nul',
        "if not errorlevel 1 (",
        "    set /a WAITED+=1",
        "    if !WAITED! GEQ 60 (",
        '        echo [错误] 统一启动器仍在运行，PID %LAUNCHER_PID%。请先关闭启动器，再重新运行本脚本。',
        "        goto fail",
        "    )",
        "    timeout /t 1 /nobreak >nul",
        "    goto wait_launcher",
        ")",
        "",
        ":ready",
        'if not exist "%PAYLOAD%\\统一启动器.py" (',
        '    echo [错误] 暂存目录缺少更新内容: "%PAYLOAD%"',
        "    goto fail",
        ")",
        "",
        'echo 正在备份当前版本到 "%BACKUP%" ...',
        'mkdir "%BACKUP%" >nul 2>nul',
        'if exist "%ROOT%\\src" robocopy "%ROOT%\\src" "%BACKUP%\\src" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul',
        "if errorlevel 8 set \"FAILED=1\"",
        'if exist "%ROOT%\\web" robocopy "%ROOT%\\web" "%BACKUP%\\web" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul',
        "if errorlevel 8 set \"FAILED=1\"",
        'if exist "%ROOT%\\统一启动器.py" copy /Y "%ROOT%\\统一启动器.py" "%BACKUP%\\统一启动器.py" >nul',
        'if exist "%ROOT%\\requirements.txt" copy /Y "%ROOT%\\requirements.txt" "%BACKUP%\\requirements.txt" >nul',
        'if "%FAILED%"=="1" (',
        "    echo [错误] 备份失败，已中止更新，未修改任何文件。",
        "    goto fail",
        ")",
        "",
        "echo 正在替换白名单文件 ...",
        'robocopy "%PAYLOAD%\\src" "%ROOT%\\src" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul',
        "if errorlevel 8 set \"FAILED=1\"",
        'robocopy "%PAYLOAD%\\web" "%ROOT%\\web" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul',
        "if errorlevel 8 set \"FAILED=1\"",
        'copy /Y "%PAYLOAD%\\统一启动器.py" "%ROOT%\\统一启动器.py" >nul',
        "if errorlevel 1 set \"FAILED=1\"",
        'if exist "%PAYLOAD%\\requirements.txt" copy /Y "%PAYLOAD%\\requirements.txt" "%ROOT%\\requirements.txt" >nul',
        "",
        "rem ---- 校验关键文件，任一缺失即回滚 ----",
        'if not exist "%ROOT%\\统一启动器.py" set "FAILED=1"',
        'if not exist "%ROOT%\\src\\web_action_service.py" set "FAILED=1"',
        'if not exist "%ROOT%\\web\\app.js" set "FAILED=1"',
        'if not exist "%ROOT%\\web\\现代启动器_UI_预览.html" set "FAILED=1"',
        "rem ---- 再逐字节比对：只判断存在会让残缺包静默留下旧文件（新旧混合）----",
        'fc /b "%PAYLOAD%\\统一启动器.py" "%ROOT%\\统一启动器.py" >nul 2>nul',
        'if errorlevel 1 set "FAILED=1"',
        'fc /b "%PAYLOAD%\\src\\web_action_service.py" "%ROOT%\\src\\web_action_service.py" >nul 2>nul',
        'if errorlevel 1 set "FAILED=1"',
        'fc /b "%PAYLOAD%\\web\\app.js" "%ROOT%\\web\\app.js" >nul 2>nul',
        'if errorlevel 1 set "FAILED=1"',
        'fc /b "%PAYLOAD%\\web\\现代启动器_UI_预览.html" "%ROOT%\\web\\现代启动器_UI_预览.html" >nul 2>nul',
        'if errorlevel 1 set "FAILED=1"',
        'if "%FAILED%"=="1" goto rollback',
        "",
        'echo [完成] 统一启动器已更新到 "%VERSION%"。',
        'echo         旧版本备份: "%BACKUP%"',
        'echo         暂存目录可在确认更新成功后删除: "%PAYLOAD%"',
        "echo         重新启动: 双击 启动器.exe 或运行 hide_run.vbs",
        "goto done",
        "",
        ":rollback",
        "echo [错误] 替换后校验失败，正在回滚到更新前版本 ...",
        'if exist "%BACKUP%\\src" robocopy "%BACKUP%\\src" "%ROOT%\\src" /MIR /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul',
        'if exist "%BACKUP%\\web" robocopy "%BACKUP%\\web" "%ROOT%\\web" /MIR /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul',
        'if exist "%BACKUP%\\统一启动器.py" copy /Y "%BACKUP%\\统一启动器.py" "%ROOT%\\统一启动器.py" >nul',
        'if exist "%BACKUP%\\requirements.txt" copy /Y "%BACKUP%\\requirements.txt" "%ROOT%\\requirements.txt" >nul',
        'echo [已回滚] 统一启动器仍为更新前版本 "%CURRENT%"。',
        "goto fail",
        "",
        ":fail",
        "echo.",
        "echo 更新未完成。请查看上面的提示后重试，或手动从 GitHub Releases 重新下载。",
        "pause",
        "endlocal",
        "exit /b 1",
        "",
        ":done",
        "echo.",
        "pause",
        "endlocal",
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


def write_apply_script(path, text):
    """Write the apply script as UTF-8 with BOM and CRLF line endings."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if text.startswith("\ufeff"):
        text = text[1:]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write(text)
    return path


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #

class LauncherUpdateService:
    """Check, verify and stage unified-launcher self-updates.

    The service is intentionally independent from :class:`CoreManager`'s core
    installer: it never replaces files, and it never touches ``Yatori/``,
    ``Autovisor/`` or user data.
    """

    def __init__(
        self,
        base_dir,
        *,
        current_version,
        log=None,
        on_result=None,
        opener=None,
        downloader=None,
        repo=LAUNCHER_REPO,
        releases_api_url=None,
        pid=None,
        request_timeout=15,
        download_timeout=120,
    ):
        self.base_dir = os.path.abspath(base_dir)
        self.current_version = str(current_version or "").strip() or "未知"
        self.repo = str(repo or LAUNCHER_REPO)
        self.releases_api_url = releases_api_url or (
            f"https://api.github.com/repos/{self.repo}/releases?per_page=20"
        )
        self.log = log if callable(log) else (lambda _message: None)
        self.on_result = on_result if callable(on_result) else None
        self.opener = opener or self._default_opener
        self.downloader = downloader or self._default_downloader
        self.pid = self._coerce_pid(pid)
        self.request_timeout = request_timeout
        self.download_timeout = download_timeout
        self._lock = threading.RLock()
        self._pending_confirmation = None
        self.installing = False

    # -------------------------------------------------- infrastructure

    @staticmethod
    def _coerce_pid(pid):
        if pid is None:
            return os.getpid()
        try:
            return int(pid)
        except (TypeError, ValueError):
            return 0

    def _log(self, message):
        try:
            self.log(str(message))
        except Exception:  # pragma: no cover - logging must never break updates
            pass

    def _progress(self, downloaded, total):
        if total:
            percent = (downloaded / total) * 100
            self._log(
                f"统一启动器更新下载中: {percent:.1f}% ({downloaded}/{total} bytes)"
            )
        else:
            self._log(f"统一启动器更新下载中: {downloaded} bytes")

    @staticmethod
    def _default_opener(url, timeout=15):
        """Return ``(status_code, body_bytes)`` without raising on HTTP errors."""
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github.v3+json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=_SSL_CONTEXT
            ) as response:
                status = int(getattr(response, "status", 200) or 200)
                return status, response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read() or b""
            except Exception:  # pragma: no cover - defensive
                body = b""
            return int(exc.code), body

    def _default_downloader(self, url, target_path, progress=None, timeout=120):
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=_SSL_CONTEXT
            ) as response:
                total = int(response.headers.get("Content-Length", 0) or 0) or None
                downloaded = 0
                with open(target_path, "wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if callable(progress):
                            progress(downloaded, total)
        except Exception as exc:
            self._log(f"下载更新资源失败: {exc}")
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
            except OSError:
                pass
            return False
        return True

    # -------------------------------------------------- checking

    def _fetch_releases(self):
        try:
            status, body = self.opener(
                self.releases_api_url, self.request_timeout
            )
        except Exception as exc:
            return None, f"无法连接 GitHub 检查更新：{exc}"
        if status == 403:
            return None, (
                "GitHub API 访问被拒绝（403，匿名请求可能已触发限流），"
                "请稍后再试，或直接打开仓库的 Releases 页面查看。"
            )
        if status == 404:
            return None, f"未找到 {self.repo} 的发布信息（404）。"
        if status and int(status) >= 400:
            return None, f"GitHub API 返回 HTTP {status}，暂时无法获取更新信息。"
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            return None, f"GitHub API 返回内容无法解析：{exc}"
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return None, "GitHub API 返回了意外的数据结构。"
        return data, ""

    def _build_release_info(self, release, version):
        asset = select_release_asset(release, version)
        manifest_asset = find_manifest_asset(release)
        return {
            "version": version,
            "download_url": (
                asset.get("browser_download_url") if asset else None
            ),
            "asset_name": (asset.get("name") if asset else None),
            "digest": (asset.get("digest") if asset else None),
            "asset_size": (asset.get("size") if asset else None),
            "asset_id": (asset.get("id") if asset else None),
            "manifest_url": (
                manifest_asset.get("browser_download_url")
                if manifest_asset
                else None
            ),
            "published_at": release.get("published_at"),
            "body": release.get("body") or "",
            "html_url": release.get("html_url") or "",
        }

    def check_for_update(self):
        """Return a status dict; never raises for network/parse problems."""
        current = self.current_version
        releases, error = self._fetch_releases()
        if error:
            return {"status": "error", "currentVersion": current, "message": error}
        if not releases:
            return {
                "status": "no_release",
                "currentVersion": current,
                "message": (
                    f"暂无可用版本：{self.repo} 还没有发布任何 Release。"
                    f"当前版本 {current}。"
                ),
            }
        release = select_latest_release(releases)
        if release is None:
            return {
                "status": "no_release",
                "currentVersion": current,
                "message": (
                    f"暂无可用版本：{self.repo} 的 Release 中没有可解析的版本标签。"
                    f"当前版本 {current}。"
                ),
            }
        version = str(release.get("tag_name") or "").strip()
        comparison = compare_launcher_versions(version, current)
        info = self._build_release_info(release, version)
        if comparison is None:
            return {
                "status": "error",
                "currentVersion": current,
                "latestVersion": version,
                "message": (
                    f"无法比较版本：远端 {version} 或本地 {current} 不是可解析的版本号。"
                ),
            }
        if comparison > 0:
            return {
                "status": "update_available",
                "currentVersion": current,
                "latestVersion": version,
                "message": f"发现统一启动器新版本 {version}（当前 {current}）。",
                "release": info,
            }
        if comparison == 0:
            return {
                "status": "up_to_date",
                "currentVersion": current,
                "latestVersion": version,
                "message": f"当前已是最新版本 ({current})。",
                "release": info,
            }
        return {
            "status": "local_newer",
            "currentVersion": current,
            "latestVersion": version,
            "message": f"本地版本 ({current}) 高于远端版本 ({version})，已跳过降级。",
            "release": info,
        }

    def prepare_update_confirmation(self):
        """Check for updates and return either a dialog or a readable result."""
        with self._lock:
            if self.installing:
                return {"ok": False, "message": "更新正在进行中，请勿重复操作"}
        self._log("正在检查统一启动器更新...")
        result = self.check_for_update()
        status = result.get("status")
        if status == "error":
            with self._lock:
                self._pending_confirmation = None
            self._log(f"检查统一启动器更新失败: {result.get('message')}")
            return {"ok": False, "message": result.get("message")}
        if status in ("no_release", "up_to_date", "local_newer"):
            with self._lock:
                self._pending_confirmation = None
            message = result.get("message") or ""
            self._log(message)
            return {
                "ok": True,
                "upToDate": status != "no_release",
                "noRelease": status == "no_release",
                "localNewer": status == "local_newer",
                "currentVersion": result.get("currentVersion"),
                "latestVersion": result.get("latestVersion"),
                "message": message,
                "toast": message,
                "toastType": "info",
            }

        release = result.get("release") or {}
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._pending_confirmation = {
                "token": token,
                "release": copy.deepcopy(release),
                "currentVersion": result.get("currentVersion"),
            }
        dialog = self._build_dialog(result, release, token)
        self._log(
            "发现统一启动器新版本 "
            f"{dialog['latestVersion']}（当前 {dialog['currentVersion']}），"
            "等待用户确认更新。"
        )
        return {
            "ok": True,
            "updateDialog": dialog,
            "toast": "已获取统一启动器更新说明，请确认是否更新",
            "toastType": "info",
        }

    def _build_dialog(self, result, release, token):
        notes = format_release_notes(release.get("body"))
        current = result.get("currentVersion")
        latest = result.get("latestVersion")
        return {
            "core": "launcher",
            "installed": True,
            "currentVersion": current,
            "latestVersion": latest,
            "assetName": release.get("asset_name") or "未提供",
            "publishedAt": release.get("published_at") or "",
            "releaseNotes": notes or "该版本暂未提供更新说明。",
            "releaseNotesAvailable": bool(notes),
            "confirmationToken": token,
            "downloadable": bool(release.get("download_url")),
            "summary": (
                f"将从 {current} 更新到 {latest}。确认后只下载并暂存新版本，"
                "不会在运行中覆盖当前文件。"
            ),
        }

    # -------------------------------------------------- install

    def _claim_confirmation(self, confirmation_token):
        """Validate and consume the one-shot confirmation (atomic)."""
        with self._lock:
            pending = self._pending_confirmation
            expected = (pending or {}).get("token")
            if (
                not isinstance(confirmation_token, str)
                or not confirmation_token
                or not isinstance(expected, str)
                or not secrets.compare_digest(confirmation_token, expected)
            ):
                return None
            if self.installing:
                return None
            self._pending_confirmation = None
            self.installing = True
            release = copy.deepcopy(pending.get("release") or {})
            current = pending.get("currentVersion") or self.current_version
        return release, current

    def install_confirmed(self, confirmation_token, *, progress=None):
        """Download, verify, stage and generate the apply script (blocking)."""
        claimed = self._claim_confirmation(confirmation_token)
        if claimed is None:
            return {
                "ok": False,
                "message": (
                    "更新确认已失效，请重新点击「检查统一启动器更新」并核对版本说明。"
                ),
            }
        release, current = claimed
        try:
            return self._install(release, current, progress=progress)
        except Exception as exc:
            message = f"统一启动器更新失败: {exc}"
            self._log(f"❌ {message}")
            return {"ok": False, "message": message}
        finally:
            with self._lock:
                self.installing = False

    def install_confirmed_async(
        self,
        confirmation_token,
        *,
        progress=None,
        run_async=None,
        schedule=None,
    ):
        """Start :meth:`install_confirmed` on a background thread."""
        claimed = self._claim_confirmation(confirmation_token)
        if claimed is None:
            return False
        release, current = claimed
        callback = progress or self._progress

        def worker():
            try:
                result = self._install(release, current, progress=callback)
            except Exception as exc:
                result = {"ok": False, "message": f"统一启动器更新失败: {exc}"}
                self._log(f"❌ {result['message']}")
            finally:
                with self._lock:
                    self.installing = False
            self._deliver_result(result, schedule)

        (run_async or _run_in_background)(worker)
        return True

    def _deliver_result(self, result, schedule):
        def notify():
            if self.on_result is not None:
                try:
                    self.on_result(result)
                except Exception:  # pragma: no cover - UI notification only
                    pass

        if schedule is None:
            notify()
            return
        try:
            schedule(0, notify)
        except Exception:  # pragma: no cover - defensive
            notify()

    def _install(self, release, current_version, *, progress=None):
        version = str(release.get("version") or "").strip()
        download_url = release.get("download_url")
        if not version:
            return {"ok": False, "message": "缺少版本信息，无法安装更新。"}
        if not download_url:
            return {
                "ok": False,
                "message": (
                    f"{version} 未提供可下载的更新资源"
                    f"（{LAUNCHER_ASSET_PREFIX}<version>{LAUNCHER_ASSET_SUFFIX}），"
                    "已拒绝自动安装；可前往 GitHub Releases 手动下载。"
                ),
            }

        # 校验来源必须先确定，避免白下载：优先 GitHub 资产摘要，其次 launcher-manifest.json。
        if (
            normalize_sha256_digest(release.get("digest")) is None
            and not release.get("manifest_url")
        ):
            return {
                "ok": False,
                "message": (
                    "更新资源既没有 GitHub 资源摘要，也没有 launcher-manifest.json，"
                    "已拒绝自动安装；可前往 GitHub Releases 手动下载并自行校验。"
                ),
            }

        staging_root = os.path.join(self.base_dir, UPDATE_STAGING_DIR_NAME)
        payload_name = safe_version_dirname(version)
        payload_dir = os.path.join(staging_root, payload_name)
        archive_path = os.path.join(staging_root, f"{payload_name}.zip")

        os.makedirs(staging_root, exist_ok=True)
        if os.path.isdir(payload_dir):
            shutil.rmtree(payload_dir, ignore_errors=True)
        os.makedirs(payload_dir, exist_ok=True)
        if os.path.exists(archive_path):
            os.remove(archive_path)

        self._log(f"开始下载统一启动器 {version} ...")
        downloaded = self.downloader(
            download_url, archive_path, progress, self.download_timeout
        )
        if not downloaded:
            return {"ok": False, "message": "更新资源下载失败，请检查网络后重试。"}

        verified, reason = self.verify_download(archive_path, release)
        if not verified:
            return {"ok": False, "message": f"更新资源校验失败：{reason}"}
        self._log("更新资源大小与 SHA-256 校验通过")

        self._log("正在解压更新包...")
        with zipfile.ZipFile(archive_path) as zip_ref:
            safe_extract_zip(zip_ref, payload_dir)

        missing = [
            relative
            for relative in REQUIRED_STAGED_FILES
            if not os.path.exists(os.path.join(payload_dir, relative))
        ]
        if missing:
            return {
                "ok": False,
                "message": "更新包缺少必要文件: " + "、".join(missing),
            }

        script_path = os.path.join(staging_root, APPLY_SCRIPT_NAME)
        write_apply_script(
            script_path,
            build_apply_script(
                version=version,
                current_version=current_version,
                launcher_pid=self.pid,
                payload_dir_name=payload_name,
            ),
        )

        message = (
            f"统一启动器 {version} 已下载并校验完成：请退出启动器后运行 "
            f"{script_path} 应用更新（脚本会先备份，失败时自动回滚）。"
        )
        self._log(f"✅ {message}")
        return {
            "ok": True,
            "staged": True,
            "version": version,
            "currentVersion": current_version,
            "stagingDir": payload_dir,
            "archivePath": archive_path,
            "scriptPath": script_path,
            "scriptName": APPLY_SCRIPT_NAME,
            "message": message,
            "toast": (
                f"更新 {version} 已准备就绪，退出启动器后运行"
                f"「{APPLY_SCRIPT_NAME}」生效"
            ),
            "toastType": "success",
        }

    # -------------------------------------------------- verification

    def verify_download(self, archive_path, release):
        """Return ``(ok, reason)`` using the asset digest, else the manifest."""
        expected_digest = normalize_sha256_digest(release.get("digest"))
        expected_size = release.get("asset_size")
        source = "GitHub Release 资源摘要"
        if expected_digest is None:
            manifest, manifest_error = self._fetch_manifest(release)
            if manifest is None:
                return False, manifest_error or (
                    "既没有 GitHub 资源摘要，也没有可用的 launcher-manifest.json，"
                    "已拒绝自动安装"
                )
            expected_digest = normalize_sha256_hex(manifest.get("sha256"))
            if expected_size in (None, ""):
                expected_size = manifest.get("size")
            source = "launcher-manifest.json"
            if expected_digest is None:
                return False, (
                    "launcher-manifest.json 中的 sha256 缺失或格式无效，"
                    "已拒绝自动安装"
                )

        try:
            expected_size = (
                int(expected_size) if expected_size not in (None, "") else None
            )
        except (TypeError, ValueError):
            return False, "更新资源大小元数据无效，已拒绝自动安装"

        if expected_size is not None and expected_size >= 0:
            actual_size = os.path.getsize(archive_path)
            if actual_size != expected_size:
                return False, (
                    "更新资源大小不匹配："
                    f"期望 {expected_size} 字节，实际 {actual_size} 字节"
                )

        actual_digest = file_sha256(archive_path)
        if not hmac.compare_digest(actual_digest, expected_digest):
            return False, "更新资源 SHA-256 校验失败，下载内容可能损坏或被替换"
        self._log(f"更新资源校验来源: {source}")
        return True, ""

    def _fetch_manifest(self, release):
        url = release.get("manifest_url")
        if not url:
            return None, (
                "Release 中没有 launcher-manifest.json，无法校验下载内容，"
                "已拒绝自动安装"
            )
        try:
            status, body = self.opener(url, self.request_timeout)
        except Exception as exc:
            return None, f"下载 launcher-manifest.json 失败：{exc}"
        if status and int(status) >= 400:
            return None, f"下载 launcher-manifest.json 失败（HTTP {status}）"
        try:
            manifest = json.loads(body.decode("utf-8"))
        except Exception as exc:
            return None, f"launcher-manifest.json 无法解析：{exc}"
        if not isinstance(manifest, dict):
            return None, "launcher-manifest.json 结构不正确"
        declared = str(manifest.get("version") or "").strip()
        expected = str(release.get("version") or "").strip()
        if declared and expected and declared != expected:
            return None, (
                f"launcher-manifest.json 版本 ({declared}) 与 Release 标签 "
                f"({expected}) 不一致"
            )
        return manifest, ""