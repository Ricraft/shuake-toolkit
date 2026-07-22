# encoding=utf-8
"""Bounded navigation to the Zhihuishu "My Courses" portal."""

from __future__ import annotations

from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from modules.login_selectors import LOGIN_HOSTS


MY_COURSE_HOMEPAGE = "https://www.zhihuishu.com/"
MY_COURSE_PORTAL = "https://onlineweb.zhihuishu.com/"
MY_COURSE_LINK = 'a[href^="https://onlineweb.zhihuishu.com"]'
MY_COURSE_TEXT_LINK = 'a:has-text("我的学堂")'
PORTAL_READY_SELECTORS = (
    '[id*="qiankun"]',
    '[data-name="onlinestuh5"]',
    '#sharingClassed',
    '.course-list',
    '.study-card',
    '.courseCard',
    '.el-scrollbar__view',
)
PORTAL_READY_SELECTOR = ", ".join(PORTAL_READY_SELECTORS)

NAVIGATION_TIMEOUT_MS = 30_000
PORTAL_RENDER_TIMEOUT_MS = 20_000


def is_login_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "login.zhihuishu.com":
        return True
    return (
        hostname in LOGIN_HOSTS
        and "login" in parsed.path.lower()
    )


def is_course_portal_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower().rstrip(".") == "onlineweb.zhihuishu.com"


async def _wait_for_portal(page, logger) -> bool:
    try:
        await page.wait_for_selector(
            PORTAL_READY_SELECTOR,
            state="attached",
            timeout=PORTAL_RENDER_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        if is_login_url(page.url):
            logger.warn("进入我的学堂时被重定向到登录页，登录状态已失效")
        else:
            logger.warn("我的学堂页面已打开，但课程列表在 20 秒内未完成渲染")
        return False

    if is_login_url(page.url):
        logger.warn("进入我的学堂时被重定向到登录页，登录状态已失效")
        return False
    logger.info(f"已进入我的学堂: {page.url[:80]}")
    return True


async def navigate_to_my_course(page, logger) -> bool:
    """Open the course portal and return only after its course UI is ready."""
    current_url = page.url
    logger.info(f"当前页面: {current_url[:80]}")
    if is_login_url(current_url):
        logger.warn("当前仍在登录页，无法进入我的学堂")
        return False

    if is_course_portal_url(current_url):
        logger.info("已在我的学堂，正在确认课程列表状态...")
        if await _wait_for_portal(page, logger):
            return True
        logger.info("当前子页面未显示课程列表，正在返回课程门户首页...")
        return await _open_portal_directly(page, logger)

    if (urlsplit(current_url).hostname or "").lower().endswith("zhihuishu.com"):
        logger.info("正在从智慧树页面进入我的学堂...")
    else:
        logger.info("正在导航到智慧树首页...")
        try:
            await page.goto(
                MY_COURSE_HOMEPAGE,
                wait_until="commit",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            if is_login_url(page.url):
                logger.warn("智慧树首页跳转到了登录页，登录状态已失效")
                return False
        except Exception as exc:
            logger.warn(f"智慧树首页打开失败，将尝试课程直达地址: {str(exc)[:80]}")
            return await _open_portal_directly(page, logger)

    for selector in (MY_COURSE_LINK, MY_COURSE_TEXT_LINK):
        try:
            await page.click(selector, timeout=10_000)
            logger.info("已点击“我的学堂”，等待课程列表渲染...")
            if await _wait_for_portal(page, logger):
                return True
            break
        except Exception as exc:
            logger.warn(f"课程入口 {selector} 不可用: {str(exc)[:80]}")

    return await _open_portal_directly(page, logger)


async def _open_portal_directly(page, logger) -> bool:
    logger.info("页面入口不可用，正在直接打开我的学堂...")
    try:
        await page.goto(
            MY_COURSE_PORTAL,
            wait_until="commit",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
        return await _wait_for_portal(page, logger)
    except Exception as exc:
        logger.error(f"进入我的学堂失败: {str(exc)[:120]}")
        return False
