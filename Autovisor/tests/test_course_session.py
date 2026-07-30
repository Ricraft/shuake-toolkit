# encoding=utf-8

import asyncio
import sys
from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.course_session import (
    COURSE_OPEN_TIMEOUT_MS,
    CourseAuthenticationError,
    CourseNavigationError,
    CourseSession,
    wait_for_authenticated_selector,
)
from modules.course_types import CourseKind, CourseProfile

sys.path.remove(_AUTOVISOR_ROOT)


@pytest.mark.parametrize(
    ("url", "expected_kind"),
    [
        ("https://studyvideoh5.zhihuishu.com/course", CourseKind.NORMAL),
        ("https://fusioncourseh5.zhihuishu.com/course", CourseKind.FUSION),
        ("https://hike.zhihuishu.com/course", CourseKind.HIKE),
        ("https://wisdom-mooc.zhihuishu.com/course", CourseKind.NATIONAL_WISDOM),
        ("https://lc.zhihuishu.com/course", CourseKind.MEETING),
        ("https://live.zhihuishu.com/course", CourseKind.MEETING),
    ],
)
def test_course_profile_classifies_supported_hosts(url, expected_kind):
    assert CourseProfile.from_url(url).kind is expected_kind


def test_course_profile_ignores_course_name_inside_query_string():
    profile = CourseProfile.from_url(
        "https://example.com/course?next=https://hike.zhihuishu.com/course"
    )
    assert profile.kind is CourseKind.NORMAL


def test_course_profile_does_not_trust_lookalike_hostname():
    profile = CourseProfile.from_url("https://fusioncourseh5.example.com/course")
    assert profile.kind is CourseKind.NORMAL


def test_working_loop_options_are_derived_from_one_kind():
    profile = CourseProfile.from_url("https://wisdom-mooc.zhihuishu.com/course")
    assert profile.working_loop_options() == {
        "is_new_version": False,
        "is_hike_class": False,
        "is_national_wisdom": True,
        "is_meeting_class": False,
    }


class _Element:
    def __init__(self, text):
        self.text = text

    async def text_content(self):
        return self.text


class _Locator:
    def __init__(self, visible):
        self.visible = visible

    async def count(self):
        return 1 if self.visible else 0

    async def is_visible(self):
        return self.visible

    async def all(self):
        return [self] if self.visible else []


class _Page:
    def __init__(
        self,
        titles=None,
        *,
        response_status=None,
        redirect_url=None,
        login_panel=False,
    ):
        self.titles = titles or {}
        self.url = "https://example.com/"
        self.response_status = response_status
        self.redirect_url = redirect_url
        self.login_panel = login_panel
        self.goto_calls = []
        self.selector_calls = []

    async def goto(self, url, **options):
        self.goto_calls.append((url, options))
        self.url = self.redirect_url or url
        if self.response_status is None:
            return None
        return type("Response", (), {"status": self.response_status})()

    async def wait_for_selector(self, selector, timeout, **_options):
        self.selector_calls.append((selector, timeout))
        if selector not in self.titles:
            raise PlaywrightTimeoutError(f"missing: {selector}")
        return _Element(self.titles[selector])

    def locator(self, _selector):
        return _Locator(self.login_panel)


class _Logger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def warn(self, message):
        self.warnings.append(message)


def test_course_session_opens_optimizes_and_resolves_title():
    captured = {}

    async def optimizer(page, config, *flags):
        captured.update(page=page, config=config, flags=flags)

    url = "https://lc.zhihuishu.com/course"
    page = _Page({".source-name": "  测试见面课  "})
    config = object()
    logger = _Logger()
    session = CourseSession.from_url(url, optimizer=optimizer)

    title = asyncio.run(session.open(page, config, logger))

    assert title == "测试见面课"
    assert page.goto_calls == [
        (url, {"wait_until": "commit", "timeout": COURSE_OPEN_TIMEOUT_MS})
    ]
    assert captured == {
        "page": page,
        "config": config,
        "flags": (False, False, False, True),
    }
    assert page.selector_calls[:2] == [
        (".course-name", 3000),
        (".source-name", 3000),
    ]
    assert logger.infos[-1] == "当前课程:<<测试见面课>>，是见面课"
    assert logger.warnings == []


def test_course_session_uses_kind_default_when_title_is_missing():
    async def optimizer(*_args):
        return None

    page = _Page()
    logger = _Logger()
    session = CourseSession.from_url(
        "https://fusioncourseh5.zhihuishu.com/course", optimizer=optimizer
    )

    title = asyncio.run(session.open(page, object(), logger))

    assert title == "新版课程"
    assert logger.infos[-1] == "当前课程:<<新版课程>>，是新版课程"


def test_course_session_rejects_http_error_before_optimization():
    optimized = []

    async def optimizer(*_args):
        optimized.append(True)

    page = _Page(response_status=503)
    session = CourseSession.from_url(
        "https://studyvideoh5.zhihuishu.com/course", optimizer=optimizer
    )

    with pytest.raises(CourseNavigationError, match="HTTP 503"):
        asyncio.run(session.open(page, object(), _Logger()))
    assert optimized == []


def test_course_session_rejects_login_redirect_before_optimization():
    optimized = []

    async def optimizer(*_args):
        optimized.append(True)

    page = _Page(redirect_url="https://passport.zhihuishu.com/login")
    session = CourseSession.from_url(
        "https://studyvideoh5.zhihuishu.com/course", optimizer=optimizer
    )

    with pytest.raises(CourseAuthenticationError, match="重定向到登录页"):
        asyncio.run(session.open(page, object(), _Logger()))
    assert optimized == []


def test_course_session_rejects_homepage_redirect_before_optimization():
    optimized = []

    async def optimizer(*_args):
        optimized.append(True)

    page = _Page(redirect_url="https://www.zhihuishu.com/")
    session = CourseSession.from_url(
        "https://studyvideoh5.zhihuishu.com/course",
        optimizer=optimizer,
    )

    with pytest.raises(CourseAuthenticationError, match="重定向到登录页"):
        asyncio.run(session.open(page, object(), _Logger()))
    assert optimized == []


def test_course_session_rejects_rendered_login_before_url_redirect():
    optimized = []

    async def optimizer(*_args):
        optimized.append(True)

    page = _Page(
        redirect_url="https://onlineweb.zhihuishu.com/",
        login_panel=True,
    )
    session = CourseSession.from_url(
        "https://studyvideoh5.zhihuishu.com/course", optimizer=optimizer
    )

    with pytest.raises(CourseAuthenticationError, match="重定向到登录页"):
        asyncio.run(session.open(page, object(), _Logger()))
    assert optimized == []


def test_authenticated_selector_wait_converts_login_timeout_to_fatal_error():
    page = _Page()
    page.url = "https://login.zhihuishu.com/?origin=zhs"

    with pytest.raises(
        CourseAuthenticationError,
        match="等待课程控件时登录状态失效",
    ):
        asyncio.run(
            wait_for_authenticated_selector(
                page,
                ".missing-course-ui",
                "等待课程控件时登录状态失效",
                state="attached",
                timeout=1000,
            )
        )


def test_authenticated_selector_wait_preserves_non_authentication_timeout():
    page = _Page()
    page.url = "https://studyvideoh5.zhihuishu.com/course"

    with pytest.raises(PlaywrightTimeoutError, match="missing"):
        asyncio.run(
            wait_for_authenticated_selector(
                page,
                ".missing-course-ui",
                "等待课程控件时登录状态失效",
                state="attached",
                timeout=1000,
            )
        )
