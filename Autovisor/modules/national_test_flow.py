# encoding=utf-8
"""全国智慧共享课测验页面的监听、答题、清理与返回流程。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from enum import Enum

from playwright._impl._errors import TargetClosedError

from modules.course_portal import is_course_list_url
from modules.course_session import (
    CourseAuthenticationError,
    ensure_course_authenticated,
)


START_BUTTON_SELECTOR = (
    "button:has-text('开始做题'), button:has-text('开始做'), "
    "a:has-text('开始做题'), a:has-text('开始做'), .start-btn, [class*='start']"
)


class NationalTestOutcome(str, Enum):
    COMPLETED = "completed"
    ANSWERED = "answered"
    ANSWER_FAILED = "answer_failed"
    NO_QUESTIONS = "no_questions"


class NationalTestSession:
    """管理一次测验尝试所创建的监听器、任务和临时页面。"""

    def __init__(self, page, course_url, logger, handler, answer_handler):
        self.page = page
        self.course_url = course_url
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

    async def _await_new_page(self) -> None:
        if not self.new_page_task:
            return
        with suppress(Exception):
            await self.new_page_task

    async def _close_new_page(self) -> None:
        if not self.new_page:
            return
        try:
            await self.new_page.close()
            self.logger.info("已关闭测试页面")
        except Exception as exc:
            self.logger.write_log(f"关闭测试页面失败: {exc}\n")
        finally:
            self.new_page = None

    async def cancel(self) -> None:
        if self._listener_active:
            self.handler.remove_listener()
            self._listener_active = False
        if self.new_page_task and not self.new_page_task.done():
            self.new_page_task.cancel()
            await asyncio.gather(self.new_page_task, return_exceptions=True)
        await self._close_new_page()

    async def _trigger_questions(self, work_page) -> bool:
        try:
            start_button = work_page.locator(START_BUTTON_SELECTOR).first
            if await start_button.count() > 0:
                self.logger.info("找到开始做题按钮，点击...")
                await start_button.click(timeout=5000)
                await work_page.wait_for_load_state("networkidle")
        except Exception as exc:
            self.logger.write_log(f"未找到开始做题按钮: {exc}\n")

        got_questions = await self.handler.wait_for_questions(timeout=20)
        if got_questions:
            self.logger.info("成功捕获题目数据")
        return got_questions

    async def restore_course_list(self) -> None:
        """强制清理测验页 CDP 状态，并确保回到课程列表。"""
        try:
            self.logger.info("测验处理后 force reload 清理CDP状态", shift=True)
            await self.page.reload(wait_until="domcontentloaded")
            await self.page.wait_for_timeout(1500)
            await ensure_course_authenticated(
                self.page,
                "测验结束后登录状态失效",
            )
            if not is_course_list_url(self.page.url, self.course_url):
                await self.page.goto(self.course_url, wait_until="domcontentloaded")
                await self.page.wait_for_timeout(1500)
            await ensure_course_authenticated(
                self.page,
                "恢复课程列表时登录状态失效",
            )
            if not is_course_list_url(self.page.url, self.course_url):
                raise RuntimeError(
                    f"恢复后地址仍不是课程列表: {self.page.url[:100]}"
                )
        except (CourseAuthenticationError, TargetClosedError):
            raise
        except Exception as reload_error:
            self.logger.write_log(f"测验页 reload 失败，改用课程地址恢复: {reload_error}\n")
            try:
                await self.page.goto(self.course_url, wait_until="domcontentloaded")
                await self.page.wait_for_timeout(1500)
                await ensure_course_authenticated(
                    self.page,
                    "恢复课程列表时登录状态失效",
                )
                if not is_course_list_url(self.page.url, self.course_url):
                    raise RuntimeError(
                        f"恢复后地址仍不是课程列表: {self.page.url[:100]}"
                    )
            except (CourseAuthenticationError, TargetClosedError):
                raise
            except Exception as goto_error:
                self.logger.warn(f"返回课程列表失败: {goto_error}", shift=True)
                raise RuntimeError(
                    f"返回课程列表失败: {goto_error}"
                ) from goto_error

    async def process(self) -> NationalTestOutcome:
        try:
            await self._await_new_page()
            work_page = self.new_page or self.page
            self.logger.info(f"工作页面URL: {work_page.url}")
            await work_page.wait_for_load_state("networkidle")
            self.logger.info(f"页面加载完成，URL: {work_page.url}")

            if self.handler.questions_data:
                if self.handler.is_completed:
                    self.logger.info("测试已完成（lookHomework），跳过答题")
                    return NationalTestOutcome.COMPLETED
                self.logger.info(
                    f"doHomework已返回 {len(self.handler.questions_data)} 道题目"
                )
            else:
                await self._trigger_questions(work_page)
                if self.handler.is_completed:
                    self.logger.info("测试已完成，跳过答题")
                    return NationalTestOutcome.COMPLETED

            if self.handler.questions_data:
                answered = await self.answer_handler(
                    work_page,
                    self.handler.questions_data,
                    auto_submit=True,
                )
                if answered:
                    return NationalTestOutcome.ANSWERED
                self.logger.warn("测试答题或提交未确认成功")
                return NationalTestOutcome.ANSWER_FAILED

            self.logger.warn("没有题目数据，跳过答题")
            return NationalTestOutcome.NO_QUESTIONS
        finally:
            await self.cancel()
            await self.restore_course_list()
