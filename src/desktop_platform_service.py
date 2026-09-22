"""Desktop and Windows integration used by the Web-only launcher."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from urllib.parse import urlparse
from typing import Callable, Iterable, Mapping, Optional

from src.atomic_io import atomic_write_text


class DesktopPlatformService:
    """Keep platform-specific side effects out of the launcher coordinator."""

    LOG_TABS = frozenset(("system", "yatori", "autovisor"))

    # 关于页致谢外链白名单：(hostname, 允许的 path 前缀)。
    EXTERNAL_URL_ALLOWLIST = (
        ("yatori-dev.github.io", "/yatori-docs"),
        ("github.com", "/cxrunfree/autovisor"),
        ("www.github.com", "/cxrunfree/autovisor"),
        ("app.zerror.cc", "/"),
        ("wwk.lanzouj.com", "/b05evsxif"),
    )

    def __init__(
        self,
        base_dir: str,
        launcher_file: str,
        *,
        log: Optional[Callable[[str], None]] = None,
        get_window: Optional[Callable[[], object]] = None,
        sound_module=None,
        platform_name: Optional[str] = None,
        environment: Optional[Mapping[str, str]] = None,
        executable: Optional[str] = None,
        frozen: Optional[bool] = None,
        command_runner=None,
        process_launcher=None,
        path_opener=None,
        url_opener=None,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.base_dir = os.path.abspath(base_dir)
        self.launcher_file = os.path.abspath(launcher_file)
        self.log = log or (lambda _message: None)
        self.get_window = get_window or (lambda: None)
        self.sound_module = sound_module
        self.platform_name = os.name if platform_name is None else platform_name
        self.environment = os.environ if environment is None else environment
        self.executable = executable or sys.executable
        self.frozen = (
            bool(getattr(sys, "frozen", False))
            if frozen is None
            else bool(frozen)
        )
        self.command_runner = command_runner or subprocess.run
        self.process_launcher = process_launcher or subprocess.Popen
        self.path_opener = (
            getattr(os, "startfile", None) if path_opener is None else path_opener
        )
        self.url_opener = (
            webbrowser.open if url_opener is None else url_opener
        )
        self.now = now or datetime.now
        self.shutdown_pending = False
        self._sound_error_logged = False
        self._export_lock = threading.Lock()

    def startup_command(self) -> str:
        if self.frozen:
            return f'"{self.executable}"'
        return f'"{self.executable}" "{self.launcher_file}"'

    def set_windows_auto_start(self, enabled: bool) -> bool:
        if self.platform_name != "nt":
            self.log("当前系统不支持自动创建开机启动项。")
            return False

        startup_dir = os.path.join(
            self.environment.get("APPDATA", ""),
            r"Microsoft\Windows\Start Menu\Programs\Startup",
        )
        if not startup_dir.strip("\\") or not os.path.isdir(startup_dir):
            self.log("未找到 Windows 启动目录，开机自动启动未生效。")
            return False

        shortcut_path = os.path.join(startup_dir, "统一刷课启动器.bat")
        if enabled:
            shortcut = "".join(
                (
                    "@echo off\n",
                    f'cd /d "{self.base_dir}"\n',
                    f'start "" {self.startup_command()}\n',
                )
            )
            atomic_write_text(shortcut_path, shortcut)
            self.log("已启用开机自动启动")
        else:
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)
            self.log("已关闭开机自动启动")
        return True

    def clean_old_runtime_logs(self, *, base_dir: Optional[str] = None, days: int = 7) -> int:
        root = os.path.abspath(base_dir or self.base_dir)
        cutoff = self.now().timestamp() - days * 24 * 60 * 60
        log_dirs = (
            os.path.join(root, "Autovisor", "logs"),
            os.path.join(root, "Yatori", "assets", "log"),
        )
        removed = 0
        for log_dir in log_dirs:
            if not os.path.isdir(log_dir):
                continue
            for entry in os.listdir(log_dir):
                path = os.path.join(log_dir, entry)
                if not os.path.isfile(path):
                    continue
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except Exception as exc:
                    self.log(f"清理日志失败: {path} ({exc})")
        if removed:
            self.log(f"已自动清理 {removed} 个 {days} 天前的日志文件")
        return removed

    def minimize_window(self) -> bool:
        window = self.get_window()
        try:
            if window and hasattr(window, "minimize"):
                window.minimize()
                return True
            if window and hasattr(window, "hide"):
                window.hide()
                return True
            self.log("当前窗口后端不支持自动最小化。")
        except Exception as exc:
            self.log(f"最小化窗口失败: {exc}")
        return False

    def play_feedback_sound(self, *, enabled: bool, error: bool = False) -> bool:
        if not enabled:
            return False
        try:
            if self.sound_module:
                sound = (
                    self.sound_module.MB_ICONHAND
                    if error
                    else self.sound_module.MB_OK
                )
                self.sound_module.MessageBeep(sound)
            else:
                print("\a", end="")
            return True
        except Exception as exc:
            if not self._sound_error_logged:
                self._sound_error_logged = True
                self.log(f"播放提示音失败: {exc}")
            return False

    def schedule_shutdown(self, *, delay_seconds: int = 60) -> dict:
        if self.platform_name != "nt":
            return {"ok": False, "message": "当前系统不支持自动关机"}

        try:
            completed = self.command_runner(
                [
                    "shutdown",
                    "/s",
                    "/t",
                    str(max(0, int(delay_seconds))),
                    "/c",
                    "刷课任务已结束，统一启动器按偏好设置自动关机。",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "自动关机指令执行超时"}
        except Exception as exc:
            return {"ok": False, "message": f"自动关机指令执行失败: {exc}"}

        return_code = int(getattr(completed, "returncode", 0) or 0)
        if return_code:
            return {
                "ok": False,
                "message": f"自动关机指令被系统拒绝，返回码: {return_code}",
            }

        self.shutdown_pending = True
        return {
            "ok": True,
            "message": f"计算机将在 {max(0, int(delay_seconds))} 秒后关闭",
        }

    def cancel_shutdown(self) -> dict:
        if self.platform_name != "nt":
            return {"ok": False, "message": "当前系统不支持取消自动关机"}

        try:
            completed = self.command_runner(
                ["shutdown", "/a"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "取消关机超时"}
        except Exception as exc:
            return {"ok": False, "message": f"取消失败: {exc}"}

        return_code = int(getattr(completed, "returncode", 0) or 0)
        if return_code == 1116:
            self.shutdown_pending = False
            return {
                "ok": True,
                "message": "当前没有等待中的自动关机",
                "alreadyCancelled": True,
            }
        if return_code:
            return {
                "ok": False,
                "message": f"取消关机失败，系统返回码: {return_code}",
            }

        self.shutdown_pending = False
        return {"ok": True, "message": "已取消关机"}

    def _next_export_path(self, export_dir: str, tab: str) -> tuple[str, str]:
        timestamp = self.now().strftime("%Y%m%d_%H%M%S_%f")
        stem = f"logs_{tab}_{timestamp}"
        filename = f"{stem}.txt"
        filepath = os.path.join(export_dir, filename)
        sequence = 2
        while os.path.exists(filepath):
            filename = f"{stem}_{sequence}.txt"
            filepath = os.path.join(export_dir, filename)
            sequence += 1
        return filename, filepath

    def reveal_file(self, filepath: str) -> dict:
        if self.platform_name != "nt":
            return {"ok": False, "message": "当前系统不支持打开文件所在位置"}
        try:
            self.process_launcher(
                ["explorer", "/select,", os.path.normpath(filepath)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "message": f"无法打开文件所在位置: {exc}"}

    def export_logs(self, tab: str, text: str) -> dict:
        normalized_tab = str(tab or "").strip().lower()
        if normalized_tab not in self.LOG_TABS:
            return {"ok": False, "message": f"未知日志类型: {tab}"}

        export_dir = os.path.join(self.base_dir, "logs")
        try:
            with self._export_lock:
                os.makedirs(export_dir, exist_ok=True)
                filename, filepath = self._next_export_path(
                    export_dir,
                    normalized_tab,
                )
                atomic_write_text(filepath, str(text or ""))
        except Exception as exc:
            self.log(f"导出日志失败: {exc}")
            return {"ok": False, "message": f"导出失败: {exc}"}

        reveal_result = self.reveal_file(filepath)
        if reveal_result.get("ok"):
            message = f"日志已导出: {filename}"
        else:
            detail = reveal_result.get("message") or "无法打开文件所在位置"
            message = f"日志已导出: {filename}；{detail}"
            self.log(message)
        return {
            "ok": True,
            "path": filepath,
            "message": message,
            "revealed": bool(reveal_result.get("ok")),
        }

    def open_path(self, path: str) -> dict:
        if not str(path or "").strip():
            return {"ok": False, "message": "本地路径为空"}
        normalized = os.path.abspath(path)
        if not os.path.exists(normalized):
            return {"ok": False, "message": f"目录或文件不存在: {normalized}"}
        if self.path_opener is None:
            return {"ok": False, "message": "当前系统不支持打开本地路径"}
        try:
            self.path_opener(normalized)
            return {"ok": True, "path": normalized}
        except Exception as exc:
            return {"ok": False, "message": f"打开本地路径失败: {exc}"}

    def open_first_existing(
        self,
        candidates: Iterable[str],
        *,
        missing_message: str,
    ) -> dict:
        for candidate in candidates:
            if os.path.exists(candidate):
                return self.open_path(candidate)
        return {"ok": False, "message": missing_message}

    def open_external_url(self, url: str) -> dict:
        """Open an allow-listed https link in the system browser.

        Only hosts/prefixes from :attr:`EXTERNAL_URL_ALLOWLIST` may be
        opened, so a compromised frontend cannot turn this method into an
        arbitrary external-link redirector.
        """
        raw = str(url or "").strip()
        if not raw:
            return {"ok": False, "message": "外部链接为空"}
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError:
            return {"ok": False, "message": f"外部链接格式无效: {raw}"}
        if parsed.scheme != "https":
            return {"ok": False, "message": "仅允许打开 https 链接"}
        if parsed.username or parsed.password or port is not None:
            return {"ok": False, "message": "外部链接不允许携带凭据或端口"}
        path = parsed.path.lower()
        allowed = any(
            host == allowed_host and path.startswith(allowed_prefix)
            for allowed_host, allowed_prefix in self.EXTERNAL_URL_ALLOWLIST
        )
        if not allowed:
            return {"ok": False, "message": f"外部链接不在白名单内: {raw}"}
        if self.url_opener is None:
            return {"ok": False, "message": "当前系统不支持打开外部链接"}
        try:
            self.url_opener(raw)
        except Exception as exc:
            return {"ok": False, "message": f"打开外部链接失败: {exc}"}
        return {"ok": True, "url": raw}
