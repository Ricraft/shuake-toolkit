# encoding=utf-8

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from playwright._impl._errors import TargetClosedError
from modules import page_monitor
from modules import tasks as task_module

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.messages = []

    def debug(self, message, **_kwargs):
        self.messages.append(("debug", message))

    def info(self, message, **_kwargs):
        self.messages.append(("info", message))

    def warn(self, message, **_kwargs):
        self.messages.append(("warn", message))

    def write_log(self, message, **_kwargs):
        self.messages.append(("log", message))


def test_tasks_preserves_page_monitor_compatibility_exports():
    assert task_module.smart_click_text is page_monitor.smart_click_text
    assert task_module.status_ocr_stream is page_monitor.status_ocr_stream
    assert task_module.wait_for_verify is page_monitor.wait_for_verify


def test_smart_click_text_safely_embeds_special_characters():
    expected = '文本"引号"\\反斜杠\n换行'

    class Page:
        def __init__(self):
            self.script = ""

        async def evaluate(self, script):
            self.script = script
            return True

    page = Page()
    result = asyncio.run(
        page_monitor.smart_click_text(
            page,
            expected,
            logger_instance=_Logger(),
        )
    )

    assert result is True
    assert f"const expected = {json.dumps(expected, ensure_ascii=False)};" in page.script


def test_smart_click_text_uses_exact_locator_fallback():
    class Target:
        def __init__(self):
            self.clicked = False

        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def click(self, **kwargs):
            self.clicked = kwargs == {"force": True, "timeout": 2000}

    target = Target()

    class Page:
        def __init__(self):
            self.lookup = None

        async def evaluate(self, _script):
            return False

        def get_by_text(self, text, *, exact):
            self.lookup = (text, exact)
            return target

    page = Page()
    result = asyncio.run(
        page_monitor.smart_click_text(
            page,
            "精确文本",
            logger_instance=_Logger(),
        )
    )

    assert result is True
    assert page.lookup == ("精确文本", True)
    assert target.clicked is True


def test_trigger_restart_sets_event_and_closes_context_once():
    class Context:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    context = Context()
    page = SimpleNamespace(context=context)
    event = asyncio.Event()

    asyncio.run(
        page_monitor.trigger_restart(
            page,
            "worker",
            event,
            logger_instance=_Logger(),
        )
    )
    asyncio.run(
        page_monitor.trigger_restart(
            page,
            "worker",
            event,
            logger_instance=_Logger(),
        )
    )

    assert event.is_set()
    assert context.close_calls == 1


def test_wait_for_verify_restores_hidden_window_and_stops_on_close(monkeypatch):
    calls = []

    async def no_sleep(_seconds):
        return None

    async def display(_page):
        calls.append("display")

    async def hide(_page):
        calls.append("hide")

    monkeypatch.setattr(page_monitor.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(page_monitor, "display_window", display)
    monkeypatch.setattr(page_monitor, "hide_window", hide)

    class Page:
        def __init__(self):
            self.selector_calls = 0

        async def wait_for_load_state(self, state):
            calls.append(("load", state))

        async def wait_for_selector(self, _selector, *, state, timeout):
            calls.append((state, timeout))
            self.selector_calls += 1
            if self.selector_calls > 2:
                raise TargetClosedError("page closed")

    event = asyncio.Event()
    log = _Logger()
    asyncio.run(
        page_monitor.wait_for_verify(
            Page(),
            SimpleNamespace(enableHideWindow=True),
            event,
            logger_instance=log,
        )
    )

    assert event.is_set()
    assert calls[:4] == [
        ("load", "domcontentloaded"),
        ("attached", 1000),
        "display",
        ("hidden", 24 * 3600 * 1000),
    ]
    assert "hide" in calls
    assert any("安全验证模块已下线" in message for _, message in log.messages)
