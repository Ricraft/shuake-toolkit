# encoding=utf-8
"""测验题库查询、浮窗更新、逐题导航和安全交卷编排。"""

from __future__ import annotations

import asyncio
import html
import inspect
import re
from collections.abc import Callable

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from modules.course_errors import CourseAuthenticationError
from modules.course_session import (
    ensure_course_authenticated,
    wait_for_authenticated_selector,
)
from modules.floating_widget import inject_widget
from modules.logger import Logger
from modules.test_page_controls import (
    answer_question,
    click_next_button,
    click_prev_button,
    has_selected_answer,
    submit_exam,
    wait_for_user_action,
)


logger = Logger()

OPTION_SELECTORS = (
    ".nodeLab",
    ".topic-item",
    ".option-item",
    ".el-radio",
    ".el-checkbox",
    ".exam-option",
    '[class*="option"]',
    '[class*="topic"]',
)
OPTION_SELECTOR = ", ".join(OPTION_SELECTORS)

QUESTION_TYPE_MAP = {
    "单选题": "single",
    "多选题": "multiple",
    "判断题": "judgement",
    "填空题": "completion",
    "简答题": "completion",
    "名词解释": "completion",
}


def clean_html_tags(text) -> str:
    """把题目/选项 HTML 转为用于查询和显示的纯文本。"""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", "", str(text))
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\(\s*\)", "（）", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _option_text(option) -> str:
    if isinstance(option, (tuple, list)):
        value = option[1] if len(option) > 1 else option[0] if option else ""
    elif isinstance(option, dict):
        value = (
            option.get("text")
            or option.get("content")
            or option.get("name")
            or option.get("value")
            or ""
        )
    else:
        value = option
    return clean_html_tags(value)


def _question_type_hint(question: dict) -> str | None:
    question_type = str(question.get("type") or "")
    if question_type in QUESTION_TYPE_MAP:
        return QUESTION_TYPE_MAP[question_type]
    return {
        1: "single",
        2: "multiple",
        14: "judgement",
    }.get(question.get("type_id"))


async def _call_query_answer(
    query_answer: Callable,
    title: str,
    options_text: str,
    question_type: str | None,
):
    result = await asyncio.to_thread(
        query_answer,
        title,
        options_text,
        question_type,
    )
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, tuple) or len(result) != 2:
        raise ValueError("题库查询必须返回 (answer, is_ai)")
    return result


async def query_test_answers(
    questions_data: list[dict],
    *,
    query_answer: Callable,
    logger_instance=None,
    max_concurrency: int = 4,
) -> None:
    """有界并发查询整份试卷，并按原题目顺序写回结果。"""
    active_logger = logger_instance or logger
    concurrency = max(1, int(max_concurrency))
    semaphore = asyncio.Semaphore(concurrency)

    async def query_one(index: int, question: dict):
        title = clean_html_tags(question.get("name", ""))
        question_type = str(question.get("type") or "")
        raw_options = question.get("options") or []
        options = [_option_text(option) for option in raw_options]
        try:
            async with semaphore:
                answer, is_ai = await _call_query_answer(
                    query_answer,
                    title,
                    "\n".join(options),
                    _question_type_hint(question),
                )
            answer = str(answer) if answer else None
            return index, title, question_type, answer, bool(is_ai), None
        except Exception as exc:
            return index, title, question_type, None, False, exc

    results = await asyncio.gather(
        *(query_one(index, question) for index, question in enumerate(questions_data))
    )
    for index, title, question_type, answer, is_ai, error in results:
        question = questions_data[index]
        question["answer"] = answer
        question["is_ai"] = is_ai
        if error is not None:
            active_logger.warn(
                "[WARN] 第%d题题库查询失败: %s"
                % (index + 1, str(error)[:80])
            )
        if answer:
            source = "AI" if is_ai else "题库"
            active_logger.info(
                "\n[%s] 第%d题 [%s]" % (source, index + 1, question_type)
            )
            active_logger.info("题目: %s..." % title[:80])
            active_logger.info("答案: %s" % answer[:60])
        else:
            active_logger.info(
                "\n[NO MATCH] 第%d题 [%s]" % (index + 1, question_type)
            )
            active_logger.info("题目: %s..." % title[:80])


async def _wait_for_options(page: Page, active_logger) -> bool:
    for retry in range(3):
        try:
            await ensure_course_authenticated(
                page,
                "等待测验选项时登录状态失效",
            )
            await wait_for_authenticated_selector(
                page,
                OPTION_SELECTOR,
                "等待测验选项时登录状态失效",
                timeout=5000,
                state="visible",
            )
            active_logger.info("[OK] 选项元素已加载")
            return True
        except CourseAuthenticationError:
            active_logger.warn("[FAIL] 等待测验选项时登录状态失效")
            raise
        except TargetClosedError:
            active_logger.warn("[WARN] 答题页面在加载选项时关闭")
            return False
        except PlaywrightTimeoutError:
            pass
        except Exception as exc:
            active_logger.warn(
                "[WARN] 检查测验选项时发生未知错误: %s"
                % str(exc)[:80]
            )
            return False
        if retry < 2:
            active_logger.warn(
                "[RETRY] 等待选项加载失败，重试 %d/3" % (retry + 1)
            )
            await page.wait_for_timeout(3000)
    try:
        page_html = await page.content()
        active_logger.warn("页面HTML前500字符: %s" % page_html[:500])
    except TargetClosedError:
        active_logger.warn("[WARN] 答题页面在读取诊断信息时关闭")
    except Exception as exc:
        active_logger.warn(
            "[WARN] 无法读取测验页面诊断信息: %s"
            % str(exc)[:80]
        )
    active_logger.error("[ERROR] 无法加载选项元素")
    return False


async def _update_widget(page: Page, widget_data: dict) -> bool:
    try:
        await page.evaluate(
            """
            data => {
                if (window.AIAnswerWidget) {
                    window.AIAnswerWidget.updateQuestion(data);
                }
            }
            """,
            widget_data,
        )
        return True
    except Exception:
        return False


async def handle_test_page(
    page: Page,
    questions_data: list,
    auto_submit: bool = False,
    manual_submit: bool = False,
    *,
    query_answer: Callable,
    widget_injector: Callable = inject_widget,
    wait_action: Callable = wait_for_user_action,
    selection_checker: Callable = has_selected_answer,
    next_clicker: Callable = click_next_button,
    prev_clicker: Callable = click_prev_button,
    answer_applier: Callable = answer_question,
    submitter: Callable = submit_exam,
    logger_instance=None,
    max_query_concurrency: int = 4,
) -> bool:
    """处理整份测验，并按真实页面状态控制导航和交卷。"""
    active_logger = logger_instance or logger
    await page.wait_for_load_state("domcontentloaded")
    if not questions_data:
        active_logger.warn("[WARN] 没有题目数据")
        return False
    if not isinstance(questions_data, list) or not all(
        isinstance(question, dict) for question in questions_data
    ):
        active_logger.error("[ERROR] 题目数据格式无效")
        return False

    try:
        await widget_injector(page)
        active_logger.info("[OK] 浮动答题助手已注入")
    except Exception as exc:
        active_logger.warn("[WARN] 浮动组件注入失败: %s" % str(exc)[:50])

    if not await _wait_for_options(page, active_logger):
        return False

    total = len(questions_data)
    active_logger.info("=" * 60)
    active_logger.info("[START] 开始答题，共 %d 道题" % total)
    active_logger.info("=" * 60)
    active_logger.info("\n[STEP1] 批量查询答案")
    await query_test_answers(
        questions_data,
        query_answer=query_answer,
        logger_instance=active_logger,
        max_concurrency=max_query_concurrency,
    )

    active_logger.info("\n" + "=" * 60)
    active_logger.info("[STEP2] 逐题答题")
    active_logger.info("=" * 60)
    current_index = 0
    while current_index < total:
        index = current_index
        question = questions_data[index]
        question_name = clean_html_tags(question.get("name", ""))
        question_type = str(question.get("type") or "")
        answer = question.get("answer") or ""
        raw_options = question.get("options") or []
        options = [_option_text(option) for option in raw_options]
        is_ai = bool(question.get("is_ai"))
        active_logger.info(
            "\n--- 第%d/%d题 [%s] ---" % (index + 1, total, question_type)
        )
        active_logger.info("题目: %s..." % question_name[:60])
        await _update_widget(
            page,
            {
                "question": question_name,
                "answer": answer or "未找到答案",
                "options": options,
                "type": question_type,
                "index": index,
                "total": total,
                "isAI": is_ai,
            },
        )

        if not auto_submit:
            active_logger.info("[手动模式] 请在页面上选择答案")
            is_multiple = "多选" in question_type
            action = await wait_action(page, is_multiple, timeout=180)
            if action not in {"closed", "error"}:
                selection_state = await selection_checker(page)
                if selection_state is None:
                    active_logger.error(
                        "[ERROR] 无法确认当前题作答状态，停止答题以防题号错位"
                    )
                    return False
                question["answer_applied"] = bool(selection_state)
                if (
                    action in {"next", "option_click", "submit", "timeout"}
                    and not question["answer_applied"]
                ):
                    active_logger.warn("[WARN] 当前题未检测到已选择或已填写的答案")

            if action == "next":
                active_logger.info("[手动模式] 用户点击下一题")
                if index < total - 1:
                    if not await next_clicker(page):
                        active_logger.error(
                            "[ERROR] 下一题按钮点击失败，停止答题以防题号错位"
                        )
                        return False
                    await page.wait_for_timeout(300)
                current_index += 1
            elif action == "prev":
                active_logger.info("[手动模式] 用户点击上一题")
                if index > 0:
                    if not await prev_clicker(page):
                        active_logger.error(
                            "[ERROR] 上一题按钮点击失败，停止答题以防题号错位"
                        )
                        return False
                    await page.wait_for_timeout(300)
                    current_index -= 1
            elif action == "option_click":
                active_logger.info("[手动模式] 用户选择了选项")
                if not is_multiple:
                    await page.wait_for_timeout(500)
                    if index < total - 1:
                        if not await next_clicker(page):
                            active_logger.error(
                                "[ERROR] 下一题按钮点击失败，停止答题以防题号错位"
                            )
                            return False
                        await page.wait_for_timeout(300)
                    current_index += 1
            elif action == "submit":
                active_logger.info("[手动模式] 用户点击提交试卷")
                break
            elif action == "closed":
                active_logger.warn("[WARN] 答题页面已关闭，答题流程终止")
                return False
            elif action == "timeout":
                active_logger.warn("[手动模式] 超时，自动下一题")
                if index < total - 1:
                    if not await next_clicker(page):
                        active_logger.error(
                            "[ERROR] 下一题按钮点击失败，停止答题以防题号错位"
                        )
                        return False
                    await page.wait_for_timeout(300)
                current_index += 1
            else:
                active_logger.error(
                    "[ERROR] 答题页面监听异常，停止答题以防题号错位"
                )
                return False
        else:
            active_logger.info("[自动模式] 自动选择答案")
            if answer.strip():
                active_logger.info("答案: %s" % answer[:40])
                applied = await answer_applier(
                    page,
                    answer,
                    options,
                    raw_options,
                )
                question["answer_applied"] = bool(applied)
                if not applied:
                    active_logger.warn("[WARN] 答案存在，但页面选项点击失败")
            else:
                question["answer_applied"] = False
                active_logger.warn(
                    "[WARN] 答案为空或无法匹配，留空跳过（可手动补充）"
                )
            await page.wait_for_timeout(500)
            if index < total - 1:
                if not await next_clicker(page):
                    active_logger.error(
                        "[ERROR] 下一题按钮点击失败，停止答题以防题号错位"
                    )
                    return False
                await page.wait_for_timeout(300)
            current_index += 1

    active_logger.info("\n" + "=" * 60)
    active_logger.info("[STEP3] 提交试卷")
    active_logger.info("=" * 60)
    if manual_submit:
        active_logger.info("[手动提交] 已跳过自动提交，请在页面上手动点击提交按钮")
        active_logger.info("[手动提交] 页面保持打开状态，请检查答案后手动提交")
        active_logger.warn("[手动提交] 尚未确认试卷已经提交")
        return False

    answered = sum(
        1 for question in questions_data if question.get("answer_applied")
    )
    unanswered = total - answered
    if unanswered > total * 0.5:
        active_logger.warn(
            "\n[WARN] 未答题数 %d/%d 超过 50%%，跳过自动交卷，请手动检查后提交"
            % (unanswered, total)
        )
        active_logger.warn("[WARN] 如需强制自动交卷，请调整答题策略或配置 AI")
        return False

    success = await submitter(page)
    if success:
        active_logger.info("\n" + "=" * 60)
        active_logger.info("[DONE] 答题完成，准备继续课程学习")
        active_logger.info("=" * 60)
        try:
            await page.wait_for_timeout(1500)
            await page.evaluate("window.close()")
            active_logger.info("[OK] 已关闭测验页面")
        except Exception as exc:
            active_logger.warn("[WARN] 关闭页面: %s" % str(exc)[:30])
    else:
        active_logger.warn("\n" + "=" * 60)
        active_logger.warn("[FAIL] 答题提交失败")
        active_logger.warn("=" * 60)
    return bool(success)
