# encoding=utf-8

import asyncio
import sys
from pathlib import Path

import pytest
from playwright._impl._errors import TargetClosedError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.course_session import CourseAuthenticationError
from modules.national_test_flow import NationalTestOutcome, NationalTestSession

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


class _Button:
    def __init__(self, count=0):
        self._count = count
        self.first = self
        self.clicked = False

    async def count(self):
        return self._count

    async def click(self, timeout):
        self.clicked = True

    async def is_visible(self):
        return False


class _Context:
    def __init__(self, new_page=None, wait_error=None):
        self.new_page = new_page
        self.wait_error = wait_error

    async def wait_for_event(self, event, timeout):
        assert event == "page"
        assert timeout == 8000
        if self.wait_error:
            raise self.wait_error
        if self.new_page is None:
            raise TimeoutError("no page")
        return self.new_page


class _Page:
    def __init__(
        self,
        url,
        *,
        new_page=None,
        reload_error=None,
        reload_url=None,
        goto_error=None,
        goto_url=None,
        button_count=0,
        context_error=None,
    ):
        self.url = url
        self.context = _Context(new_page, context_error)
        self.reload_error = reload_error
        self.reload_url = reload_url
        self.goto_error = goto_error
        self.goto_url = goto_url
        self.button = _Button(button_count)
        self.load_states = []
        self.goto_calls = []
        self.reload_calls = []
        self.closed = False

    async def wait_for_load_state(self, state):
        self.load_states.append(state)

    def locator(self, _selector):
        return self.button

    async def reload(self, wait_until):
        self.reload_calls.append(wait_until)
        if self.reload_error:
            raise self.reload_error
        if self.reload_url:
            self.url = self.reload_url

    async def goto(self, url, wait_until):
        self.goto_calls.append((url, wait_until))
        if self.goto_error:
            raise self.goto_error
        self.url = self.goto_url or url

    async def wait_for_timeout(self, _timeout):
        return None

    async def close(self):
        self.closed = True


class _Handler:
    def __init__(
        self,
        questions=None,
        *,
        completed=False,
        wait_result=False,
        remove_error=None,
    ):
        self.questions_data = questions or []
        self.is_completed = completed
        self.wait_result = wait_result
        self.remove_error = remove_error
        self.setup_context = None
        self.removed = False
        self.wait_calls = 0

    def setup_listener(self, context):
        self.setup_context = context

    def remove_listener(self):
        self.removed = True
        if self.remove_error:
            raise self.remove_error

    async def wait_for_questions(self, timeout):
        assert timeout == 20
        self.wait_calls += 1
        return self.wait_result


def test_existing_questions_are_answered_and_resources_are_cleaned():
    page = _Page("https://wisdom-mooc.zhihuishu.com/study/index")
    handler = _Handler([{"id": 1}])
    logger = _Logger()
    answer_calls = []

    async def answer_handler(work_page, questions, **options):
        answer_calls.append((work_page, questions, options))
        return True

    session = NationalTestSession(page, page.url, logger, handler, answer_handler)

    async def run_session():
        session.prepare()
        return await session.process()

    outcome = asyncio.run(run_session())

    assert outcome is NationalTestOutcome.ANSWERED
    assert answer_calls == [
        (page, [{"id": 1}], {"auto_submit": True})
    ]
    assert handler.removed is True
    assert page.reload_calls == ["domcontentloaded"]
    assert page.goto_calls == []


def test_false_answer_result_is_not_reported_as_answered():
    page = _Page("https://wisdom-mooc.zhihuishu.com/study/index")
    handler = _Handler([{"id": 1}])
    logger = _Logger()

    async def answer_handler(*_args, **_kwargs):
        return False

    session = NationalTestSession(page, page.url, logger, handler, answer_handler)

    async def run_session():
        session.prepare()
        return await session.process()

    outcome = asyncio.run(run_session())

    assert outcome is NationalTestOutcome.ANSWER_FAILED
    assert "测试答题或提交未确认成功" in logger.warnings
    assert handler.removed is True
    assert page.reload_calls == ["domcontentloaded"]


def test_completed_test_closes_new_page_without_answering():
    test_page = _Page("https://exam.zhihuishu.com/test")
    page = _Page(
        "https://wisdom-mooc.zhihuishu.com/study/index", new_page=test_page
    )
    handler = _Handler([{"id": 1}], completed=True)
    logger = _Logger()

    async def answer_handler(*_args, **_kwargs):
        raise AssertionError("completed test must not be answered")

    session = NationalTestSession(page, page.url, logger, handler, answer_handler)

    async def run_session():
        session.prepare()
        return await session.process()

    outcome = asyncio.run(run_session())

    assert outcome is NationalTestOutcome.COMPLETED
    assert test_page.closed is True
    assert handler.removed is True


def test_missing_questions_returns_no_questions_and_triggers_start_button():
    page = _Page(
        "https://wisdom-mooc.zhihuishu.com/study/index", button_count=1
    )
    handler = _Handler(wait_result=False)
    logger = _Logger()

    async def answer_handler(*_args, **_kwargs):
        raise AssertionError("missing questions must not be answered")

    session = NationalTestSession(page, page.url, logger, handler, answer_handler)

    async def run_session():
        session.prepare()
        return await session.process()

    outcome = asyncio.run(run_session())

    assert outcome is NationalTestOutcome.NO_QUESTIONS
    assert page.button.clicked is True
    assert logger.warnings == ["没有题目数据，跳过答题"]


def test_login_popup_stops_before_waiting_for_questions():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    login_page = _Page("https://login.zhihuishu.com/?from=exam")
    page = _Page(course_url, new_page=login_page)
    handler = _Handler([{"id": 1}])
    answer_calls = []

    async def answer_handler(*_args, **_kwargs):
        answer_calls.append(True)
        return True

    session = NationalTestSession(
        page,
        course_url,
        _Logger(),
        handler,
        answer_handler,
    )

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(CourseAuthenticationError, match="测验页面登录状态失效"):
        asyncio.run(run_session())

    assert handler.wait_calls == 0
    assert answer_calls == []
    assert handler.removed is True
    assert login_page.closed is True
    assert page.reload_calls == ["domcontentloaded"]


def test_fatal_auth_survives_cleanup_and_restore_failures():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    login_page = _Page("https://login.zhihuishu.com/?from=exam")
    page = _Page(
        course_url,
        new_page=login_page,
        reload_error=RuntimeError("reload failed"),
        goto_error=RuntimeError("goto failed"),
    )
    handler = _Handler(
        [{"id": 1}],
        remove_error=RuntimeError("listener cleanup failed"),
    )
    logger = _Logger()
    session = NationalTestSession(page, course_url, logger, handler, None)

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(CourseAuthenticationError, match="测验页面登录状态失效"):
        asyncio.run(run_session())

    assert handler.removed is True
    assert login_page.closed is True
    assert page.reload_calls == ["domcontentloaded"]
    assert page.goto_calls == [(course_url, "domcontentloaded")]
    assert any("移除全国共享课测验监听器失败" in message for message in logger.logs)
    assert any("保留原始处理错误" in message for message in logger.logs)


def test_closed_context_survives_course_restore_failure():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page(
        course_url,
        context_error=TargetClosedError("context closed"),
        reload_error=RuntimeError("reload failed"),
        goto_error=RuntimeError("goto failed"),
    )
    handler = _Handler()
    logger = _Logger()
    session = NationalTestSession(page, course_url, logger, handler, None)

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(TargetClosedError, match="context closed"):
        asyncio.run(run_session())

    assert handler.removed is True
    assert any("保留原始处理错误 TargetClosedError" in message for message in logger.logs)


def test_context_wait_preserves_page_closed_error():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page(
        course_url,
        context_error=TargetClosedError("context closed"),
    )
    handler = _Handler()
    session = NationalTestSession(page, course_url, _Logger(), handler, None)

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(TargetClosedError, match="context closed"):
        asyncio.run(run_session())

    assert handler.removed is True
    assert page.reload_calls == ["domcontentloaded"]


def test_start_button_redirect_is_restored_as_authentication_error():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    test_page = _Page("https://exam.zhihuishu.com/test")

    class _RedirectingButton(_Button):
        async def click(self, timeout):
            assert timeout == 5000
            test_page.url = "https://login.zhihuishu.com/?from=start"
            raise RuntimeError("execution context destroyed")

    test_page.button = _RedirectingButton(count=1)
    page = _Page(course_url, new_page=test_page)
    handler = _Handler()
    session = NationalTestSession(page, course_url, _Logger(), handler, None)

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(CourseAuthenticationError, match="打开全国共享课测验题目"):
        asyncio.run(run_session())

    assert handler.wait_calls == 0
    assert handler.removed is True
    assert test_page.closed is True


def test_answer_redirect_is_restored_as_authentication_error():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    test_page = _Page("https://exam.zhihuishu.com/test")
    page = _Page(course_url, new_page=test_page)
    handler = _Handler([{"id": 1}])

    async def redirecting_answer(work_page, *_args, **_kwargs):
        work_page.url = "https://login.zhihuishu.com/?from=answer"
        raise RuntimeError("execution context destroyed")

    session = NationalTestSession(
        page,
        course_url,
        _Logger(),
        handler,
        redirecting_answer,
    )

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(CourseAuthenticationError, match="测验答题期间"):
        asyncio.run(run_session())

    assert handler.removed is True
    assert test_page.closed is True
    assert page.reload_calls == ["domcontentloaded"]


def test_restore_falls_back_to_course_url_when_reload_fails():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page("https://exam.zhihuishu.com/test", reload_error=RuntimeError("boom"))
    session = NationalTestSession(
        page, course_url, _Logger(), _Handler(), lambda *_args, **_kwargs: None
    )

    asyncio.run(session.restore_course_list())

    assert page.goto_calls == [(course_url, "domcontentloaded")]


def test_restore_rejects_login_redirect_after_reload():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page(
        course_url,
        reload_url="https://passport.zhihuishu.com/login",
    )
    session = NationalTestSession(
        page, course_url, _Logger(), _Handler(), lambda *_args, **_kwargs: None
    )

    with pytest.raises(CourseAuthenticationError, match="登录状态失效"):
        asyncio.run(session.restore_course_list())

    assert page.goto_calls == []


def test_restore_rejects_wrong_address_after_fallback():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page(
        "https://exam.zhihuishu.com/test",
        reload_error=RuntimeError("reload failed"),
        goto_url="https://exam.zhihuishu.com/still-here",
    )
    logger = _Logger()
    session = NationalTestSession(
        page, course_url, logger, _Handler(), lambda *_args, **_kwargs: None
    )

    with pytest.raises(RuntimeError, match="返回课程列表失败"):
        asyncio.run(session.restore_course_list())

    assert any("地址仍不是课程列表" in message for message in logger.warnings)


def test_process_does_not_hide_course_restore_failure():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page(
        course_url,
        reload_error=RuntimeError("reload failed"),
        goto_error=RuntimeError("goto failed"),
    )
    handler = _Handler([{"id": 1}])

    async def answer_handler(*_args, **_kwargs):
        return True

    session = NationalTestSession(
        page, course_url, _Logger(), handler, answer_handler
    )

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(RuntimeError, match="返回课程列表失败"):
        asyncio.run(run_session())

    assert handler.removed is True


def test_cancel_removes_listener_and_cancels_page_waiter():
    page = _Page("https://wisdom-mooc.zhihuishu.com/study/index")
    handler = _Handler()
    session = NationalTestSession(page, page.url, _Logger(), handler, None)

    async def run_cancel():
        session.prepare()
        await session.cancel()

    asyncio.run(run_cancel())

    assert handler.removed is True


def test_answer_failure_still_cleans_listener_and_restores_page():
    page = _Page("https://wisdom-mooc.zhihuishu.com/study/index")
    handler = _Handler([{"id": 1}])

    async def failing_answer(*_args, **_kwargs):
        raise RuntimeError("answer failed")

    session = NationalTestSession(page, page.url, _Logger(), handler, failing_answer)

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(RuntimeError, match="answer failed"):
        asyncio.run(run_session())

    assert handler.removed is True
    assert page.reload_calls == ["domcontentloaded"]


def test_answer_failure_survives_cleanup_and_restore_failures():
    course_url = "https://wisdom-mooc.zhihuishu.com/study/index"
    page = _Page(
        course_url,
        reload_error=RuntimeError("reload failed"),
        goto_error=RuntimeError("goto failed"),
    )
    handler = _Handler(
        [{"id": 1}],
        remove_error=RuntimeError("listener cleanup failed"),
    )
    logger = _Logger()

    async def failing_answer(*_args, **_kwargs):
        raise RuntimeError("answer failed")

    session = NationalTestSession(
        page, course_url, logger, handler, failing_answer
    )

    async def run_session():
        session.prepare()
        await session.process()

    with pytest.raises(RuntimeError, match="answer failed"):
        asyncio.run(run_session())

    assert handler.removed is True
    assert page.reload_calls == ["domcontentloaded"]
    assert page.goto_calls == [(course_url, "domcontentloaded")]
    assert any("移除全国共享课测验监听器失败" in message for message in logger.logs)
    assert any("保留原始处理错误 RuntimeError" in message for message in logger.logs)
