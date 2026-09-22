"""Shared, bounded Zhihuishu login flow for normal and practice modes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from modules.course_portal import is_course_portal_url, is_login_url
from modules.login_selectors import (
    AUTO_LOGIN_TIMEOUT_MS,
    LOGIN_AGREEMENT_CHECKBOX,
    LOGIN_AGREEMENT_CLICKABLE,
    LOGIN_FORM_TIMEOUT_MS,
    LOGIN_PANEL,
    LOGIN_SUBMIT,
    MANUAL_LOGIN_TIMEOUT_MS,
    PASSWORD_INPUT,
    USERNAME_INPUT,
)
from modules.utils import save_cookies


# 已有凭证时的静默 SSO 预算。实测这条链路（passport → cloudapi.polymas.com →
# gologin → onlineweb）在门户出现前大约需要 20 秒，旧实现只给 15 秒，会把成功
# 的登录判成超时；这里留出安全余量，同时仍然是有界等待。
PASSIVE_LOGIN_TIMEOUT_MS = 60_000
# 面板出现与否的判定窗口。实测静默 SSO 在地址栏仍显示登录页时就已开始跳转，
# 因此这个窗口只需覆盖“面板渲染 vs 直接落到门户”的竞争，不需要覆盖整条链路。
FORM_DETECTION_TIMEOUT_MS = 15_000
# 轮询当前地址的间隔；同一个 SPA 内的客户端跳转不会产生新的导航事件，
# 因此只能靠轮询而不是 page.wait_for_url()。
DESTINATION_POLL_SECONDS = 0.25


def _current_url(page) -> str:
    return str(getattr(page, "url", "") or "")


async def accept_login_agreement(page, logger) -> None:
    """Tick the 用户协议 checkbox across both login panel generations.

    The current panel renders an Element-UI checkbox whose real ``input`` is a
    0x0, ``opacity: 0`` node, so ``check(force=True)`` fails with "Element is
    outside of the viewport". Click the visible ``.el-checkbox__inner`` instead
    and confirm through the input's own checked state. The legacy panel has no
    agreement checkbox, so its absence is not an error.
    """
    checkbox = page.locator(LOGIN_AGREEMENT_CHECKBOX)
    checkbox_count = await checkbox.count()
    if checkbox_count == 0:
        return
    if checkbox_count > 1:
        raise RuntimeError("登录协议勾选框不唯一，页面结构可能已变化")
    if await checkbox.is_checked():
        return

    clickable = page.locator(LOGIN_AGREEMENT_CLICKABLE)
    if await clickable.count() == 1:
        try:
            await clickable.first.click(timeout=LOGIN_FORM_TIMEOUT_MS)
        except Exception as exc:
            logger.warn(f"点击协议勾选框失败，改用脚本勾选: {str(exc)[:80]}")
            await checkbox.evaluate("element => element.click()")
    else:
        # 旧版结构没有 Element-UI 包装层，直接点击 input 本身。
        try:
            await checkbox.click(timeout=LOGIN_FORM_TIMEOUT_MS)
        except Exception as exc:
            logger.warn(f"点击协议勾选框失败，改用脚本勾选: {str(exc)[:80]}")
            await checkbox.evaluate("element => element.click()")

    if not await checkbox.is_checked():
        raise RuntimeError("未能勾选智慧树用户协议和隐私政策")
    logger.info("已勾选智慧树用户协议和隐私政策")


async def wait_for_login_destination(page, timeout: int) -> None:
    """Wait through the authentication gateway until the course portal.

    2026-09 实测的静默 SSO 链路会经过
    ``passport → cloudapi.polymas.com → gologin → onlineweb``，整条链路在门户
    出现前大约要 20 秒。

    这里必须轮询当前地址而不是 ``page.wait_for_url()``：后者只观察“新发生的”
    导航，而调用本函数时 ``passport`` / ``cloudapi`` 这两跳往往已经提交完毕，
    之后剩下的最后一跳如果不再产生新的导航事件，等待就会无谓地超时。
    同时等待预算由调用方的 ``timeout`` 决定，不能再压回更短的上限。
    """
    if is_course_portal_url(_current_url(page)):
        return

    deadline = time.monotonic() + max(timeout, 0) / 1000
    while True:
        if is_course_portal_url(_current_url(page)):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            current_url = _current_url(page)[:120]
            raise RuntimeError(
                f"登录跳转未到达智慧树课程门户，当前地址: {current_url}"
            )
        await asyncio.sleep(min(DESTINATION_POLL_SECONDS, remaining))


async def _panel_attached(page) -> bool:
    """Whether the login panel is currently in the DOM.

    实测：静默 SSO 期间页面会在轮询间隙发生导航，此时求值上下文会被销毁。
    这种“正在跳转”的状态必须当成“面板尚未渲染”，而不是抛错中断登录。
    """
    try:
        return await page.locator(LOGIN_PANEL).count() > 0
    except Exception:
        return False


async def wait_for_login_form_or_portal(page, timeout: int):
    """Return ``"form"`` when the login panel renders, ``"portal"`` on silent SSO.

    实测回归：带有效凭证打开登录入口时，地址栏在 0.6 秒时仍停在
    ``login.zhihuishu.com``，但服务端 SSO 链路会在登录面板渲染之前就把页面带到
    课程门户。此时若按地址一次性判定“需要填写表单”，随后等待永远不会出现的登录
    表单就会超时，把一次成功的静默登录报成失败。因此这里同时观察两件事：
    登录面板是否真的渲染出来，以及地址是否已经离开登录主机。
    """
    if is_course_portal_url(_current_url(page)):
        return "portal"

    deadline = time.monotonic() + max(timeout, 0) / 1000
    while True:
        if await _panel_attached(page):
            return "form"
        if is_course_portal_url(_current_url(page)):
            return "portal"
        if not is_login_url(_current_url(page)):
            # 已离开登录主机（中间认证地址），继续等待最终门户。
            await wait_for_login_destination(page, max(timeout, 0))
            return "portal"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "unknown"
        await asyncio.sleep(min(DESTINATION_POLL_SECONDS, remaining))


async def wait_for_login_form_to_leave(page, timeout: int) -> None:
    """Wait until the rendered login form is left behind for the course portal.

    页面在 ``wait_until="commit"`` 之后可能仍处于 ``loading``：此时选择器计数为
    0，直接等 ``state="hidden"`` 会立刻返回，把“登录面板还没渲染”当成“登录面板
    已消失”。因此先确认面板真的渲染过，再等它消失并落到课程门户。
    """
    if not await _panel_attached(page):
        try:
            outcome = await wait_for_login_form_or_portal(page, timeout)
        except Exception:
            # 轮询期间页面可能在跳转，最终仍以真实地址判定。
            outcome = "portal" if is_course_portal_url(_current_url(page)) else "unknown"
        if outcome == "portal":
            return
        if outcome == "unknown":
            raise RuntimeError("未检测到智慧树登录面板，且未跳转到课程门户")

    try:
        await page.wait_for_selector(
            LOGIN_PANEL,
            state="hidden",
            timeout=timeout,
        )
    except PlaywrightTimeoutError:
        if not is_course_portal_url(_current_url(page)):
            raise
    await wait_for_login_destination(page, timeout)


async def wait_for_login_completion(page, timeout: int) -> None:
    """Wait until the login form is left and the course portal is committed."""
    await wait_for_login_form_to_leave(page, timeout)


async def login_to_zhihuishu(
    context,
    page,
    config,
    logger,
    *,
    modules=None,
    cookie_path: str,
    slider_handler: Callable | None = None,
    cookie_saver: Callable = save_cookies,
    passive_timeout_ms: int = PASSIVE_LOGIN_TIMEOUT_MS,
    form_detection_timeout_ms: int = FORM_DETECTION_TIMEOUT_MS,
    auto_login_timeout_ms: int = AUTO_LOGIN_TIMEOUT_MS,
) -> bool:
    """Log in and return whether the login panel was successfully left.

    Automatic login is intentionally bounded so bad credentials or a changed
    login page cannot stall a worker for a day. Manual login retains a long,
    explicit wait because it depends on the user completing the page.
    ``passive_timeout_ms`` bounds the silent-SSO wait and
    ``form_detection_timeout_ms`` the form-versus-portal race.
    """
    try:
        await page.goto(
            config.login_url,
            wait_until="commit",
            timeout=LOGIN_FORM_TIMEOUT_MS,
        )
        # 不能只看 goto 之后那一瞬间的地址：地址栏可能还停在登录页，而服务端 SSO
        # 正在后台把会话带到课程门户。等登录面板真的渲染出来，或确认已经到门户。
        outcome = await wait_for_login_form_or_portal(page, form_detection_timeout_ms)
        if outcome == "portal":
            logger.info("检测到已登录,跳过登录步骤.")
            await wait_for_login_destination(page, passive_timeout_ms)
            cookies = await context.cookies()
            cookie_saver(cookies, cookie_path)
            logger.info(f"登录成功,凭证已保存到: {cookie_path}")
            return True
        if outcome == "unknown":
            logger.error("未检测到智慧树登录面板，且未跳转到课程门户，已停止登录.")
            return False

        has_credentials = bool(config.username and config.password)
        if has_credentials:
            await page.wait_for_selector(
                USERNAME_INPUT,
                state="attached",
                timeout=LOGIN_FORM_TIMEOUT_MS,
            )
            await page.wait_for_selector(
                PASSWORD_INPUT,
                state="attached",
                timeout=LOGIN_FORM_TIMEOUT_MS,
            )
            await page.locator(USERNAME_INPUT).fill(config.username)
            await page.locator(PASSWORD_INPUT).fill(config.password)
            await accept_login_agreement(page, logger)
            await page.wait_for_selector(
                LOGIN_SUBMIT,
                state="attached",
                timeout=LOGIN_FORM_TIMEOUT_MS,
            )
            await page.locator(LOGIN_SUBMIT).click(timeout=LOGIN_FORM_TIMEOUT_MS)
            captcha_handled = True
            if config.enableAutoCaptcha and modules and slider_handler:
                captcha_handled = bool(await slider_handler(page))
            wait_timeout = auto_login_timeout_ms
            if not captcha_handled:
                # 实测：登录按钮本身就会触发易盾拼图弹窗，自动拖动失败时不会有任何
                # 认证请求发出。此处必须明确提示人工介入，并在有界等待内允许用户
                # 在可见浏览器窗口中完成验证，否则只会以“自动登录超时”收场。
                logger.warn(
                    "自动滑块验证未通过，请在浏览器窗口中手动完成安全验证，"
                    "登录将在验证通过后继续."
                )
            logger.info("登录信息已提交,等待登录结果...")
        else:
            wait_timeout = MANUAL_LOGIN_TIMEOUT_MS
            logger.info("等待手动完成登录...")

        await wait_for_login_completion(page, wait_timeout)
        cookies = await context.cookies()
        cookie_saver(cookies, cookie_path)
        logger.info(f"登录成功,凭证已保存到: {cookie_path}")
        return True
    except PlaywrightTimeoutError:
        if config.username and config.password:
            logger.error(
                "自动登录超时，请检查账号密码、验证码或登录页面是否发生变化."
            )
        else:
            logger.error("等待手动登录超时，本次任务已停止.")
        return False
    except Exception as exc:
        logger.error(f"登录流程失败: {str(exc)[:160]}")
        return False
