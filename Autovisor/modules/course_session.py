# encoding=utf-8
"""单门课程的导航、页面优化与标题解析。"""

from __future__ import annotations

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from modules.course_portal import is_login_url
from modules.course_types import CourseProfile
from modules.utils import optimize_page


COURSE_OPEN_TIMEOUT_MS = 30_000


class CourseNavigationError(RuntimeError):
    """A course-specific navigation failure that may not affect later courses."""


class CourseAuthenticationError(CourseNavigationError):
    """The course navigation lost authentication and the whole queue must stop."""


class CourseSession:
    def __init__(self, profile: CourseProfile, optimizer=optimize_page):
        self.profile = profile
        self.optimizer = optimizer
        self.title = profile.adapter.default_title

    @classmethod
    def from_url(cls, url: str, optimizer=optimize_page) -> "CourseSession":
        return cls(CourseProfile.from_url(url), optimizer=optimizer)

    async def resolve_title(self, page, timeout_ms: int = 3000) -> str:
        for selector in self.profile.adapter.title_selectors:
            try:
                title_element = await page.wait_for_selector(selector, timeout=timeout_ms)
                title = await title_element.text_content()
                if title and title.strip():
                    return title.strip()
            except PlaywrightTimeoutError:
                continue
        return self.profile.adapter.default_title

    async def open(self, page, config, logger) -> str:
        logger.info("正在加载播放页...")
        response = await page.goto(
            self.profile.url,
            wait_until="commit",
            timeout=COURSE_OPEN_TIMEOUT_MS,
        )
        status = getattr(response, "status", None)
        if isinstance(status, int) and status >= 400:
            raise CourseNavigationError(f"课程页面返回 HTTP {status}")
        if is_login_url(page.url):
            raise CourseAuthenticationError("课程页面重定向到登录页")
        await self.optimizer(
            page,
            config,
            self.profile.is_new_version,
            self.profile.is_hike_class,
            self.profile.is_national_wisdom,
            self.profile.is_meeting_class,
        )
        if is_login_url(page.url):
            raise CourseAuthenticationError("页面优化期间登录状态失效")
        logger.info("页面优化完成!")

        try:
            self.title = await self.resolve_title(page)
        except Exception as exc:
            logger.warn(
                f"{self.profile.adapter.default_title}获取课程标题失败: {repr(exc)}"
            )
            self.title = self.profile.adapter.default_title
        logger.info(self.profile.adapter.format_title(self.title))
        return self.title
