# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright._impl._errors import TargetClosedError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.video_tasks import activate_window, play_video, task_monitor, video_optimize

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.warnings = []
        self.logs = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

    def write_log(self, message):
        self.logs.append(message)


def test_task_monitor_reports_task_that_failed_before_monitor_started():
    logger = _Logger()

    async def run():
        async def failing_worker():
            raise RuntimeError("boom")

        task = asyncio.create_task(failing_worker())
        await asyncio.sleep(0)
        await task_monitor([task], logger_instance=logger, poll_interval=0)

    asyncio.run(run())

    assert logger.errors == ["任务函数failing_worker 出现异常."]
    assert "RuntimeError('boom')" in logger.logs[0]


class _ClosedPage:
    async def wait_for_load_state(self, _state):
        raise TargetClosedError("closed")


def test_video_optimize_handles_page_closed_during_initial_load():
    logger = _Logger()
    asyncio.run(
        video_optimize(
            _ClosedPage(),
            SimpleNamespace(),
            logger_instance=logger,
            poll_interval=0,
        )
    )
    assert logger.logs == ["浏览器已关闭,视频调节模块已下线.\n"]


def test_play_video_handles_page_closed_during_initial_load():
    logger = _Logger()
    asyncio.run(
        play_video(
            _ClosedPage(),
            SimpleNamespace(limitSpeed=1.5),
            logger_instance=logger,
            poll_interval=0,
        )
    )
    assert logger.logs == ["浏览器已关闭,视频播放模块已下线.\n"]


class _PausedVideoPage:
    url = "https://studyservice-api.zhihuishu.com/course"

    def __init__(self):
        self.selector_checks = 0
        self.settings = None
        self.evaluated = []

    async def wait_for_load_state(self, _state):
        return None

    async def wait_for_selector(self, _selector, **_kwargs):
        self.selector_checks += 1
        if self.selector_checks > 1:
            raise TargetClosedError("closed")
        return object()

    async def evaluate(self, script, argument=None):
        self.evaluated.append((script, argument))
        if script == "document.querySelector('video').paused":
            return True
        if argument is not None:
            self.settings = argument
            return {"success": True, "playbackRate": argument["speed"], "muted": True}
        if "await video.play()" in script:
            return {"success": True, "paused": False, "error": None}
        return None


def test_play_video_restores_paused_video_with_runtime_settings():
    logger = _Logger()
    page = _PausedVideoPage()
    config = SimpleNamespace(
        limitSpeed=1.5,
        soundOff=True,
        remove_pause="remove-pause-script",
    )

    asyncio.run(
        play_video(page, config, logger_instance=logger, poll_interval=0)
    )

    assert page.settings == {"speed": 1.5, "mute": True}
    assert ("remove-pause-script", None) in page.evaluated
    assert "检测到视频暂停,正在尝试播放." in logger.infos
    assert "视频已恢复播放（静音+1.5x倍速）.\n" in logger.logs
    assert logger.logs[-1] == "浏览器已关闭,视频播放模块已下线.\n"


class _RepeatedFailurePage:
    url = "https://studyservice-api.zhihuishu.com/course"

    def __init__(self, exception_type=RuntimeError, failures=6):
        self.exception_type = exception_type
        self.failures = failures
        self.calls = 0

    async def wait_for_load_state(self, _state):
        return None

    async def wait_for_selector(self, _selector, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exception_type("selector changed")
        raise TargetClosedError("closed")


def test_video_optimize_rate_limits_unknown_loop_failures():
    logger = _Logger()
    page = _RepeatedFailurePage()

    asyncio.run(
        video_optimize(
            page,
            SimpleNamespace(),
            logger_instance=logger,
            poll_interval=0,
        )
    )

    assert len(logger.warnings) == 2
    assert "视频调节异常（连续1次）" in logger.warnings[0]
    assert "RuntimeError('selector changed')" in logger.warnings[0]
    assert "视频调节异常（连续5次）" in logger.warnings[1]
    assert logger.logs[-1] == "浏览器已关闭,视频调节模块已下线.\n"


def test_video_optimize_ignores_expected_selector_timeouts():
    logger = _Logger()
    page = _RepeatedFailurePage(PlaywrightTimeoutError, failures=2)

    asyncio.run(
        video_optimize(
            page,
            SimpleNamespace(),
            logger_instance=logger,
            poll_interval=0,
        )
    )

    assert logger.warnings == []
    assert logger.logs[-1] == "浏览器已关闭,视频调节模块已下线.\n"


def test_play_video_rate_limits_unknown_loop_failures():
    logger = _Logger()
    page = _RepeatedFailurePage()

    asyncio.run(
        play_video(
            page,
            SimpleNamespace(limitSpeed=1.5),
            logger_instance=logger,
            poll_interval=0,
        )
    )

    assert len(logger.warnings) == 2
    assert "视频播放异常（连续1次）" in logger.warnings[0]
    assert "视频播放异常（连续5次）" in logger.warnings[1]
    assert logger.logs[-1] == "浏览器已关闭,视频播放模块已下线.\n"


def test_activate_window_rate_limits_unknown_loop_failures():
    logger = _Logger()
    calls = 0

    async def failing_window_lookup(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 6:
            raise RuntimeError("window API changed")
        raise TargetClosedError("closed")

    with patch(
        "modules.video_tasks.get_browser_window",
        new=failing_window_lookup,
    ):
        asyncio.run(
            activate_window(
                SimpleNamespace(),
                logger_instance=logger,
                poll_interval=0,
            )
        )

    assert len(logger.warnings) == 2
    assert "窗口激活异常（连续1次）" in logger.warnings[0]
    assert "窗口激活异常（连续5次）" in logger.warnings[1]
    assert logger.logs[-1] == "浏览器已关闭,窗口激活模块已下线.\n"
