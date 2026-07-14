# encoding=utf-8
"""单门课程的导航、页面优化与标题解析。"""

from __future__ import annotations

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from modules.course_types import CourseProfile
from modules.utils import optimize_page


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
        await page.goto(self.profile.url, wait_until="commit")
        await self.optimizer(
            page,
            config,
            self.profile.is_new_version,
            self.profile.is_hike_class,
            self.profile.is_national_wisdom,
            self.profile.is_meeting_class,
        )
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
