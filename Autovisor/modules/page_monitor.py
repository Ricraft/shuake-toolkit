# encoding=utf-8
"""页面通用点击、运行状态探测和安全验证监控。"""

from __future__ import annotations

import asyncio
import json

from playwright._impl._errors import TargetClosedError
from playwright.async_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from modules.diagnostics import RateLimitedDiagnostics
from modules.course_session import ensure_course_authenticated
from modules.logger import Logger
from modules.utils import display_window, hide_window


logger = Logger()
VERIFY_SELECTOR = ".yidun_modal__title"


async def smart_click_text(
    page: Page,
    text: str,
    *,
    logger_instance=None,
) -> bool:
    """精确点击文本，并确保任意文本都不会破坏注入脚本。"""
    active_logger = logger_instance or logger
    expected = str(text)
    selectors = [
        "div",
        "span",
        "p",
        "label",
        "a",
        "li",
        "button",
        '[role="option"]',
    ]
    selectors_literal = json.dumps(selectors, ensure_ascii=False)
    text_literal = json.dumps(expected, ensure_ascii=False)
    try:
        clicked = await page.evaluate(
            f"""
            (() => {{
                const selectors = {selectors_literal};
                const expected = {text_literal};
                for (const selector of selectors) {{
                    for (const element of document.querySelectorAll(selector)) {{
                        if (element.textContent.trim() === expected) {{
                            element.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})()
            """
        )
        if clicked:
            active_logger.info("JS方式点击成功: %s" % expected)
            return True
    except TargetClosedError:
        return False
    except Exception as exc:
        active_logger.debug("JS方式失败: %s" % str(exc)[:80])

    try:
        target = page.get_by_text(expected, exact=True).first
        if await target.count() > 0:
            await target.click(force=True, timeout=2000)
            active_logger.info("精确文本点击成功: %s" % expected)
            return True
    except TargetClosedError:
        return False
    except Exception as exc:
        active_logger.debug("精确文本回退失败: %s" % str(exc)[:80])

    active_logger.warn("[FAIL] 点击失败: %s" % expected)
    return False


STATUS_PROBE_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const visibleText = [];
    for (const el of Array.from(document.querySelectorAll('body *'))) {
        if (visibleText.length >= 5) break;
        const text = normalize(el.textContent || '');
        if (!text) continue;
        if (!(text.includes('学习进度') || text.includes('掌握度') || text.includes('%'))) continue;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (rect.width < 40 || rect.height < 16) continue;
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        if (!visibleText.includes(text)) visibleText.push(text.slice(0, 80));
    }

    const titleSelectors = [
        '#lessonOrder', '.current_play [title]', '.current_play',
        'h1', 'h2', '[title]'
    ];
    let lessonTitle = '';
    for (const selector of titleSelectors) {
        const el = document.querySelector(selector);
        if (!el) continue;
        lessonTitle = normalize(
            el.getAttribute && el.getAttribute('title')
                ? el.getAttribute('title')
                : el.textContent
        );
        if (lessonTitle) break;
    }

    const video = document.querySelector('video');
    const currentTime = video ? Number(video.currentTime || 0) : null;
    const duration = video ? Number(video.duration || 0) : null;
    const percent = video && duration > 0
        ? Math.floor((currentTime / duration) * 100)
        : null;
    return {
        pageTitle: normalize(document.title || ''),
        lessonTitle,
        hasVideo: !!video,
        paused: video ? !!video.paused : null,
        ended: video ? !!video.ended : null,
        currentTime,
        duration,
        percent,
        text: visibleText,
        url: location.href,
    };
}
"""


async def trigger_restart(
    page: Page,
    worker_name: str,
    restart_event: asyncio.Event | None = None,
    *,
    logger_instance=None,
    diagnostics=None,
) -> None:
    active_logger = logger_instance or logger
    active_diagnostics = diagnostics or RateLimitedDiagnostics(active_logger)
    if restart_event is not None and restart_event.is_set():
        return
    if restart_event is not None:
        restart_event.set()
    active_logger.warn(
        f"[{worker_name}] 触发强制重建，立即关闭当前上下文.",
        shift=True,
    )
    try:
        await page.context.close()
    except TargetClosedError:
        return
    except Exception as exc:
        active_diagnostics.warn(
            "restart-context-close",
            f"[{worker_name}] 关闭旧浏览器上下文失败",
            exc,
        )


async def status_ocr_stream(
    page: Page,
    worker_name: str,
    interval_sec: int = 5,
    restart_event: asyncio.Event | None = None,
    *,
    logger_instance=None,
    diagnostics=None,
) -> None:
    active_logger = logger_instance or logger
    active_diagnostics = diagnostics or RateLimitedDiagnostics(active_logger)
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(interval_sec)
            data = await page.evaluate(STATUS_PROBE_JS)
            if data["hasVideo"]:
                current = data["currentTime"] or 0
                duration = data["duration"] or 0
                active_logger.info(
                    f"[{worker_name}/OCR] "
                    f"{data['lessonTitle'] or data['pageTitle']} | "
                    f"{data['percent'] or 0}% | paused={data['paused']} | "
                    f"ended={data['ended']} | {current:.0f}/{duration:.0f}s"
                )
                if data["ended"]:
                    active_logger.warn(
                        f"[{worker_name}/OCR] 检测到 ended=True，等待 worker 重建.",
                        shift=True,
                    )
                    await trigger_restart(
                        page,
                        worker_name,
                        restart_event,
                        logger_instance=active_logger,
                    )
            elif data["text"]:
                active_logger.info(
                    f"[{worker_name}/OCR] {' | '.join(data['text'][:3])}"
                )
        except TargetClosedError:
            active_logger.write_log(f"{worker_name} status stream offline.\n")
            return
        except PlaywrightTimeoutError:
            continue
        except Exception as exc:
            active_diagnostics.warn(
                "status-probe",
                f"[{worker_name}/OCR] 页面状态探测失败",
                exc,
            )


async def wait_for_verify(
    page: Page,
    config,
    event_loop,
    *,
    logger_instance=None,
    diagnostics=None,
) -> None:
    active_logger = logger_instance or logger
    active_diagnostics = diagnostics or RateLimitedDiagnostics(active_logger)
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(3)
            await page.wait_for_selector(
                VERIFY_SELECTOR,
                state="visible",
                timeout=1000,
            )
            event_loop.clear()
            active_logger.warn("检测到安全验证,请手动完成验证...", shift=True)
            if config.enableHideWindow:
                await display_window(page)
            await page.wait_for_selector(
                VERIFY_SELECTOR,
                state="hidden",
                timeout=24 * 3600 * 1000,
            )
            event_loop.set()
            if config.enableHideWindow:
                await hide_window(page)
            active_logger.info("安全验证已完成.", shift=True)
            await asyncio.sleep(30)
        except TargetClosedError:
            active_logger.write_log("浏览器已关闭,安全验证模块已下线.\n")
            return
        except PlaywrightTimeoutError:
            continue
        except Exception as exc:
            active_diagnostics.warn(
                "security-verification-monitor",
                "安全验证监控异常",
                exc,
            )


async def wait_for_verification_resolution(
    page: Page,
    event: asyncio.Event,
    *,
    timeout: float = 300,
    poll_interval: float = 0.5,
    logger_instance=None,
) -> bool:
    """Wait boundedly for the current manual security verification."""
    active_logger = logger_instance or logger
    event.clear()

    async def ensure_active() -> None:
        try:
            await ensure_course_authenticated(
                page,
                "等待安全验证完成时登录状态失效",
            )
        except TargetClosedError:
            raise
        except PlaywrightError:
            try:
                await page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=3000,
                )
            except TargetClosedError:
                raise
            except PlaywrightTimeoutError:
                pass
            await ensure_course_authenticated(
                page,
                "等待安全验证完成时登录状态失效",
            )

    async def verification_remains() -> bool:
        try:
            element = await page.query_selector(VERIFY_SELECTOR)
        except TargetClosedError:
            raise
        except PlaywrightError:
            await ensure_active()
            element = await page.query_selector(VERIFY_SELECTOR)
        if not element:
            return False
        is_visible = getattr(element, "is_visible", None)
        if callable(is_visible):
            return bool(await is_visible())
        return True

    await ensure_active()
    if not await verification_remains():
        return True

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    timeout = max(0.0, float(timeout))
    interval = max(0.05, float(poll_interval))
    while loop.time() - started_at < timeout:
        await ensure_active()
        if event.is_set():
            return True

        remaining = timeout - (loop.time() - started_at)
        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=min(interval, max(0.0, remaining)),
            )
            return True
        except asyncio.TimeoutError:
            pass

        await ensure_active()
        if not await verification_remains():
            active_logger.info("安全验证弹窗已消失，继续课程播放")
            return True

    active_logger.warn(
        "安全验证在 %s 秒内未完成，停止当前视频以避免永久等待"
        % f"{timeout:g}"
    )
    return False
