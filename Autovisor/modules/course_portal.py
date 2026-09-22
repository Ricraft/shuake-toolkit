# encoding=utf-8
"""Bounded navigation to the Zhihuishu "My Courses" portal."""

from __future__ import annotations

from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from modules.login_selectors import LOGIN_HOSTS, LOGIN_PANEL


MY_COURSE_HOMEPAGE = "https://www.zhihuishu.com/"
MY_COURSE_PORTAL = "https://onlineweb.zhihuishu.com/"
# 实测：带认证会话打开门户根地址会被 passport → cloudapi → gologin 重定向链带到
# https://onlineweb.zhihuishu.com/onlinestuh5；直接使用该地址可跳过这条链路。
MY_COURSE_INDEX = "https://onlineweb.zhihuishu.com/onlinestuh5"
MY_COURSE_LINK = 'a[href^="https://onlineweb.zhihuishu.com"]'
MY_COURSE_TEXT_LINK = 'a:has-text("我的学堂")'
# 2026-09 实测：“我的课堂”首页是 qiankun 微前端，课程按板块渲染：
#   #sharingClassed 共享课 / #flipLessoned 翻转课 /
#   #interestingClassed 兴趣课 / #aiCourseed 智慧课程。
# 旧的 .course-list / .study-card / .courseCard 已不再出现，因此“课程首页已就绪”
# 只能依赖这组板块容器。
PORTAL_READY_SELECTORS = (
    '[id*="qiankun"]',
    '#sharingClassed',
    '#flipLessoned',
    '#interestingClassed',
    '#aiCourseed',
)
PORTAL_READY_SELECTOR = ", ".join(PORTAL_READY_SELECTORS)
# 门户自身的课程首页路径；其余路径都是子路由，需要先回到首页。
PORTAL_INDEX_PATHS = frozenset({"", "/", "/onlinestuh5"})

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


def is_zhihuishu_host(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    return hostname == "zhihuishu.com" or hostname.endswith(".zhihuishu.com")


def is_course_homepage_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower().rstrip(".") == "www.zhihuishu.com"


def is_portal_index_url(url: str) -> bool:
    """Recognize the portal's own course index, excluding child routes."""
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower().rstrip(".") != "onlineweb.zhihuishu.com":
        return False
    return (parsed.path.rstrip("/") or "/") in PORTAL_INDEX_PATHS


def is_course_list_url(current_url: str, course_url: str) -> bool:
    """Compare a current address with its course-list address by host and path."""
    current = urlsplit(current_url)
    target = urlsplit(course_url)
    return (
        (current.hostname or "").lower() == (target.hostname or "").lower()
        and current.path.rstrip("/") == target.path.rstrip("/")
    )


async def is_login_page(page) -> bool:
    """Recognize login state from either the current URL or rendered login DOM."""
    if is_login_url(getattr(page, "url", "")):
        return True

    locator_factory = getattr(page, "locator", None)
    if not callable(locator_factory):
        return False
    panel = locator_factory(LOGIN_PANEL)
    count = await panel.count()
    if count == 0:
        return False
    if count == 1:
        return await panel.is_visible()
    for item in await panel.all():
        if await item.is_visible():
            return True
    return False


async def _wait_for_portal(page, logger) -> bool:
    try:
        await page.wait_for_selector(
            PORTAL_READY_SELECTOR,
            state="attached",
            timeout=PORTAL_RENDER_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        if await is_login_page(page):
            logger.warn("进入我的学堂时被重定向到登录页，登录状态已失效")
        else:
            logger.warn("我的学堂页面已打开，但课程板块在 20 秒内未完成渲染")
        return False

    if await is_login_page(page):
        logger.warn("进入我的学堂时被重定向到登录页，登录状态已失效")
        return False
    logger.info(f"已进入我的学堂: {page.url[:80]}")
    return True


async def navigate_to_my_course(page, logger) -> bool:
    """Open the course portal and return only after its course UI is ready."""
    current_url = page.url
    logger.info(f"当前页面: {current_url[:80]}")
    if await is_login_page(page):
        logger.warn("当前仍在登录页，无法进入我的学堂")
        return False

    if is_course_portal_url(current_url):
        if is_portal_index_url(current_url):
            logger.info("已在我的学堂首页，正在确认课程板块状态...")
            if await _wait_for_portal(page, logger):
                return True
        else:
            logger.info("当前在门户子页面，未显示课程板块，正在返回课程首页...")
        return await _open_portal_directly(page, logger)

    if is_zhihuishu_host(current_url):
        logger.info("正在从智慧树页面进入我的学堂...")
    else:
        logger.info("正在导航到智慧树首页...")
        try:
            await page.goto(
                MY_COURSE_HOMEPAGE,
                wait_until="commit",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            if await is_login_page(page):
                logger.warn("智慧树首页跳转到了登录页，登录状态已失效")
                return False
        except Exception as exc:
            logger.warn(f"智慧树首页打开失败，将尝试课程直达地址: {str(exc)[:80]}")
            return await _open_portal_directly(page, logger)

    for selector in (MY_COURSE_LINK, MY_COURSE_TEXT_LINK):
        try:
            await page.click(selector, timeout=10_000)
            logger.info("已点击“我的学堂”，等待课程板块渲染...")
            if await _wait_for_portal(page, logger):
                return True
            break
        except Exception as exc:
            logger.warn(f"课程入口 {selector} 不可用: {str(exc)[:80]}")

    return await _open_portal_directly(page, logger)


async def _open_portal_directly(page, logger) -> bool:
    logger.info("页面入口不可用，正在直接打开我的学堂...")
    for target in (MY_COURSE_INDEX, MY_COURSE_PORTAL):
        try:
            await page.goto(
                target,
                wait_until="commit",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            if await _wait_for_portal(page, logger):
                return True
        except Exception as exc:
            logger.warn(f"打开 {target} 失败: {str(exc)[:100]}")
    logger.error("进入我的学堂失败: 课程首页与门户根地址均未就绪")
    return False
