# encoding=utf-8
"""随堂题弹窗识别、题库匹配、作答和后台监听。"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from modules.course_session import ensure_course_authenticated
from modules.course_types import CourseKind, CourseProfile
from modules.logger import Logger
from modules.question_bank_client import _match_option


logger = Logger()


async def _extract_question_title(page: Page) -> str:
    """从普通随堂题弹窗提取当前题目。"""
    title_element = await page.query_selector(".topic-title")
    if title_element:
        text = (await title_element.text_content() or "").strip()
        if text:
            return text
    for selector in (".subject-title", ".question-title", ".el-dialog__body"):
        element = await page.query_selector(selector)
        if element:
            text = (await element.text_content() or "").strip()
            if text:
                return text[:500]
    return ""


async def _extract_options(page: Page) -> list[str]:
    """从普通随堂题弹窗提取选项。"""
    options = []
    for item in await page.query_selector_all(".topic-item"):
        text = await item.text_content()
        if text:
            options.append(text.strip())
    return options


async def _extract_wisdom_question(page_or_dialog) -> str:
    """从全国智慧课随堂练习提取题目。"""
    question = page_or_dialog.locator(".question-info").first
    if await question.count() > 0:
        text = await question.text_content()
        return text.strip() if text else ""
    return ""


async def _extract_wisdom_options(page_or_dialog) -> list[str]:
    """从全国智慧课随堂练习提取带字母的选项。"""
    options = []
    items = page_or_dialog.locator(".option")
    for index in range(await items.count()):
        item = items.nth(index)
        letter_element = item.locator(".class-question-select").first
        answer_element = item.locator(".answer").first
        letter = ""
        answer = ""
        if await letter_element.count() > 0:
            letter = (await letter_element.text_content() or "").strip()
        if await answer_element.count() > 0:
            answer = (await answer_element.text_content() or "").strip()
        if not answer:
            answer = (await item.text_content() or "").strip()
        if answer:
            options.append(f"{letter}. {answer}" if letter else answer)
    return options


async def _query_answer(
    query_answer: Callable,
    title: str,
    options: list[str],
    active_logger,
) -> tuple[str | None, bool]:
    """在线程中执行同步题库查询，避免阻塞浏览器事件循环。"""
    if not title or not options:
        return None, False
    try:
        result = await asyncio.to_thread(
            query_answer,
            title,
            "\n".join(options),
        )
        if not isinstance(result, tuple) or len(result) != 2:
            active_logger.warn("随堂题题库返回格式无效，改用回退策略")
            return None, False
        answer, is_ai = result
        return (str(answer) if answer else None), bool(is_ai)
    except Exception as exc:
        active_logger.warn("随堂题题库查询失败: %s" % str(exc)[:80])
        return None, False


def _fallback_choice_indexes(
    option_count: int,
    is_multiple: bool,
    *,
    randrange: Callable[[int], int] = random.randrange,
) -> list[int]:
    """无答案时保留原自动推进意图，并按题型控制选择数量。"""
    if option_count <= 0:
        return []
    first = randrange(option_count)
    if not is_multiple or option_count == 1:
        return [first]
    return [first, (first + 1) % option_count]


async def _click_locator_indexes(
    options,
    indexes: list[int],
    page: Page,
    active_logger,
) -> bool:
    if not indexes:
        return False
    succeeded = True
    count = await options.count()
    for index in indexes:
        if index < 0 or index >= count:
            succeeded = False
            continue
        try:
            await options.nth(index).click(timeout=1000)
            await page.wait_for_timeout(100)
        except TargetClosedError:
            raise
        except Exception as exc:
            active_logger.warn(
                "随堂题第 %d 个选项点击失败: %s"
                % (index + 1, str(exc)[:60])
            )
            succeeded = False
    return succeeded


async def _dialog_visible(dialog) -> bool:
    return await dialog.count() > 0 and await dialog.is_visible()


async def _dismiss_wisdom_dialog(page: Page, dialog, active_logger) -> bool:
    """优先使用关闭按钮，失败后使用 Escape，并验证弹窗确实消失。"""
    try:
        close_button = dialog.locator(".header-icon").first
        if await close_button.count() > 0 and await close_button.is_visible():
            await close_button.click(timeout=1000)
            await page.wait_for_timeout(500)
            if not await _dialog_visible(dialog):
                active_logger.info("智慧课 - 关闭随堂练习弹窗成功")
                return True
    except TargetClosedError:
        raise
    except Exception as exc:
        active_logger.warn(
            "智慧课 - 关闭按钮操作失败，尝试 Escape: %s"
            % str(exc)[:60]
        )
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except TargetClosedError:
        raise
    except Exception as exc:
        active_logger.warn(
            "智慧课 - Escape 关闭弹窗失败: %s" % str(exc)[:60]
        )
        return False
    return not await _dialog_visible(dialog)


async def handle_wisdom_dialog(
    page: Page,
    dialog,
    *,
    query_answer: Callable,
    logger_instance=None,
    randrange: Callable[[int], int] = random.randrange,
) -> bool:
    """处理一个全国智慧课随堂练习，返回弹窗是否已解除。"""
    active_logger = logger_instance or logger
    active_logger.info("智慧课 - 检测到AI随堂练习弹窗")
    if await _dismiss_wisdom_dialog(page, dialog, active_logger):
        return True

    title = await _extract_wisdom_question(dialog)
    option_texts = await _extract_wisdom_options(dialog)
    answer, is_ai = await _query_answer(
        query_answer,
        title,
        option_texts,
        active_logger,
    )
    options = dialog.locator(".option")
    option_count = await options.count()
    matched = _match_option(answer, option_texts) if answer else []
    if matched:
        source = "AI" if is_ai else "题库"
        active_logger.info(
            "智慧课 - [%s] 匹配到答案: %s" % (source, answer[:80])
        )
        indexes = matched
    else:
        is_multiple = await dialog.locator(
            'input[type="checkbox"], .el-checkbox'
        ).count() > 0
        indexes = _fallback_choice_indexes(
            option_count,
            is_multiple,
            randrange=randrange,
        )
        active_logger.warn(
            "智慧课 - 题库未命中，使用%s回退选项"
            % ("多选" if is_multiple else "单选")
        )
    if not await _click_locator_indexes(
        options,
        indexes,
        page,
        active_logger,
    ):
        return False

    try:
        submit_button = dialog.locator(
            "button:has-text('提交'), button:has-text('确定'), "
            ".dialog-footer button.btn, .dialog-footer button"
        ).first
        if await submit_button.count() > 0 and await submit_button.is_visible():
            await submit_button.click(timeout=2000)
            active_logger.info("智慧课 - 已点击提交作答")
            await page.wait_for_timeout(800)
    except TargetClosedError:
        raise
    except Exception as exc:
        active_logger.warn("智慧课 - 提交作答失败: %s" % str(exc)[:60])
        return False

    if not await _dialog_visible(dialog):
        return True
    return await _dismiss_wisdom_dialog(page, dialog, active_logger)


async def handle_standard_dialog(
    page: Page,
    question_container,
    *,
    query_answer: Callable,
    logger_instance=None,
    randrange: Callable[[int], int] = random.randrange,
) -> bool:
    """处理普通/融合课程的一组随堂题。"""
    active_logger = logger_instance or logger
    questions = await question_container.query_selector_all(".number")
    if not questions:
        return False
    active_logger.write_log(f"检测到{len(questions)}道题目.\n")
    all_handled = True
    for question in questions:
        await question.click(timeout=500)
        await page.wait_for_timeout(300)
        if await page.query_selector(".answer"):
            continue

        title = await _extract_question_title(page)
        option_texts = await _extract_options(page)
        answer, is_ai = await _query_answer(
            query_answer,
            title,
            option_texts,
            active_logger,
        )
        matched = _match_option(answer, option_texts) if answer else []
        choices = page.locator(".topic-item")
        if matched:
            source = "AI" if is_ai else "题库"
            active_logger.write_log(
                f"[{source}] 匹配到答案: {answer[:80]}\n"
            )
            indexes = matched
        else:
            is_multiple = await page.locator(
                '.topic-item input[type="checkbox"], .topic-item.el-checkbox'
            ).count() > 0
            indexes = _fallback_choice_indexes(
                await choices.count(),
                is_multiple,
                randrange=randrange,
            )
            active_logger.write_log(
                "未匹配到答案,使用%s默认策略.\n"
                % ("多选" if is_multiple else "单选")
            )
        handled = await _click_locator_indexes(
            choices,
            indexes,
            page,
            active_logger,
        )
        all_handled = handled and all_handled

    if not all_handled:
        active_logger.warn("随堂题存在未成功点击的选项，保留弹窗等待重试")
        return False
    try:
        await page.press(".el-dialog", "Escape", timeout=1000)
        await page.wait_for_timeout(300)
        is_visible = getattr(question_container, "is_visible", None)
        if callable(is_visible):
            dialog_remains = await is_visible()
        else:
            dialog_remains = bool(
                await page.query_selector(".el-dialog:visible .number")
            )
        if dialog_remains:
            active_logger.warn("随堂题弹窗关闭后仍可见，保留等待重试")
            return False
        return True
    except TargetClosedError:
        raise
    except Exception as exc:
        active_logger.warn("随堂题弹窗关闭失败: %s" % str(exc)[:60])
        return False


async def wait_for_question_resolution(
    page: Page,
    event: asyncio.Event,
    selectors: tuple[str, ...],
    *,
    timeout: float = 180,
    poll_interval: float = 0.5,
    logger_instance=None,
) -> bool:
    """Wait boundedly for a live question popup to be resolved."""
    active_logger = logger_instance or logger
    event.clear()

    async def question_remains() -> bool:
        for selector in selectors:
            if await page.query_selector(selector):
                return True
        return False

    await ensure_course_authenticated(
        page,
        "等待随堂题完成时登录状态失效",
    )
    if not await question_remains():
        return True

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    timeout = max(0.0, float(timeout))
    interval = max(0.05, float(poll_interval))
    while loop.time() - started_at < timeout:
        await ensure_course_authenticated(
            page,
            "等待随堂题完成时登录状态失效",
        )
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

        await ensure_course_authenticated(
            page,
            "等待随堂题完成时登录状态失效",
        )
        if not await question_remains():
            active_logger.info("随堂题弹窗已消失，继续课程播放")
            return True

    active_logger.warn(
        "随堂题在 %s 秒内未完成，停止当前视频以避免永久等待"
        % f"{timeout:g}"
    )
    return False


async def skip_questions(
    page: Page,
    event_loop: asyncio.Event,
    *,
    query_answer: Callable,
    logger_instance=None,
    poll_interval: float = 2,
    randrange: Callable[[int], int] = random.randrange,
) -> None:
    """长期监听随堂题；页面切到不支持的课程时暂停而不是永久退出。"""
    active_logger = logger_instance or logger
    await page.wait_for_load_state("domcontentloaded")
    consecutive_errors = 0
    warned_hike_url = None
    while True:
        try:
            profile = CourseProfile.from_url(page.url)
            if profile.kind is CourseKind.HIKE:
                if warned_hike_url != profile.url:
                    active_logger.warn(
                        "当前课程为新版本,随堂题监听暂停.",
                        shift=True,
                    )
                    warned_hike_url = profile.url
                if await page.query_selector(".question-info"):
                    event_loop.clear()
                else:
                    # HIKE 暂不自动作答，但用户手动关闭后仍需解除主流程等待。
                    event_loop.set()
                await asyncio.sleep(poll_interval)
                consecutive_errors = 0
                continue
            warned_hike_url = None

            if profile.kind is CourseKind.NATIONAL_WISDOM:
                await asyncio.sleep(poll_interval)
                dialog = page.locator(".ai-class-exercise-dialog")
                if not await _dialog_visible(dialog):
                    event_loop.set()
                    consecutive_errors = 0
                    continue
                event_loop.clear()
                if await handle_wisdom_dialog(
                    page,
                    dialog,
                    query_answer=query_answer,
                    logger_instance=active_logger,
                    randrange=randrange,
                ):
                    event_loop.set()
                consecutive_errors = 0
                continue

            await asyncio.sleep(poll_interval)
            try:
                container = await page.wait_for_selector(
                    ".el-scrollbar__view",
                    state="attached",
                    timeout=1000,
                )
            except PlaywrightTimeoutError:
                event_loop.set()
                consecutive_errors = 0
                continue
            event_loop.clear()
            if await handle_standard_dialog(
                page,
                container,
                query_answer=query_answer,
                logger_instance=active_logger,
                randrange=randrange,
            ):
                event_loop.set()
            consecutive_errors = 0
        except asyncio.CancelledError:
            raise
        except TargetClosedError:
            active_logger.write_log("浏览器已关闭,答题模块已下线.\n")
            return
        except Exception as exc:
            consecutive_errors += 1
            if consecutive_errors == 1 or consecutive_errors % 5 == 0:
                active_logger.warn(
                    "随堂题监听异常（连续%d次）: %s"
                    % (consecutive_errors, repr(exc)[:100])
                )
            await asyncio.sleep(min(5.0, 0.5 * consecutive_errors))
