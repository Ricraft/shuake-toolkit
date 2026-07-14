# encoding=utf-8
"""普通课视频/测验执行流程。"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from enum import Enum

from modules.utils import (
    get_filtered_class,
    get_lesson_name,
    optimize_page,
    scan_normal_class_tests,
)


class NormalTestOutcome(str, Enum):
    CLICK_FAILED = "click_failed"
    COMPLETED = "completed"
    ANSWERED = "answered"
    NO_QUESTIONS = "no_questions"


class NormalTestSession:
    """管理一次普通课测验的响应监听器和临时页面。"""

    def __init__(self, page, logger, handler, answer_handler):
        self.page = page
        self.logger = logger
        self.handler = handler
        self.answer_handler = answer_handler
        self.new_page = None
        self.new_page_task = None
        self._listener_active = False

    def prepare(self) -> None:
        self.handler.setup_listener(self.page.context)
        self._listener_active = True
        self.new_page_task = asyncio.create_task(self._wait_for_new_page())

    async def _wait_for_new_page(self) -> None:
        try:
            self.new_page = await self.page.context.wait_for_event(
                "page", timeout=8000
            )
            self.logger.info(f"检测到新页面打开: {self.new_page.url}")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.write_log("未检测到新页面\n")

    async def cleanup(self) -> None:
        if self._listener_active:
            self.handler.remove_listener()
            self._listener_active = False
        if self.new_page_task and not self.new_page_task.done():
            self.new_page_task.cancel()
            await asyncio.gather(self.new_page_task, return_exceptions=True)
        if self.new_page:
            try:
                await self.new_page.close()
                self.logger.info("已关闭测验页面")
            except Exception as exc:
                self.logger.write_log(f"关闭测验页面失败: {exc}\n")
            finally:
                self.new_page = None

    async def process(self, course) -> NormalTestOutcome:
        self.prepare()
        try:
            self.logger.info("点击测验项...")
            try:
                await course.click()
            except Exception as exc:
                self.logger.warn(f"点击测验项失败: {str(exc)[:50]}，跳过")
                return NormalTestOutcome.CLICK_FAILED

            if self.new_page_task:
                with suppress(Exception):
                    await self.new_page_task

            work_page = self.new_page or self.page
            self.logger.info(f"工作页面URL: {work_page.url}")
            await work_page.wait_for_load_state("domcontentloaded")
            await work_page.wait_for_timeout(2000)

            self.logger.info("等待题目数据...")
            got_questions = await self.handler.wait_for_questions(timeout=20)
            if got_questions and self.handler.questions_data:
                if self.handler.is_completed:
                    self.logger.info("测验已完成（API确认），跳过")
                    return NormalTestOutcome.COMPLETED
                self.logger.info(
                    f"开始处理测验，共 {len(self.handler.questions_data)} 题"
                )
                await self.answer_handler(
                    work_page,
                    self.handler.questions_data,
                    auto_submit=True,
                )
                return NormalTestOutcome.ANSWERED

            self.logger.warn("未能获取测验题目数据，尝试手动处理")
            return NormalTestOutcome.NO_QUESTIONS
        finally:
            await self.cleanup()


async def restore_normal_course_list(
    page,
    config,
    logger,
    *,
    is_new_version: bool,
    optimizer=optimize_page,
) -> None:
    logger.info("测验处理完成，返回课程列表...")
    try:
        await page.goto(config.course_urls[0], wait_until="domcontentloaded")
        logger.info("已返回课程列表页面")
    except Exception as exc:
        logger.warn(f"返回课程列表失败: {str(exc)[:50]}")
    await page.wait_for_timeout(2000)
    try:
        await optimizer(page, config, is_new_version, False, False, False)
        logger.info("页面优化完成")
    except Exception as exc:
        logger.warn(f"页面优化失败: {str(exc)[:50]}")


async def check_normal_course_time_limit(
    page,
    start_time,
    all_class,
    title,
    config,
    logger,
    *,
    clock=time.time,
) -> bool:
    page.set_default_timeout(24 * 3600 * 1000)
    time_period = (clock() - start_time) / 60
    if 0 < config.limitMaxTime <= time_period:
        logger.info(f"当前课程已达时限:{config.limitMaxTime}min", shift=True)
        logger.info("即将进入下门课程!")
        return True

    class_name = await all_class[-1].get_attribute("class")
    if "current_play" in class_name:
        logger.info("已学完本课程全部内容!", shift=True)
        print("==" * 10)
    else:
        logger.info(f'"{title}" 已完成!', shift=True)
        logger.info(f"本次课程已学习:{time_period:.1f} min")
    return False


async def run_normal_course(
    page,
    config,
    logger,
    *,
    close_popup,
    learning_loop,
    review_loop,
    handler_factory,
    answer_handler,
    is_new_version: bool = False,
    class_provider=get_filtered_class,
    test_scanner=scan_normal_class_tests,
    title_reader=get_lesson_name,
    optimizer=optimize_page,
    time_limit_checker=check_normal_course_time_limit,
    test_session_factory=NormalTestSession,
    clock=time.time,
) -> None:
    await page.wait_for_selector(".clearfix.video, .chapter-test", state="attached")
    await page.wait_for_timeout(2000)
    for _ in range(5):
        if not await close_popup(page, logger):
            break
        await page.wait_for_timeout(500)

    logger.info("扫描课程列表中的测验项...")
    test_items = await test_scanner(page, is_new_version)
    incomplete_tests = [item for item in test_items if not item["completed"]]
    if incomplete_tests:
        logger.info(f"发现 {len(incomplete_tests)} 个未完成的测验")

    to_learn_class = await class_provider(page, is_new_version, False, False)
    learning = bool(to_learn_class)
    start_time = clock()
    current_index = 0
    loop_count = 0
    max_loop = 200
    test_retry_limit = 5
    test_attempts: dict[int, int] = {}
    answered_tests: set[tuple[int, str]] = set()

    while True:
        loop_count += 1
        if loop_count > max_loop:
            logger.warn(f"循环次数超限({max_loop})，强制退出")
            break

        await close_popup(page, logger)
        all_class = await class_provider(
            page,
            is_new_version,
            False,
            False,
            include_all=not learning,
        )
        if current_index >= len(all_class):
            logger.info("本页课程列表已遍历完毕。")
            break

        course = all_class[current_index]
        try:
            course_class = await course.get_attribute("class")
        except Exception:
            logger.warn("获取课程class属性失败，跳过该课程")
            current_index += 1
            continue

        if "chapter-test" in (course_class or ""):
            title_element = course.locator(".name").first
            test_title = (
                await title_element.text_content()
                if await title_element.count() > 0
                else "平时测试"
            )
            logger.info(f"检测到测验项: {test_title.strip()}")
            test_marker = (current_index, test_title.strip())

            if await course.locator("b.finish").count() > 0:
                logger.info("测验已完成，跳过")
                answered_tests.discard(test_marker)
                test_attempts.pop(current_index, None)
                current_index += 1
                continue

            if test_marker in answered_tests:
                logger.warn("测验提交后状态未刷新，本轮不重复作答")
                answered_tests.discard(test_marker)
                test_attempts.pop(current_index, None)
                current_index += 1
                continue

            attempt = test_attempts.get(current_index, 0) + 1
            test_attempts[current_index] = attempt
            if attempt > test_retry_limit:
                logger.warn(f"测验重试超限({test_retry_limit})，强制跳过")
                test_attempts.pop(current_index, None)
                current_index += 1
                continue

            session = test_session_factory(
                page,
                logger,
                handler_factory(),
                answer_handler,
            )
            outcome = await session.process(course)
            if outcome is NormalTestOutcome.CLICK_FAILED:
                current_index += 1
                continue

            await restore_normal_course_list(
                page,
                config,
                logger,
                is_new_version=is_new_version,
                optimizer=optimizer,
            )
            if outcome is NormalTestOutcome.ANSWERED:
                test_attempts.pop(current_index, None)
                answered_tests.add(test_marker)
                logger.info("继续处理下一个课程...")
                continue

            if outcome is NormalTestOutcome.COMPLETED:
                test_attempts.pop(current_index, None)
            current_index += 1
            continue

        test_attempts.clear()
        try:
            await course.click()
        except Exception as exc:
            logger.warn(f"点击课程失败: {str(exc)[:50]}，跳过该课程")
            current_index += 1
            continue

        await page.wait_for_selector(".current_play", state="attached")
        await page.wait_for_timeout(500)
        await close_popup(page, logger)

        title = await title_reader(page, False, False)
        logger.info(f"正在学习:{title}")
        page.set_default_timeout(10000)
        await page.wait_for_selector("video", state="attached")
        await page.evaluate(config.remove_pause)
        if learning:
            await learning_loop(page, start_time, is_new_version, False, False, False)
        else:
            await review_loop(page, start_time, False)

        try:
            if "current_play" in await all_class[current_index].get_attribute("class"):
                current_index += 1
        except Exception as exc:
            logger.warn(f"获取课程状态失败: {str(exc)[:50]}，强制推进")
            current_index += 1

        if await time_limit_checker(
            page,
            start_time,
            all_class,
            title,
            config,
            logger,
        ):
            return
