# encoding=utf-8

import asyncio
import json
import sys
from pathlib import Path

import pytest


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules import tasks as task_module
from modules import test_page_controls as controls
from modules.course_session import CourseAuthenticationError

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def info(self, *_args, **_kwargs):
        return None

    def warn(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class _EmptyLocator:
    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    def locator(self, _selector):
        return self

    async def count(self):
        return 0


class _FailingKeyboard:
    async def press(self, _key):
        raise RuntimeError("keyboard unavailable")


def test_force_click_uses_playwright_dispatch_event_fallback():
    class Locator:
        def __init__(self):
            self.events = []

        async def click(self, **_kwargs):
            raise RuntimeError("click blocked")

        async def dispatch_event(self, event):
            self.events.append(event)

    locator = Locator()
    result = asyncio.run(controls._force_click_locator(locator))

    assert result == "dispatch-event-click"
    assert locator.events == ["click"]


def test_click_next_button_reports_failure_when_all_fallbacks_fail():
    class Page:
        keyboard = _FailingKeyboard()

        async def evaluate(self, _script):
            return {
                "href": "https://example.test/exam",
                "question": "第一题",
                "active": "1",
            }

        def locator(self, _selector):
            return _EmptyLocator()

    result = asyncio.run(
        controls.click_next_button(Page(), logger_instance=_Logger())
    )

    assert result is False


def test_click_prev_button_reports_real_click_success():
    class Locator:
        def __init__(self, page):
            self.page = page
            self.clicked = False

        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def click(self, **_kwargs):
            self.clicked = True
            self.page.question = "第一题"

    class Page:
        question = "第二题"

        def __init__(self):
            self.keyboard = _FailingKeyboard()
            self.locator_instance = Locator(self)

        async def evaluate(self, _script):
            return {
                "href": "https://example.test/exam",
                "question": self.question,
                "active": "",
            }

        async def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return self.locator_instance

    page = Page()
    result = asyncio.run(
        controls.click_prev_button(page, logger_instance=_Logger())
    )

    assert result is True
    assert page.locator_instance.clicked is True


def test_prev_button_fallback_uses_valid_playwright_text_engine():
    class Page:
        keyboard = _FailingKeyboard()

        def __init__(self):
            self.selectors = []

        async def evaluate(self, _script):
            return {
                "href": "https://example.test/exam",
                "question": "第二题",
                "active": "2",
            }

        def locator(self, selector):
            self.selectors.append(selector)
            return _EmptyLocator()

    page = Page()
    result = asyncio.run(
        controls.click_prev_button(page, logger_instance=_Logger())
    )

    assert result is False
    assert "text=上一题" in page.selectors
    assert all(not selector.startswith("text*=") for selector in page.selectors)


def test_next_click_without_question_change_is_not_reported_as_success():
    class Keyboard:
        def __init__(self):
            self.calls = []

        async def press(self, key):
            self.calls.append(key)

    class Locator:
        clicked = False

        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def click(self, **_kwargs):
            self.clicked = True

    class Page:
        keyboard = Keyboard()
        locator_instance = Locator()

        async def evaluate(self, _script):
            return {
                "href": "https://example.test/exam",
                "question": "第一题",
                "active": "1",
            }

        async def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return self.locator_instance

    page = Page()
    result = asyncio.run(
        controls.click_next_button(page, logger_instance=_Logger())
    )

    assert result is False
    assert page.locator_instance.clicked is True
    assert page.keyboard.calls == []


def test_keyboard_navigation_requires_and_accepts_question_change():
    class Keyboard:
        def __init__(self, page):
            self.page = page

        async def press(self, _key):
            self.page.question = "第二题"

    class Page:
        question = "第一题"

        def __init__(self):
            self.keyboard = Keyboard(self)

        async def evaluate(self, _script):
            return {
                "href": "https://example.test/exam",
                "question": self.question,
                "active": "",
            }

        async def wait_for_timeout(self, _milliseconds):
            return None

        def locator(self, _selector):
            return _EmptyLocator()

    result = asyncio.run(
        controls.click_next_button(Page(), logger_instance=_Logger())
    )

    assert result is True


class _SubmitLocator:
    def __init__(self, *, count=0, visible=False):
        self._count = count
        self._visible = visible
        self.clicked = False

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def click(self, **_kwargs):
        self.clicked = True


class _SubmitPage:
    def __init__(self, *, result_visible=False):
        self.url = "https://example.test/exam"
        self.submit = _SubmitLocator(count=1, visible=True)
        self.result = _SubmitLocator(
            count=int(result_visible), visible=result_visible
        )
        self.empty = _SubmitLocator()

    def locator(self, selector):
        if "提交作业" in selector:
            return self.submit
        if ".exam-result" in selector:
            return self.result
        return self.empty

    async def wait_for_timeout(self, _milliseconds):
        return None

    def is_closed(self):
        return False


def test_submit_exam_does_not_treat_a_click_as_completion():
    page = _SubmitPage(result_visible=False)

    result = asyncio.run(
        controls.submit_exam(page, logger_instance=_Logger())
    )

    assert page.submit.clicked is True
    assert result is False


def test_submit_exam_accepts_visible_result_state():
    page = _SubmitPage(result_visible=True)

    result = asyncio.run(
        controls.submit_exam(page, logger_instance=_Logger())
    )

    assert result is True


def test_submit_exam_waits_for_delayed_result_state():
    class DelayedResult(_SubmitLocator):
        def __init__(self):
            super().__init__(count=1, visible=True)
            self.checks = 0

        async def count(self):
            self.checks += 1
            return 1 if self.checks >= 3 else 0

    page = _SubmitPage()
    page.result = DelayedResult()

    result = asyncio.run(
        controls.submit_exam(page, logger_instance=_Logger())
    )

    assert result is True
    assert page.result.checks == 3


def test_click_option_by_text_embeds_special_text_as_json():
    expected = '含"引号"、反斜杠\\与\n换行'

    class Page:
        def __init__(self):
            self.scripts = []

        async def evaluate(self, script):
            self.scripts.append(script)
            return None

        def locator(self, _selector):
            return _EmptyLocator()

    page = Page()
    result = asyncio.run(
        controls.click_option_by_text(
            page,
            expected,
            logger_instance=_Logger(),
        )
    )

    assert result is False
    assert f"const expected = {json.dumps(expected, ensure_ascii=False)};" in page.scripts[0]


def test_has_selected_answer_uses_current_page_state():
    class Page:
        def __init__(self):
            self.script = ""

        async def evaluate(self, script):
            self.script = script
            return True

    page = Page()
    result = asyncio.run(controls.has_selected_answer(page))

    assert result is True
    assert 'input[type="radio"]' in page.script
    assert '[contenteditable="true"]' in page.script


def test_has_selected_answer_returns_unknown_on_page_error():
    class Page:
        async def evaluate(self, _script):
            raise RuntimeError("selection context destroyed")

    result = asyncio.run(controls.has_selected_answer(Page()))

    assert result is None


def test_submit_exam_does_not_accept_disappearing_button_as_completion():
    class DisappearingSubmit(_SubmitLocator):
        async def click(self, **_kwargs):
            self.clicked = True
            self._count = 0
            self._visible = False

    page = _SubmitPage()
    page.submit = DisappearingSubmit(count=1, visible=True)

    result = asyncio.run(
        controls.submit_exam(page, logger_instance=_Logger())
    )

    assert page.submit.clicked is True
    assert result is False


def test_submit_exam_reports_completion_probe_errors():
    class BrokenResult(_SubmitLocator):
        async def count(self):
            raise RuntimeError("result probe failed")

    class CaptureLogger(_Logger):
        def __init__(self):
            self.warnings = []

        def warn(self, message, **_kwargs):
            self.warnings.append(message)

    page = _SubmitPage()
    page.result = BrokenResult()
    active_logger = CaptureLogger()

    result = asyncio.run(
        controls.submit_exam(page, logger_instance=active_logger)
    )

    assert result is False
    assert any(
        "result probe failed" in message
        for message in active_logger.warnings
    )


def test_submission_completion_keeps_text_engines_out_of_css_groups():
    class Page:
        url = "https://example.test/exam"

        def __init__(self):
            self.selectors = []

        def is_closed(self):
            return False

        def locator(self, selector):
            self.selectors.append(selector)
            if "," in selector and "text" in selector:
                raise RuntimeError("invalid mixed selector")
            if selector == "text='提交成功'":
                return _SubmitLocator(count=1, visible=True)
            return _SubmitLocator()

    page = Page()
    result = asyncio.run(
        controls._submission_completed(page, page.url)
    )

    assert result is True
    assert page.selectors[-1] == "text='提交成功'"
    assert all(
        not ("," in selector and "text=" in selector)
        for selector in page.selectors
    )


def test_submit_exam_propagates_login_redirect_as_authentication_failure():
    class RedirectingSubmit(_SubmitLocator):
        def __init__(self, page):
            super().__init__(count=1, visible=True)
            self.page = page

        async def click(self, **_kwargs):
            self.clicked = True
            self.page.url = "https://login.zhihuishu.com/?origin=exam"

    page = _SubmitPage()
    page.submit = RedirectingSubmit(page)

    with pytest.raises(
        CourseAuthenticationError,
        match="交卷期间登录状态失效",
    ):
        asyncio.run(
            controls.submit_exam(page, logger_instance=_Logger())
        )


def test_submit_exam_rejects_close_before_visible_confirmation_is_clicked():
    class ClosingConfirmation(_SubmitLocator):
        async def click(self, **_kwargs):
            raise controls.TargetClosedError("page closed before confirm")

    page = _SubmitPage()
    confirmation = ClosingConfirmation(count=1, visible=True)
    original_locator = page.locator

    def locator(selector):
        if "el-button--primary:has-text" in selector:
            return confirmation
        return original_locator(selector)

    page.locator = locator
    result = asyncio.run(
        controls.submit_exam(page, logger_instance=_Logger())
    )

    assert page.submit.clicked is True
    assert result is False


def test_wait_for_user_action_distinguishes_page_error_from_timeout():
    class Page:
        async def evaluate(self, _script):
            raise RuntimeError("execution context was destroyed")

    result = asyncio.run(
        controls.wait_for_user_action(
            Page(),
            timeout=0.01,
            poll_interval=0,
        )
    )

    assert result == "error"


class _FlowPage:
    async def wait_for_load_state(self, _state):
        return None

    async def wait_for_selector(self, _selector, **_kwargs):
        return object()

    async def evaluate(self, _script):
        return None

    async def content(self):
        return "<html></html>"

    async def wait_for_timeout(self, _milliseconds):
        return None


def test_handle_test_page_stops_when_next_button_click_fails(monkeypatch):
    submit_calls = []

    async def no_op(*_args, **_kwargs):
        return None

    async def answered(*_args, **_kwargs):
        return True

    async def next_failed(*_args, **_kwargs):
        return False

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "answer_question", answered)
    monkeypatch.setattr(task_module, "click_next_button", next_failed)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    questions = [
        {
            "name": f"测试题{index}",
            "type": "单选题",
            "type_id": 1,
            "options": [("A", "甲"), ("B", "乙")],
        }
        for index in range(2)
    ]
    result = asyncio.run(
        task_module.handle_test_page(
            _FlowPage(),
            questions,
            auto_submit=True,
        )
    )

    assert result is False
    assert questions[0]["answer_applied"] is True
    assert "answer_applied" not in questions[1]
    assert submit_calls == []


def test_manual_submit_handoff_is_not_reported_as_confirmed_submission(monkeypatch):
    submit_calls = []

    async def no_op(*_args, **_kwargs):
        return None

    async def answered(*_args, **_kwargs):
        return True

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "answer_question", answered)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    result = asyncio.run(
        task_module.handle_test_page(
            _FlowPage(),
            [
                {
                    "name": "待手动提交",
                    "type": "单选题",
                    "type_id": 1,
                    "options": [("A", "甲"), ("B", "乙")],
                }
            ],
            auto_submit=True,
            manual_submit=True,
        )
    )

    assert result is False
    assert submit_calls == []


def _manual_question(question_type="单选题"):
    return {
        "name": "手动作答测试题",
        "type": question_type,
        "type_id": 2 if "多选" in question_type else 1,
        "options": [("A", "甲"), ("B", "乙")],
    }


def test_manual_answer_uses_page_selection_even_without_bank_answer(monkeypatch):
    submit_calls = []

    async def no_op(*_args, **_kwargs):
        return None

    async def next_action(*_args, **_kwargs):
        return "next"

    async def selected(*_args, **_kwargs):
        return True

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: (None, False),
    )
    monkeypatch.setattr(task_module, "wait_for_user_action", next_action)
    monkeypatch.setattr(task_module, "has_selected_answer", selected)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    question = _manual_question("多选题")
    result = asyncio.run(
        task_module.handle_test_page(_FlowPage(), [question])
    )

    assert result is True
    assert question["answer_applied"] is True
    assert submit_calls == [True]


def test_manual_answer_does_not_use_bank_hit_as_page_selection(monkeypatch):
    submit_calls = []

    async def no_op(*_args, **_kwargs):
        return None

    async def next_action(*_args, **_kwargs):
        return "next"

    async def not_selected(*_args, **_kwargs):
        return False

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "wait_for_user_action", next_action)
    monkeypatch.setattr(task_module, "has_selected_answer", not_selected)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    question = _manual_question()
    result = asyncio.run(
        task_module.handle_test_page(_FlowPage(), [question])
    )

    assert result is False
    assert question["answer_applied"] is False
    assert submit_calls == []


def test_manual_answer_stops_cleanly_when_page_is_closed(monkeypatch):
    selection_checks = []
    submit_calls = []

    async def no_op(*_args, **_kwargs):
        return None

    async def closed_action(*_args, **_kwargs):
        return "closed"

    async def selected(*_args, **_kwargs):
        selection_checks.append(True)
        return True

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "wait_for_user_action", closed_action)
    monkeypatch.setattr(task_module, "has_selected_answer", selected)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    result = asyncio.run(
        task_module.handle_test_page(_FlowPage(), [_manual_question()])
    )

    assert result is False
    assert selection_checks == []
    assert submit_calls == []


def test_manual_answer_stops_when_page_listener_fails(monkeypatch):
    selection_checks = []
    navigation_calls = []
    submit_calls = []

    class CaptureLogger(_Logger):
        def __init__(self):
            self.errors = []

        def error(self, message, **_kwargs):
            self.errors.append(message)

    async def no_op(*_args, **_kwargs):
        return None

    async def error_action(*_args, **_kwargs):
        return "error"

    async def selected(*_args, **_kwargs):
        selection_checks.append(True)
        return True

    async def next_page(*_args, **_kwargs):
        navigation_calls.append(True)
        return True

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "wait_for_user_action", error_action)
    monkeypatch.setattr(task_module, "has_selected_answer", selected)
    monkeypatch.setattr(task_module, "click_next_button", next_page)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    logger = CaptureLogger()
    monkeypatch.setattr(task_module, "logger", logger)
    result = asyncio.run(
        task_module.handle_test_page(
            _FlowPage(),
            [_manual_question()],
        )
    )

    assert result is False
    assert selection_checks == []
    assert navigation_calls == []
    assert submit_calls == []
    assert any("页面监听异常" in message for message in logger.errors)


def test_manual_answer_stops_when_selection_state_is_unknown(monkeypatch):
    navigation_calls = []
    submit_calls = []

    class CaptureLogger(_Logger):
        def __init__(self):
            self.errors = []

        def error(self, message, **_kwargs):
            self.errors.append(message)

    async def no_op(*_args, **_kwargs):
        return None

    async def next_action(*_args, **_kwargs):
        return "next"

    async def unknown_selection(*_args, **_kwargs):
        return None

    async def next_page(*_args, **_kwargs):
        navigation_calls.append(True)
        return True

    async def submit(*_args, **_kwargs):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", no_op)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "wait_for_user_action", next_action)
    monkeypatch.setattr(task_module, "has_selected_answer", unknown_selection)
    monkeypatch.setattr(task_module, "click_next_button", next_page)
    monkeypatch.setattr(task_module, "submit_exam", submit)

    logger = CaptureLogger()
    monkeypatch.setattr(task_module, "logger", logger)
    result = asyncio.run(
        task_module.handle_test_page(
            _FlowPage(),
            [_manual_question()],
        )
    )

    assert result is False
    assert navigation_calls == []
    assert submit_calls == []
    assert any("无法确认当前题作答状态" in message for message in logger.errors)
