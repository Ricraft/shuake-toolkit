# encoding=utf-8
"""智慧共享课（翻转课）的扫描与逐卡学习流程。"""

from __future__ import annotations

import time

from playwright._impl._errors import TargetClosedError

from modules.course_session import (
    CourseAuthenticationError,
    ensure_course_authenticated,
)
from modules.utils import (
    click_card_by_id,
    get_lesson_name,
    optimize_page,
    scan_pending_lessons_deep,
)


async def run_hike_course(
    page,
    config,
    logger,
    *,
    close_popup,
    learning_loop,
    course_url: str | None = None,
    is_new_version: bool = False,
    is_national_wisdom: bool = False,
    scanner=scan_pending_lessons_deep,
    card_clicker=click_card_by_id,
    title_reader=get_lesson_name,
    optimizer=optimize_page,
    clock=time.time,
    empty_scan_limit: int = 3,
) -> None:
    """执行一次智慧共享课扫描，保持原有页面操作顺序。"""
    logger.info("开始运行时滚动扫描... (智慧共享课)", shift=True)
    empty_scan_count = 0
    while True:
        await ensure_course_authenticated(
            page,
            "翻转课扫描时登录状态失效",
        )
        pending_lessons, summary = await scanner(page)
        await ensure_course_authenticated(
            page,
            "翻转课扫描后登录状态失效",
        )
        logger.info(
            f"整页统计: 总卡片 {summary['total']} | 未完成 {summary['pending']} | 已完成 {summary['done']}",
            shift=True,
        )
        if summary["sections"]:
            top_sections = [
                f"{section}:{stats['pending']}/{stats['total']}"
                for section, stats in summary["sections"].items()
            ]
            logger.info(f"分组统计: {' | '.join(top_sections[:6])}")
        if summary["total"] > 0:
            break
        empty_scan_count += 1
        if empty_scan_count >= empty_scan_limit:
            message = (
                f"连续 {empty_scan_count} 次未检测到翻转课卡片，"
                "无法确认课程列表"
            )
            logger.warn(message, shift=True)
            raise RuntimeError(message)
        logger.info("未检测到翻转课卡片，等待页面渲染后重试...", shift=True)
        await page.wait_for_timeout(1500)

    if not pending_lessons:
        logger.info("没有未完成的课程，本轮结束。")
        return

    target_course_url = course_url or config.course_urls[0]
    start_time = clock()
    completed_card_ids: set[str] = set()
    for lesson in pending_lessons:
        title = lesson["title"]
        card_id = lesson["card_id"]
        scope_id = lesson.get("scope_id")
        top = lesson.get("top")

        time_period = (clock() - start_time) / 60
        if 0 < config.limitMaxTime <= time_period:
            logger.info(f"当前课程已达时限:{config.limitMaxTime}min", shift=True)
            return

        logger.info(
            f"锁定蓝条未满节次: {lesson.get('section', '')} / {title} ({lesson['progress']}%)"
        )
        if not await card_clicker(
            page,
            card_id,
            title,
            scope_id=scope_id,
            top=top,
        ):
            logger.warn(f"未能定位卡片:{title}, 本轮跳过.", shift=True)
            continue

        await page.wait_for_timeout(1000)
        await close_popup(page, logger)
        await ensure_course_authenticated(page, "打开视频后登录状态失效")

        try:
            current_title = await title_reader(page, True) or title
        except TargetClosedError:
            raise
        except Exception as exc:
            try:
                await ensure_course_authenticated(
                    page,
                    "读取视频标题时登录状态失效",
                )
            except CourseAuthenticationError as auth_error:
                raise auth_error from exc
            logger.warn(
                f"读取课程标题失败，继续使用列表标题: {str(exc)[:80]}"
            )
            current_title = title
        logger.info(f"开始观看:{current_title}")

        try:
            await page.wait_for_selector("video", state="attached", timeout=15000)
            await ensure_course_authenticated(page, "等待视频时登录状态失效")
            await page.evaluate(config.remove_pause)
        except (CourseAuthenticationError, TargetClosedError):
            raise
        except Exception as exc:
            try:
                await ensure_course_authenticated(
                    page,
                    "等待视频时登录状态失效",
                )
            except CourseAuthenticationError as auth_error:
                raise auth_error from exc
            logger.warn(
                f"未及时检测到视频元素,进入宽松等待模式: {str(exc)[:80]}",
                shift=True,
            )

        completed = await learning_loop(
            page,
            start_time,
            is_new_version,
            True,
            is_national_wisdom,
        )

        await page.goto(target_course_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await ensure_course_authenticated(
            page,
            "返回课程列表时登录状态失效",
        )
        await optimizer(
            page,
            config,
            is_new_version,
            True,
            is_national_wisdom,
        )
        await ensure_course_authenticated(
            page,
            "恢复课程列表时登录状态失效",
        )
        if completed is not True:
            raise RuntimeError(f"视频未确认完成: {current_title}")

        completed_card_ids.add(card_id)
        await ensure_course_authenticated(
            page,
            "翻转课重新扫描时登录状态失效",
        )
        refreshed_lessons, _summary = await scanner(page)
        await ensure_course_authenticated(
            page,
            "翻转课重新扫描后登录状态失效",
        )
        unresolved_lessons = [
            item
            for item in refreshed_lessons
            if item["card_id"] not in completed_card_ids
        ]
        if not unresolved_lessons:
            logger.info("所有课程已完成!", shift=True)
            return

    await ensure_course_authenticated(
        page,
        "翻转课最终扫描时登录状态失效",
    )
    refreshed_lessons, _summary = await scanner(page)
    await ensure_course_authenticated(
        page,
        "翻转课最终扫描后登录状态失效",
    )
    unresolved_lessons = [
        item
        for item in refreshed_lessons
        if item["card_id"] not in completed_card_ids
    ]
    if unresolved_lessons:
        titles = [item["title"] for item in unresolved_lessons]
        message = (
            f"翻转课仍有 {len(unresolved_lessons)} 个项目未确认完成: {titles}"
        )
        logger.warn(message, shift=True)
        raise RuntimeError(message)
    logger.info("所有课程已完成!", shift=True)
