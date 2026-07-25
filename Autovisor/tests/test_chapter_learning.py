# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.chapter_learning import (
    VIDEO_ITEM_SELECTOR,
    chapter_learning_flow,
    get_chapter_videos_status,
    wait_for_video_completion,
)

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
        self.items = items
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        return _Collection(self.items)


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


def test_video_status_skips_one_broken_item_without_losing_later_videos():
    logger = _Logger()
    page = _ListingPage(
        [
            _BrokenItem("broken"),
            _Item("观看 第二讲", css_class="video-item"),
        ]
    )

    videos = asyncio.run(
        get_chapter_videos_status(page, logger_instance=logger)
    )

    assert videos == [
        {"name": "观看 第二讲", "completed": False, "index": 1}
    ]
    assert len(logger.warnings) == 1
    assert "索引0" in logger.warnings[0]
    assert "RuntimeError('lesson card changed')" in logger.warnings[0]


def test_chapter_flow_fails_when_every_lesson_item_is_unreadable():
    logger = _Logger()
    page = _ListingPage([_BrokenItem("broken")])

    result = asyncio.run(
        chapter_learning_flow(page, logger_instance=logger)
    )

    assert result is False
    assert any("所有章节条目均读取失败" in line for line in logger.errors)


class _PlaybackPage:
    def __init__(self):
        self.probe_calls = 0
        self.resume_calls = 0

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


def test_chapter_flow_stops_before_test_when_video_does_not_finish():
    page = _ChapterPage([_Item("第一讲", css_class="video-item")])
    answer_calls = []

    async def status_getter(_page, **_kwargs):
        return [{"name": "第一讲", "completed": False, "index": 0}]

    async def completion_waiter(_page, **_kwargs):
        return False

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
