# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.meeting_course_flow import (
    run_meeting_course,
    set_meeting_playback_rate,
    start_meeting_video,
)
from modules.course_session import CourseAuthenticationError

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.logs = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

    def write_log(self, message):
        self.logs.append(message)


class _Locator:
    def __init__(self, *, count=0, visible=False, click_error=None):
        self._count = count
        self._visible = visible
        self.click_error = click_error
        self.clicked = False
        self.first = self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def click(self, timeout):
        if self.click_error:
            raise self.click_error
        self.clicked = True

    def locator(self, _selector):
        return _Locator()


class _Page:
    def __init__(
        self,
        locators=None,
        *,
        url="https://lc.zhihuishu.com/course",
    ):
        self.locators = locators or {}
        self.url = url
        self.evaluations = []
        self.waits = []

    def locator(self, selector):
        return self.locators.get(selector, _Locator())

    async def evaluate(self, script):
        self.evaluations.append(script)
        if "paused" in script:
            return True
        return None

    async def wait_for_timeout(self, timeout):
        self.waits.append(timeout)

    async def wait_for_selector(self, selector, **options):
        self.waits.append((selector, options))


class _VideoElement:
    def __init__(self, *, fails=False):
        self.fails = fails
        self.clicked = False

    async def click(self, timeout):
        if self.fails:
            raise RuntimeError("click failed")
        self.clicked = True


async def _noop(*_args, **_kwargs):
    return None


def test_playback_rate_falls_back_to_native_video_property():
    page = _Page()
    logger = _Logger()

    result = asyncio.run(
        set_meeting_playback_rate(
            page, 1.75, logger, mouse_mover=_noop
        )
    )

    assert result is True
    assert page.evaluations == [
        "document.querySelector('video').playbackRate = 1.75;"
    ]


def test_playback_rate_uses_native_fallback_when_ui_control_fails():
    broken_speed_box = _Locator(
        count=1, visible=True, click_error=RuntimeError("broken control")
    )
    page = _Page({".speedBox": broken_speed_box})
    logger = _Logger()

    result = asyncio.run(
        set_meeting_playback_rate(page, 1.5, logger, mouse_mover=_noop)
    )

    assert result is True
    assert page.evaluations == [
        "document.querySelector('video').playbackRate = 1.5;"
    ]
    assert any("智慧树倍速控件设置失败" in message for message in logger.logs)


def test_autoplay_falls_back_to_native_play_when_buttons_are_missing():
    page = _Page()
    logger = _Logger()

    result = asyncio.run(start_meeting_video(page, logger, mouse_mover=_noop))

    assert result is True
    assert page.evaluations == [
        'document.querySelector("video")?.paused ?? true',
        "document.querySelector('video').play();",
    ]


def test_no_video_list_uses_direct_player_flow():
    page = _Page()
    logger = _Logger()
    calls = SimpleNamespace(prepares=[], learns=[])

    async def scanner(_page):
        return []

    async def preparer(*_args, **kwargs):
        calls.prepares.append(kwargs["set_speed"])
        return True

    async def learning_loop(_page, start_time, *flags):
        calls.learns.append((start_time, flags))

    config = SimpleNamespace(playbackRate=1.5)
    asyncio.run(
        run_meeting_course(
            page,
            config,
            logger,
            close_popup=_noop,
            learning_loop=learning_loop,
            scanner=scanner,
            video_preparer=preparer,
            clock=lambda: 10,
        )
    )

    assert calls.prepares == [True]
    assert calls.learns == [(10, (False, False, False, True))]
    assert logger.infos[-1] == "见面课已完成！"


def test_all_completed_videos_exit_without_clicking():
    element = _VideoElement()

    async def scanner(_page):
        return [{"completed": True, "element": element, "title": "已完成", "duration": "1:00"}]

    logger = _Logger()
    asyncio.run(
        run_meeting_course(
            _Page(),
            SimpleNamespace(playbackRate=1.5),
            logger,
            close_popup=_noop,
            learning_loop=_noop,
            scanner=scanner,
        )
    )

    assert element.clicked is False
    assert logger.infos[-1] == "所有视频已完成！"


def test_speed_is_configured_on_first_successfully_opened_video():
    failed = _VideoElement(fails=True)
    opened = _VideoElement()
    calls = SimpleNamespace(prepares=[], learns=0)

    async def scanner(_page):
        return [
            {"completed": False, "element": failed, "title": "失败", "duration": "1:00"},
            {"completed": False, "element": opened, "title": "成功", "duration": "2:00"},
        ]

    async def preparer(*_args, **kwargs):
        calls.prepares.append(kwargs["set_speed"])
        return True

    async def learning_loop(*_args):
        calls.learns += 1

    logger = _Logger()
    with pytest.raises(RuntimeError, match="仍有 1 个视频"):
        asyncio.run(
            run_meeting_course(
                _Page(),
                SimpleNamespace(playbackRate=1.5),
                logger,
                close_popup=_noop,
                learning_loop=learning_loop,
                scanner=scanner,
                video_preparer=preparer,
                clock=lambda: 20,
            )
        )

    assert failed.clicked is False
    assert opened.clicked is True
    assert calls.prepares == [True]
    assert calls.learns == 1
    assert logger.warnings[-1] == "本轮完成 1/2 个视频，仍有 1 个未确认完成"


def test_false_learning_result_is_not_reported_as_completed():
    element = _VideoElement()

    async def scanner(_page):
        return [
            {
                "completed": False,
                "element": element,
                "title": "未完成",
                "duration": "1:00",
            }
        ]

    async def failed_learning(*_args):
        return False

    logger = _Logger()
    with pytest.raises(RuntimeError, match="仍有 1 个视频"):
        asyncio.run(
            run_meeting_course(
                _Page(),
                SimpleNamespace(playbackRate=1.5),
                logger,
                close_popup=_noop,
                learning_loop=failed_learning,
                scanner=scanner,
                video_preparer=_noop,
            )
        )

    assert "视频 '未完成' 已完成！" not in logger.infos
    assert any("未确认完成" in warning for warning in logger.warnings)


def test_login_page_stops_before_meeting_video_scan():
    scanner_calls = []

    async def scanner(_page):
        scanner_calls.append(True)
        return []

    with pytest.raises(CourseAuthenticationError, match="见面课扫描时"):
        asyncio.run(
            run_meeting_course(
                _Page(url="https://login.zhihuishu.com/?origin=zhs"),
                SimpleNamespace(playbackRate=1.5),
                _Logger(),
                close_popup=_noop,
                learning_loop=_noop,
                scanner=scanner,
            )
        )

    assert scanner_calls == []


def test_login_redirect_after_video_click_stops_before_preparation():
    page = _Page()
    calls = SimpleNamespace(prepares=0, learns=0)

    class _RedirectingVideo:
        async def click(self, timeout):
            assert timeout == 5000
            page.url = "https://www.zhihuishu.com/"
            raise RuntimeError("detached during redirect")

    async def scanner(_page):
        return [
            {
                "completed": False,
                "element": _RedirectingVideo(),
                "title": "待学习",
                "duration": "1:00",
            }
        ]

    async def preparer(*_args, **_kwargs):
        calls.prepares += 1

    async def learning_loop(*_args):
        calls.learns += 1

    with pytest.raises(CourseAuthenticationError, match="点击见面课视频后"):
        asyncio.run(
            run_meeting_course(
                page,
                SimpleNamespace(playbackRate=1.5),
                _Logger(),
                close_popup=_noop,
                learning_loop=learning_loop,
                scanner=scanner,
                video_preparer=preparer,
            )
        )

    assert calls.prepares == 0
    assert calls.learns == 0
