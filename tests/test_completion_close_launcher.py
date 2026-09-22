# encoding=utf-8
"""契约测试：刷完自动关闭启动器偏好与空闲收尾接线。"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from src.preferences_service import DEFAULT_PREFERENCES
from src.runtime_coordinator import RuntimeCoordinator
from 统一启动器 import UnifiedLauncher


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bare_launcher(*, enabled=True, shutdown_pending=False, logs=None):
    logs = logs if logs is not None else []
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher._launcher_close_pending = False
    launcher._shutdown_pending = shutdown_pending
    launcher.log_system = logs.append
    launcher._preference_enabled = (
        lambda key, default=True: enabled if key == "closeLauncherOnComplete" else True
    )
    launcher.logs = logs
    return launcher


def test_preference_default_is_off():
    assert DEFAULT_PREFERENCES["closeLauncherOnComplete"] is False


def test_close_is_skipped_when_preference_disabled():
    launcher = _bare_launcher(enabled=False)
    spawned = []
    launcher._spawn_launcher_close = lambda: spawned.append(True)

    assert launcher._maybe_close_launcher_after_completion() is False
    assert spawned == []
    assert launcher._launcher_close_pending is False


def test_close_is_skipped_when_shutdown_is_already_pending():
    launcher = _bare_launcher(shutdown_pending=True)
    spawned = []
    launcher._spawn_launcher_close = lambda: spawned.append(True)

    assert launcher._maybe_close_launcher_after_completion() is False
    assert spawned == []
    assert any("跳过自动关闭启动器" in line for line in launcher.logs)


def test_close_runs_once_per_completion_round():
    launcher = _bare_launcher()
    spawned = []
    launcher._spawn_launcher_close = lambda: spawned.append(True)

    assert launcher._maybe_close_launcher_after_completion() is True
    assert spawned == [True]
    assert launcher._launcher_close_pending is True

    assert launcher._maybe_close_launcher_after_completion() is False
    assert spawned == [True]


def test_spawn_launcher_close_confirms_exit_and_resets_flag():
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher._launcher_close_pending = True
    closed = threading.Event()
    calls = []

    def on_closing(confirmed=False):
        calls.append(confirmed)
        closed.set()

    launcher.on_closing = on_closing
    launcher.log_system = calls.append

    launcher._spawn_launcher_close()

    assert closed.wait(2)
    assert calls[0] is True
    assert launcher._launcher_close_pending is False


def test_spawn_launcher_close_logs_failure_and_clears_flag():
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher._launcher_close_pending = True
    logs = []
    launcher.log_system = logs.append

    def on_closing(confirmed=False):
        raise RuntimeError("destroy failed")

    launcher.on_closing = on_closing
    launcher._spawn_launcher_close()

    deadline = time.time() + 2
    while launcher._launcher_close_pending and time.time() < deadline:
        time.sleep(0.01)

    assert launcher._launcher_close_pending is False
    assert any("自动关闭启动器失败" in line for line in logs)


def _coordinator(failed=False):
    system_logs = []
    shutdowns = []
    closes = []
    launcher = SimpleNamespace(
        _runtime_failure_since_batch=failed,
        log_system=system_logs.append,
        _maybe_shutdown_after_completion=lambda: shutdowns.append(True),
        _maybe_close_launcher_after_completion=lambda: closes.append(True),
    )
    coordinator = RuntimeCoordinator(launcher)
    return coordinator, system_logs, shutdowns, closes


def test_coordinator_invokes_close_after_successful_idle_round():
    coordinator, _logs, shutdowns, closes = _coordinator()
    coordinator._finish_idle_round(allow_shutdown=True)
    assert shutdowns == [True]
    assert closes == [True]


def test_coordinator_skips_close_after_failed_round():
    coordinator, logs, shutdowns, closes = _coordinator(failed=True)
    coordinator._finish_idle_round(allow_shutdown=True)
    assert shutdowns == []
    assert closes == []
    assert any("跳过自动关闭启动器" in line for line in logs)


def test_coordinator_tolerates_launcher_without_close_hook():
    system_logs = []
    launcher = SimpleNamespace(
        _runtime_failure_since_batch=False,
        log_system=system_logs.append,
        _maybe_shutdown_after_completion=lambda: None,
    )
    coordinator = RuntimeCoordinator(launcher)
    coordinator._finish_idle_round(allow_shutdown=True)


def _settings_html() -> str:
    for path in (PROJECT_ROOT / "web").glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "pref-auto-shutdown" in text:
            return text
    raise AssertionError("settings page not found")


def test_settings_page_and_frontend_wire_close_preference():
    html = _settings_html()
    assert 'id="pref-close-launcher"' in html
    assert "savePreference('closeLauncherOnComplete', this.checked)" in html

    frontend = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "closeLauncherOnComplete: 'pref-close-launcher'" in frontend
    assert "closeLauncherOnComplete: false" in frontend
