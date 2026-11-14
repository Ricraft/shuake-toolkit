"""Application startup and failure cleanup for the Web-only launcher."""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


def launch_desktop_process(command, *, root, debug=False, run=None):
    """Launch independently of bootstrap's console and redirected output pipes."""
    command = list(map(str, command))
    root = Path(root)
    env = os.environ.copy()
    env.update(PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
    kwargs = {'cwd': str(root), 'env': env}
    log_path = None
    runner = run or subprocess.Popen
    if os.name == 'nt' and not debug:
        # Some Windows venv pythonw redirectors still spawn base python.exe.
        # Use the console interpreter with an explicit no-console creation flag.
        executable = Path(command[0])
        if executable.name.lower() == 'pythonw.exe':
            console_python = executable.with_name('python.exe')
            if console_python.is_file():
                command[0] = str(console_python)
        log_dir = root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f'launcher_startup_{datetime.now():%Y%m%d_%H%M%S_%f}.log'
        env['SHUAKE_WINDOWLESS_CHILD'] = '1'
        with log_path.open('a', encoding='utf-8') as output:
            process = runner(command, **kwargs, creationflags=subprocess.CREATE_NO_WINDOW,
                             stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    else:
        process = runner(command, **kwargs)

    # A short confirmation catches immediate failures without waiting for the UI lifetime.
    returncode = None
    wait = getattr(process, 'wait', None)
    if callable(wait):
        try:
            returncode = wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return 0
        except TypeError:
            poll = getattr(process, 'poll', None)
            if callable(poll):
                returncode = poll()
    else:
        poll = getattr(process, 'poll', None)
        if callable(poll):
            returncode = poll()
    if returncode is None:
        returncode = getattr(process, 'returncode', None)
    if isinstance(returncode, int):
        detail = f'；启动日志: {log_path}' if log_path else ''
        raise RuntimeError(f'启动器在启动确认期间提前退出，返回码: {returncode}{detail}')
    return 0


def relaunch_without_console(root, *, args=None):
    """Hand off direct .py / legacy launches before starting any account work."""
    args = list(sys.argv[1:] if args is None else args)
    if os.name != 'nt' or '--dev' in args or getattr(sys, 'frozen', False):
        return False
    import ctypes
    windowless_child = os.environ.pop('SHUAKE_WINDOWLESS_CHILD', '') == '1'
    if not ctypes.windll.kernel32.GetConsoleWindow():
        return False
    if windowless_child:
        # Defensive fallback for a nonstandard interpreter; avoid a relaunch loop.
        if not ctypes.windll.kernel32.FreeConsole():
            raise RuntimeError('无法脱离启动控制台，请通过统一启动器.exe 启动并检查日志')
        return False
    launch_desktop_process([sys.executable, str(Path(root) / '统一启动器.py'), *args], root=root)
    return True


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
