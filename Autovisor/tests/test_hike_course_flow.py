# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.hike_course_flow import run_hike_course

sys.path.remove(_AUTOVISOR_ROOT)


def _summary(total, pending):
    return {
        "total": total,
        "pending": pending,
        "done": total - pending,
        "sections": {},
    }


def _lesson(key):
    return {
        "card_id": key,
        "title": f"课程-{key}",
        "progress": 20,
        "section": "第一章",
    }


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)


class _Page:
    def __init__(self):
        self.goto_calls = []
        self.timeouts = []
        self.evaluations = []

    async def wait_for_timeout(self, timeout):
        self.timeouts.append(timeout)

    async def wait_for_selector(self, selector, **options):
        assert selector == "video"
        assert options == {"state": "attached", "timeout": 15000}

    async def evaluate(self, script):
        self.evaluations.append(script)

    async def goto(self, url, wait_until):
        self.goto_calls.append((url, wait_until))


def _dependencies(scan_results, *, click_results=None):
    calls = SimpleNamespace(clicks=[], learns=[], optimizes=[], popups=0)
    scan_results = iter(scan_results)
    click_results = iter(click_results or [])

    async def scanner(_page):
        return next(scan_results)

    async def card_clicker(_page, card_id, title, scope_id, top):
        calls.clicks.append((card_id, title, scope_id, top))
        return next(click_results, True)

    async def title_reader(_page, is_hike):
        assert is_hike is True
        return "页面标题"

    async def close_popup(_page, _logger):
        calls.popups += 1

    async def learning_loop(_page, start_time, *flags):
        calls.learns.append((start_time, flags))

    async def optimizer(_page, _config, *flags):
        calls.optimizes.append(flags)

    return calls, {
        "scanner": scanner,
        "card_clicker": card_clicker,
        "title_reader": title_reader,
        "close_popup": close_popup,
        "learning_loop": learning_loop,
        "optimizer": optimizer,
    }


def test_empty_scan_finishes_without_clicking():
    calls, dependencies = _dependencies([([], _summary(0, 0))])
    logger = _Logger()
    config = SimpleNamespace(
        limitMaxTime=0,
        course_urls=["first-course", "second-course"],
        remove_pause="js",
    )

    asyncio.run(run_hike_course(_Page(), config, logger, **dependencies))

    assert calls.clicks == []
    assert logger.infos[-1] == "没有未完成的课程，本轮结束。"


def test_lessons_are_clicked_learned_and_rescanned_in_page_order():
    lessons = [_lesson("a"), _lesson("b")]
    calls, dependencies = _dependencies(
        [
            (lessons, _summary(2, 2)),
            ([lessons[1]], _summary(2, 1)),
            ([], _summary(2, 0)),
        ]
    )
    page = _Page()
    logger = _Logger()
    config = SimpleNamespace(limitMaxTime=0, course_urls=["course"], remove_pause="js")

    asyncio.run(
        run_hike_course(
            page,
            config,
            logger,
            course_url="second-course",
            clock=lambda: 10,
            **dependencies,
        )
    )

    assert [click[0] for click in calls.clicks] == ["a", "b"]
    assert len(calls.learns) == 2
    assert calls.popups == 2
    assert calls.optimizes == [(False, True, False), (False, True, False)]
    assert page.goto_calls == [
        ("second-course", "domcontentloaded"),
        ("second-course", "domcontentloaded"),
    ]
    assert logger.infos[-1] == "所有课程已完成!"


def test_click_failure_skips_lesson_without_starting_learning():
    lesson = _lesson("a")
    calls, dependencies = _dependencies(
        [([lesson], _summary(1, 1))], click_results=[False]
    )
    config = SimpleNamespace(limitMaxTime=0, course_urls=["course"], remove_pause="js")
    logger = _Logger()

    asyncio.run(run_hike_course(_Page(), config, logger, **dependencies))

    assert calls.learns == []
    assert logger.warnings == ["未能定位卡片:课程-a, 本轮跳过."]


def test_time_limit_stops_before_clicking_next_lesson():
    lesson = _lesson("a")
    calls, dependencies = _dependencies([([lesson], _summary(1, 1))])
    times = iter([0, 120])
    config = SimpleNamespace(limitMaxTime=1, course_urls=["course"], remove_pause="js")
    logger = _Logger()

    asyncio.run(
        run_hike_course(
            _Page(), config, logger, clock=lambda: next(times), **dependencies
        )
    )

    assert calls.clicks == []
    assert logger.infos[-1] == "当前课程已达时限:1min"
