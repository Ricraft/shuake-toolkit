# encoding=utf-8

import asyncio
import sys
from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.course_session import CourseSession
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


class _Page:
    def __init__(self, titles=None):
        self.titles = titles or {}
        self.goto_calls = []
        self.selector_calls = []

    async def goto(self, url, wait_until):
        self.goto_calls.append((url, wait_until))

    async def wait_for_selector(self, selector, timeout):
        self.selector_calls.append((selector, timeout))
        if selector not in self.titles:
            raise PlaywrightTimeoutError(f"missing: {selector}")
        return _Element(self.titles[selector])


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
    assert page.goto_calls == [(url, "commit")]
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
