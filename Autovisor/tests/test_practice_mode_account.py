import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


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
            raise RuntimeError("page closed")

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
