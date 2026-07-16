import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
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
        self.infos = []
        self.warnings = []
        self.errors = []
        self.logs = []
        self.saved = False

    def configure(self, *_args, **_kwargs):
        return None

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

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


class _LearningPage:
    def __init__(
        self,
        *,
        url="https://studyvideoh5.zhihuishu.com/course",
        video_error=None,
    ):
        self.url = url
        self.video_error = video_error
        self.reloads = 0
        self.position = 0

    async def wait_for_timeout(self, _milliseconds):
        return None

    async def wait_for_selector(self, *_args, **_kwargs):
        return object()

    async def reload(self, **_kwargs):
        self.reloads += 1

    async def evaluate(self, script):
        if "return {" in script:
            return {
                "error": self.video_error,
                "networkState": 1,
                "readyState": 4,
                "paused": False,
                "currentTime": self.position,
                "duration": 100,
            }
        if "currentTime ?? null" in script:
            self.position += 10
            return self.position
        return None


def _patch_learning_runtime(monkeypatch, progress_values):
    values = iter(progress_values)
    last_value = progress_values[-1]

    async def progress(*_args, **_kwargs):
        nonlocal last_value
        try:
            last_value = next(values)
        except StopIteration:
            pass
        return last_value

    async def close_popup(*_args, **_kwargs):
        return False

    monkeypatch.setattr(runtime, "get_course_progress", progress)
    monkeypatch.setattr(runtime, "close_popup", close_popup)
    monkeypatch.setattr(runtime, "show_course_progress", lambda **_kwargs: None)
    monkeypatch.setattr(
        runtime,
        "config",
        SimpleNamespace(limitMaxTime=0, remove_pause="resume-video"),
    )
    monkeypatch.setattr(runtime, "logger", _Logger())


def test_learning_loop_returns_false_when_time_limit_stops_video(monkeypatch):
    _patch_learning_runtime(monkeypatch, ["0%"])
    runtime.config.limitMaxTime = 1

    result = runtime.asyncio.run(
        runtime.learning_loop(
            _LearningPage(),
            0,
            clock=lambda: 120,
            minimum_watch_seconds=0,
        )
    )

    assert result is False
    assert any("最终进度: 0%" in item for item in runtime.logger.warnings)


def test_learning_loop_limits_repeated_stall_recovery(monkeypatch):
    _patch_learning_runtime(monkeypatch, ["0%"])
    page = _LearningPage()
    tick = 0

    def clock():
        nonlocal tick
        tick += 1
        return tick

    async def no_sleep(_seconds):
        return None

    result = runtime.asyncio.run(
        runtime.learning_loop(
            page,
            0,
            clock=clock,
            sleep_func=no_sleep,
            minimum_watch_seconds=0,
            stuck_timeout=0,
            max_recovery_attempts=2,
            health_check_interval=999,
            position_check_interval=999,
        )
    )

    assert result is False
    assert page.reloads == 2
    assert any("恢复上限" in item for item in runtime.logger.errors)


def test_learning_loop_uses_video_position_to_avoid_false_stall(monkeypatch):
    _patch_learning_runtime(monkeypatch, ["0%", "0%", "0%", "100%"])
    page = _LearningPage()
    tick = 0

    def clock():
        nonlocal tick
        tick += 1
        return tick

    async def no_sleep(_seconds):
        return None

    result = runtime.asyncio.run(
        runtime.learning_loop(
            page,
            0,
            clock=clock,
            sleep_func=no_sleep,
            minimum_watch_seconds=0,
            stuck_timeout=5,
            health_check_interval=999,
            position_check_interval=1,
        )
    )

    assert result is True
    assert page.reloads == 0


def test_learning_loop_raises_when_login_expires(monkeypatch):
    _patch_learning_runtime(monkeypatch, ["0%"])
    page = _LearningPage(url="https://passport.zhihuishu.com/login")

    with pytest.raises(CourseAuthenticationError, match="视频播放期间"):
        runtime.asyncio.run(
            runtime.learning_loop(
                page,
                0,
                clock=lambda: 1,
                minimum_watch_seconds=0,
                health_check_interval=1,
                position_check_interval=999,
            )
        )


def test_learning_loop_limits_repeated_video_load_recovery(monkeypatch):
    _patch_learning_runtime(monkeypatch, ["0%"])
    page = _LearningPage(video_error=3)
    tick = 0

    def clock():
        nonlocal tick
        tick += 1
        return tick

    result = runtime.asyncio.run(
        runtime.learning_loop(
            page,
            0,
            clock=clock,
            minimum_watch_seconds=0,
            stuck_timeout=999,
            max_recovery_attempts=2,
            health_check_interval=1,
            position_check_interval=999,
        )
    )

    assert result is False
    assert page.reloads == 2
    assert any("加载持续失败" in item for item in runtime.logger.errors)
