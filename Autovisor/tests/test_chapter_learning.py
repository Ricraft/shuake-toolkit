# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.chapter_learning import (
    VIDEO_ITEM_SELECTOR,
    chapter_learning_flow,
    get_chapter_videos_status,
    wait_for_video_completion,
)
from modules.course_session import CourseAuthenticationError
from playwright._impl._errors import TargetClosedError

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)


class _CountLocator:
    def __init__(self, count):
        self._count = count

    async def count(self):
        return self._count


class _Item:
    def __init__(self, text, css_class="lesson-item", child_count=0):
        self.text = text
        self.css_class = css_class
        self.child_count = child_count
        self.clicks = 0

    async def text_content(self):
        return self.text

    async def get_attribute(self, name):
        return self.css_class if name == "class" else None

    def locator(self, _selector):
        return _CountLocator(self.child_count)

    async def click(self):
        self.clicks += 1


class _BrokenItem(_Item):
    async def text_content(self):
        raise RuntimeError("lesson card changed")


class _Collection:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _ListingPage:
    def __init__(self, items):
        self.url = "https://study.zhihuishu.com/learning/videoList"
        self.items = items
        self.selectors = []

    def locator(self, selector):
        if selector == VIDEO_ITEM_SELECTOR:
            self.selectors.append(selector)
            return _Collection(self.items)
        return _CountLocator(0)


class _SequencedListingPage(_ListingPage):
    def __init__(self, scans):
        super().__init__([])
        self.scans = scans
        self.scan_calls = 0

    def locator(self, selector):
        if selector != VIDEO_ITEM_SELECTOR:
            return _CountLocator(0)
        self.selectors.append(selector)
        items = self.scans[min(self.scan_calls, len(self.scans) - 1)]
        self.scan_calls += 1
        return _Collection(items)


def test_video_status_excludes_incomplete_non_video_lessons():
    page = _ListingPage(
        [
            _Item("章节测验"),
            _Item("第一讲 已完成", css_class="video-item"),
            _Item("观看 第二讲"),
            _Item("第三讲", child_count=1),
        ]
    )

    videos = asyncio.run(get_chapter_videos_status(page))

    assert videos == [
        {"name": "第一讲 已完成", "completed": True, "index": 1},
        {"name": "观看 第二讲", "completed": False, "index": 2},
        {"name": "第三讲", "completed": False, "index": 3},
    ]
    assert page.selectors == [VIDEO_ITEM_SELECTOR]


def test_video_status_retries_transient_broken_item_without_losing_videos():
    logger = _Logger()
    page = _SequencedListingPage(
        [
            [
                _BrokenItem("观看 第一讲", css_class="video-item"),
                _Item("观看 第二讲", css_class="video-item"),
            ],
            [
                _Item("观看 第一讲", css_class="video-item"),
                _Item("观看 第二讲", css_class="video-item"),
            ],
        ]
    )

    videos = asyncio.run(
        get_chapter_videos_status(
            page,
            logger_instance=logger,
            retry_interval=0,
        )
    )

    assert videos == [
        {"name": "观看 第一讲", "completed": False, "index": 0},
        {"name": "观看 第二讲", "completed": False, "index": 1},
    ]
    assert page.scan_calls == 2
    assert "索引0" in logger.warnings[0]
    assert "RuntimeError('lesson card changed')" in logger.warnings[0]


def test_video_status_retries_transient_empty_listing():
    page = _SequencedListingPage(
        [
            [],
            [_Item("观看 第一讲", css_class="video-item")],
        ]
    )

    videos = asyncio.run(
        get_chapter_videos_status(
            page,
            retry_interval=0,
        )
    )

    assert videos == [
        {"name": "观看 第一讲", "completed": False, "index": 0}
    ]
    assert page.scan_calls == 2


def test_video_status_rejects_persistent_partial_scan_failure():
    logger = _Logger()
    page = _ListingPage(
        [
            _BrokenItem("观看 第一讲", css_class="video-item"),
            _Item("观看 第二讲", css_class="video-item"),
        ]
    )

    with pytest.raises(RuntimeError, match="1/2 项不可读"):
        asyncio.run(
            get_chapter_videos_status(
                page,
                logger_instance=logger,
                scan_attempts=2,
                retry_interval=0,
            )
        )


def test_chapter_flow_fails_when_lesson_items_remain_unreadable():
    logger = _Logger()
    page = _ListingPage([_BrokenItem("broken")])

    async def status_getter(active_page, **kwargs):
        return await get_chapter_videos_status(
            active_page,
            scan_attempts=1,
            retry_interval=0,
            **kwargs,
        )

    result = asyncio.run(
        chapter_learning_flow(
            page,
            logger_instance=logger,
            video_status_getter=status_getter,
        )
    )

    assert result is False
    assert any("连续扫描仍有 1/1 项不可读" in line for line in logger.errors)


@pytest.mark.parametrize(
    "error",
    [
        CourseAuthenticationError("expired"),
        TargetClosedError("page closed"),
    ],
)
def test_chapter_flow_propagates_fatal_page_errors(error):
    async def failed_status_getter(*_args, **_kwargs):
        raise error

    with pytest.raises(type(error), match=str(error)):
        asyncio.run(
            chapter_learning_flow(
                _ListingPage([]),
                video_status_getter=failed_status_getter,
            )
        )


class _PlaybackPage:
    def __init__(self):
        self.url = "https://study.zhihuishu.com/learning/videoList"
        self.probe_calls = 0
        self.resume_calls = 0

    def locator(self, _selector):
        return _CountLocator(0)

    async def evaluate(self, script):
        if "return {" in script:
            self.probe_calls += 1
            if self.probe_calls == 1:
                return {
                    "currentTime": 10,
                    "duration": 100,
                    "ended": False,
                    "paused": True,
                }
            return {
                "currentTime": 100,
                "duration": 100,
                "ended": True,
                "paused": False,
            }
        self.resume_calls += 1
        return None


def test_wait_for_video_completion_resumes_paused_video():
    page = _PlaybackPage()
    sleeps = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    result = asyncio.run(
        wait_for_video_completion(
            page,
            timeout=5,
            poll_interval=0,
            sleep_func=no_sleep,
        )
    )

    assert result is True
    assert page.resume_calls == 1
    assert sleeps == [0]


def test_wait_for_video_completion_propagates_authentication_loss():
    class LoginPage(_PlaybackPage):
        def __init__(self):
            super().__init__()
            self.url = "https://login.zhihuishu.com/?origin=video"

        async def evaluate(self, _script):
            raise AssertionError("登录页不应继续探测视频")

    with pytest.raises(
        CourseAuthenticationError,
        match="等待章节视频完成时登录状态失效",
    ):
        asyncio.run(
            wait_for_video_completion(
                LoginPage(),
                timeout=1,
                poll_interval=0,
            )
        )


def test_wait_for_video_completion_propagates_closed_page():
    class ClosedPage(_PlaybackPage):
        async def evaluate(self, _script):
            raise TargetClosedError("page closed")

    with pytest.raises(TargetClosedError, match="page closed"):
        asyncio.run(
            wait_for_video_completion(
                ClosedPage(),
                timeout=1,
                poll_interval=0,
            )
        )


class _ChapterPage(_ListingPage):
    def __init__(self, items):
        super().__init__(items)
        self.waited_selectors = []
        self.go_back_calls = 0

    async def wait_for_timeout(self, _milliseconds):
        return None

    async def wait_for_selector(self, selector, **_kwargs):
        self.waited_selectors.append(selector)
        return object()

    async def evaluate(self, _script):
        return None

    async def go_back(self):
        self.go_back_calls += 1


def test_chapter_flow_uses_same_selector_for_scan_and_click():
    item = _Item("第一讲", css_class="video-item")
    page = _ChapterPage([item])
    status_calls = 0

    async def status_getter(_page, **_kwargs):
        nonlocal status_calls
        status_calls += 1
        return [
            {
                "name": "第一讲",
                "completed": status_calls > 1,
                "index": 0,
            }
        ]

    async def completion_waiter(_page, **_kwargs):
        return True

    async def unused_answer_handler(*_args, **_kwargs):
        raise AssertionError("no test data should skip answering")

    result = asyncio.run(
        chapter_learning_flow(
            page,
            answer_handler=unused_answer_handler,
            video_status_getter=status_getter,
            completion_waiter=completion_waiter,
        )
    )

    assert result is True
    assert page.selectors == [VIDEO_ITEM_SELECTOR]
    assert item.clicks == 1
    assert page.waited_selectors == ["video"]
    assert page.go_back_calls == 1
    assert status_calls == 2


@pytest.mark.parametrize("completion_result", [False, None, "yes"])
def test_chapter_flow_stops_before_test_when_video_is_not_confirmed(
    completion_result,
):
    page = _ChapterPage([_Item("第一讲", css_class="video-item")])
    answer_calls = []

    async def status_getter(_page, **_kwargs):
        return [{"name": "第一讲", "completed": False, "index": 0}]

    async def completion_waiter(_page, **_kwargs):
        return completion_result

    async def answer_handler(*_args, **_kwargs):
        answer_calls.append(True)
        return True

    result = asyncio.run(
        chapter_learning_flow(
            page,
            test_handler=SimpleNamespace(questions_data=[{"id": 1}]),
            answer_handler=answer_handler,
            video_status_getter=status_getter,
            completion_waiter=completion_waiter,
        )
    )

    assert result is False
    assert answer_calls == []
