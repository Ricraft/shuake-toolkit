# encoding=utf-8

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

from playwright._impl._errors import TargetClosedError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.test_capture import TestResponseHandler

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)


class _Emitter:
    def __init__(self):
        self.handlers = defaultdict(list)

    def on(self, event, handler):
        self.handlers[event].append(handler)

    def remove_listener(self, event, handler):
        if handler in self.handlers[event]:
            self.handlers[event].remove(handler)

    async def emit(self, event, value):
        for handler in list(self.handlers[event]):
            result = handler(value)
            if asyncio.iscoroutine(result):
                await result


class _Page(_Emitter):
    def __init__(self, url):
        super().__init__()
        self.url = url


class _Context(_Emitter):
    def __init__(self, pages=()):
        super().__init__()
        self.pages = list(pages)


class _Response:
    def __init__(self, url, body, status=200):
        self.url = url
        self.status = status
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _question_body():
    return {
        "rt": {
            "examBase": {
                "workExamParts": [
                    {
                        "questionDtos": [
                            {
                                "name": "1 + 1 = ?",
                                "questionType": {"name": "单选题", "id": 1},
                                "questionOptions": [
                                    {"id": "A", "content": "1"},
                                    {"id": "B", "content": "2"},
                                ],
                                "questionScore": 5,
                                "eid": "question-1",
                            }
                        ]
                    }
                ]
            }
        }
    }


def test_captures_completed_homework_and_normalizes_questions():
    logger = _Logger()
    page = _Page("https://example.test/course")
    context = _Context([page])
    handler = TestResponseHandler(logger_instance=logger)

    async def run():
        handler.setup_listener(context)
        await context.emit(
            "response",
            _Response("https://example.test/lookHomework?id=1", _question_body()),
        )
        return await handler.wait_for_questions(timeout=0)

    assert asyncio.run(run()) is True
    assert handler.is_completed is True
    assert handler.questions_data == [
        {
            "name": "1 + 1 = ?",
            "type": "单选题",
            "type_id": 1,
            "options": [("A", "1"), ("B", "2")],
            "score": 5,
            "eid": "question-1",
        }
    ]
    assert handler.questions_event.is_set()


def test_registers_new_pages_and_remove_listener_is_idempotent():
    logger = _Logger()
    existing_page = _Page("https://example.test/existing")
    new_page = _Page("https://example.test/new")
    context = _Context([existing_page])
    handler = TestResponseHandler(logger_instance=logger)

    handler.remove_listener()
    handler.setup_listener(context)
    asyncio.run(context.emit("page", new_page))

    assert len(context.handlers["response"]) == 1
    assert len(context.handlers["page"]) == 1
    assert len(existing_page.handlers["response"]) == 1
    assert len(new_page.handlers["response"]) == 1

    handler.remove_listener()
    handler.remove_listener()

    assert context.handlers["response"] == []
    assert context.handlers["page"] == []
    assert existing_page.handlers["response"] == []
    assert new_page.handlers["response"] == []


def test_repeated_setup_removes_previous_context_listeners():
    handler = TestResponseHandler(logger_instance=_Logger())
    first_page = _Page("https://example.test/first")
    first_context = _Context([first_page])
    second_context = _Context()

    handler.setup_listener(first_context)
    handler.setup_listener(second_context)

    assert first_context.handlers["response"] == []
    assert first_context.handlers["page"] == []
    assert first_page.handlers["response"] == []
    assert len(second_context.handlers["response"]) == 1


def test_ignores_invalid_responses_and_times_out_cleanly():
    logger = _Logger()
    context = _Context()
    handler = TestResponseHandler(logger_instance=logger)

    async def run():
        handler.setup_listener(context)
        await context.emit(
            "response",
            _Response("https://example.test/doHomework", {}, status=503),
        )
        await context.emit(
            "response",
            _Response("https://example.test/lookHomework", ["invalid"]),
        )
        return await handler.wait_for_questions(timeout=0)

    assert asyncio.run(run()) is False
    assert handler.questions_data is None
    assert handler.is_completed is False
    assert "目标响应状态码: 503" in logger.warnings
    assert any("响应中未找到题目数据" in message for message in logger.warnings)
    assert any("等待响应超时" in message for message in logger.warnings)


def test_existing_page_registration_failure_is_reported_and_isolated():
    logger = _Logger()

    class BrokenPage(_Emitter):
        @property
        def url(self):
            raise OSError("existing page unavailable")

    valid_page = _Page("https://example.test/valid")
    context = _Context([BrokenPage(), valid_page])
    handler = TestResponseHandler(logger_instance=logger)

    handler.setup_listener(context)

    assert len(valid_page.handlers["response"]) == 1
    assert any("existing page unavailable" in item for item in logger.warnings)


def test_new_page_registration_failure_does_not_escape_callback():
    logger = _Logger()

    class BrokenPage(_Page):
        def on(self, _event, _handler):
            raise OSError("new page registration failed")

    context = _Context()
    handler = TestResponseHandler(logger_instance=logger)
    handler.setup_listener(context)

    asyncio.run(
        context.emit(
            "page",
            BrokenPage("https://example.test/new"),
        )
    )

    assert any(
        "new page registration failed" in item for item in logger.warnings
    )


def test_response_url_failure_is_reported_without_breaking_listener():
    logger = _Logger()

    class BrokenResponse:
        @property
        def url(self):
            raise OSError("response url failed")

    context = _Context()
    handler = TestResponseHandler(logger_instance=logger)
    handler.setup_listener(context)

    asyncio.run(context.emit("response", BrokenResponse()))

    assert handler.questions_data is None
    assert any("response url failed" in item for item in logger.warnings)


def test_page_cleanup_failure_is_reported_but_closed_page_is_quiet():
    logger = _Logger()

    class CleanupPage(_Page):
        def __init__(self, url, cleanup_error):
            super().__init__(url)
            self.cleanup_error = cleanup_error

        def remove_listener(self, _event, _handler):
            raise self.cleanup_error

    closed_page = CleanupPage(
        "https://example.test/closed",
        TargetClosedError("already closed"),
    )
    broken_page = CleanupPage(
        "https://example.test/broken",
        OSError("page cleanup failed"),
    )
    context = _Context([closed_page, broken_page])
    handler = TestResponseHandler(logger_instance=logger)
    handler.setup_listener(context)

    handler.remove_listener()

    assert len(logger.warnings) == 1
    assert "page cleanup failed" in logger.warnings[0]
