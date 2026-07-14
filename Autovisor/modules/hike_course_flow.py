# encoding=utf-8
"""智慧共享课（翻转课）的扫描与逐卡学习流程。"""

from __future__ import annotations

import time

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
    is_new_version: bool = False,
    is_national_wisdom: bool = False,
    scanner=scan_pending_lessons_deep,
    card_clicker=click_card_by_id,
    title_reader=get_lesson_name,
    optimizer=optimize_page,
    clock=time.time,
) -> None:
    """执行一次智慧共享课扫描，保持原有页面操作顺序。"""
    logger.info("开始运行时滚动扫描... (智慧共享课)", shift=True)
    pending_lessons, summary = await scanner(page)
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

    if not pending_lessons:
        logger.info("没有未完成的课程，本轮结束。")
        return

    start_time = clock()
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
        if not await card_clicker(page, card_id, title, scope_id, top):
            logger.warn(f"未能定位卡片:{title}, 本轮跳过.", shift=True)
            continue

        await page.wait_for_timeout(1000)
        await close_popup(page, logger)

        try:
            current_title = await title_reader(page, True) or title
        except Exception:
            current_title = title
        logger.info(f"开始观看:{current_title}")

        try:
            await page.wait_for_selector("video", state="attached", timeout=15000)
            await page.evaluate(config.remove_pause)
        except Exception:
            logger.warn("未及时检测到视频元素,进入宽松等待模式.", shift=True)

        await learning_loop(
            page,
            start_time,
            is_new_version,
            True,
            is_national_wisdom,
        )

        await page.goto(config.course_urls[0], wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await optimizer(
            page,
            config,
            is_new_version,
            True,
            is_national_wisdom,
        )

        refreshed_lessons, _summary = await scanner(page)
        if not refreshed_lessons:
            logger.info("所有课程已完成!", shift=True)
            return

