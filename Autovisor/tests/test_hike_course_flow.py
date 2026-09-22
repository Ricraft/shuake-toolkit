# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.hike_course_flow import run_hike_course
from modules.course_session import CourseAuthenticationError
from modules import utils as utils_module

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


class _EmptyLocator:
    async def count(self):
        return 0


class _Page:
    def __init__(self, url="https://hike.zhihuishu.com/course"):
        self.url = url
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
        self.url = url

    def locator(self, _selector):
        return _EmptyLocator()


def _dependencies(scan_results, *, click_results=None):
    calls = SimpleNamespace(clicks=[], learns=[], optimizes=[], popups=0)
    scan_results = iter(scan_results)
    click_results = iter(click_results or [])

    async def scanner(_page):
        return next(scan_results)

    async def card_clicker(
        _page,
        card_id,
        title,
        *,
        scope_id,
        top,
    ):
        calls.clicks.append((card_id, title, scope_id, top))
        return next(click_results, True)

    async def title_reader(_page, is_hike):
        assert is_hike is True
        return "页面标题"

    async def close_popup(_page, _logger):
        calls.popups += 1

    async def learning_loop(_page, start_time, *flags):
        calls.learns.append((start_time, flags))
        return True

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


def test_empty_scan_reports_failure_after_bounded_retries():
    calls, dependencies = _dependencies(
        [
            ([], _summary(0, 0)),
            ([], _summary(0, 0)),
        ]
    )
    logger = _Logger()
    config = SimpleNamespace(
        limitMaxTime=0,
        course_urls=["first-course", "second-course"],
        remove_pause="js",
    )

    with pytest.raises(RuntimeError, match="无法确认课程列表"):
        asyncio.run(
            run_hike_course(
                _Page(),
                config,
                logger,
                empty_scan_limit=2,
                **dependencies,
            )
        )

    assert calls.clicks == []
    assert logger.warnings[-1] == "连续 2 次未检测到翻转课卡片，无法确认课程列表"


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


def test_rescan_uses_latest_card_id_for_remaining_lesson():
    first = _lesson("a")
    second = _lesson("b")
    regenerated_second = {**second, "card_id": "b-new"}
    calls, dependencies = _dependencies(
        [
            ([first, second], _summary(2, 2)),
            ([regenerated_second], _summary(1, 1)),
            ([], _summary(0, 0)),
        ]
    )

    asyncio.run(
        run_hike_course(
            _Page(),
            SimpleNamespace(
                limitMaxTime=0,
                course_urls=["course"],
                remove_pause="js",
            ),
            _Logger(),
            clock=lambda: 10,
            **dependencies,
        )
    )

    assert [click[0] for click in calls.clicks] == ["a", "b-new"]
    assert len(calls.learns) == 2


def test_click_failure_reports_unfinished_lesson():
    lesson = _lesson("a")
    calls, dependencies = _dependencies(
        [
            ([lesson], _summary(1, 1)),
            ([lesson], _summary(1, 1)),
        ],
        click_results=[False],
    )
    config = SimpleNamespace(limitMaxTime=0, course_urls=["course"], remove_pause="js")
    logger = _Logger()

    with pytest.raises(RuntimeError, match="仍有 1 个项目未确认完成"):
        asyncio.run(run_hike_course(_Page(), config, logger, **dependencies))

    assert calls.learns == []
    assert logger.warnings[0] == "未能定位卡片:课程-a, 本轮跳过."
    assert "课程-a" in logger.warnings[-1]


def test_confirmed_lesson_can_finish_when_page_progress_is_stale():
    lesson = _lesson("a")
    calls, dependencies = _dependencies(
        [
            ([lesson], _summary(1, 1)),
            ([lesson], _summary(1, 1)),
        ]
    )
    logger = _Logger()

    asyncio.run(
        run_hike_course(
            _Page(),
            SimpleNamespace(
                limitMaxTime=0,
                course_urls=["course"],
                remove_pause="js",
            ),
            logger,
            **dependencies,
        )
    )

    assert len(calls.learns) == 1
    assert logger.infos[-1] == "所有课程已完成!"


def test_default_card_clicker_accepts_deep_scan_metadata():
    result = asyncio.run(
        utils_module.click_card_by_id(
            _Page(),
            "missing",
            "缺失卡片",
            scope_id="window",
            top=240,
        )
    )

    assert result is False


def test_login_page_stops_hike_before_scanner_runs():
    scanner_calls = 0

    async def scanner(_page):
        nonlocal scanner_calls
        scanner_calls += 1
        return [], _summary(0, 0)

    with pytest.raises(CourseAuthenticationError, match="扫描时"):
        asyncio.run(
            run_hike_course(
                _Page("https://login.zhihuishu.com/"),
                SimpleNamespace(
                    limitMaxTime=0,
                    course_urls=["course"],
                    remove_pause="js",
                ),
                _Logger(),
                close_popup=lambda *_args: asyncio.sleep(0),
                learning_loop=lambda *_args: asyncio.sleep(0, result=True),
                scanner=scanner,
            )
        )

    assert scanner_calls == 0


def test_login_redirect_during_hike_scan_is_fatal():
    page = _Page()

    async def scanner(work_page):
        work_page.url = "https://login.zhihuishu.com/"
        return [], _summary(0, 0)

    with pytest.raises(CourseAuthenticationError, match="扫描后"):
        asyncio.run(
            run_hike_course(
                page,
                SimpleNamespace(
                    limitMaxTime=0,
                    course_urls=["course"],
                    remove_pause="js",
                ),
                _Logger(),
                close_popup=lambda *_args: asyncio.sleep(0),
                learning_loop=lambda *_args: asyncio.sleep(0, result=True),
                scanner=scanner,
            )
        )


@pytest.mark.parametrize("learning_result", [False, None])
def test_unconfirmed_learning_result_fails_course_after_returning_to_list(
    learning_result,
):
    lesson = _lesson("a")
    calls, dependencies = _dependencies([([lesson], _summary(1, 1))])

    async def failed_learning(*_args):
        return learning_result

    dependencies["learning_loop"] = failed_learning
    page = _Page()
    with pytest.raises(RuntimeError, match="视频未确认完成"):
        asyncio.run(
            run_hike_course(
                page,
                SimpleNamespace(
                    limitMaxTime=0,
                    course_urls=["course"],
                    remove_pause="js",
                ),
                _Logger(),
                **dependencies,
            )
        )

    assert page.goto_calls == [("course", "domcontentloaded")]


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


def test_login_redirect_after_card_click_stops_before_learning():
    lesson = _lesson("a")
    calls, dependencies = _dependencies([([lesson], _summary(1, 1))])

    async def close_to_login(page, _logger):
        page.url = "https://login.zhihuishu.com/?origin=zhs"

    dependencies["close_popup"] = close_to_login
    page = _Page()

    with pytest.raises(CourseAuthenticationError, match="打开视频后"):
        asyncio.run(
            run_hike_course(
                page,
                SimpleNamespace(
                    limitMaxTime=0,
                    course_urls=["https://hike.zhihuishu.com/course"],
                    remove_pause="js",
                ),
                _Logger(),
                **dependencies,
            )
        )

    assert calls.learns == []


def test_login_redirect_while_waiting_for_video_is_not_loose_mode():
    lesson = _lesson("a")
    calls, dependencies = _dependencies([([lesson], _summary(1, 1))])

    class _RedirectingPage(_Page):
        async def wait_for_selector(self, _selector, **_options):
            self.url = "https://login.zhihuishu.com/?origin=zhs"
            raise RuntimeError("video missing")

    logger = _Logger()
    with pytest.raises(CourseAuthenticationError, match="等待视频时"):
        asyncio.run(
            run_hike_course(
                _RedirectingPage(),
                SimpleNamespace(
                    limitMaxTime=0,
                    course_urls=["https://hike.zhihuishu.com/course"],
                    remove_pause="js",
                ),
                logger,
                **dependencies,
            )
        )

    assert calls.learns == []
    assert not any("宽松等待" in warning for warning in logger.warnings)


def test_login_redirect_when_returning_to_list_skips_optimizer_and_rescan():
    lesson = _lesson("a")
    calls, dependencies = _dependencies([([lesson], _summary(1, 1))])

    class _RedirectingPage(_Page):
        async def goto(self, url, wait_until):
            self.goto_calls.append((url, wait_until))
            self.url = "https://www.zhihuishu.com/"

    with pytest.raises(CourseAuthenticationError, match="返回课程列表时"):
        asyncio.run(
            run_hike_course(
                _RedirectingPage(),
                SimpleNamespace(
                    limitMaxTime=0,
                    course_urls=["https://hike.zhihuishu.com/course"],
                    remove_pause="js",
                ),
                _Logger(),
                **dependencies,
            )
        )

    assert len(calls.learns) == 1
    assert calls.optimizes == []
