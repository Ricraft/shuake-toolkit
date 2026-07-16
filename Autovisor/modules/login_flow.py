"""Shared, bounded Zhihuishu login flow for normal and practice modes."""

from __future__ import annotations

from collections.abc import Callable

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from modules.login_selectors import (
    AUTO_LOGIN_TIMEOUT_MS,
    LOGIN_FORM_TIMEOUT_MS,
    LOGIN_PANEL,
    LOGIN_SUBMIT,
    MANUAL_LOGIN_TIMEOUT_MS,
    PASSWORD_INPUT,
    USERNAME_INPUT,
)
from modules.utils import save_cookies


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
) -> bool:
    """Log in and return whether the login panel was successfully left.

    Automatic login is intentionally bounded so bad credentials or a changed
    login page cannot stall a worker for a day. Manual login retains a long,
    explicit wait because it depends on the user completing the page.
    """
    try:
        await page.goto(
            config.login_url,
            wait_until="commit",
            timeout=LOGIN_FORM_TIMEOUT_MS,
        )
        if "login" not in page.url:
            logger.info("检测到已登录,跳过登录步骤.")
            return True

        await page.wait_for_selector(
            LOGIN_PANEL,
            state="attached",
            timeout=LOGIN_FORM_TIMEOUT_MS,
        )
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
            await page.wait_for_selector(
                LOGIN_SUBMIT,
                state="attached",
                timeout=LOGIN_FORM_TIMEOUT_MS,
            )
            await page.locator(LOGIN_SUBMIT).click(timeout=LOGIN_FORM_TIMEOUT_MS)
            if config.enableAutoCaptcha and modules and slider_handler:
                await slider_handler(page)
            wait_timeout = AUTO_LOGIN_TIMEOUT_MS
            logger.info("登录信息已提交,等待登录结果...")
        else:
            wait_timeout = MANUAL_LOGIN_TIMEOUT_MS
            logger.info("等待手动完成登录...")

        await page.wait_for_selector(
            LOGIN_PANEL,
            state="hidden",
            timeout=wait_timeout,
        )
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
