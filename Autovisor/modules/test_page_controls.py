# encoding=utf-8
"""测验页面的用户操作等待、选项定位、翻页和交卷控件。"""

from __future__ import annotations

import asyncio
import json

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page

from modules.answer_strategy import build_answer_actions
from modules.logger import Logger


logger = Logger()

_QUESTION_NAVIGATION_MARKER_JS = """
() => {
    const normalize = value => String(value || '')
        .replace(/\\s+/g, ' ')
        .trim();
    const isVisible = element => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
            && style.visibility !== 'hidden'
            && rect.width > 0
            && rect.height > 0;
    };
    const selectors = [
        '.examPaper_subject',
        '.subject_node',
        '.examquestions',
        '.question-item',
        '.question-info',
        '.topic-title'
    ];
    const viewportHeight = window.innerHeight || 1;
    const candidates = [];
    for (const selector of selectors) {
        Array.from(document.querySelectorAll(selector)).forEach((element, index) => {
            if (!isVisible(element)) return;
            const rect = element.getBoundingClientRect();
            const overlap = Math.max(
                0,
                Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0)
            );
            const text = normalize(element.innerText || element.textContent);
            if (text) {
                candidates.push({
                    marker: `${selector}:${index}:${text.slice(0, 1000)}`,
                    overlap,
                    distance: Math.abs(
                        (rect.top + rect.bottom) / 2 - viewportHeight / 2
                    )
                });
            }
        });
    }
    candidates.sort(
        (left, right) =>
            right.overlap - left.overlap || left.distance - right.distance
    );
    const activeSelectors = [
        '.question-number.active',
        '.number.active',
        '.answer-card .active',
        '.answerCard .active',
        '.el-tabs__item.is-active',
        '[aria-current="step"]',
        '[aria-current="true"]'
    ];
    let active = '';
    for (const selector of activeSelectors) {
        const element = document.querySelector(selector);
        if (isVisible(element)) {
            active = `${selector}:${normalize(element.textContent)}`;
            break;
        }
    }
    return {
        href: window.location.href,
        question: candidates.length ? candidates[0].marker : '',
        active
    };
}
"""


async def has_selected_answer(page: Page) -> bool | None:
    """返回当前题作答状态；页面异常时返回 None，禁止按未作答继续推进。"""
    try:
        return bool(
            await page.evaluate(
                """
                (() => {
                    const visible = element => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const questionSelectors = [
                        '.examPaper_subject',
                        '.subject_node',
                        '.examquestions',
                        '.question-item'
                    ];
                    const questions = questionSelectors.flatMap(
                        selector => Array.from(document.querySelectorAll(selector))
                    );
                    const scope = questions.find(question =>
                        visible(question)
                        && question.querySelector(
                            '.nodeLab, input, textarea, [contenteditable="true"]'
                        )
                    ) || document;
                    const choiceSelected = Array.from(scope.querySelectorAll(
                        'input[type="radio"], input[type="checkbox"]'
                    )).some(input => input.checked);
                    if (choiceSelected) return true;
                    if (scope.querySelector(
                        '.nodeLab.flagChecked, .nodeLab.is-checked, '
                        + '.nodeLab[aria-checked="true"], '
                        + '.el-radio.is-checked, .el-checkbox.is-checked, '
                        + '[role="radio"][aria-checked="true"], '
                        + '[role="checkbox"][aria-checked="true"]'
                    )) return true;
                    const textInputs = scope.querySelectorAll(
                        'textarea, input[type="text"], '
                        + 'input:not([type]), [contenteditable="true"]'
                    );
                    return Array.from(textInputs).some(element => {
                        const value = element.isContentEditable
                            ? element.textContent
                            : element.value;
                        return visible(element) && Boolean(value && value.trim());
                    });
                })()
                """
            )
        )
    except TargetClosedError:
        return None
    except Exception:
        return None


async def wait_for_page_option_click(
    page: Page,
    timeout: float = 120,
    *,
    poll_interval: float = 0.2,
) -> bool:
    """等待用户在页面原生选项上点击。"""
    try:
        await page.evaluate(
            """
            window._optionClicked = false;
            document.querySelectorAll(
                '.nodeLab, .examquestions-answer, .flagChecked'
            ).forEach(el => {
                el.addEventListener('click', () => {
                    window._optionClicked = true;
                }, { once: false });
            });
            """
        )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        while loop.time() - started_at < timeout:
            if await page.evaluate("window._optionClicked"):
                await page.evaluate("window._optionClicked = false")
                await page.wait_for_timeout(300)
                return True
            await asyncio.sleep(poll_interval)
    except TargetClosedError:
        return False
    except Exception:
        return False
    return False


async def wait_for_user_action(
    page: Page,
    is_multiple: bool = False,
    timeout: float = 180,
    *,
    poll_interval: float = 0.2,
) -> str:
    """返回 next/prev/option_click/submit/timeout/closed/error。"""
    try:
        await page.evaluate(
            """
            window._optionClicked = false;
            window._widgetNextClicked = false;
            window._widgetPrevClicked = false;
            document.querySelectorAll(
                '.nodeLab, .examquestions-answer, .flagChecked'
            ).forEach(el => {
                el.addEventListener('click', () => {
                    window._optionClicked = true;
                }, { once: false });
            });
            """
        )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        while loop.time() - started_at < timeout:
            if await page.evaluate("window._widgetNextClicked"):
                await page.evaluate("window._widgetNextClicked = false")
                is_last = await page.evaluate(
                    """
                    () => {
                        const widget = document.getElementById('ai-answer-widget');
                        if (!widget) return false;
                        const button = widget.querySelector('.nav-btn.next');
                        return Boolean(button && button.textContent.includes('提交'));
                    }
                    """
                )
                return "submit" if is_last else "next"
            if await page.evaluate("window._widgetPrevClicked"):
                await page.evaluate("window._widgetPrevClicked = false")
                return "prev"
            if not is_multiple and await page.evaluate("window._optionClicked"):
                await page.evaluate("window._optionClicked = false")
                await page.wait_for_timeout(300)
                return "option_click"
            await asyncio.sleep(poll_interval)
    except TargetClosedError:
        return "closed"
    except Exception:
        return "error"
    return "timeout"


async def click_prev_button(page: Page, *, logger_instance=None) -> bool:
    """点击上一题，并以页面题目变化作为成功依据。"""
    return await _click_question_navigation(
        page,
        (
            "text='上一题'",
            "text=上一题",
            ".prev-btn",
            "button:has-text('上一题')",
            ".exam-btn-prev",
        ),
        key="ArrowLeft",
        label="上一题",
        logger_instance=logger_instance,
    )


async def wait_for_widget_next_button(
    page: Page,
    timeout: float = 180,
    *,
    poll_interval: float = 0.3,
) -> bool:
    try:
        await page.evaluate("window._widgetNextClicked = false")
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        while loop.time() - started_at < timeout:
            if await page.evaluate("window._widgetNextClicked"):
                await page.evaluate("window._widgetNextClicked = false")
                return True
            await asyncio.sleep(poll_interval)
    except Exception:
        return False
    return False


async def wait_for_widget_submit(page: Page, timeout: float = 60) -> bool:
    """兼容第三方扩展可能派发的 answerSubmitted 事件。"""
    try:
        return bool(
            await page.evaluate(
                f"""
                new Promise((resolve) => {{
                    const widget = document.getElementById('ai-answer-widget');
                    if (!widget) return resolve(false);
                    widget.addEventListener(
                        'answerSubmitted',
                        () => resolve(true),
                        {{ once: true }}
                    );
                    setTimeout(() => resolve(false), {max(0, timeout) * 1000});
                }})
                """
            )
        )
    except Exception:
        return False


async def _force_click_locator(locator):
    """依次尝试普通点击、强制点击与 DOM 事件派发。"""
    try:
        await locator.click(timeout=3000)
        return "click"
    except Exception:
        pass
    try:
        await locator.click(force=True, timeout=3000)
        return "click-force"
    except Exception:
        pass
    try:
        await locator.dispatch_event("click")
        return "dispatch-event-click"
    except Exception:
        pass
    try:
        await locator.dispatch_event("mousedown")
        await locator.dispatch_event("mouseup")
        await locator.dispatch_event("click")
        return "dispatch-event-chain"
    except Exception:
        return None


async def click_option_by_index(
    page: Page,
    index: int,
    option_value: str | None = None,
    *,
    logger_instance=None,
) -> bool:
    """通过选项索引点击，使用多层 Locator 回退。"""
    active_logger = logger_instance or logger
    if index < 0:
        active_logger.warn("[FAIL] 选项索引不能为负数")
        return False
    try:
        if option_value is not None:
            value_literal = json.dumps(str(option_value), ensure_ascii=False)
            selector_info = await page.evaluate(
                f"""
                (() => {{
                    const value = {value_literal};
                    const input = Array.from(
                        document.querySelectorAll('input')
                    ).find(element => element.value === value);
                    if (!input) return null;
                    const node = input.closest('.nodeLab');
                    if (!node) return null;
                    const nodes = Array.from(document.querySelectorAll('.nodeLab'));
                    return {{
                        what: node.querySelector('.label') ? 'label' : 'nodeLab',
                        idx: nodes.indexOf(node)
                    }};
                }})()
                """
            )
            if selector_info and selector_info.get("idx", -1) >= 0:
                target = page.locator(".nodeLab").nth(selector_info["idx"])
                if selector_info.get("what") == "label":
                    target = target.locator(".label")
                strategy = await _force_click_locator(target)
                if strategy:
                    active_logger.info(
                        "[OK] 通过value点击: value=%s (%s)"
                        % (option_value, strategy)
                    )
                    await page.wait_for_timeout(200)
                    return True

            inputs = page.locator('input[type="radio"], input[type="checkbox"]')
            for input_index in range(await inputs.count()):
                candidate = inputs.nth(input_index)
                if await candidate.get_attribute("value") == str(option_value):
                    strategy = await _force_click_locator(candidate)
                    if strategy:
                        active_logger.info(
                            "[OK] 通过value点input: value=%s (%s)"
                            % (option_value, strategy)
                        )
                        await page.wait_for_timeout(200)
                        return True

        index_info = await page.evaluate(
            f"""
            (() => {{
                const subjects = document.querySelectorAll('.examPaper_subject');
                for (const subject of subjects) {{
                    const parent = subject.parentElement;
                    if (parent && parent.style.display === 'none') continue;
                    const rect = subject.getBoundingClientRect();
                    if (rect.height <= 0 || rect.width <= 0) continue;
                    const localNodes = subject.querySelectorAll('.nodeLab');
                    if (!localNodes[{index}]) continue;
                    const allNodes = Array.from(document.querySelectorAll('.nodeLab'));
                    return {{ idx: allNodes.indexOf(localNodes[{index}]) }};
                }}
                const allNodes = Array.from(document.querySelectorAll('.nodeLab'));
                const visibleNodes = allNodes.filter(node => node.offsetParent !== null);
                const target = visibleNodes[{index}] || allNodes[{index}];
                return target ? {{ idx: allNodes.indexOf(target) }} : null;
            }})()
            """
        )
        if index_info and index_info.get("idx", -1) >= 0:
            node = page.locator(".nodeLab").nth(index_info["idx"])
            for target, name in ((node.locator(".label"), "label"), (node, "nodeLab")):
                strategy = await _force_click_locator(target)
                if strategy:
                    active_logger.info(
                        "[OK] 通过索引点击%s: 选项%d (%s)"
                        % (name, index + 1, strategy)
                    )
                    await page.wait_for_timeout(200)
                    return True

        for selector in (
            ".topic-item",
            ".option-item",
            ".el-radio",
            ".el-checkbox",
            'input[type="radio"]',
            'input[type="checkbox"]',
        ):
            candidates = page.locator(selector)
            count = await candidates.count()
            if index < count:
                strategy = await _force_click_locator(candidates.nth(index))
                if strategy:
                    active_logger.info(
                        "[OK] 通过%s点击: 选项%d (%s)"
                        % (selector, index + 1, strategy)
                    )
                    return True
    except TargetClosedError:
        active_logger.warn("[FAIL] 页面已关闭，无法点击选项")
        return False
    except Exception as exc:
        active_logger.warn("[FAIL] 点击异常: %s" % str(exc)[:80])
        return False

    active_logger.warn("[FAIL] 所有方案失败: index=%d" % index)
    return False


async def click_option_by_text(
    page: Page,
    text: str,
    *,
    logger_instance=None,
) -> bool:
    """安全嵌入文本并点击匹配的选项。"""
    active_logger = logger_instance or logger
    text = str(text)
    text_literal = json.dumps(text, ensure_ascii=False)
    try:
        index_info = await page.evaluate(
            f"""
            (() => {{
                const expected = {text_literal};
                const nodes = Array.from(document.querySelectorAll('.nodeLab'));
                const allowed = node => {{
                    const subject = node.closest('.examPaper_subject');
                    const parent = subject && subject.parentElement;
                    return !(parent && parent.style.display === 'none');
                }};
                const visible = nodes.findIndex(
                    node => allowed(node)
                        && node.offsetParent !== null
                        && node.textContent.includes(expected)
                );
                if (visible >= 0) return {{ idx: visible }};
                const hidden = nodes.findIndex(
                    node => allowed(node) && node.textContent.includes(expected)
                );
                return hidden >= 0 ? {{ idx: hidden, hidden: true }} : null;
            }})()
            """
        )
        if index_info and index_info.get("idx", -1) >= 0:
            node = page.locator(".nodeLab").nth(index_info["idx"])
            for target, name in ((node.locator(".label"), "label"), (node, "nodeLab")):
                strategy = await _force_click_locator(target)
                if strategy:
                    active_logger.info(
                        "[OK] 通过文本点击%s: text=%s (%s)"
                        % (name, text, strategy)
                    )
                    await page.wait_for_timeout(200)
                    return True

        for selector in (".topic-item", ".option-item"):
            candidates = page.locator(selector)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                candidate_text = await candidate.text_content()
                if candidate_text and text in candidate_text:
                    strategy = await _force_click_locator(candidate)
                    if strategy:
                        active_logger.info(
                            "[OK] 通过文本点击%s: text=%s (%s)"
                            % (selector, text, strategy)
                        )
                        return True
    except TargetClosedError:
        active_logger.warn("[FAIL] 页面已关闭，无法按文本点击选项")
        return False
    except Exception as exc:
        active_logger.warn("[FAIL] 通过文本点击异常: %s" % str(exc)[:80])
        return False

    active_logger.warn("[FAIL] 通过文本所有方法失败: text=%s" % text)
    return False


async def answer_question(
    page: Page,
    answer: str,
    options: list | None = None,
    raw_options: list | None = None,
    *,
    logger_instance=None,
) -> bool:
    """把答案策略逐项应用到页面，并汇总真实点击结果。"""
    active_logger = logger_instance or logger
    actions = build_answer_actions(answer, options, raw_options)
    if not actions:
        active_logger.info("[ANS] 答题: 答案为空，跳过")
        return False

    active_logger.info("[ANS] 答题: %s" % str(answer).strip())
    if options:
        active_logger.info("[ANS] 选项列表: %s" % str(options[:5]))

    all_succeeded = True
    for action_index, action in enumerate(actions):
        if action.kind == "index":
            active_logger.info("[ANS] 按索引点击选项: %s" % action.value)
            succeeded = await click_option_by_index(
                page,
                int(action.value),
                action.option_value,
                logger_instance=active_logger,
            )
        else:
            active_logger.info("[ANS] 按文本点击选项: %s" % action.value)
            succeeded = await click_option_by_text(
                page,
                str(action.value),
                logger_instance=active_logger,
            )
        all_succeeded = bool(succeeded) and all_succeeded
        if action_index < len(actions) - 1:
            await page.wait_for_timeout(300)
    return all_succeeded


async def click_next_button(page: Page, *, logger_instance=None) -> bool:
    """点击下一题，并以页面题目变化作为成功依据。"""
    return await _click_question_navigation(
        page,
        (
            "button:has-text('下一题')",
            ".switch-btn-box button:last-child",
            ".next-btn, [class*='next']",
        ),
        key="ArrowRight",
        label="下一题",
        logger_instance=logger_instance,
    )


async def _read_question_navigation_marker(page: Page) -> dict[str, str]:
    marker = await page.evaluate(_QUESTION_NAVIGATION_MARKER_JS)
    if not isinstance(marker, dict):
        raise RuntimeError("题目导航标记格式无效")
    return {
        "href": str(marker.get("href") or ""),
        "question": str(marker.get("question") or ""),
        "active": str(marker.get("active") or ""),
    }


def _question_navigation_changed(
    before: dict[str, str],
    after: dict[str, str],
) -> bool:
    return any(
        before[field] and after[field] and before[field] != after[field]
        for field in ("href", "question", "active")
    )


async def _wait_for_question_navigation(
    page: Page,
    before: dict[str, str],
    *,
    attempts: int = 8,
    poll_interval_ms: int = 250,
) -> bool:
    for _attempt in range(max(1, attempts)):
        await page.wait_for_timeout(poll_interval_ms)
        after = await _read_question_navigation_marker(page)
        if _question_navigation_changed(before, after):
            return True
    return False


async def _click_question_navigation(
    page: Page,
    selectors: tuple[str, ...],
    *,
    key: str,
    label: str,
    logger_instance=None,
) -> bool:
    """执行一次导航动作；动作成功后不再重复点击，只等待页面证据。"""
    active_logger = logger_instance or logger
    try:
        before = await _read_question_navigation_marker(page)
    except TargetClosedError:
        active_logger.warn("[FAIL] 页面已关闭，无法点击%s" % label)
        return False
    except Exception as exc:
        active_logger.warn(
            "[FAIL] 无法读取%s前的题目状态: %s"
            % (label, str(exc)[:60])
        )
        return False

    last_error = None
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if await button.count() > 0:
                await button.click(timeout=3000)
                try:
                    changed = await _wait_for_question_navigation(page, before)
                except TargetClosedError:
                    active_logger.warn(
                        "[FAIL] 点击%s后页面关闭，无法确认题目变化" % label
                    )
                    return False
                except Exception as exc:
                    active_logger.warn(
                        "[FAIL] 点击%s后无法验证题目变化: %s"
                        % (label, str(exc)[:60])
                    )
                    return False
                if changed:
                    active_logger.info("[OK] 点击%s，题目已切换" % label)
                    return True
                active_logger.warn(
                    "[FAIL] 已点击%s，但题目未发生变化" % label
                )
                return False
        except TargetClosedError:
            active_logger.warn("[FAIL] 页面已关闭，无法点击%s" % label)
            return False
        except Exception as exc:
            last_error = exc
            continue

    try:
        await page.keyboard.press(key)
        changed = await _wait_for_question_navigation(page, before)
        if changed:
            active_logger.info("[OK] 使用键盘%s，题目已切换" % label)
            return True
        active_logger.warn("[FAIL] 使用键盘%s后题目未发生变化" % label)
        return False
    except TargetClosedError:
        active_logger.warn("[FAIL] 页面已关闭，无法点击%s" % label)
        return False
    except Exception as exc:
        reason = exc if exc else last_error
        active_logger.warn(
            "[FAIL] 点击%s失败: %s" % (label, str(reason)[:60])
        )
        return False


async def _submission_completed(page: Page, original_url: str) -> bool:
    """只接受页面关闭、地址变化或明确结果元素，不使用按钮消失作证。"""
    if page.is_closed():
        return True
    if original_url and page.url != original_url:
        return True
    for selector in (
        ".exam-result, .result-page, .score-page, .answer-result, "
        ".success-page, .el-message--success, .el-notification--success, "
        "[data-status='submitted']",
        "text='提交成功'",
        "text=考试成绩",
        "text=作业成绩",
    ):
        result = page.locator(selector).first
        if await result.count() > 0 and await result.is_visible():
            return True
    return False


async def _wait_for_submission_completion(
    page: Page,
    original_url: str,
    *,
    attempts: int = 30,
    poll_interval_ms: int = 500,
) -> bool:
    """Poll bounded page evidence so slow submissions are not retried early."""
    for attempt in range(max(1, attempts)):
        if await _submission_completed(page, original_url):
            return True
        if attempt < attempts - 1:
            await page.wait_for_timeout(poll_interval_ms)
    return False


async def submit_exam(page: Page, *, logger_instance=None) -> bool:
    """提交作业，并以页面结果而不是单次点击作为成功依据。"""
    active_logger = logger_instance or logger
    submit_clicked = False
    try:
        original_url = page.url
        submit_button = page.locator(
            "button:has-text('提交作业'), .btnStyleXSumit"
        ).first
        if await submit_button.count() == 0:
            active_logger.warn("[FAIL] 未找到提交作业按钮")
            return False
        await submit_button.click(timeout=5000)
        submit_clicked = True
        active_logger.info("[OK] 点击提交作业按钮")
        await page.wait_for_timeout(1000)

        confirmed = False
        for selector in (
            "button.el-button--primary:has-text('确定')",
            ".el-message-box__btns button.el-button--primary",
            ".el-message-box__confirm",
            ".confirm-btn",
        ):
            try:
                confirm_button = page.locator(selector).first
                if await confirm_button.count() > 0 and await confirm_button.is_visible():
                    await confirm_button.click(timeout=3000)
                    active_logger.info("[OK] 点击确认按钮")
                    confirmed = True
                    break
            except Exception:
                continue
        if not confirmed:
            active_logger.warn("[WARN] 未找到确认按钮，等待页面自行提交")

        if not await _wait_for_submission_completion(
            page,
            original_url,
        ):
            active_logger.warn("[FAIL] 未检测到交卷完成状态")
            return False
        active_logger.info("[OK] 作业提交完成")

        for selector in (
            ".close-btn",
            "button:has-text('关闭')",
            ".el-dialog__close",
            ".exam-close-btn",
        ):
            try:
                close_button = page.locator(selector).first
                if await close_button.count() > 0 and await close_button.is_visible():
                    await close_button.click(timeout=2000)
                    active_logger.info("[OK] 关闭测验窗口")
                    break
            except Exception:
                continue
        return True
    except TargetClosedError:
        # 提交点击后页面主动关闭可视为流程完成；点击前关闭则是失败。
        return submit_clicked
    except Exception as exc:
        active_logger.warn("[FAIL] 提交失败：%s" % str(exc)[:50])
        return False
