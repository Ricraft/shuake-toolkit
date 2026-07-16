# encoding=utf-8
"""见面课视频扫描、播放器准备与逐项学习流程。"""

from __future__ import annotations

import time

from modules.progress import move_mouse_meeting_class
from modules.utils import scan_meeting_class_videos


async def set_meeting_playback_rate(
    page,
    playback_rate,
    logger,
    *,
    mouse_mover=move_mouse_meeting_class,
) -> bool:
    """依次尝试智慧树控件、video.js 菜单和原生 video 属性。"""
    if not playback_rate:
        return False
    try:
        await mouse_mover(page)
        await page.wait_for_timeout(500)
    except Exception as exc:
        logger.write_log(f"显示倍速控件失败，继续尝试直接设置: {exc}\n")

    try:
        speed_box = page.locator(".speedBox").first
        if await speed_box.count() > 0 and await speed_box.is_visible():
            await speed_box.click(timeout=2000)
            await page.wait_for_timeout(500)
            rate_map = {
                1.0: ".speedTab05",
                1.25: ".speedTab10",
                1.5: ".speedTab15",
            }
            rate_button = page.locator(rate_map.get(playback_rate, ".speedTab15")).first
            if await rate_button.count() > 0:
                await rate_button.click(timeout=2000)
                logger.info(f"倍数已设置为 {playback_rate}x")
                await page.wait_for_timeout(500)
                return True
    except Exception as exc:
        logger.write_log(f"智慧树倍速控件设置失败: {exc}\n")

    try:
        rate_box = page.locator(".vjs-playback-rate").first
        if await rate_box.count() > 0 and await rate_box.is_visible():
            await rate_box.click(timeout=2000)
            await page.wait_for_timeout(500)
            rate_text = {
                1.0: "1x",
                1.25: "1.25x",
                1.5: "1.5x",
            }.get(playback_rate, f"{playback_rate}x")
            rate_item = rate_box.locator(
                f".vjs-menu-item:has-text('{rate_text}')"
            ).first
            if await rate_item.count() > 0:
                await rate_item.click(timeout=2000)
                logger.info(f"倍数已设置为 {playback_rate}x (video.js)")
                await page.wait_for_timeout(500)
                return True
    except Exception as exc:
        logger.write_log(f"video.js 倍速控件设置失败: {exc}\n")

    try:
        await page.evaluate(
            f"document.querySelector('video').playbackRate = {playback_rate};"
        )
        logger.info(f"通过JS设置倍速为 {playback_rate}x")
        return True
    except Exception as exc:
        logger.warn(f"设置倍速失败: {str(exc)[:50]}")
        return False


async def start_meeting_video(
    page,
    logger,
    *,
    mouse_mover=move_mouse_meeting_class,
) -> bool:
    """尝试两类播放按钮，最后回退到原生 video.play()。"""
    try:
        await mouse_mover(page)
        await page.wait_for_timeout(1000)

        for selector in (".bigPlayButton", ".vjs-big-play-button"):
            button = page.locator(selector).first
            if await button.count() == 0 or not await button.is_visible():
                continue
            try:
                await button.click(timeout=5000)
                logger.info(f"点击播放按钮({selector})成功")
                await page.wait_for_timeout(2000)
                return True
            except Exception as exc:
                logger.write_log(f"点击播放按钮 {selector} 失败: {exc}\n")

        paused = await page.evaluate(
            'document.querySelector("video")?.paused ?? true'
        )
        if paused:
            logger.info("尝试直接播放视频...")
            await page.evaluate("document.querySelector('video').play();")
            await page.wait_for_timeout(2000)
        return True
    except Exception as exc:
        logger.warn(f"自动播放失败: {str(exc)[:100]}")
        return False


async def prepare_meeting_video(
    page,
    config,
    logger,
    *,
    close_popup,
    set_speed: bool,
    mouse_mover=move_mouse_meeting_class,
) -> bool:
    await close_popup(page, logger)
    logger.info("移动鼠标到视频区域...")
    await mouse_mover(page)
    await page.wait_for_timeout(1000)

    speed_applied = False
    if set_speed:
        speed_applied = await set_meeting_playback_rate(
            page,
            getattr(config, "playbackRate", None),
            logger,
            mouse_mover=mouse_mover,
        )
    await start_meeting_video(page, logger, mouse_mover=mouse_mover)
    return speed_applied


async def run_meeting_course(
    page,
    config,
    logger,
    *,
    close_popup,
    learning_loop,
    is_new_version: bool = False,
    is_hike_class: bool = False,
    is_national_wisdom: bool = False,
    scanner=scan_meeting_class_videos,
    video_preparer=prepare_meeting_video,
    clock=time.time,
) -> None:
    logger.info("见面课模式：签到进度达到80%即完成签到")
    logger.info("扫描视频列表...")
    videos = await scanner(page)

    if not videos:
        logger.warn("未找到视频列表，尝试直接播放")
        await page.wait_for_selector("video", state="attached", timeout=15000)
        start_time = clock()
        await video_preparer(
            page,
            config,
            logger,
            close_popup=close_popup,
            set_speed=True,
        )
        completed = await learning_loop(
            page,
            start_time,
            is_new_version,
            is_hike_class,
            is_national_wisdom,
            True,
        )
        if completed is False:
            raise RuntimeError("见面课视频未确认完成")
        logger.info("见面课已完成！", shift=True)
        return

    incomplete_videos = [video for video in videos if not video["completed"]]
    if not incomplete_videos:
        logger.info("所有视频已完成！", shift=True)
        return

    logger.info(f"发现 {len(incomplete_videos)} 个未完成的视频")
    speed_configured = False
    completed_this_run = 0
    limit_reached = False
    for video_idx, video in enumerate(incomplete_videos):
        if video_idx >= 50:
            logger.warn("视频播放次数超限(50)，强制停止", shift=True)
            limit_reached = True
            break
        logger.info(
            f"\n开始播放 ({video_idx + 1}/{len(incomplete_videos)}): "
            f"{video['title']} ({video['duration']})"
        )

        try:
            await video["element"].click(timeout=5000)
            await page.wait_for_timeout(2000)
        except Exception as exc:
            logger.warn(f"点击视频失败: {str(exc)[:50]}")
            continue

        await page.wait_for_selector("video", state="attached", timeout=15000)
        start_time = clock()
        should_set_speed = not speed_configured
        speed_applied = await video_preparer(
            page,
            config,
            logger,
            close_popup=close_popup,
            set_speed=should_set_speed,
        )
        if should_set_speed and speed_applied:
            speed_configured = True

        completed = await learning_loop(
            page,
            start_time,
            is_new_version,
            is_hike_class,
            is_national_wisdom,
            True,
        )
        if completed is False:
            logger.warn(f"视频 '{video['title']}' 未确认完成", shift=True)
            continue
        completed_this_run += 1
        logger.info(f"视频 '{video['title']}' 已完成！", shift=True)

    if not limit_reached and completed_this_run == len(incomplete_videos):
        logger.info("\n所有视频已完成！", shift=True)
    else:
        remaining = len(incomplete_videos) - completed_this_run
        logger.warn(
            f"本轮完成 {completed_this_run}/{len(incomplete_videos)} 个视频，"
            f"仍有 {remaining} 个未确认完成",
            shift=True,
        )
        raise RuntimeError(
            f"见面课仍有 {remaining} 个视频未确认完成"
        )
