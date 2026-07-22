import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.course_portal import (
    MY_COURSE_HOMEPAGE,
    MY_COURSE_LINK,
    MY_COURSE_PORTAL,
    PORTAL_READY_SELECTOR,
    PORTAL_RENDER_TIMEOUT_MS,
    is_course_portal_url,
    is_login_page,
    is_login_url,
    navigate_to_my_course,
)
from modules import practice_mode

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, message, **_kwargs):
        self.infos.append(message)

    def warn(self, message, **_kwargs):
        self.warnings.append(message)

    def error(self, message, **_kwargs):
        self.errors.append(message)


class _Locator:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return 1 if self.page.login_panel else 0

    async def is_visible(self):
        return self.page.login_panel

    async def all(self):
        return [self] if self.page.login_panel else []


class _Page:
    def __init__(
        self,
        url,
        *,
        click_failures=(),
        goto_failures=(),
        render=False,
        redirect_to=None,
        login_panel=False,
    ):
        self.url = url
        self.click_failures = set(click_failures)
        self.goto_failures = set(goto_failures)
        self.render = render
        self.redirect_to = redirect_to
        self.login_panel = login_panel
        self.clicks = []
        self.goto_calls = []
        self.waits = []

    async def click(self, selector, **options):
        self.clicks.append((selector, options))
        if selector in self.click_failures:
            raise RuntimeError("missing link")
        self.url = self.redirect_to or MY_COURSE_PORTAL

    async def goto(self, url, **options):
        self.goto_calls.append((url, options))
        if url in self.goto_failures:
            raise RuntimeError("navigation failed")
        self.url = self.redirect_to or url

    async def wait_for_selector(self, selector, **options):
        self.waits.append((selector, options))
        if not self.render:
            raise PlaywrightTimeoutError("portal not ready")
        return object()

    def locator(self, _selector):
        return _Locator(self)


def test_course_portal_url_checks_are_host_scoped():
    assert is_login_url("https://login.zhihuishu.com/")
    assert is_login_url(
        "https://login.zhihuishu.com/?origin=zhs&service=https://example.com/"
    )
    assert is_login_url("https://passport.zhihuishu.com/login")
    assert not is_login_url("https://passport.zhihuishu.com/profile")
    assert not is_login_url("https://example.com/?next=passport.zhihuishu.com/login")
    assert is_course_portal_url("https://onlineweb.zhihuishu.com/onlinestuh5")
    assert not is_course_portal_url("https://onlineweb.zhihuishu.com.example.com/")


def test_rendered_login_panel_is_detected_before_url_redirect_finishes():
    page = _Page(MY_COURSE_PORTAL, login_panel=True)

    assert asyncio.run(is_login_page(page)) is True
    assert asyncio.run(navigate_to_my_course(page, _Logger())) is False
    assert page.clicks == []
    assert page.goto_calls == []


def test_login_page_is_rejected_without_navigation():
    page = _Page("https://passport.zhihuishu.com/login", render=True)
    result = asyncio.run(navigate_to_my_course(page, _Logger()))
    assert result is False
    assert page.clicks == []
    assert page.goto_calls == []


def test_existing_portal_requires_a_rendered_course_container():
    page = _Page(MY_COURSE_PORTAL, render=True)
    result = asyncio.run(navigate_to_my_course(page, _Logger()))
    assert result is True
    assert page.waits == [
        (
            PORTAL_READY_SELECTOR,
            {"state": "attached", "timeout": PORTAL_RENDER_TIMEOUT_MS},
        )
    ]


def test_existing_portal_child_without_course_list_returns_to_portal_root():
    page = _Page("https://onlineweb.zhihuishu.com/exam", render=False)

    async def render_after_root(url, **options):
        page.goto_calls.append((url, options))
        page.url = url
        page.render = True

    page.goto = render_after_root
    result = asyncio.run(navigate_to_my_course(page, _Logger()))
    assert result is True
    assert page.goto_calls[-1][0] == MY_COURSE_PORTAL


def test_homepage_uses_verified_course_link_and_waits_for_render():
    page = _Page(MY_COURSE_HOMEPAGE, render=True)
    result = asyncio.run(navigate_to_my_course(page, _Logger()))
    assert result is True
    assert page.clicks[0][0] == MY_COURSE_LINK
    assert page.goto_calls == []


def test_missing_links_fall_back_to_direct_portal_navigation():
    page = _Page(
        MY_COURSE_HOMEPAGE,
        click_failures={MY_COURSE_LINK, 'a:has-text("我的学堂")'},
        render=True,
    )
    result = asyncio.run(navigate_to_my_course(page, _Logger()))
    assert result is True
    assert page.goto_calls[-1][0] == MY_COURSE_PORTAL


def test_homepage_failure_still_tries_the_direct_portal():
    page = _Page(
        "https://example.com/",
        goto_failures={MY_COURSE_HOMEPAGE},
        render=True,
    )
    result = asyncio.run(navigate_to_my_course(page, _Logger()))
    assert result is True
    assert [call[0] for call in page.goto_calls] == [
        MY_COURSE_HOMEPAGE,
        MY_COURSE_PORTAL,
    ]


def test_redirected_login_and_missing_render_are_not_reported_as_success():
    redirected = _Page(
        "https://example.com/",
        click_failures={MY_COURSE_LINK, 'a:has-text("我的学堂")'},
        render=False,
        redirect_to="https://passport.zhihuishu.com/login",
    )
    assert asyncio.run(navigate_to_my_course(redirected, _Logger())) is False

    missing = _Page(MY_COURSE_PORTAL, render=False)
    assert asyncio.run(navigate_to_my_course(missing, _Logger())) is False


def test_practice_loop_stops_when_course_portal_cannot_be_opened(monkeypatch):
    async def fail_navigation(*_args, **_kwargs):
        return False

    monkeypatch.setattr(practice_mode, "navigate_to_my_course", fail_navigation)
    with pytest.raises(RuntimeError, match="未能进入我的学堂"):
        asyncio.run(practice_mode.practice_loop(object(), object(), object()))


def test_return_to_my_course_propagates_navigation_failure(monkeypatch):
    async def fail_navigation(*_args, **_kwargs):
        return False

    monkeypatch.setattr(practice_mode, "navigate_to_my_course", fail_navigation)
    main_page = SimpleNamespace(url="https://onlineweb.zhihuishu.com/exam")
    context = SimpleNamespace(pages=[main_page])
    result = asyncio.run(
        practice_mode._return_to_my_course(
            main_page, context, main_page, object()
        )
    )
    assert result is False
