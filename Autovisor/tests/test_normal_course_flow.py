# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.normal_course_flow import (
    NormalTestOutcome,
    NormalTestSession,
    check_normal_course_time_limit,
    run_normal_course,
)

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


class _Context:
    def __init__(self, *, wait_forever=False):
        self.wait_forever = wait_forever

    async def wait_for_event(self, _event, timeout):
        assert timeout == 8000
        if self.wait_forever:
            await asyncio.Event().wait()
        raise TimeoutError("no new page")


class _Page:
    def __init__(self, *, wait_forever=False):
        self.context = _Context(wait_forever=wait_forever)
        self.url = "https://studyvideoh5.zhihuishu.com/course"
        self.default_timeouts = []
        self.waits = []
        self.evaluations = []
        self.goto_calls = []

    async def wait_for_load_state(self, _state):
        return None

    async def wait_for_timeout(self, timeout):
        self.waits.append(timeout)

    async def wait_for_selector(self, selector, **options):
        self.waits.append((selector, options))

    async def evaluate(self, script):
        self.evaluations.append(script)

    async def goto(self, url, wait_until):
        self.goto_calls.append((url, wait_until))

    def set_default_timeout(self, timeout):
        self.default_timeouts.append(timeout)


class _Handler:
    def __init__(self, questions=None, *, completed=False, wait_result=True):
        self.questions_data = questions or []
        self.is_completed = completed
        self.wait_result = wait_result
        self.removed = False

    def setup_listener(self, _context):
        return None

    def remove_listener(self):
        self.removed = True

    async def wait_for_questions(self, timeout):
        assert timeout == 20
        return self.wait_result


class _ClickableCourse:
    def __init__(
        self,
        *,
        click_error=None,
        class_name="video current_play",
        title="测验",
        completed=False,
    ):
        self.click_error = click_error
        self.class_name = class_name
        self.title = title
        self.completed = completed
        self.clicked = False

    async def click(self):
        if self.click_error:
            raise self.click_error
        self.clicked = True

    async def get_attribute(self, _name):
        return self.class_name

    def locator(self, selector):
        if selector == ".name":
            return _TextLocator(self.title)
        if selector == "b.finish":
            return _TextLocator("", count=1 if self.completed else 0)
        return _TextLocator("", count=0)


class _TextLocator:
    def __init__(self, text, *, count=1):
        self.text = text
        self._count = count
        self.first = self

    async def count(self):
        return self._count

    async def text_content(self):
        return self.text


def test_test_session_cancels_page_waiter_when_click_fails():
    page = _Page(wait_forever=True)
    handler = _Handler()
    session = NormalTestSession(page, _Logger(), handler, None)
    course = _ClickableCourse(click_error=RuntimeError("boom"))

    outcome = asyncio.run(session.process(course))

    assert outcome is NormalTestOutcome.CLICK_FAILED
    assert handler.removed is True
    assert session.new_page_task.done()


def test_test_session_answers_questions_and_cleans_listener():
    page = _Page()
    handler = _Handler([{"id": 1}])
    answer_calls = []

    async def answer_handler(work_page, questions, **options):
        answer_calls.append((work_page, questions, options))

    session = NormalTestSession(page, _Logger(), handler, answer_handler)
    outcome = asyncio.run(session.process(_ClickableCourse()))

    assert outcome is NormalTestOutcome.ANSWERED
    assert answer_calls == [(page, [{"id": 1}], {"auto_submit": True})]
    assert handler.removed is True


def test_time_limit_is_checked_without_global_config_state():
    page = _Page()
    logger = _Logger()
    config = SimpleNamespace(limitMaxTime=1)

    result = asyncio.run(
        check_normal_course_time_limit(
            page,
            0,
            [_ClickableCourse()],
            "课程",
            config,
            logger,
            clock=lambda: 120,
        )
    )

    assert result is True
    assert logger.infos[-1] == "即将进入下门课程!"
    assert page.default_timeouts == []


def test_empty_normal_course_list_finishes_cleanly():
    page = _Page()
    logger = _Logger()

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return []

    async def test_scanner(*_args):
        return []

    asyncio.run(
        run_normal_course(
            page,
            SimpleNamespace(remove_pause="js", course_urls=["course"]),
            logger,
            close_popup=close_popup,
            learning_loop=None,
            review_loop=None,
            handler_factory=None,
            answer_handler=None,
            class_provider=class_provider,
            test_scanner=test_scanner,
            clock=lambda: 0,
        )
    )

    assert logger.infos[-1] == "本页课程列表已遍历完毕。"


def test_answered_test_is_not_repeated_when_completion_state_is_stale():
    page = _Page()
    logger = _Logger()
    course = _ClickableCourse(class_name="chapter-test", title="第一章测验")
    session_calls = []

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return [course]

    async def test_scanner(*_args):
        return []

    async def optimizer(*_args):
        return None

    class _AnsweredSession:
        def __init__(self, *_args):
            return None

        async def process(self, selected_course):
            session_calls.append(selected_course)
            return NormalTestOutcome.ANSWERED

    asyncio.run(
        run_normal_course(
            page,
            SimpleNamespace(
                remove_pause="js", course_urls=["course"], limitMaxTime=0
            ),
            logger,
            close_popup=close_popup,
            learning_loop=None,
            review_loop=None,
            handler_factory=lambda: _Handler(),
            answer_handler=None,
            course_url="second-course",
            class_provider=class_provider,
            test_scanner=test_scanner,
            optimizer=optimizer,
            test_session_factory=_AnsweredSession,
            clock=lambda: 0,
        )
    )

    assert session_calls == [course]
    assert "测验提交后状态未刷新，本轮不重复作答" in logger.warnings
    assert page.goto_calls == [("second-course", "domcontentloaded")]
