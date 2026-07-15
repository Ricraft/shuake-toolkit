# encoding=utf-8

import asyncio
import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from playwright._impl._errors import TargetClosedError
from modules import in_class_questions as questions
from modules import tasks as task_module

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, **_kwargs):
        self.messages.append(("info", message))

    def warn(self, message, **_kwargs):
        self.messages.append(("warn", message))

    def write_log(self, message, **_kwargs):
        self.messages.append(("log", message))


class _Locator:
    def __init__(
        self,
        *,
        count=0,
        visible=False,
        text="",
        items=None,
        children=None,
        on_click=None,
    ):
        self._count = count
        self._visible = visible
        self._text = text
        self._items = items or []
        self._children = children or {}
        self._on_click = on_click

    @property
    def first(self):
        return self

    async def count(self):
        return len(self._items) if self._items else self._count

    async def is_visible(self):
        return self._visible

    async def text_content(self):
        return self._text

    async def click(self, **_kwargs):
        if self._on_click:
            self._on_click()

    def nth(self, index):
        return self._items[index]

    def locator(self, selector):
        for key, locator in self._children.items():
            if key in selector:
                return locator
        return _Locator()


def _wisdom_option(letter, answer, clicked, index):
    return _Locator(
        count=1,
        visible=True,
        text=f"{letter}. {answer}",
        children={
            ".class-question-select": _Locator(count=1, text=letter),
            ".answer": _Locator(count=1, text=answer),
        },
        on_click=lambda: clicked.append(index),
    )


def test_tasks_preserves_in_class_question_compatibility_exports():
    assert task_module._extract_question_title is questions._extract_question_title
    assert task_module._extract_options is questions._extract_options
    assert (
        task_module.wait_for_question_resolution
        is questions.wait_for_question_resolution
    )


def test_fallback_choice_count_respects_single_and_multiple_types():
    choose_last = lambda count: count - 1

    assert questions._fallback_choice_indexes(
        3,
        False,
        randrange=choose_last,
    ) == [2]
    assert questions._fallback_choice_indexes(
        3,
        True,
        randrange=choose_last,
    ) == [2, 0]


def test_wisdom_dialog_queries_bank_before_fallback():
    clicked = []
    submitted = []
    keyboard_presses = []
    option_items = [
        _wisdom_option("A", "甲", clicked, 0),
        _wisdom_option("B", "乙", clicked, 1),
    ]
    options = _Locator(items=option_items)
    dialog = _Locator(count=1, visible=True)

    def hide_dialog():
        submitted.append(True)
        dialog._visible = False

    dialog._children = {
        ".header-icon": _Locator(),
        ".question-info": _Locator(count=1, text="正确答案是哪项？"),
        ".option": options,
        'input[type="checkbox"]': _Locator(),
        "button:has-text('提交')": _Locator(
            count=1,
            visible=True,
            on_click=hide_dialog,
        ),
    }

    class Keyboard:
        async def press(self, key):
            keyboard_presses.append(key)

    class Page:
        keyboard = Keyboard()

        async def wait_for_timeout(self, _milliseconds):
            return None

    query_calls = []

    def query_answer(title, option_text):
        query_calls.append((title, option_text))
        return "B", False

    result = asyncio.run(
        questions.handle_wisdom_dialog(
            Page(),
            dialog,
            query_answer=query_answer,
            logger_instance=_Logger(),
            randrange=lambda _count: 0,
        )
    )

    assert result is True
    assert query_calls == [("正确答案是哪项？", "A. 甲\nB. 乙")]
    assert clicked == [1]
    assert submitted == [True]
    assert keyboard_presses == ["Escape"]


class _TextElement:
    def __init__(self, text):
        self.text = text

    async def text_content(self):
        return self.text


class _QuestionNumber:
    def __init__(self):
        self.clicked = False

    async def click(self, **_kwargs):
        self.clicked = True


def test_standard_single_fallback_clicks_only_one_option():
    clicked = []
    question_number = _QuestionNumber()
    choices = _Locator(
        items=[
            _Locator(on_click=lambda index=index: clicked.append(index))
            for index in range(3)
        ]
    )

    class Container:
        async def query_selector_all(self, selector):
            assert selector == ".number"
            return [question_number]

    class Page:
        def __init__(self):
            self.closed_dialog = False

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def query_selector(self, selector):
            if selector == ".answer":
                return None
            if selector == ".topic-title":
                return _TextElement("测试题")
            return None

        async def query_selector_all(self, selector):
            if selector == ".topic-item":
                return [_TextElement("甲"), _TextElement("乙"), _TextElement("丙")]
            return []

        def locator(self, selector):
            if selector == ".topic-item":
                return choices
            return _Locator()

        async def press(self, selector, key, **_kwargs):
            self.closed_dialog = (selector, key) == (".el-dialog", "Escape")

    page = Page()
    result = asyncio.run(
        questions.handle_standard_dialog(
            page,
            Container(),
            query_answer=lambda *_args: (None, False),
            logger_instance=_Logger(),
            randrange=lambda _count: 1,
        )
    )

    assert result is True
    assert question_number.clicked is True
    assert clicked == [1]
    assert page.closed_dialog is True


def test_wait_for_question_resolution_clears_stale_signal():
    class Page:
        async def query_selector(self, _selector):
            return None

    event = asyncio.Event()
    event.set()
    result = asyncio.run(
        questions.wait_for_question_resolution(
            Page(),
            event,
            (".topic-title",),
        )
    )

    assert result is False
    assert not event.is_set()


def test_hike_page_pauses_listener_instead_of_terminating(monkeypatch):
    sleep_calls = []

    class Page:
        def __init__(self):
            self.url = "https://hike.zhihuishu.com/course"
            self.normal_poll_reached = False

        async def wait_for_load_state(self, _state):
            return None

        async def query_selector(self, _selector):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            self.normal_poll_reached = True
            raise TargetClosedError("page closed")

    page = Page()

    async def switch_course(_seconds):
        sleep_calls.append(True)
        if len(sleep_calls) == 1:
            page.url = "https://study.zhihuishu.com/course"

    monkeypatch.setattr(questions.asyncio, "sleep", switch_course)
    asyncio.run(
        questions.skip_questions(
            page,
            asyncio.Event(),
            query_answer=lambda *_args: (None, False),
            logger_instance=_Logger(),
            poll_interval=0,
        )
    )

    assert len(sleep_calls) >= 2
    assert page.normal_poll_reached is True


def test_hike_manual_question_unblocks_after_it_disappears(monkeypatch):
    class Page:
        url = "https://hike.zhihuishu.com/course"

        def __init__(self):
            self.question_visible = True

        async def wait_for_load_state(self, _state):
            return None

        async def query_selector(self, selector):
            if selector == ".question-info" and self.question_visible:
                return object()
            return None

    page = Page()
    event = asyncio.Event()
    event.set()
    sleep_calls = 0

    async def finish_manually(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            assert not event.is_set()
            page.question_visible = False
            return
        raise TargetClosedError("page closed")

    monkeypatch.setattr(questions.asyncio, "sleep", finish_manually)
    asyncio.run(
        questions.skip_questions(
            page,
            event,
            query_answer=lambda *_args: (None, False),
            logger_instance=_Logger(),
            poll_interval=0,
        )
    )

    assert event.is_set()
