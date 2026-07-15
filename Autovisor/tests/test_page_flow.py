# encoding=utf-8

import asyncio
import sys
import threading
import time
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules import tasks as task_module
from modules import test_page_flow as flow

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, **_kwargs):
        self.messages.append(("info", message))

    def warn(self, message, **_kwargs):
        self.messages.append(("warn", message))

    def error(self, message, **_kwargs):
        self.messages.append(("error", message))


def _question(index, question_type="单选题", type_id=1):
    return {
        "name": f"<b>题目{index}</b>",
        "type": question_type,
        "type_id": type_id,
        "options": [("A", "甲"), ("B", "乙")],
    }


def test_tasks_preserves_test_page_flow_compatibility_exports():
    assert task_module.clean_html_tags is flow.clean_html_tags
    assert task_module.query_test_answers is flow.query_test_answers


def test_clean_html_tags_decodes_entities_and_blank_parentheses():
    assert flow.clean_html_tags(
        "<b>问题</b>(&nbsp; &nbsp;)&amp;  答案"
    ) == "问题（）& 答案"


def test_query_test_answers_uses_bounded_concurrency_and_type_hints():
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    calls = []

    def query_answer(title, options, question_type):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            calls.append((title, options, question_type))
            time.sleep(0.04)
            return "A", False
        finally:
            with lock:
                active -= 1

    questions_data = [_question(index) for index in range(4)]
    asyncio.run(
        flow.query_test_answers(
            questions_data,
            query_answer=query_answer,
            logger_instance=_Logger(),
            max_concurrency=2,
        )
    )

    assert maximum_active == 2
    assert len(calls) == 4
    assert all(call[2] == "single" for call in calls)
    assert [question["answer"] for question in questions_data] == ["A"] * 4


def test_query_test_answers_isolates_one_question_failure():
    def query_answer(title, _options, _question_type):
        if title == "题目1":
            raise RuntimeError("temporary query failure")
        return "B", True

    questions_data = [_question(0), _question(1), _question(2)]
    log = _Logger()
    asyncio.run(
        flow.query_test_answers(
            questions_data,
            query_answer=query_answer,
            logger_instance=log,
        )
    )

    assert questions_data[0]["answer"] == "B"
    assert questions_data[0]["is_ai"] is True
    assert questions_data[1]["answer"] is None
    assert questions_data[1]["is_ai"] is False
    assert questions_data[2]["answer"] == "B"
    assert any("第2题题库查询失败" in message for _, message in log.messages)


def test_widget_update_passes_data_as_evaluate_argument():
    class Page:
        def __init__(self):
            self.script = None
            self.data = None

        async def evaluate(self, script, data):
            self.script = script
            self.data = data

    page = Page()
    widget_data = {
        "question": '含"引号"\\和\n换行',
        "answer": "A",
    }
    result = asyncio.run(flow._update_widget(page, widget_data))

    assert result is True
    assert page.data is widget_data
    assert widget_data["question"] not in page.script


def test_handle_test_page_rejects_malformed_question_collection():
    class Page:
        async def wait_for_load_state(self, _state):
            return None

    result = asyncio.run(
        flow.handle_test_page(
            Page(),
            {"name": "不是题目列表"},
            query_answer=lambda *_args: (None, False),
            logger_instance=_Logger(),
        )
    )

    assert result is False
