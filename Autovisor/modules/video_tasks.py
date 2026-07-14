# encoding=utf-8
"""浏览器窗口、视频设置和自动恢复播放的后台任务。"""

from __future__ import annotations

import asyncio

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page

from modules.configs import Config
from modules.course_types import CourseProfile
from modules.logger import Logger
from modules.utils import (
    APPLY_VIDEO_SETTINGS_JS,
    get_browser_window,
    get_video_attr,
    is_playwright_window,
)


logger = Logger()


async def task_monitor(
    tasks: list[asyncio.Task],
    *,
    logger_instance=None,
    poll_interval: float = 1,
) -> None:
    active_logger = logger_instance or logger
    checked_tasks = set()
    active_logger.info("任务监控已启动.")
    while True:
        for task in tasks:
            if not task.done() or task in checked_tasks:
                continue
            checked_tasks.add(task)
            if task.cancelled():
                continue
            try:
                exception = task.exception()
            except asyncio.CancelledError:
                continue
            if exception is None:
                continue
            coroutine = task.get_coro()
            function_name = getattr(coroutine, "__name__", type(coroutine).__name__)
            active_logger.error(f"任务函数{function_name} 出现异常.", shift=True)
            active_logger.write_log(f"{repr(exception)}\n")
        if all(task.done() for task in tasks):
            break
        await asyncio.sleep(poll_interval)
    active_logger.info("任务监控已退出.", shift=True)


async def activate_window(
    page: Page,
    *,
    logger_instance=None,
    poll_interval: float = 2,
) -> None:
    active_logger = logger_instance or logger
    while True:
        try:
            await asyncio.sleep(poll_interval)
            window = await get_browser_window(page, retries=1, delay_ms=50)
            if window and is_playwright_window(window) and window.isMinimized:
                window.moveTo(-3200, -3200)
                await asyncio.sleep(0.3)
                window.restore()
                active_logger.info("检测到播放窗口最小化,已自动恢复.")
        except TargetClosedError:
            active_logger.write_log("浏览器已关闭,窗口激活模块已下线.\n")
            return
        except Exception:
            continue


async def video_optimize(
    page: Page,
    config: Config,
    *,
    logger_instance=None,
    poll_interval: float = 2,
) -> None:
    active_logger = logger_instance or logger
    try:
        await page.wait_for_load_state("domcontentloaded")
    except TargetClosedError:
        active_logger.write_log("浏览器已关闭,视频调节模块已下线.\n")
        return

    click_counter = 0
    first_set_rate = True
    while True:
        try:
            await asyncio.sleep(poll_interval)
            await page.wait_for_selector("video", state="attached", timeout=3000)
            volume = await get_video_attr(page, "volume")
            rate = await get_video_attr(page, "playbackRate")
            profile = CourseProfile.from_url(page.url)

            if profile.is_hike_class or profile.is_national_wisdom:
                click_counter += 1
                video_container = page.locator("#vjs_container")
                if await video_container.count() > 0:
                    await video_container.first.hover()
                if config.soundOff and volume != 0:
                    await page.evaluate(config.volume_none)
                if (
                    not profile.is_national_wisdom
                    and (rate != config.limitSpeed or click_counter >= 3)
                ):
                    click_counter = 0
                    speed_map = {
                        2.0: ".speedTab.speedTab20",
                        1.5: ".speedTab.speedTab15",
                        1.25: ".speedTab.speedTab10",
                        1.0: ".speedTab.speedTab05",
                    }
                    speed_button = page.locator(
                        speed_map.get(config.limitSpeed, ".speedTab.speedTab15")
                    )
                    if await speed_button.count() > 0:
                        await speed_button.first.click()
                        await page.wait_for_timeout(200)
                        if first_set_rate:
                            active_logger.info(
                                f"智慧共享课倍数已设置为 {config.limitSpeed}x"
                            )
                            first_set_rate = False
            else:
                if config.soundOff and volume != 0:
                    await page.evaluate(config.volume_none)
                    await page.evaluate(config.set_none_icon)
                if rate != config.limitSpeed:
                    await page.evaluate(config.revise_speed)
                    await page.evaluate(config.revise_speed_name)
                    if first_set_rate:
                        active_logger.info(f"倍数已设置为 {config.limitSpeed}x")
                        first_set_rate = False
        except TargetClosedError:
            active_logger.write_log("浏览器已关闭,视频调节模块已下线.\n")
            return
        except Exception:
            continue


async def play_video(
    page: Page,
    config: Config,
    *,
    logger_instance=None,
    poll_interval: float = 2,
) -> None:
    active_logger = logger_instance or logger
    try:
        await page.wait_for_load_state("domcontentloaded")
    except TargetClosedError:
        active_logger.write_log("浏览器已关闭,视频播放模块已下线.\n")
        return

    default_speed = config.limitSpeed
    while True:
        try:
            await asyncio.sleep(poll_interval)
            await page.wait_for_selector("video", state="attached", timeout=3000)
            paused = await page.evaluate("document.querySelector('video').paused")
            profile = CourseProfile.from_url(page.url)

            if paused:
                if profile.is_hike_class or profile.is_national_wisdom:
                    ended = await page.evaluate("document.querySelector('video').ended")
                    current_time = await page.evaluate(
                        "document.querySelector('video').currentTime"
                    )
                    duration = await page.evaluate(
                        "document.querySelector('video').duration"
                    )
                    if ended and duration > 0 and current_time < min(duration * 0.1, 3):
                        active_logger.info(
                            "检测到视频异常结束(播放时间过短)，重置并重新播放."
                        )
                        await page.evaluate(
                            "document.querySelector('video').currentTime = 0;"
                        )
                        await page.evaluate(
                            "Object.defineProperty(document.querySelector('video'), "
                            "'ended', { value: false, writable: true });"
                        )
                    elif ended:
                        continue

                active_logger.info("检测到视频暂停,正在尝试播放.")
                speed = 1.0 if profile.is_national_wisdom else default_speed
                await page.evaluate(config.remove_pause)
                result = await page.evaluate(
                    APPLY_VIDEO_SETTINGS_JS,
                    {"speed": speed, "mute": config.soundOff},
                )
                if result and result["success"]:
                    active_logger.info(
                        f"视频设置已应用: 倍速={result['playbackRate']}x, "
                        f"静音={result['muted']}"
                    )
                play_result = await page.evaluate(
                    """async () => {
                        const video = document.querySelector('video');
                        if (!video) return { success: false, error: 'video not found' };
                        try {
                            await video.play();
                            return { success: true, paused: video.paused, error: null };
                        } catch (e) {
                            return { success: false, error: e.message || 'play failed' };
                        }
                    }"""
                )
                if play_result["success"]:
                    active_logger.write_log(
                        f"视频已恢复播放（静音+{speed}x倍速）.\n"
                    )
                else:
                    active_logger.warn(f"视频播放失败: {play_result['error']}")
            elif profile.is_national_wisdom:
                try:
                    status = await page.evaluate(
                        """() => {
                            const video = document.querySelector('video');
                            if (!video) return null;
                            return {
                                currentTime: video.currentTime,
                                duration: video.duration,
                                readyState: video.readyState,
                                networkState: video.networkState,
                                src: video.src || video.currentSrc || null
                            };
                        }"""
                    )
                    if status and status["duration"] > 0 and status["currentTime"] < 0.5:
                        await page.evaluate(config.remove_pause)
                        await page.evaluate('document.querySelector("video").play();')
                except Exception:
                    pass
        except TargetClosedError:
            active_logger.write_log("浏览器已关闭,视频播放模块已下线.\n")
            return
        except Exception:
            continue
