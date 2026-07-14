# encoding=utf-8

import asyncio
import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.answer_strategy import AnswerAction, build_answer_actions
from modules import tasks as task_module

sys.path.remove(_AUTOVISOR_ROOT)


def test_build_actions_prefers_matched_option_ids():
    actions = build_answer_actions(
        "第二项",
        ["第一项", "第二项"],
        [(101, "第一项"), (202, "第二项")],
    )

    assert actions == [AnswerAction("index", 1, "202")]


def test_build_actions_supports_multiple_judgement_and_letter_fallbacks():
    assert build_answer_actions("正确###错误###C") == [
        AnswerAction("text", "对"),
        AnswerAction("text", "错"),
        AnswerAction("index", 2),
    ]


def test_build_actions_rejects_empty_answers_and_invalid_raw_ids():
    assert build_answer_actions("  ") == []
    assert build_answer_actions("A", ["A. 甲"], [None]) == [
        AnswerAction("index", 0)
    ]


class _Page:
    def __init__(self):
        self.waits = []

    async def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def test_answer_question_returns_false_when_any_click_fails(monkeypatch):
    clicked = []

    async def click_by_index(_page, index, _option_value=None):
        clicked.append(index)
        return index == 0

    monkeypatch.setattr(task_module, "click_option_by_index", click_by_index)

    page = _Page()
    result = asyncio.run(task_module.answer_question(page, "A###B"))

    assert result is False
    assert clicked == [0, 1]
    assert page.waits == [300]


class _TestPage(_Page):
    async def wait_for_load_state(self, _state):
        return None

    async def wait_for_selector(self, _selector, **_kwargs):
        return object()

    async def evaluate(self, _script):
        return None

    async def content(self):
        return "<html></html>"


def test_auto_answer_does_not_submit_when_option_click_failed(monkeypatch):
    submit_calls = []

    async def inject_widget(_page):
        return None

    async def failed_answer(*_args, **_kwargs):
        return False

    async def submit_exam(_page):
        submit_calls.append(True)
        return True

    monkeypatch.setattr(task_module, "inject_widget", inject_widget)
    monkeypatch.setattr(
        task_module,
        "query_question_bank",
        lambda *_args, **_kwargs: ("A", False),
    )
    monkeypatch.setattr(task_module, "answer_question", failed_answer)
    monkeypatch.setattr(task_module, "submit_exam", submit_exam)

    questions = [
        {
            "name": "测试题",
            "type": "单选题",
            "type_id": 1,
            "options": [("A", "甲"), ("B", "乙")],
        }
    ]
    result = asyncio.run(
        task_module.handle_test_page(_TestPage(), questions, auto_submit=True)
    )

    assert result is False
    assert questions[0]["answer_applied"] is False
    assert submit_calls == []
