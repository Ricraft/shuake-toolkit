# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.national_course_flow import is_course_list_url, run_national_course
from modules.course_session import CourseAuthenticationError
from modules.national_test_flow import NationalTestOutcome

sys.path.remove(_AUTOVISOR_ROOT)


def _summary(total, pending):
    return {"total": total, "pending": pending, "done": total - pending}


def _video_card():
    return {
        "key": "video-1",
        "card_id": "card-1",
        "title": "视频一",
        "section": "第一章",
        "progress": 20,
        "type": "video",
    }


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.logs = []
        self.errors = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

    def write_log(self, message):
        self.logs.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)


class _Page:
    def __init__(self, url):
        self.url = url
        self.goto_calls = []
        self.go_back_calls = 0
        self.evaluations = []

    async def wait_for_timeout(self, _timeout):
        return None

    async def wait_for_selector(self, _selector, **_options):
        return None

    async def goto(self, url, wait_until):
        self.goto_calls.append((url, wait_until))
        self.url = url

    async def go_back(self, wait_until):
        self.go_back_calls += 1
        self.url = "https://wisdom-mooc.zhihuishu.com/study/index"

    async def evaluate(self, script, argument=None):
        self.evaluations.append((script, argument))
        if "paused" in script:
            return False
        return None

    def locator(self, _selector):
        return _LoginLocator(self)


class _LoginLocator:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return 1 if "login.zhihuishu.com" in self.page.url else 0

    async def is_visible(self):
        return True

    async def all(self):
        return [self]


async def _close_popup(*_args):
    return False


async def _noop(*_args, **_kwargs):
    return None


def _config():
    return SimpleNamespace(
        course_urls=["https://wisdom-mooc.zhihuishu.com/study/index"],
        limitMaxTime=0,
        remove_pause="remove-pause",
        soundOff=True,
    )


def test_course_list_url_requires_matching_host_and_path():
    target = "https://wisdom-mooc.zhihuishu.com/study/index?id=1"
    assert is_course_list_url(
        "https://wisdom-mooc.zhihuishu.com/study/index?id=2", target
    )
    assert not is_course_list_url(
        "https://wisdom-mooc.zhihuishu.com/video/index", target
    )


def test_completed_scan_exits_without_clicking():
    async def scanner(_page):
        card = {**_video_card(), "progress": 100}
        return [card], _summary(1, 0), False

    clicks = []

    async def clicker(*args):
        clicks.append(args)
        return True

    asyncio.run(
        run_national_course(
            _Page(_config().course_urls[0]),
            _config(),
            _Logger(),
            close_popup=_close_popup,
            learning_loop=_noop,
            handler_factory=lambda: None,
            answer_handler=None,
            scanner=scanner,
            card_clicker=clicker,
        )
    )
    assert clicks == []


def test_empty_scan_stops_after_configured_limit():
    scans = 0

    async def scanner(_page):
        nonlocal scans
        scans += 1
        return [], _summary(0, 0), False

    logger = _Logger()
    asyncio.run(
        run_national_course(
            _Page(_config().course_urls[0]),
            _config(),
            logger,
            close_popup=_close_popup,
            learning_loop=_noop,
            handler_factory=lambda: None,
            answer_handler=None,
            scanner=scanner,
            empty_scan_limit=2,
        )
    )

    assert scans == 2
    assert logger.warnings[-1] == "连续 2 次未检测到课程卡片，停止本轮"


def test_broken_card_is_skipped_after_limited_click_retries():
    card = _video_card()

    async def scanner(_page):
        return [card], _summary(1, 1), False

    clicks = 0

    async def clicker(*_args):
        nonlocal clicks
        clicks += 1
        return False

    logger = _Logger()
    asyncio.run(
        run_national_course(
            _Page(_config().course_urls[0]),
            _config(),
            logger,
            close_popup=_close_popup,
            learning_loop=_noop,
            handler_factory=lambda: None,
            answer_handler=None,
            scanner=scanner,
            card_clicker=clicker,
            click_retry_limit=3,
        )
    )

    assert clicks == 3
    assert any("连续定位失败 3 次" in warning for warning in logger.warnings)


def test_video_is_learned_and_returns_to_course_list():
    card = _video_card()
    scan_results = iter(
        [
            ([card], _summary(1, 1), False),
            ([{**card, "progress": 100}], _summary(1, 0), False),
        ]
    )

    async def scanner(_page):
        return next(scan_results)

    page = _Page(_config().course_urls[0])
    learn_calls = []

    async def clicker(*_args):
        page.url = "https://wisdom-mooc.zhihuishu.com/video/index"
        return True

    async def learning_loop(*args):
        learn_calls.append(args)
        return True

    asyncio.run(
        run_national_course(
            page,
            _config(),
            _Logger(),
            close_popup=_close_popup,
            learning_loop=learning_loop,
            handler_factory=lambda: None,
            answer_handler=None,
            scanner=scanner,
            card_clicker=clicker,
            title_reader=lambda *_args: None,
            clock=lambda: 10,
        )
    )

    assert len(learn_calls) == 1
    assert page.go_back_calls == 1
    assert is_course_list_url(page.url, _config().course_urls[0])


@pytest.mark.parametrize("learning_result", [False, None])
def test_unconfirmed_video_result_returns_to_list_then_fails_course(
    learning_result,
):
    card = _video_card()

    async def scanner(_page):
        return [card], _summary(1, 1), False

    page = _Page(_config().course_urls[0])

    async def clicker(*_args):
        page.url = "https://wisdom-mooc.zhihuishu.com/video/index"
        return True

    async def failed_learning(*_args):
        return learning_result

    with pytest.raises(RuntimeError, match="视频未确认完成"):
        asyncio.run(
            run_national_course(
                page,
                _config(),
                _Logger(),
                close_popup=_close_popup,
                learning_loop=failed_learning,
                handler_factory=lambda: None,
                answer_handler=None,
                scanner=scanner,
                card_clicker=clicker,
                title_reader=lambda *_args: None,
                clock=lambda: 10,
            )
        )

    assert page.go_back_calls == 1
    assert is_course_list_url(page.url, _config().course_urls[0])


def test_video_redirect_to_login_is_a_fatal_authentication_error():
    card = _video_card()

    async def scanner(_page):
        return [card], _summary(1, 1), False

    page = _Page(_config().course_urls[0])

    async def clicker(*_args):
        page.url = "https://login.zhihuishu.com/"
        return True

    with pytest.raises(CourseAuthenticationError, match="登录状态失效"):
        asyncio.run(
            run_national_course(
                page,
                _config(),
                _Logger(),
                close_popup=_close_popup,
                learning_loop=_noop,
                handler_factory=lambda: None,
                answer_handler=None,
                scanner=scanner,
                card_clicker=clicker,
                title_reader=lambda *_args: None,
                clock=lambda: 10,
            )
        )


def test_return_failure_is_not_reported_as_completed_video():
    card = _video_card()

    async def scanner(_page):
        return [card], _summary(1, 1), False

    page = _Page(_config().course_urls[0])

    async def clicker(*_args):
        page.url = "https://wisdom-mooc.zhihuishu.com/video/index"
        return True

    async def failed_goto(url, wait_until):
        page.goto_calls.append((url, wait_until))
        raise RuntimeError("navigation offline")

    async def wrong_go_back(wait_until):
        page.go_back_calls += 1
        page.url = "https://example.com/not-course-list"

    page.goto = failed_goto
    page.go_back = wrong_go_back
    logger = _Logger()

    with pytest.raises(RuntimeError, match="返回课程列表失败"):
        asyncio.run(
            run_national_course(
                page,
                _config(),
                logger,
                close_popup=_close_popup,
                learning_loop=_noop,
                handler_factory=lambda: None,
                answer_handler=None,
                scanner=scanner,
                card_clicker=clicker,
                title_reader=lambda *_args: None,
                clock=lambda: 10,
            )
        )

    assert not any("视频播放完成，已返回课程列表" in item for item in logger.infos)


def test_confirmed_test_submission_is_not_repeated_on_stale_scan():
    card = {
        "key": "test-1",
        "card_id": "test-card-1",
        "title": "章节测试",
        "section": "第一章",
        "progress": 0,
        "type": "test",
    }
    scans = 0
    process_calls = 0

    async def scanner(_page):
        nonlocal scans
        scans += 1
        return [card], _summary(1, 1), False

    async def clicker(*_args):
        return True

    class _SubmittedSession:
        def __init__(self, *_args):
            return None

        def prepare(self):
            return None

        async def process(self):
            nonlocal process_calls
            process_calls += 1
            return NationalTestOutcome.ANSWERED

        async def cancel(self):
            return None

    asyncio.run(
        run_national_course(
            _Page(_config().course_urls[0]),
            _config(),
            _Logger(),
            close_popup=_close_popup,
            learning_loop=_noop,
            handler_factory=lambda: object(),
            answer_handler=None,
            scanner=scanner,
            card_clicker=clicker,
            test_session_factory=_SubmittedSession,
        )
    )

    assert process_calls == 1
    assert scans == 2
