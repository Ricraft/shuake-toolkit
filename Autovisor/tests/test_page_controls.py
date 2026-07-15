# encoding=utf-8

import asyncio
import json
import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules import tasks as task_module
from modules import test_page_controls as controls

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

        def locator(self, _selector):
            return _EmptyLocator()

    result = asyncio.run(
        controls.click_next_button(Page(), logger_instance=_Logger())
    )

    assert result is False


def test_click_prev_button_reports_real_click_success():
    class Locator:
        clicked = False

        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def click(self, **_kwargs):
            self.clicked = True

    locator = Locator()

    class Page:
        def locator(self, _selector):
            return locator

    result = asyncio.run(
        controls.click_prev_button(Page(), logger_instance=_Logger())
    )

    assert result is True
    assert locator.clicked is True


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
