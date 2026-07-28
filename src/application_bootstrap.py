"""Application startup and failure cleanup for the Web-only launcher."""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime


def install_crash_hook(base_dir, *, clock=None):
    """Install one uncaught-exception hook that writes a UTF-8 crash log."""
    clock = clock or datetime.now
    crash_dir = os.path.join(base_dir, "logs")
    os.makedirs(crash_dir, exist_ok=True)
    original_hook = sys.excepthook

    def crash_handler(exc_type, exc_value, exc_tb):
        timestamp = clock().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(crash_dir, f"crash_{timestamp}.log")
        try:
            with open(path, "w", encoding="utf-8") as crash_file:
                crash_file.write(f"Time: {clock()}\n")
                crash_file.write(f"Type: {exc_type.__name__}\n")
                crash_file.write(f"Value: {exc_value}\n")
                crash_file.write("Traceback:\n")
                traceback.print_exception(
                    exc_type,
                    exc_value,
                    exc_tb,
                    file=crash_file,
                )
            print(f"[CRASH] 崩溃日志已写入: {path}")
        except Exception as log_error:
            print(f"[CRASH] 崩溃日志写入失败: {log_error}")
        original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = crash_handler
    return crash_handler


def resolve_file_dialog_constants(webview_module):
    """Support both current and legacy pywebview dialog constants."""
    file_dialog = getattr(webview_module, "FileDialog", None)
    open_dialog = getattr(
        file_dialog,
        "OPEN",
        getattr(webview_module, "OPEN_DIALOG", 10),
    )
    save_dialog = getattr(
        file_dialog,
        "SAVE",
        getattr(webview_module, "SAVE_DIALOG", 30),
    )
    return open_dialog, save_dialog


def enable_windows_dpi_awareness():
    if os.name != "nt":
        return False
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        return False


def hide_windows_console():
    if os.name != "nt":
        return False
    try:
        import ctypes

        window = ctypes.windll.kernel32.GetConsoleWindow()
        ctypes.windll.user32.ShowWindow(window, 0)
        return True
    except Exception:
        return False


class WebApplicationBootstrap:
    """Create the launcher window and clean up partial startup failures."""

    def __init__(
        self,
        *,
        launcher_factory,
        api_factory,
        webview_module,
        log=None,
    ):
        self.launcher_factory = launcher_factory
        self.api_factory = api_factory
        self.webview = webview_module
        self.log = log

    def _log_cleanup_failure(self, message):
        if callable(self.log):
            self.log(message)
        else:
            print(f"[STARTUP] {message}")

    def _cleanup_failed_start(self, launcher):
        if launcher is None:
            return
        try:
            launcher.on_closing(confirmed=True)
        except Exception as cleanup_error:
            self._log_cleanup_failure(
                f"启动失败后的资源清理异常: {cleanup_error}"
            )

    def run(self, *, debug=False):
        launcher = None
        try:
            launcher = self.launcher_factory()
            api = self.api_factory(launcher)
            html_path = launcher.resolve_web_ui_path(
                launcher.get_base_dir()
            )
            window = self.webview.create_window(
                "统一启动器",
                html_path,
                js_api=api,
                width=1480,
                height=940,
                min_size=(1180, 760),
                confirm_close=False,
            )
            launcher.attach_web_window(window)
            if not debug:
                hide_windows_console()
            self.webview.start(debug=debug, http_server=True)
            return launcher
        except BaseException:
            self._cleanup_failed_start(launcher)
            raise
