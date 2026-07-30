import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.application_bootstrap import (
    WebApplicationBootstrap,
    install_crash_hook,
    resolve_file_dialog_constants,
)


def test_main_delegates_window_lifecycle_to_bootstrap(monkeypatch):
    import 统一启动器 as launcher_module

    calls = []
    webview = SimpleNamespace(
        FileDialog=SimpleNamespace(OPEN=12, SAVE=32),
    )

    class Bootstrap:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def run(self, *, debug=False):
            calls.append(("run", debug))
            return "launcher"

    monkeypatch.setattr(
        launcher_module,
        "install_crash_hook",
        lambda base_dir: calls.append(("crash_hook", base_dir)),
    )
    monkeypatch.setattr(
        launcher_module,
        "ensure_core_dependencies",
        lambda: calls.append(("dependencies",)),
    )
    monkeypatch.setattr(
        launcher_module,
        "enable_windows_dpi_awareness",
        lambda: calls.append(("dpi",)),
    )
    monkeypatch.setattr(
        launcher_module,
        "WebApplicationBootstrap",
        Bootstrap,
    )
    monkeypatch.setattr(
        launcher_module,
        "FD_OPEN",
        launcher_module.FD_OPEN,
    )
    monkeypatch.setattr(
        launcher_module,
        "FD_SAVE",
        launcher_module.FD_SAVE,
    )
    monkeypatch.setattr(launcher_module, "webview", webview)
    monkeypatch.setattr(sys, "argv", ["launcher.py", "--dev"])

    result = launcher_module.main()

    assert result == "launcher"
    assert launcher_module.FD_OPEN == 12
    assert launcher_module.FD_SAVE == 32
    assert ("dependencies",) in calls
    assert ("dpi",) in calls
    assert ("run", True) in calls
    init_call = next(item for item in calls if item[0] == "init")
    assert init_call[1]["launcher_factory"] is launcher_module.UnifiedLauncher
    assert init_call[1]["api_factory"] is launcher_module.WebLauncherAPI
    assert init_call[1]["webview_module"] is webview


def test_main_stops_before_window_when_dependencies_remain_broken(monkeypatch):
    import 统一启动器 as launcher_module

    calls = []
    monkeypatch.setattr(
        launcher_module,
        "install_crash_hook",
        lambda _base_dir: calls.append("crash_hook"),
    )
    monkeypatch.setattr(
        launcher_module,
        "ensure_core_dependencies",
        lambda: ["playwright>=1.52,<2"],
    )
    monkeypatch.setattr(
        launcher_module,
        "enable_windows_dpi_awareness",
        lambda: calls.append("dpi"),
    )
    monkeypatch.setattr(
        launcher_module,
        "WebApplicationBootstrap",
        lambda **_kwargs: calls.append("bootstrap"),
    )

    with pytest.raises(RuntimeError, match="playwright.*requirements.txt"):
        launcher_module.main()

    assert calls == ["crash_hook"]


class FakeLauncher:
    def __init__(self):
        self.attached = []
        self.closed = []

    @staticmethod
    def get_base_dir():
        return "project-root"

    @staticmethod
    def resolve_web_ui_path(base_dir):
        return f"{base_dir}/web/launcher.html"

    def attach_web_window(self, window):
        self.attached.append(window)

    def on_closing(self, confirmed=False):
        self.closed.append(confirmed)


class FakeWebview:
    def __init__(self, *, create_error=None, start_error=None):
        self.create_error = create_error
        self.start_error = start_error
        self.create_calls = []
        self.start_calls = []
        self.window = object()

    def create_window(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        if self.create_error:
            raise self.create_error
        return self.window

    def start(self, **kwargs):
        self.start_calls.append(kwargs)
        if self.start_error:
            raise self.start_error


def test_file_dialog_constants_support_current_and_legacy_pywebview():
    current = SimpleNamespace(
        FileDialog=SimpleNamespace(OPEN="open", SAVE="save"),
        OPEN_DIALOG="old-open",
        SAVE_DIALOG="old-save",
    )
    legacy = SimpleNamespace(OPEN_DIALOG=11, SAVE_DIALOG=31)

    assert resolve_file_dialog_constants(current) == ("open", "save")
    assert resolve_file_dialog_constants(legacy) == (11, 31)
    assert resolve_file_dialog_constants(SimpleNamespace()) == (10, 30)


def test_bootstrap_creates_window_attaches_launcher_and_starts_webview():
    launcher = FakeLauncher()
    webview = FakeWebview()
    api = object()
    bootstrap = WebApplicationBootstrap(
        launcher_factory=lambda: launcher,
        api_factory=lambda value: api if value is launcher else None,
        webview_module=webview,
    )

    result = bootstrap.run(debug=True)

    assert result is launcher
    assert launcher.attached == [webview.window]
    assert launcher.closed == []
    assert webview.start_calls == [{"debug": True, "http_server": True}]
    args, kwargs = webview.create_calls[0]
    assert args == ("统一启动器", "project-root/web/launcher.html")
    assert kwargs["js_api"] is api
    assert kwargs["min_size"] == (1180, 760)
    assert kwargs["confirm_close"] is False


@pytest.mark.parametrize("failure_stage", ["create", "start"])
def test_partial_webview_start_failure_closes_launcher(failure_stage):
    launcher = FakeLauncher()
    error = RuntimeError(f"{failure_stage} failed")
    webview = FakeWebview(
        create_error=error if failure_stage == "create" else None,
        start_error=error if failure_stage == "start" else None,
    )
    bootstrap = WebApplicationBootstrap(
        launcher_factory=lambda: launcher,
        api_factory=lambda _launcher: object(),
        webview_module=webview,
    )

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        bootstrap.run(debug=True)

    assert launcher.closed == [True]


def test_cleanup_failure_does_not_replace_original_start_error():
    logs = []

    class BrokenCleanupLauncher(FakeLauncher):
        def on_closing(self, confirmed=False):
            raise OSError("cleanup failed")

    bootstrap = WebApplicationBootstrap(
        launcher_factory=BrokenCleanupLauncher,
        api_factory=lambda _launcher: object(),
        webview_module=FakeWebview(start_error=RuntimeError("start failed")),
        log=logs.append,
    )

    with pytest.raises(RuntimeError, match="start failed"):
        bootstrap.run(debug=True)

    assert logs == ["启动失败后的资源清理异常: cleanup failed"]


def test_crash_hook_writes_one_detailed_log_and_calls_previous_hook(
    tmp_path,
):
    previous_hook = sys.excepthook
    forwarded = []
    sys.excepthook = lambda *args: forwarded.append(args)
    fixed_time = datetime(2026, 7, 28, 12, 34, 56, 123456)
    try:
        hook = install_crash_hook(
            str(tmp_path),
            clock=lambda: fixed_time,
        )
        try:
            raise ValueError("boom")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            hook(exc_type, exc_value, exc_tb)
    finally:
        sys.excepthook = previous_hook

    logs = list((tmp_path / "logs").glob("crash_*.log"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "Type: ValueError" in content
    assert "Value: boom" in content
    assert "Traceback (most recent call last)" in content
    assert len(forwarded) == 1
