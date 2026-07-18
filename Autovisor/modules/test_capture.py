# encoding=utf-8
"""测验接口响应监听、题目解析与监听器生命周期管理。"""

from __future__ import annotations

import asyncio

from playwright._impl._errors import TargetClosedError

from modules.diagnostics import RateLimitedDiagnostics
from modules.logger import Logger


logger = Logger()


class TestResponseHandler:
    """在点击测验卡片前监听 doHomework/lookHomework 响应。"""

    __test__ = False

    def __init__(self, *, logger_instance=None, diagnostics=None):
        self._logger = logger_instance or logger
        self._diagnostics = diagnostics or RateLimitedDiagnostics(self._logger)
        self.questions_data = None
        self.questions_event = asyncio.Event()
        self._handler = None
        self._new_page_handler = None
        self._context = None
        self._debug_count = 0
        self._source = None
        self._page_handlers = {}

    def _parse_questions_from_body(self, body):
        """从响应体中解析统一的题目字段。"""
        if not isinstance(body, dict):
            return None
        rt = body.get("rt")
        if not isinstance(rt, dict):
            return None
        exam_base = rt.get("examBase") or rt
        if not isinstance(exam_base, dict):
            return None
        parts = exam_base.get("workExamParts", [])
        if not isinstance(parts, list):
            return None

        questions = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            question_items = part.get("questionDtos", [])
            if not isinstance(question_items, list):
                continue
            for question in question_items:
                if not isinstance(question, dict):
                    continue
                question_type = question.get("questionType") or {}
                if not isinstance(question_type, dict):
                    question_type = {}
                raw_options = question.get("questionOptions") or []
                if not isinstance(raw_options, list):
                    raw_options = []
                options = [
                    (option.get("id"), option.get("content", ""))
                    for option in raw_options
                    if isinstance(option, dict)
                ]
                questions.append(
                    {
                        "name": question.get("name", ""),
                        "type": question_type.get("name", ""),
                        "type_id": question_type.get("id"),
                        "options": options,
                        "score": question.get("questionScore", ""),
                        "eid": question.get("eid", ""),
                    }
                )
        return questions or None

    @property
    def is_completed(self):
        """lookHomework 表示当前测验已经完成。"""
        return self._source == "lookHomework"

    def setup_listener(self, context, clear_data=True):
        """在 context 和已有页面上设置响应监听器。"""
        if self._context is not None:
            self.remove_listener()

        self._context = context
        if clear_data:
            self.questions_data = None
            self.questions_event.clear()
            self._source = None
        self._debug_count = 0
        self._page_handlers = {}
        active_logger = self._logger

        async def on_response(response):
            if self.questions_data:
                return
            self._debug_count += 1
            try:
                url = response.url
            except TargetClosedError:
                return
            except Exception as exc:
                self._diagnostics.warn(
                    "test-response-url",
                    "读取测验响应地址失败",
                    exc,
                )
                return
            is_target = any(
                keyword in url for keyword in ("doHomework", "lookHomework")
            )
            if is_target:
                active_logger.info(
                    f"[响应监听] #{self._debug_count} 命中目标: {url[:100]}"
                )
            if not is_target:
                return

            try:
                if response.status != 200:
                    active_logger.warn(f"目标响应状态码: {response.status}")
                    return
                body = await response.json()
                body_keys = list(body.keys()) if isinstance(body, dict) else []
                active_logger.info(f"响应体keys: {body_keys}")
                questions = self._parse_questions_from_body(body)
                if questions:
                    self.questions_data = questions
                    self._source = (
                        "doHomework" if "doHomework" in url else "lookHomework"
                    )
                    active_logger.info(
                        f"从 {self._source} 拦截到 {len(questions)} 道题目"
                    )
                    self.questions_event.set()
                else:
                    active_logger.warn(
                        "响应中未找到题目数据 "
                        f"(keys: {body_keys}, has rt: {isinstance(body, dict) and 'rt' in body})"
                    )
            except TargetClosedError:
                return
            except Exception as exc:
                self._diagnostics.warn(
                    "test-response-parse",
                    "解析测验响应失败",
                    exc,
                )

        self._handler = on_response
        context.on("response", self._handler)

        for page in context.pages:
            try:
                page_url = page.url
                page.on("response", self._handler)
                self._page_handlers[id(page)] = page
                active_logger.info(f"已为页面注册监听器: {page_url[:60]}")
            except TargetClosedError:
                continue
            except Exception as exc:
                self._diagnostics.warn(
                    "test-listener-page-setup",
                    "为已有页面注册测验响应监听器失败",
                    exc,
                )

        async def on_new_page(new_page):
            try:
                page_url = new_page.url
                new_page.on("response", self._handler)
                self._page_handlers[id(new_page)] = new_page
                active_logger.info(f"新页面打开，注册监听器: {page_url[:60]}")
            except TargetClosedError:
                return
            except Exception as exc:
                self._diagnostics.warn(
                    "test-listener-new-page",
                    "为新页面注册测验响应监听器失败",
                    exc,
                )

        self._new_page_handler = on_new_page
        context.on("page", self._new_page_handler)
        active_logger.info(
            f"已设置响应监听器 (context + {len(self._page_handlers)} pages)"
        )

    async def wait_for_questions(self, timeout: float = 25) -> bool:
        """等待题目数据，返回是否成功。"""
        self._logger.info(
            f"开始等待题目数据，当前已捕获 {self._debug_count} 个响应"
        )
        if self.questions_event.is_set():
            return self.questions_data is not None
        try:
            await asyncio.wait_for(self.questions_event.wait(), timeout=timeout)
            return self.questions_data is not None
        except asyncio.TimeoutError:
            self._logger.warn(
                f"等待响应超时({timeout}s)，共捕获 {self._debug_count} 个响应"
            )
            return False

    def remove_listener(self):
        """移除所有监听器；可在未 setup 或重复清理时安全调用。"""
        context = self._context
        handler = self._handler
        if context is not None and handler is not None:
            try:
                context.remove_listener("response", handler)
            except TargetClosedError:
                pass
            except Exception as exc:
                self._diagnostics.warn(
                    "test-listener-context-cleanup",
                    "移除上下文测验响应监听器失败",
                    exc,
                )

        for page in list(self._page_handlers.values()):
            try:
                page.remove_listener("response", handler)
            except TargetClosedError:
                pass
            except Exception as exc:
                self._diagnostics.warn(
                    "test-listener-page-cleanup",
                    "移除页面测验响应监听器失败",
                    exc,
                )
        self._page_handlers.clear()

        if context is not None and self._new_page_handler is not None:
            try:
                context.remove_listener("page", self._new_page_handler)
            except TargetClosedError:
                pass
            except Exception as exc:
                self._diagnostics.warn(
                    "test-listener-new-page-cleanup",
                    "移除新页面监听器失败",
                    exc,
                )

        self._context = None
        self._handler = None
        self._new_page_handler = None
