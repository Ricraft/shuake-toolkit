# encoding=utf-8

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright._impl._errors import TargetClosedError

_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.normal_course_flow import (
    NormalTestOutcome,
    NormalTestSession,
    check_normal_course_time_limit,
    next_normal_course_index,
    restore_normal_course_list,
    run_normal_course,
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


class _Context:
    def __init__(self, *, wait_forever=False, new_page=None, wait_error=None):
        self.wait_forever = wait_forever
        self.new_page = new_page
        self.wait_error = wait_error

    async def wait_for_event(self, _event, timeout):
        assert timeout == 8000
        if self.wait_forever:
            await asyncio.Event().wait()
        if self.wait_error:
            raise self.wait_error
        if self.new_page is not None:
            return self.new_page
        raise TimeoutError("no new page")


class _Page:
    def __init__(
        self,
        *,
        wait_forever=False,
        redirect_url=None,
        new_page=None,
        context_error=None,
        url="https://studyvideoh5.zhihuishu.com/course",
    ):
        self.context = _Context(
            wait_forever=wait_forever,
            new_page=new_page,
            wait_error=context_error,
        )
        self.url = url
        self.redirect_url = redirect_url
        self.default_timeouts = []
        self.waits = []
        self.evaluations = []
        self.goto_calls = []
        self.closed = False

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
        self.url = self.redirect_url or url

    def set_default_timeout(self, timeout):
        self.default_timeouts.append(timeout)

    async def close(self):
        self.closed = True


class _Handler:
    def __init__(
        self,
        questions=None,
        *,
        completed=False,
        wait_result=True,
        remove_error=None,
    ):
        self.questions_data = questions or []
        self.is_completed = completed
        self.wait_result = wait_result
        self.remove_error = remove_error
        self.removed = False

    def setup_listener(self, _context):
        return None

    def remove_listener(self):
        self.removed = True
        if self.remove_error:
            raise self.remove_error

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
        if selector == ".time_icofinish":
            return _TextLocator("", count=1 if self.completed else 0)
        if selector == ".progress-num":
            return _TextLocator("100%" if self.completed else "50%")
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
        return True

    session = NormalTestSession(page, _Logger(), handler, answer_handler)
    outcome = asyncio.run(session.process(_ClickableCourse()))

    assert outcome is NormalTestOutcome.ANSWERED
    assert answer_calls == [(page, [{"id": 1}], {"auto_submit": True})]
    assert handler.removed is True


def test_test_session_reports_false_answer_result_as_failure():
    page = _Page()
    handler = _Handler([{"id": 1}])
    logger = _Logger()

    async def answer_handler(*_args, **_kwargs):
        return False

    session = NormalTestSession(page, logger, handler, answer_handler)
    outcome = asyncio.run(session.process(_ClickableCourse()))

    assert outcome is NormalTestOutcome.ANSWER_FAILED
    assert "测验答题或提交未确认成功" in logger.warnings
    assert handler.removed is True


def test_test_session_rejects_login_popup_before_waiting_for_questions():
    login_page = _Page(url="https://login.zhihuishu.com/?from=exam")
    page = _Page(new_page=login_page)
    handler = _Handler([{"id": 1}])
    answer_calls = []

    async def answer_handler(*_args, **_kwargs):
        answer_calls.append(True)
        return True

    session = NormalTestSession(page, _Logger(), handler, answer_handler)

    with pytest.raises(CourseAuthenticationError, match="测验页面登录状态失效"):
        asyncio.run(session.process(_ClickableCourse()))

    assert answer_calls == []
    assert handler.removed is True
    assert login_page.closed is True


def test_fatal_auth_survives_listener_cleanup_failure_and_closes_popup():
    login_page = _Page(url="https://login.zhihuishu.com/?from=exam")
    page = _Page(new_page=login_page)
    handler = _Handler(
        [{"id": 1}],
        remove_error=RuntimeError("listener cleanup failed"),
    )
    logger = _Logger()
    session = NormalTestSession(page, logger, handler, None)

    with pytest.raises(CourseAuthenticationError, match="测验页面登录状态失效"):
        asyncio.run(session.process(_ClickableCourse()))

    assert handler.removed is True
    assert login_page.closed is True
    assert any("移除普通课测验监听器失败" in message for message in logger.logs)


def test_test_session_preserves_closed_context_error():
    page = _Page(context_error=TargetClosedError("context closed"))
    handler = _Handler([{"id": 1}])
    session = NormalTestSession(page, _Logger(), handler, None)

    with pytest.raises(TargetClosedError, match="context closed"):
        asyncio.run(session.process(_ClickableCourse()))

    assert handler.removed is True


def test_test_session_converts_click_redirect_to_authentication_error():
    page = _Page(wait_forever=True)
    handler = _Handler()

    class _RedirectingCourse:
        async def click(self):
            page.url = "https://login.zhihuishu.com/?from=course"
            raise RuntimeError("execution context destroyed")

    session = NormalTestSession(page, _Logger(), handler, None)

    with pytest.raises(CourseAuthenticationError, match="点击普通课测验后"):
        asyncio.run(session.process(_RedirectingCourse()))

    assert handler.removed is True
    assert session.new_page_task.done()


def test_test_session_converts_answer_redirect_to_authentication_error():
    page = _Page()
    handler = _Handler([{"id": 1}])

    async def redirecting_answer(work_page, *_args, **_kwargs):
        work_page.url = "https://login.zhihuishu.com/?from=answer"
        raise RuntimeError("execution context destroyed")

    session = NormalTestSession(page, _Logger(), handler, redirecting_answer)

    with pytest.raises(CourseAuthenticationError, match="测验答题期间"):
        asyncio.run(session.process(_ClickableCourse()))

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


def test_completion_message_does_not_read_stale_course_locator():
    class _StaleCourse:
        async def get_attribute(self, _name):
            raise RuntimeError("detached")

    logger = _Logger()
    result = asyncio.run(
        check_normal_course_time_limit(
            _Page(),
            0,
            [_StaleCourse()],
            "第一课",
            SimpleNamespace(limitMaxTime=0),
            logger,
            clock=lambda: 30,
        )
    )

    assert result is False
    assert '"第一课" 已完成!' in logger.infos


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


def test_normal_course_loop_limit_reports_unfinished_course():
    page = _Page()

    class _UnreadableCompletionCourse(_ClickableCourse):
        def locator(self, selector):
            if selector == ".time_icofinish":
                raise RuntimeError("detached")
            return super().locator(selector)

    course = _UnreadableCompletionCourse()

    async def class_provider(*_args, **_kwargs):
        return [course]

    with pytest.raises(RuntimeError, match="循环次数超限\\(2\\)"):
        asyncio.run(
            run_normal_course(
                page,
                SimpleNamespace(
                    remove_pause="js",
                    course_urls=["course"],
                    limitMaxTime=0,
                ),
                _Logger(),
                close_popup=lambda *_args: asyncio.sleep(0, result=False),
                learning_loop=lambda *_args: asyncio.sleep(0, result=True),
                review_loop=None,
                handler_factory=lambda: _Handler(),
                answer_handler=None,
                class_provider=class_provider,
                test_scanner=lambda *_args: asyncio.sleep(0, result=[]),
                title_reader=lambda *_args: asyncio.sleep(
                    0,
                    result="未完成课时",
                ),
                time_limit_checker=lambda *_args, **_kwargs: asyncio.sleep(
                    0,
                    result=False,
                ),
                clock=lambda: 0,
                max_loop=2,
            )
        )


@pytest.mark.parametrize("learning_result", [False, None])
def test_unconfirmed_video_result_is_not_advanced_as_completed(
    learning_result,
):
    page = _Page()
    course = _ClickableCourse()

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return [course]

    async def test_scanner(*_args):
        return []

    async def failed_learning(*_args):
        return learning_result

    async def title_reader(*_args):
        return "未完成视频"

    with pytest.raises(RuntimeError, match="视频未确认完成"):
        asyncio.run(
            run_normal_course(
                page,
                SimpleNamespace(
                    remove_pause="js",
                    course_urls=["course"],
                    limitMaxTime=0,
                ),
                _Logger(),
                close_popup=close_popup,
                learning_loop=failed_learning,
                review_loop=None,
                handler_factory=lambda: _Handler(),
                answer_handler=None,
                class_provider=class_provider,
                test_scanner=test_scanner,
                title_reader=title_reader,
                clock=lambda: 0,
            )
        )

    assert course.clicked is True


def test_completed_video_does_not_skip_next_item_when_pending_list_shrinks():
    page = _Page()
    courses = [
        _ClickableCourse(title="第一课"),
        _ClickableCourse(title="第二课"),
    ]
    learned = []

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return [course for course in courses if not course.completed]

    async def learning_loop(*_args):
        course = next(
            item for item in courses if item.clicked and not item.completed
        )
        learned.append(course.title)
        course.completed = True
        return True

    async def title_reader(*_args):
        return next(
            item.title for item in courses if item.clicked and not item.completed
        )

    async def no_time_limit(*_args, **_kwargs):
        return False

    asyncio.run(
        run_normal_course(
            page,
            SimpleNamespace(
                remove_pause="js",
                course_urls=["course"],
                limitMaxTime=0,
            ),
            _Logger(),
            close_popup=close_popup,
            learning_loop=learning_loop,
            review_loop=None,
            handler_factory=lambda: _Handler(),
            answer_handler=None,
            class_provider=class_provider,
            test_scanner=lambda *_args: asyncio.sleep(0, result=[]),
            title_reader=title_reader,
            time_limit_checker=no_time_limit,
            clock=lambda: 0,
        )
    )

    assert learned == ["第一课", "第二课"]
    assert all(course.completed for course in courses)


def test_confirmed_video_advances_when_completion_marker_is_delayed():
    page = _Page()
    course = _ClickableCourse(completed=False)

    result = asyncio.run(
        next_normal_course_index(
            page,
            course,
            3,
            learning=True,
            is_new_version=False,
            logger=_Logger(),
        )
    )

    assert result == 4


@pytest.mark.parametrize("is_new_version", [False, True])
def test_updated_completion_marker_keeps_index_for_shrinking_list(
    is_new_version,
):
    result = asyncio.run(
        next_normal_course_index(
            _Page(),
            _ClickableCourse(completed=True),
            3,
            learning=True,
            is_new_version=is_new_version,
            logger=_Logger(),
        )
    )

    assert result == 3


def test_completion_marker_read_failure_keeps_index_for_rescan():
    class _BrokenCourse(_ClickableCourse):
        def locator(self, _selector):
            raise RuntimeError("detached")

    logger = _Logger()
    result = asyncio.run(
        next_normal_course_index(
            _Page(),
            _BrokenCourse(),
            2,
            learning=True,
            is_new_version=False,
            logger=logger,
        )
    )

    assert result == 2
    assert "保留索引重新扫描" in logger.warnings[-1]


def test_detached_stable_course_handle_keeps_index_before_locator_retargets():
    class _DetachedHandle:
        async def evaluate(self, _script):
            return False

    result = asyncio.run(
        next_normal_course_index(
            _Page(),
            _ClickableCourse(completed=False),
            1,
            learning=True,
            is_new_version=True,
            logger=_Logger(),
            course_handle=_DetachedHandle(),
        )
    )

    assert result == 1


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
    assert "测验确认完成后状态未刷新，本轮不重复处理" in logger.warnings
    assert page.goto_calls == [("second-course", "domcontentloaded")]


def test_completed_test_does_not_skip_next_item_when_pending_list_shrinks():
    page = _Page()
    logger = _Logger()
    courses = [
        _ClickableCourse(
            class_name="chapter-test",
            title="第一章测验",
        ),
        _ClickableCourse(
            class_name="chapter-test",
            title="第二章测验",
        ),
    ]
    completed = []

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return [course for course in courses if not course.completed]

    async def optimizer(*_args):
        return None

    class _CompletedSession:
        def __init__(self, *_args):
            return None

        async def process(self, selected_course):
            completed.append(selected_course.title)
            selected_course.completed = True
            return NormalTestOutcome.COMPLETED

    asyncio.run(
        run_normal_course(
            page,
            SimpleNamespace(
                remove_pause="js",
                course_urls=["course"],
                limitMaxTime=0,
            ),
            logger,
            close_popup=close_popup,
            learning_loop=None,
            review_loop=None,
            handler_factory=lambda: _Handler(),
            answer_handler=None,
            class_provider=class_provider,
            test_scanner=lambda *_args: asyncio.sleep(0, result=[]),
            optimizer=optimizer,
            test_session_factory=_CompletedSession,
            clock=lambda: 0,
        )
    )

    assert completed == ["第一章测验", "第二章测验"]
    assert all(course.completed for course in courses)


def test_failed_test_submission_retries_before_advancing():
    page = _Page()
    logger = _Logger()
    course = _ClickableCourse(class_name="chapter-test", title="第一章测验")
    outcomes = iter(
        [NormalTestOutcome.ANSWER_FAILED, NormalTestOutcome.COMPLETED]
    )
    process_calls = 0

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return [course]

    async def test_scanner(*_args):
        return []

    async def optimizer(*_args):
        return None

    class _RetrySession:
        def __init__(self, *_args):
            return None

        async def process(self, _selected_course):
            nonlocal process_calls
            process_calls += 1
            return next(outcomes)

    asyncio.run(
        run_normal_course(
            page,
            SimpleNamespace(
                remove_pause="js",
                course_urls=["course"],
                limitMaxTime=0,
            ),
            logger,
            close_popup=close_popup,
            learning_loop=None,
            review_loop=None,
            handler_factory=lambda: _Handler(),
            answer_handler=None,
            class_provider=class_provider,
            test_scanner=test_scanner,
            optimizer=optimizer,
            test_session_factory=_RetrySession,
            clock=lambda: 0,
        )
    )

    assert process_calls == 2
    assert any("准备第 2 次尝试" in warning for warning in logger.warnings)


def test_restore_course_list_rejects_homepage_redirect_before_optimizer():
    page = _Page(redirect_url="https://www.zhihuishu.com/")
    optimized = []

    with pytest.raises(CourseAuthenticationError, match="返回课程列表时"):
        asyncio.run(
            restore_normal_course_list(
                page,
                SimpleNamespace(),
                _Logger(),
                course_url="https://studyvideoh5.zhihuishu.com/course",
                is_new_version=False,
                optimizer=lambda *_args: optimized.append(True),
            )
        )

    assert optimized == []


def test_video_open_login_redirect_stops_before_learning():
    class _RedirectingPage(_Page):
        async def wait_for_selector(self, selector, **options):
            await super().wait_for_selector(selector, **options)
            if selector == ".current_play":
                self.url = "https://login.zhihuishu.com/?origin=zhs"

    page = _RedirectingPage()
    course = _ClickableCourse()
    learning_calls = []

    async def close_popup(*_args):
        return False

    async def class_provider(*_args, **_kwargs):
        return [course]

    with pytest.raises(CourseAuthenticationError, match="打开视频后"):
        asyncio.run(
            run_normal_course(
                page,
                SimpleNamespace(
                    remove_pause="js",
                    course_urls=["course"],
                    limitMaxTime=0,
                ),
                _Logger(),
                close_popup=close_popup,
                learning_loop=lambda *_args: learning_calls.append(True),
                review_loop=None,
                handler_factory=lambda: _Handler(),
                answer_handler=None,
                class_provider=class_provider,
                test_scanner=lambda *_args: asyncio.sleep(0, result=[]),
                clock=lambda: 0,
            )
        )

    assert learning_calls == []
