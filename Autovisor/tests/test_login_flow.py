import asyncio
import sys
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.login_flow import (
    FORM_DETECTION_TIMEOUT_MS,
    PASSIVE_LOGIN_TIMEOUT_MS,
    login_to_zhihuishu,
    wait_for_login_destination,
    wait_for_login_form_or_portal,
    wait_for_login_form_to_leave,
)
from modules.login_selectors import (
    AUTO_LOGIN_TIMEOUT_MS,
    LOGIN_AGREEMENT_CHECKBOX,
    LOGIN_AGREEMENT_CLICKABLE,
    LOGIN_FORM_TIMEOUT_MS,
    LOGIN_PANEL,
    LOGIN_SUBMIT,
    LOGIN_URL,
    MANUAL_LOGIN_TIMEOUT_MS,
)

sys.path.remove(_AUTOVISOR_ROOT)


PORTAL_URL = "https://onlineweb.zhihuishu.com/"
LOGIN_HOST_URL = LOGIN_URL
AUTH_GATEWAY_URL = (
    "https://onlineservice-api.zhihuishu.com/gateway/f/v1/login/gologin"
)


class _Logger:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.warnings = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

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
        if self.selector in (LOGIN_AGREEMENT_CLICKABLE, LOGIN_AGREEMENT_CHECKBOX):
            self.page.agreement_checked = True
        if self.selector == LOGIN_SUBMIT:
            self.page.submitted = True
            if self.page.panel_count_after_submit is not None:
                self.page.panel_count = self.page.panel_count_after_submit

    async def evaluate(self, script):
        self.page.evaluates.append((self.selector, script))
        if self.selector == LOGIN_AGREEMENT_CHECKBOX:
            self.page.agreement_checked = True

    async def count(self):
        if self.selector == LOGIN_PANEL:
            return self.page.panel_count
        if self.selector == LOGIN_AGREEMENT_CHECKBOX:
            return self.page.agreement_count
        if self.selector == LOGIN_AGREEMENT_CLICKABLE:
            return self.page.agreement_clickable_count
        return 0

    async def is_checked(self):
        return self.page.agreement_checked

    async def check(self, **options):
        self.page.checks.append((self.selector, options))
        self.page.agreement_checked = True

    @property
    def first(self):
        return self


class _Page:
    """Deterministic stub mirroring only what the login flow observes.

    ``portal_arrives`` controls when the portal address replaces the login
    address: ``"after_panel_hidden"`` reproduces the form path, ``"silent"`` the
    silent-SSO path where the panel never renders, ``None`` never redirects.
    """

    def __init__(
        self,
        *,
        url=LOGIN_URL,
        panel_count=1,
        panel_count_after_submit=None,
        hide_panel_on_hidden_wait=False,
        portal_arrives=None,
        agreement_count=1,
        agreement_clickable_count=1,
        agreement_checked=False,
    ):
        self._url = url
        self.panel_count = panel_count
        self.panel_count_after_submit = panel_count_after_submit
        # 等待“面板消失”时面板真的消失，用于模拟一次成功的登录跳转。
        self.hide_panel_on_hidden_wait = hide_panel_on_hidden_wait
        self.portal_arrives = portal_arrives
        self.agreement_count = agreement_count
        self.agreement_clickable_count = agreement_clickable_count
        self.agreement_checked = agreement_checked
        self.submitted = False
        self.goto_calls = []
        self.waits = []
        self.fills = []
        self.clicks = []
        self.checks = []
        self.evaluates = []
        self.url_reads = []

    @property
    def start_url(self):
        return self._url

    @property
    def url(self):
        self.url_reads.append(monotonic())
        if self.portal_arrives == "after_panel_hidden":
            # 面板消失之后门户才出现。
            if self.panel_count == 0:
                return PORTAL_URL
        elif self.portal_arrives == "silent":
            # 静默 SSO：面板从未渲染，若干次读取后直接到门户。
            if len(self.url_reads) >= 3:
                return PORTAL_URL
        return self._url

    @url.setter
    def url(self, value):
        self._url = value

    async def goto(self, url, **options):
        self.goto_calls.append((url, options))

    async def wait_for_selector(self, selector, **options):
        self.waits.append((selector, options))
        if selector == LOGIN_PANEL and options.get("state") == "hidden":
            if self.panel_count:
                if self.hide_panel_on_hidden_wait:
                    self.panel_count = 0
                else:
                    raise PlaywrightTimeoutError("login panel never hid")

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


def _login(page, logger=None, config=None, **kwargs):
    return asyncio.run(
        login_to_zhihuishu(
            _Context(),
            page,
            config or _config(),
            logger or _Logger(),
            cookie_path="cookies.json",
            cookie_saver=lambda *_args: None,
            **kwargs,
        )
    )


# --------------------------------------------------------------------------
# 静默 SSO：地址栏可能还停在登录页，而门户已经/即将出现
# --------------------------------------------------------------------------


def test_form_or_portal_reports_the_portal_when_it_replaces_the_login_page():
    # 实测：地址栏先停在登录页，随后（面板从未渲染）直接变成门户。
    page = _Page(
        url=LOGIN_URL,
        panel_count=0,
        portal_arrives="silent",
    )

    outcome = asyncio.run(wait_for_login_form_or_portal(page, 5_000))

    assert outcome == "portal"
    assert len(page.url_reads) > 1


def test_form_or_portal_reports_the_form_when_the_panel_renders():
    page = _Page(url=LOGIN_URL, panel_count=1)

    assert asyncio.run(wait_for_login_form_or_portal(page, 5_000)) == "form"


def test_form_or_portal_returns_unknown_when_neither_happens():
    page = _Page(url=LOGIN_URL, panel_count=0)

    assert asyncio.run(wait_for_login_form_or_portal(page, 200)) == "unknown"


def test_silent_sso_never_rendering_the_login_form_is_a_success():
    """实测回归：带有效凭证时登录面板根本不会渲染，直接落到门户。"""
    page = _Page(
        url=LOGIN_URL,
        panel_count=0,
        portal_arrives="silent",
    )
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
            passive_timeout_ms=5_000,
            form_detection_timeout_ms=5_000,
        )
    )

    assert result is True
    assert page.fills == []
    assert page.clicks == []
    assert saved == [([{"name": "session", "value": "token"}], "account-cookies.json")]
    assert any("检测到已登录" in message for message in logger.infos)
    assert logger.errors == []


def test_landing_on_the_portal_immediately_skips_the_login_form():
    page = _Page(url=PORTAL_URL, panel_count=0)
    logger = _Logger()

    result = _login(page, logger, passive_timeout_ms=1_000)

    assert result is True
    assert page.waits == []
    assert page.fills == []
    assert page.clicks == []
    assert any("检测到已登录" in message for message in logger.infos)


def test_authentication_gateway_waits_for_the_final_course_portal():
    page = _Page(
        url=AUTH_GATEWAY_URL,
        panel_count=0,
        portal_arrives="silent",
    )

    result = _login(page, passive_timeout_ms=5_000)

    assert result is True
    assert page.waits == []
    assert page.fills == []
    assert page.url == PORTAL_URL


def test_untrusted_landing_page_is_not_reported_as_existing_login():
    page = _Page(url="https://example.com/login-result", panel_count=0)
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
            passive_timeout_ms=300,
            form_detection_timeout_ms=300,
        )
    )

    assert result is False
    assert saved == []
    assert page.fills == []
    assert logger.errors


def test_missing_panel_without_any_redirect_stops_instead_of_stalling():
    page = _Page(url=LOGIN_URL, panel_count=0)
    logger = _Logger()

    result = _login(page, logger, form_detection_timeout_ms=300)

    assert result is False
    assert page.fills == []
    assert any("未检测到智慧树登录面板" in message for message in logger.errors)


# --------------------------------------------------------------------------
# 自动登录表单：协议勾选 / 提交 / 有界等待
# --------------------------------------------------------------------------


def test_automatic_login_is_bounded_and_saves_cookies_after_success():
    page = _Page(
        panel_count=1,
        hide_panel_on_hidden_wait=True,
        portal_arrives="after_panel_hidden",
    )
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
    assert len(page.fills) == 2
    # 新版面板的隐藏 input 无法点击，必须点击可见的 Element-UI 包装节点。
    assert page.checks == []
    assert page.evaluates == []
    assert page.clicks == [
        (LOGIN_AGREEMENT_CLICKABLE, {"timeout": LOGIN_FORM_TIMEOUT_MS}),
        (LOGIN_SUBMIT, {"timeout": LOGIN_FORM_TIMEOUT_MS}),
    ]
    assert page.agreement_checked is True
    assert saved == [([{"name": "session", "value": "token"}], "account-cookies.json")]


def test_login_form_path_reports_success_once_the_portal_is_reached():
    page = _Page(
        panel_count=1,
        hide_panel_on_hidden_wait=True,
        portal_arrives="after_panel_hidden",
    )

    assert _login(page) is True
    assert len(page.fills) == 2
    assert page.url == PORTAL_URL


def test_automatic_login_timeout_returns_failure_and_does_not_save_cookies():
    # 面板一直存在且始终没有跳到课程门户（例如验证码未通过）。
    page = _Page(panel_count=1)
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
            auto_login_timeout_ms=300,
        )
    )

    assert result is False
    assert saved == []
    assert any("自动登录超时" in message for message in logger.errors)


def test_manual_login_keeps_an_explicit_long_wait_without_global_timeout():
    # 人工登录：面板渲染出来后由用户在浏览器里完成，面板消失并落到门户。
    page = _Page(
        url=LOGIN_URL,
        panel_count=1,
        portal_arrives="after_panel_hidden",
    )

    async def wait_for_selector(selector, **options):
        page.waits.append((selector, options))
        if selector == LOGIN_PANEL and options.get("state") == "hidden":
            page.panel_count = 0

    page.wait_for_selector = wait_for_selector

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


def test_captcha_handler_runs_only_for_configured_automatic_login():
    calls = []

    async def slider(page):
        calls.append(page)
        return False

    page = _Page(panel_count=1, hide_panel_on_hidden_wait=True, portal_arrives="after_panel_hidden")
    logger = _Logger()

    result = _login(
        page,
        logger,
        config=_config(captcha=True),
        modules=[object()],
        slider_handler=slider,
    )

    assert result is True
    assert calls == [page]
    # 自动过滑块失败时必须明确提示人工完成，而不是静默等到超时。
    assert any("手动完成安全验证" in message for message in logger.warnings)


def test_captcha_handler_is_skipped_without_automatic_captcha():
    calls = []

    async def slider(page):
        calls.append(page)
        return True

    page = _Page(panel_count=1, hide_panel_on_hidden_wait=True, portal_arrives="after_panel_hidden")

    result = _login(
        page,
        config=_config(captcha=False),
        modules=[object()],
        slider_handler=slider,
    )

    assert result is True
    assert calls == []


# --------------------------------------------------------------------------
# 协议勾选框：Element-UI 隐藏 input 不能再被 check(force=True)
# --------------------------------------------------------------------------


def test_legacy_login_form_does_not_require_an_agreement_checkbox():
    page = _Page(url="https://passport.zhihuishu.com/login", agreement_count=0, hide_panel_on_hidden_wait=True, portal_arrives="after_panel_hidden")

    result = _login(page)

    assert result is True
    assert len(page.fills) == 2
    assert len(page.clicks) == 1
    assert page.checks == []


def test_ambiguous_agreement_checkbox_stops_before_login_submission():
    page = _Page(agreement_count=2)
    logger = _Logger()

    result = _login(page, logger)

    assert result is False
    assert page.clicks == []
    assert any("登录协议勾选框不唯一" in message for message in logger.errors)


def test_legacy_agreement_input_is_clicked_when_no_wrapper_exists():
    """旧版面板没有 Element-UI 包装层时退回点击 input 本身。"""
    page = _Page(agreement_clickable_count=0, hide_panel_on_hidden_wait=True, portal_arrives="after_panel_hidden")

    result = _login(page)

    assert result is True
    assert page.clicks[0] == (
        LOGIN_AGREEMENT_CHECKBOX,
        {"timeout": LOGIN_FORM_TIMEOUT_MS},
    )
    assert page.evaluates == []
    assert page.agreement_checked is True


def test_agreement_falls_back_to_script_click_when_wrapper_is_not_clickable():
    """包装节点被遮挡时用脚本勾选，仍以 input 的勾选状态为准。"""
    page = _Page(hide_panel_on_hidden_wait=True, portal_arrives="after_panel_hidden")
    logger = _Logger()

    async def failing_click(**options):
        page.clicks.append((LOGIN_AGREEMENT_CLICKABLE, options))
        raise RuntimeError("element is not clickable")

    original_locator = page.locator

    def locator(selector):
        item = original_locator(selector)
        if selector == LOGIN_AGREEMENT_CLICKABLE:
            item.click = failing_click
        return item

    page.locator = locator

    result = _login(page, logger)

    assert result is True
    assert page.evaluates == [
        (LOGIN_AGREEMENT_CHECKBOX, "element => element.click()")
    ]
    assert page.agreement_checked is True
    assert any("改用脚本勾选" in message for message in logger.warnings)


def test_unchecked_agreement_stops_before_login_submission():
    page = _Page(agreement_clickable_count=0)
    logger = _Logger()

    async def noop_click(**options):
        page.clicks.append((LOGIN_AGREEMENT_CHECKBOX, options))

    original_locator = page.locator

    def locator(selector):
        item = original_locator(selector)
        if selector == LOGIN_AGREEMENT_CHECKBOX:
            item.click = noop_click
        return item

    page.locator = locator

    result = _login(page, logger)

    assert result is False
    assert any(
        "未能勾选智慧树用户协议和隐私政策" in message
        for message in logger.errors
    )


# --------------------------------------------------------------------------
# 目的地等待必须轮询当前地址
# --------------------------------------------------------------------------


def test_destination_wait_polls_until_the_portal_appears():
    """实测回归：最后一跳在等待窗口中途出现，且不再产生新的导航事件。"""
    page = _Page(url=AUTH_GATEWAY_URL, panel_count=0, portal_arrives="silent")

    asyncio.run(wait_for_login_destination(page, 5_000))

    assert page.url == PORTAL_URL
    assert len(page.url_reads) > 1


def test_destination_wait_fails_closed_when_the_portal_never_appears():
    page = _Page(url=LOGIN_URL, panel_count=0)

    with pytest.raises(RuntimeError, match="登录跳转未到达智慧树课程门户"):
        asyncio.run(wait_for_login_destination(page, 300))

    assert page.url == LOGIN_URL


def test_form_leave_treats_a_never_rendered_panel_without_redirect_as_failure():
    page = _Page(url=LOGIN_URL, panel_count=0)

    with pytest.raises(RuntimeError, match="未检测到智慧树登录面板"):
        asyncio.run(wait_for_login_form_to_leave(page, 300))


def test_form_leave_accepts_a_silent_portal_redirect_without_a_rendered_panel():
    page = _Page(url=LOGIN_URL, panel_count=0, portal_arrives="silent")

    asyncio.run(wait_for_login_form_to_leave(page, 5_000))

    assert page.waits == []


def test_passive_budget_leaves_room_for_the_measured_sso_chain():
    """实测静默 SSO 约 20 秒；预算必须明显大于旧实现的 15 秒上限。"""
    assert PASSIVE_LOGIN_TIMEOUT_MS >= 45_000
