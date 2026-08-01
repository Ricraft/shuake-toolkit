# encoding=utf-8

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Error as PlaywrightError
from modules.course_errors import CourseAuthenticationError
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
    assert (
        task_module.wait_for_verification_resolution
        is page_monitor.wait_for_verification_resolution
    )


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


def test_smart_click_text_keeps_fallback_error_detail():
    class Page:
        async def evaluate(self, _script):
            return False

        def get_by_text(self, _text, *, exact):
            assert exact is True
            raise RuntimeError("locator contract changed")

    log = _Logger()
    result = asyncio.run(
        page_monitor.smart_click_text(
            Page(),
            "目标",
            logger_instance=log,
        )
    )

    assert result is False
    assert any(
        level == "debug" and "locator contract changed" in message
        for level, message in log.messages
    )


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


def test_trigger_restart_reports_context_close_failure():
    class Context:
        async def close(self):
            raise RuntimeError("close failed")

    log = _Logger()
    asyncio.run(
        page_monitor.trigger_restart(
            SimpleNamespace(context=Context()),
            "worker",
            logger_instance=log,
        )
    )

    assert any(
        level == "warn" and "关闭旧浏览器上下文失败" in message
        for level, message in log.messages
    )


def test_status_stream_limits_unknown_probe_errors_and_stops_on_close(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(page_monitor.asyncio, "sleep", no_sleep)

    class Page:
        def __init__(self):
            self.calls = 0

        async def wait_for_load_state(self, _state):
            return None

        async def evaluate(self, _script):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("probe changed")
            raise TargetClosedError("page closed")

    log = _Logger()
    asyncio.run(
        page_monitor.status_ocr_stream(
            Page(),
            "worker",
            interval_sec=0,
            logger_instance=log,
        )
    )

    warnings = [
        message
        for level, message in log.messages
        if level == "warn" and "页面状态探测失败" in message
    ]
    assert len(warnings) == 1
    assert "RuntimeError: probe changed" in warnings[0]
    assert any("status stream offline" in message for _, message in log.messages)


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
        ("visible", 1000),
        "display",
        ("hidden", 24 * 3600 * 1000),
    ]
    assert "hide" in calls
    assert any("安全验证模块已下线" in message for _, message in log.messages)


def test_wait_for_verify_limits_unknown_monitor_errors(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(page_monitor.asyncio, "sleep", no_sleep)

    class Page:
        def __init__(self):
            self.calls = 0

        async def wait_for_load_state(self, _state):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("verification selector changed")
            raise TargetClosedError("page closed")

    log = _Logger()
    asyncio.run(
        page_monitor.wait_for_verify(
            Page(),
            SimpleNamespace(enableHideWindow=False),
            asyncio.Event(),
            logger_instance=log,
        )
    )

    warnings = [
        message
        for level, message in log.messages
        if level == "warn" and "安全验证监控异常" in message
    ]
    assert len(warnings) == 1
    assert "verification selector changed" in warnings[0]


def test_wait_for_verify_clears_stale_signal_when_challenge_appears(monkeypatch):
    observed = []
    event = asyncio.Event()
    event.set()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(page_monitor.asyncio, "sleep", no_sleep)

    class Page:
        async def wait_for_load_state(self, _state):
            return None

        async def wait_for_selector(self, _selector, *, state, timeout):
            if state == "hidden":
                observed.append(event.is_set())
                raise TargetClosedError("page closed")

    asyncio.run(
        page_monitor.wait_for_verify(
            Page(),
            SimpleNamespace(enableHideWindow=False),
            event,
            logger_instance=_Logger(),
        )
    )

    assert observed == [False]


class _VerificationPage:
    def __init__(self, states, *, url="https://studyvideoh5.zhihuishu.com/course"):
        self.states = iter(states)
        self.url = url
        self.last_state = None

    async def query_selector(self, _selector):
        try:
            self.last_state = next(self.states)
        except StopIteration:
            pass
        if isinstance(self.last_state, Exception):
            raise self.last_state
        return self.last_state

    async def wait_for_load_state(self, _state, *, timeout):
        return None


def test_verification_resolution_clears_stale_signal_and_times_out():
    event = asyncio.Event()
    event.set()
    log = _Logger()

    result = asyncio.run(
        page_monitor.wait_for_verification_resolution(
            _VerificationPage([object()]),
            event,
            timeout=0,
            logger_instance=log,
        )
    )

    assert result is False
    assert event.is_set() is False
    assert any("避免永久等待" in message for _, message in log.messages)


def test_verification_resolution_accepts_fresh_completion_signal():
    async def scenario():
        event = asyncio.Event()
        task = asyncio.create_task(
            page_monitor.wait_for_verification_resolution(
                _VerificationPage([object()]),
                event,
                timeout=1,
                poll_interval=0.05,
            )
        )
        await asyncio.sleep(0)
        event.set()
        return await task

    assert asyncio.run(scenario()) is True


def test_verification_resolution_accepts_popup_disappearance():
    log = _Logger()
    result = asyncio.run(
        page_monitor.wait_for_verification_resolution(
            _VerificationPage([object(), None]),
            asyncio.Event(),
            timeout=1,
            poll_interval=0.05,
            logger_instance=log,
        )
    )

    assert result is True
    assert any("弹窗已消失" in message for _, message in log.messages)


def test_verification_resolution_accepts_popup_becoming_hidden():
    class HiddenElement:
        async def is_visible(self):
            return False

    event = asyncio.Event()
    event.set()
    result = asyncio.run(
        page_monitor.wait_for_verification_resolution(
            _VerificationPage([HiddenElement()]),
            event,
        )
    )

    assert result is True
    assert event.is_set() is False


def test_verification_resolution_propagates_login_redirect():
    with pytest.raises(CourseAuthenticationError, match="安全验证完成时"):
        asyncio.run(
            page_monitor.wait_for_verification_resolution(
                _VerificationPage([], url="https://login.zhihuishu.com/"),
                asyncio.Event(),
            )
        )


def test_verification_resolution_rechecks_auth_after_navigation_race(monkeypatch):
    calls = 0

    async def ensure(_page, _message):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PlaywrightError("execution context destroyed")
        raise CourseAuthenticationError("redirected after navigation")

    monkeypatch.setattr(page_monitor, "ensure_course_authenticated", ensure)

    with pytest.raises(CourseAuthenticationError, match="redirected"):
        asyncio.run(
            page_monitor.wait_for_verification_resolution(
                _VerificationPage([object()]),
                asyncio.Event(),
            )
        )

    assert calls == 2


def test_verification_resolution_propagates_closed_page():
    with pytest.raises(TargetClosedError, match="closed during verification"):
        asyncio.run(
            page_monitor.wait_for_verification_resolution(
                _VerificationPage(
                    [TargetClosedError("closed during verification")]
                ),
                asyncio.Event(),
            )
        )
