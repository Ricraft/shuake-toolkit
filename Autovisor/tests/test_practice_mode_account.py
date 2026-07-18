import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from playwright._impl._errors import TargetClosedError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules import practice_mode

sys.path.remove(_AUTOVISOR_ROOT)


class _PlaywrightContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False


class _Browser:
    def __init__(self, close_error=None):
        self.closed = False
        self.close_error = close_error

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


def test_practice_main_loads_selected_account(monkeypatch):
    captured = {}
    browser = _Browser()

    class FakeConfig:
        def __init__(self, config_path, account_id=None):
            captured["config"] = (config_path, account_id)
            self.enableAutoCaptcha = False
            self.username = "selected-user"
            self.password = "selected-password"

    async def fake_init_page(_playwright, config):
        captured["init_config"] = config
        return browser, object(), object()

    async def fake_auto_login(_context, _page, config, _modules):
        captured["login_config"] = config
        return True

    async def fake_practice_loop(_page, _context, config):
        captured["loop_config"] = config

    monkeypatch.setattr(practice_mode, "Config", FakeConfig)
    monkeypatch.setattr(
        practice_mode,
        "async_playwright",
        lambda: _PlaywrightContext(),
    )
    monkeypatch.setattr(practice_mode, "init_page", fake_init_page)
    monkeypatch.setattr(practice_mode, "auto_login", fake_auto_login)
    monkeypatch.setattr(practice_mode, "practice_loop", fake_practice_loop)
    monkeypatch.setattr(
        practice_mode.logger,
        "configure",
        lambda account_id, **kwargs: captured.setdefault(
            "logger",
            (account_id, kwargs),
        ),
    )

    asyncio.run(
        practice_mode.main(
            account_id=7,
            config_path="selected-config.ini",
        )
    )

    self_config = captured["init_config"]
    assert captured["config"] == ("selected-config.ini", 7)
    assert captured["logger"] == (7, {"force": True, "clear": True})
    assert captured["login_config"] is self_config
    assert captured["loop_config"] is self_config
    assert browser.closed is True


def test_practice_main_closes_browser_when_login_fails(monkeypatch):
    browser = _Browser()

    class FakeConfig:
        enableAutoCaptcha = False
        username = "user"
        password = "password"

        def __init__(self, *_args, **_kwargs):
            pass

    async def fake_init_page(_playwright, _config):
        return browser, object(), object()

    async def failed_login(*_args, **_kwargs):
        return False

    monkeypatch.setattr(practice_mode, "Config", FakeConfig)
    monkeypatch.setattr(
        practice_mode,
        "async_playwright",
        lambda: _PlaywrightContext(),
    )
    monkeypatch.setattr(practice_mode, "init_page", fake_init_page)
    monkeypatch.setattr(practice_mode, "auto_login", failed_login)
    monkeypatch.setattr(practice_mode.logger, "configure", lambda *_args, **_kwargs: None)

    try:
        asyncio.run(practice_mode.main(account_id=2))
    except RuntimeError as exc:
        assert "登录未完成" in str(exc)
    else:
        raise AssertionError("登录失败应终止刷题模式")

    assert browser.closed is True


def test_practice_main_logs_browser_close_failure_without_masking_success(monkeypatch):
    browser = _Browser(close_error=OSError("already closed"))
    warnings = []

    class FakeConfig:
        enableAutoCaptcha = False
        username = "user"
        password = "password"

        def __init__(self, *_args, **_kwargs):
            pass

    async def fake_init_page(_playwright, _config):
        return browser, object(), object()

    async def successful_login(*_args, **_kwargs):
        return True

    async def completed_loop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(practice_mode, "Config", FakeConfig)
    monkeypatch.setattr(
        practice_mode,
        "async_playwright",
        lambda: _PlaywrightContext(),
    )
    monkeypatch.setattr(practice_mode, "init_page", fake_init_page)
    monkeypatch.setattr(practice_mode, "auto_login", successful_login)
    monkeypatch.setattr(practice_mode, "practice_loop", completed_loop)
    monkeypatch.setattr(practice_mode.logger, "configure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(practice_mode.logger, "warn", warnings.append)

    asyncio.run(practice_mode.main(account_id=2))

    assert browser.closed is True
    assert any("already closed" in message for message in warnings)


def test_practice_login_uses_account_specific_cookie_file(monkeypatch):
    captured = {}

    async def fake_login(context, page, config, logger, **kwargs):
        captured.update(
            context=context,
            page=page,
            config=config,
            logger=logger,
            kwargs=kwargs,
        )
        return True

    config = SimpleNamespace(cookies_file="res/cookies_7.json")
    monkeypatch.setattr(practice_mode, "login_to_zhihuishu", fake_login)

    result = asyncio.run(
        practice_mode.auto_login("context", "page", config, ["slider"])
    )

    assert result is True
    assert captured["kwargs"]["cookie_path"] == "res/cookies_7.json"


def test_practice_loop_removes_listener_when_main_page_closes(monkeypatch):
    events = []

    class ClosedPage:
        async def wait_for_timeout(self, _milliseconds):
            return None

        async def title(self):
            raise TargetClosedError("page closed")

    class Handler:
        def setup_listener(self, context, clear_data=False):
            events.append(("setup", context, clear_data))

        def remove_listener(self):
            events.append(("remove",))

    async def navigated(*_args, **_kwargs):
        return True

    context = SimpleNamespace(pages=[])
    monkeypatch.setattr(practice_mode, "navigate_to_my_course", navigated)
    monkeypatch.setattr(practice_mode, "TestResponseHandler", Handler)

    asyncio.run(
        practice_mode.practice_loop(
            ClosedPage(),
            context,
            SimpleNamespace(),
        )
    )

    assert events[0] == ("setup", context, True)
    assert events[-1] == ("remove",)


def test_practice_loop_reports_session_recovery_error_once(monkeypatch):
    warnings = []

    class Page:
        def __init__(self):
            self.title_calls = 0

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def title(self):
            self.title_calls += 1
            if self.title_calls > 1:
                raise TargetClosedError("page closed")
            return "course"

        @property
        def url(self):
            raise OSError("session probe failed")

    class Handler:
        questions_data = None

        def setup_listener(self, *_args, **_kwargs):
            return None

        async def wait_for_questions(self, timeout=10):
            return False

        def remove_listener(self):
            return None

    async def navigated(*_args, **_kwargs):
        return True

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(practice_mode, "navigate_to_my_course", navigated)
    monkeypatch.setattr(practice_mode, "TestResponseHandler", Handler)
    monkeypatch.setattr(practice_mode.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(practice_mode.logger, "warn", warnings.append)

    asyncio.run(
        practice_mode.practice_loop(
            Page(),
            SimpleNamespace(pages=[]),
            SimpleNamespace(),
        )
    )

    matching = [message for message in warnings if "session probe failed" in message]
    assert len(matching) == 1


def test_return_to_course_reports_second_navigation_failure(monkeypatch):
    warnings = []

    async def close_pages(*_args, **_kwargs):
        return None

    attempts = iter(
        [RuntimeError("first navigation failed"), OSError("retry failed")]
    )

    async def failed_navigation(*_args, **_kwargs):
        raise next(attempts)

    monkeypatch.setattr(practice_mode, "_close_extra_pages", close_pages)
    monkeypatch.setattr(
        practice_mode,
        "navigate_to_my_course",
        failed_navigation,
    )
    monkeypatch.setattr(practice_mode.logger, "warn", warnings.append)

    result = asyncio.run(
        practice_mode._return_to_my_course(
            object(),
            object(),
            object(),
            object(),
        )
    )

    assert result is False
    assert any("first navigation failed" in message for message in warnings)
    assert any("retry failed" in message for message in warnings)


def test_find_test_page_reports_unknown_url_failure_and_keeps_scanning():
    warnings = []
    main_page = object()

    class BrokenPage:
        @property
        def url(self):
            raise OSError("url unavailable")

    valid_page = SimpleNamespace(url="https://example.zhihuishu.com/exam/1")
    diagnostics = practice_mode.RateLimitedDiagnostics(
        SimpleNamespace(warn=warnings.append)
    )

    result = practice_mode._find_test_page(
        SimpleNamespace(pages=[main_page, BrokenPage(), valid_page]),
        main_page,
        diagnostics,
    )

    assert result is valid_page
    assert len(warnings) == 1
    assert "url unavailable" in warnings[0]


def test_close_extra_pages_reports_unknown_failure_but_ignores_closed_page():
    warnings = []
    main_page = object()

    class ExtraPage:
        def __init__(self, error):
            self.error = error

        async def close(self):
            raise self.error

    diagnostics = practice_mode.RateLimitedDiagnostics(
        SimpleNamespace(warn=warnings.append)
    )
    context = SimpleNamespace(
        pages=[
            main_page,
            ExtraPage(TargetClosedError("already closed")),
            ExtraPage(OSError("close failed")),
        ]
    )

    asyncio.run(
        practice_mode._close_extra_pages(context, main_page, diagnostics)
    )

    assert len(warnings) == 1
    assert "close failed" in warnings[0]


def test_practice_loop_does_not_misreport_unknown_health_error_as_closed(
    monkeypatch,
):
    events = []
    warnings = []

    class Page:
        def __init__(self):
            self.title_calls = 0

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def title(self):
            self.title_calls += 1
            if self.title_calls == 1:
                raise OSError("health probe failed")
            raise TargetClosedError("page closed")

    class Handler:
        def setup_listener(self, *_args, **_kwargs):
            events.append("setup")

        def remove_listener(self):
            events.append("remove")

    async def navigated(*_args, **_kwargs):
        return True

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(practice_mode, "navigate_to_my_course", navigated)
    monkeypatch.setattr(practice_mode, "TestResponseHandler", Handler)
    monkeypatch.setattr(practice_mode.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(practice_mode.logger, "warn", warnings.append)

    asyncio.run(
        practice_mode.practice_loop(
            Page(),
            SimpleNamespace(pages=[]),
            SimpleNamespace(),
        )
    )

    assert events == ["setup", "remove"]
    assert len([item for item in warnings if "health probe failed" in item]) == 1


def test_practice_loop_reports_listener_failure_then_cleans_up(monkeypatch):
    events = []
    warnings = []

    class Page:
        def __init__(self):
            self.title_calls = 0

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def title(self):
            self.title_calls += 1
            if self.title_calls > 1:
                raise TargetClosedError("page closed")
            return "course"

    class Handler:
        questions_data = None

        def setup_listener(self, *_args, **_kwargs):
            events.append("setup")

        async def wait_for_questions(self, timeout=10):
            raise OSError("listener failed")

        def remove_listener(self):
            events.append("remove")

    async def navigated(*_args, **_kwargs):
        return True

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(practice_mode, "navigate_to_my_course", navigated)
    monkeypatch.setattr(practice_mode, "TestResponseHandler", Handler)
    monkeypatch.setattr(practice_mode.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(practice_mode.logger, "warn", warnings.append)

    asyncio.run(
        practice_mode.practice_loop(
            Page(),
            SimpleNamespace(pages=[]),
            SimpleNamespace(),
        )
    )

    assert events == ["setup", "remove"]
    assert len([item for item in warnings if "listener failed" in item]) == 1


def test_practice_loop_cleans_up_when_listener_setup_raises(monkeypatch):
    events = []

    class Page:
        async def wait_for_timeout(self, _milliseconds):
            return None

    class Handler:
        def setup_listener(self, *_args, **_kwargs):
            events.append("setup")
            raise OSError("setup failed")

        def remove_listener(self):
            events.append("remove")

    async def navigated(*_args, **_kwargs):
        return True

    monkeypatch.setattr(practice_mode, "navigate_to_my_course", navigated)
    monkeypatch.setattr(practice_mode, "TestResponseHandler", Handler)

    try:
        asyncio.run(
            practice_mode.practice_loop(
                Page(),
                SimpleNamespace(pages=[]),
                SimpleNamespace(),
            )
        )
    except OSError as exc:
        assert str(exc) == "setup failed"
    else:
        raise AssertionError("监听器 setup 异常应继续向上传播")

    assert events == ["setup", "remove"]
