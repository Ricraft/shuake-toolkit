import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from playwright._impl._errors import TargetClosedError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.course_session import CourseAuthenticationError

_RUNTIME_SPEC = importlib.util.spec_from_file_location(
    "autovisor_runtime_entry",
    Path(_AUTOVISOR_ROOT) / "Autovisor.py",
)
runtime = importlib.util.module_from_spec(_RUNTIME_SPEC)
sys.modules[_RUNTIME_SPEC.name] = runtime
_RUNTIME_SPEC.loader.exec_module(runtime)

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    last = None

    def __init__(self):
        type(self).last = self
        self.errors = []
        self.logs = []
        self.saved = False

    def configure(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def write_log(self, message):
        self.logs.append(message)

    def save(self):
        self.saved = True


def _patch_runtime(monkeypatch, error):
    async def fail_main():
        raise error

    monkeypatch.setattr(runtime, "Logger", _Logger)
    monkeypatch.setattr(
        runtime,
        "Config",
        lambda *_args, **_kwargs: SimpleNamespace(
            course_urls=["https://studyvideoh5.zhihuishu.com/course"]
        ),
    )
    monkeypatch.setattr(runtime, "main", fail_main)


def test_unexpected_browser_close_returns_failure_exit_code(monkeypatch):
    _patch_runtime(monkeypatch, TargetClosedError("page closed"))
    assert runtime.run() == 1
    assert _Logger.last.saved is True
    assert any("课程完成前被关闭" in message for message in _Logger.last.errors)


def test_expired_login_returns_actionable_failure(monkeypatch):
    _patch_runtime(monkeypatch, CourseAuthenticationError("expired"))
    assert runtime.run() == 1
    assert any("登录状态已失效" in message for message in _Logger.last.errors)
