import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import TimeoutError as PlaywrightTimeoutError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.login_flow import login_to_zhihuishu
from modules.login_selectors import (
    AUTO_LOGIN_TIMEOUT_MS,
    LOGIN_AGREEMENT_CHECKBOX,
    LOGIN_FORM_TIMEOUT_MS,
    LOGIN_PANEL,
    LOGIN_URL,
    MANUAL_LOGIN_TIMEOUT_MS,
)

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)


class _Locator:
    def __init__(self, selector, page):
        self.selector = selector
        self.page = page

    async def fill(self, value):
        self.page.fills.append((self.selector, value))

    async def click(self, **options):
        self.page.clicks.append((self.selector, options))

    async def count(self):
        if self.selector == LOGIN_AGREEMENT_CHECKBOX:
            return self.page.agreement_count
        return 0

    async def is_checked(self):
        return self.page.agreement_checked

    async def check(self, **options):
        self.page.checks.append((self.selector, options))
        self.page.agreement_checked = True


class _Page:
    def __init__(
        self,
        *,
        url=LOGIN_URL,
        fail_hidden=False,
        agreement_count=1,
        agreement_checked=False,
    ):
        self.url = url
        self.fail_hidden = fail_hidden
        self.agreement_count = agreement_count
        self.agreement_checked = agreement_checked
        self.goto_calls = []
        self.waits = []
        self.fills = []
        self.clicks = []
        self.checks = []

    async def goto(self, url, **options):
        self.goto_calls.append((url, options))

    async def wait_for_selector(self, selector, **options):
        self.waits.append((selector, options))
        if self.fail_hidden and selector == LOGIN_PANEL and options.get("state") == "hidden":
            raise PlaywrightTimeoutError("login timeout")

    def locator(self, selector):
        return _Locator(selector, self)


class _Context:
    async def cookies(self):
        return [{"name": "session", "value": "token"}]


def _config(*, username="alice", password="secret", captcha=False):
    return SimpleNamespace(
        login_url=LOGIN_URL,
        username=username,
        password=password,
        enableAutoCaptcha=captcha,
    )


def test_automatic_login_is_bounded_and_saves_cookies_after_success():
    page = _Page()
    logger = _Logger()
    saved = []

    result = asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            _config(),
            logger,
            cookie_path="account-cookies.json",
            cookie_saver=lambda cookies, path: saved.append((cookies, path)),
        )
    )

    assert result is True
    assert page.goto_calls[0][1]["timeout"] == LOGIN_FORM_TIMEOUT_MS
    hidden_wait = [item for item in page.waits if item[1].get("state") == "hidden"]
    assert hidden_wait == [(LOGIN_PANEL, {"state": "hidden", "timeout": AUTO_LOGIN_TIMEOUT_MS})]
    assert len(page.fills) == 2
    assert page.checks == [
        (
            LOGIN_AGREEMENT_CHECKBOX,
            {"force": True, "timeout": LOGIN_FORM_TIMEOUT_MS},
        )
    ]
    assert page.agreement_checked is True
    assert saved == [([{"name": "session", "value": "token"}], "account-cookies.json")]


def test_legacy_login_form_does_not_require_an_agreement_checkbox():
    page = _Page(
        url="https://passport.zhihuishu.com/login",
        agreement_count=0,
    )

    result = asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            _config(),
            _Logger(),
            cookie_path="legacy.json",
            cookie_saver=lambda *_args: None,
        )
    )

    assert result is True
    assert len(page.fills) == 2
    assert len(page.clicks) == 1
    assert page.checks == []


def test_ambiguous_agreement_checkbox_stops_before_login_submission():
    page = _Page(agreement_count=2)
    logger = _Logger()

    result = asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            _config(),
            logger,
            cookie_path="cookies.json",
            cookie_saver=lambda *_args: None,
        )
    )

    assert result is False
    assert page.clicks == []
    assert any("登录协议勾选框不唯一" in message for message in logger.errors)


def test_manual_login_keeps_an_explicit_long_wait_without_global_timeout():
    page = _Page()

    result = asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            _config(username="", password=""),
            _Logger(),
            cookie_path="manual.json",
            cookie_saver=lambda *_args: None,
        )
    )

    assert result is True
    hidden_wait = [item for item in page.waits if item[1].get("state") == "hidden"]
    assert hidden_wait[0][1]["timeout"] == MANUAL_LOGIN_TIMEOUT_MS
    assert page.fills == []


def test_automatic_login_timeout_returns_failure_and_does_not_save_cookies():
    page = _Page(fail_hidden=True)
    logger = _Logger()
    saved = []

    result = asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            _config(),
            logger,
            cookie_path="cookies.json",
            cookie_saver=lambda *args: saved.append(args),
        )
    )

    assert result is False
    assert saved == []
    assert any("自动登录超时" in message for message in logger.errors)


def test_captcha_handler_runs_only_for_configured_automatic_login():
    calls = []

    async def slider(page):
        calls.append(page)

    page = _Page()
    result = asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            _config(captcha=True),
            _Logger(),
            modules=[object()],
            cookie_path="cookies.json",
            slider_handler=slider,
            cookie_saver=lambda *_args: None,
        )
    )

    assert result is True
    assert calls == [page]
