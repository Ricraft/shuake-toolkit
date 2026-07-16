# encoding=utf-8
"""全国智慧共享课的扫描、测验与视频学习流程。"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from modules.lesson_navigation import (
    LessonNavigationState,
    SelectionReason,
    find_course_card,
    pending_course_cards,
)
from modules.national_test_flow import NationalTestOutcome, NationalTestSession
from modules.utils import (
    APPLY_VIDEO_SETTINGS_JS,
    click_card_by_id,
    get_lesson_name,
    scan_national_wisdom_cards,
)


def is_course_list_url(current_url: str, course_url: str) -> bool:
    current = urlsplit(current_url)
    target = urlsplit(course_url)
    return (
        (current.hostname or "").lower() == (target.hostname or "").lower()
        and current.path.rstrip("/") == target.path.rstrip("/")
    )


async def return_to_course_list(page, course_url, logger) -> None:
    try:
        await page.go_back(wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        if not is_course_list_url(page.url, course_url):
            logger.info("go_back未回到课程列表，改用goto导航", shift=True)
            await page.goto(course_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
    except Exception as exc:
        logger.write_log(f"go_back失败，改用goto导航回课程列表: {exc}\n")
        try:
            await page.goto(course_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
        except Exception as goto_error:
            logger.warn(f"返回课程列表失败: {goto_error}", shift=True)


async def run_national_course(
    page,
    config,
    logger,
    *,
    close_popup,
    learning_loop,
    handler_factory,
    answer_handler,
    course_url: str | None = None,
    is_new_version: bool = False,
    scanner=scan_national_wisdom_cards,
    card_clicker=click_card_by_id,
    title_reader=get_lesson_name,
    test_session_factory=NationalTestSession,
    navigation_factory=LessonNavigationState,
    clock=time.time,
    empty_scan_limit: int = 5,
    click_retry_limit: int = 3,
) -> None:
    logger.info("开始运行时滚动扫描... (全国智慧共享课)", shift=True)
    await page.wait_for_timeout(2000)
    for _ in range(5):
        if not await close_popup(page, logger):
            break
        await page.wait_for_timeout(500)

    course_url = course_url or config.course_urls[0]
    start_time = clock()
    navigation = navigation_factory(test_retry_limit=5)
    loop_count = 0
    empty_scan_count = 0
    click_failures: dict[str, int] = {}

    while True:
        loop_count += 1
        if loop_count > 200:
            logger.warn("循环次数超限(200)，强制退出", shift=True)
            return

        await close_popup(page, logger)
        all_cards, summary, is_in_iframe = await scanner(page)
        pending_lessons = pending_course_cards(all_cards)
        logger.info(
            f"整页统计: 总卡片 {summary['total']} | 未完成 {summary['pending']} | 已完成 {summary['done']}",
            shift=True,
        )

        if not pending_lessons:
            if summary["total"] == 0:
                empty_scan_count += 1
                if empty_scan_count >= empty_scan_limit:
                    logger.warn(
                        f"连续 {empty_scan_count} 次未检测到课程卡片，停止本轮",
                        shift=True,
                    )
                    return
                logger.info("未检测到课程卡片，可能页面还在加载，重试中...", shift=True)
                await page.wait_for_timeout(3000)
                continue
            logger.info("所有课程已完成!", shift=True)
            return
        empty_scan_count = 0

        test_cards = [card for card in all_cards if card.get("type") == "test"]
        if test_cards:
            logger.info(
                f"检测到 {len(test_cards)} 个测试项: "
                f"{[card['title'] for card in test_cards]}"
            )

        time_period = (clock() - start_time) / 60
        if 0 < config.limitMaxTime <= time_period:
            logger.info(f"当前课程已达时限:{config.limitMaxTime}min", shift=True)
            return

        selection = navigation.choose(pending_lessons)
        lesson = selection.lesson
        if lesson is None:
            if selection.reason is SelectionReason.NO_CANDIDATES:
                logger.info("所有未完成项均已处理或跳过，退出", shift=True)
            else:
                logger.info("所有剩余测验均已达到重试上限，退出", shift=True)
            return
        if selection.reason is SelectionReason.ROUND_RESET:
            logger.info("所有可尝试项均尝试过，清空记录重新开始", shift=True)

        title = lesson["title"]
        card_id = lesson["card_id"]
        lesson_key = lesson["key"]
        logger.info(
            f"锁定未完成节次: {lesson.get('section', '')} / "
            f"{title} ({lesson['progress']}%)"
        )

        test_session = None
        if lesson.get("type") == "test":
            if navigation.begin_test_attempt(lesson_key) is None:
                logger.warn(
                    f"测验 '{title}' 重试超限({navigation.test_retry_limit})，永久跳过",
                    shift=True,
                )
                continue
            test_session = test_session_factory(
                page,
                course_url,
                logger,
                handler_factory(),
                answer_handler,
            )
            test_session.prepare()
            logger.info(f"已设置测试响应监听器，准备点击测试卡片: {title}")

        try:
            if not is_course_list_url(page.url, course_url):
                logger.info("点击卡片前检测到页面不在课程列表，重新导航", shift=True)
                await page.goto(course_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                all_cards, _summary, is_in_iframe = await scanner(page)
                lesson = find_course_card(pending_course_cards(all_cards), lesson_key)
                if lesson is None:
                    logger.warn(f"重新导航后未找到卡片:{title}", shift=True)
                    if test_session:
                        await test_session.cancel()
                    navigation.mark_attempted(lesson_key)
                    continue
                card_id = lesson["card_id"]
                title = lesson["title"]
        except Exception as exc:
            logger.warn(f"恢复课程列表失败: {exc}", shift=True)
            if test_session:
                await test_session.cancel()
            navigation.mark_attempted(lesson_key)
            continue

        if not await card_clicker(page, card_id, title, is_in_iframe):
            logger.warn(f"未能定位卡片:{title}, 本轮跳过.", shift=True)
            if test_session:
                await test_session.cancel()
            await page.wait_for_timeout(1000)
            failures = click_failures.get(lesson_key, 0) + 1
            click_failures[lesson_key] = failures
            if failures >= click_retry_limit:
                navigation.skip(lesson_key)
                logger.warn(
                    f"卡片 '{title}' 连续定位失败 {failures} 次，本轮永久跳过",
                    shift=True,
                )
            else:
                navigation.mark_attempted(lesson_key)
            continue
        click_failures.pop(lesson_key, None)

        if lesson.get("type") == "test":
            logger.info("已点击测试卡片，等待页面加载和API响应...")
            outcome = await test_session.process()
            navigation.mark_attempted(
                lesson_key,
                test_completed=outcome is NationalTestOutcome.COMPLETED,
            )
            continue

        await page.wait_for_timeout(1000)
        await close_popup(page, logger)
        try:
            current_title = await title_reader(page, False, True) or title
        except Exception:
            current_title = title
        logger.info(f"开始观看:{current_title}")

        try:
            await page.wait_for_selector("video", state="attached", timeout=15000)
            await page.wait_for_timeout(500)
            if "www.zhihuishu.com" in page.url:
                logger.error("检测到被重定向到首页，session 可能已过期，退出", shift=True)
                return
            await page.evaluate(config.remove_pause)
            await page.evaluate(
                APPLY_VIDEO_SETTINGS_JS,
                {"speed": 1.0, "mute": config.soundOff},
            )
            sound_state = "静音" if config.soundOff else "声音开启"
            logger.write_log(f"{sound_state}+1.0x倍速 已应用\n")
            await page.wait_for_timeout(300)
            paused = await page.evaluate(
                "document.querySelector('video')?.paused ?? true"
            )
            if paused:
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
                    logger.write_log("视频已开始播放\n")
                else:
                    logger.warn(f"视频播放失败: {play_result['error']}")
        except Exception as exc:
            logger.warn(
                f"未及时检测到视频元素,进入宽松等待模式: {str(exc)[:80]}",
                shift=True,
            )

        lesson_start = clock()
        completed = await learning_loop(
            page, start_time, is_new_version, False, True
        )
        lesson_elapsed = (clock() - lesson_start) / 60
        logger.write_log(f'"{title}" 本课学习用时: {lesson_elapsed:.1f}min\n')
        await return_to_course_list(page, course_url, logger)
        if completed is False:
            raise RuntimeError(f"视频未确认完成: {title}")
        navigation.mark_attempted(lesson_key)
        logger.info("视频播放完成，已返回课程列表", shift=True)
