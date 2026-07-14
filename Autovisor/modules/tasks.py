import asyncio
import json
import os
import random
import re
import http.client  # 兼容外部通过 tasks.http.client 注入连接实现

from playwright.async_api import Page
from pygetwindow import Win32Window
from modules.course_types import CourseKind, CourseProfile
from modules.utils import display_window, hide_window
from playwright._impl._errors import TargetClosedError
from modules.logger import Logger
from modules.floating_widget import inject_widget
from modules.answer_strategy import build_answer_actions
from modules.chapter_learning import (
    _extract_test_questions_from_dom,
    chapter_learning_flow,
    get_chapter_videos_status,
    wait_for_video_completion,
)
from modules import question_bank_client as _question_bank_client
from modules.question_bank_client import (
    _build_model_query_prompt,
    _detect_question_kind,
    _extract_answer_from_json,
    _extract_last_balanced_json,
    _is_model_error,
    _match_option,
    _normalize_qb,
)
from modules.test_capture import TestResponseHandler
from modules.video_tasks import activate_window, play_video, task_monitor, video_optimize

logger = Logger()
QB_URL = os.environ.get("QB_URL", "http://127.0.0.1:8083/query")
QB_TIMEOUT = 15


def _question_bank_endpoint():
    return _question_bank_client._question_bank_endpoint(QB_URL)


def query_question_bank(title, options_text=None, query_type=None):
    """兼容旧入口，并把运行时覆盖的题库地址传给独立客户端。"""
    return _question_bank_client.query_question_bank(
        title,
        options_text,
        query_type,
        qb_url=QB_URL,
        timeout=QB_TIMEOUT,
        logger_instance=logger,
    )


def clean_html_tags(text):
    """清理HTML标签和特殊字符
    
    将HTML内容转换为纯文本显示
    """
    if not text:
        return ""
    
    # 去除HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    
    # 替换HTML实体
    html_entities = {
        '&nbsp;': ' ',
        '&amp;': '&',
        '&lt;': '<',
        '&gt;': '>',
        '&quot;': '"',
        '&apos;': "'",
        '&#39;': "'",
    }
    
    for entity, char in html_entities.items():
        text = text.replace(entity, char)
    
    # 处理括号内的空格占位符：(&nbsp; &nbsp;) -> （）
    text = re.sub(r'\(\s+\)', '（）', text)
    text = re.sub(r'\(\s*\)', '（）', text)
    
    # 去除多余空白
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

async def smart_click_text(page, text: str) -> bool:
    selectors = ['div', 'span', 'p', 'label', 'a', 'li', 'button', '[role="option"]']

    # 方法1：JS 直接精确匹配 textContent
    try:
        clicked = await page.evaluate(f'''
            (function() {{
                const selectors = {selectors};
                for (const sel of selectors) {{
                    for (const el of document.querySelectorAll(sel)) {{
                        if (el.textContent.trim() === '{text}') {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})()
        ''')
        if clicked:
            logger.info(f"JS方式点击成功: {text}")
            return True
    except Exception as e:
        logger.debug(f"JS方式失败: {e}")

    # 方法2：XPath 精确匹配直接文本节点
    try:
        await page.click(f'xpath=//div[text()="{text}"]', timeout=2000)
        logger.info(f"XPath直接文本节点点击成功: {text}")
        return True
    except:
        pass

    # 方法3：XPath 包含文本（匹配子元素）
    try:
        await page.click(f'xpath=//div[contains(text(),"{text}")]', timeout=2000)
        logger.info(f"XPath包含文本点击成功: {text}")
        return True
    except:
        pass

    # 方法4：get_by_text + force
    try:
        await page.get_by_text(text, exact=True).click(force=True, timeout=2000)
        logger.info(f"force点击成功: {text}")
        return True
    except:
        pass

    logger.warn("[FAIL] 点击失败: %s" % text)
    return False

STATUS_PROBE_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const visibleText = [];
    for (const el of Array.from(document.querySelectorAll('body *'))) {
        if (visibleText.length >= 5) break;
        const text = normalize(el.textContent || '');
        if (!text) continue;
        if (!(text.includes('学习进度') || text.includes('掌握度') || text.includes('%'))) continue;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (rect.width < 40 || rect.height < 16) continue;
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        if (!visibleText.includes(text)) {
            visibleText.push(text.slice(0, 80));
        }
    }

    const titleSelectors = ['#lessonOrder', '.current_play [title]', '.current_play', 'h1', 'h2', '[title]'];
    let lessonTitle = '';
    for (const selector of titleSelectors) {
        const el = document.querySelector(selector);
        if (!el) continue;
        lessonTitle = normalize(el.getAttribute && el.getAttribute('title') ? el.getAttribute('title') : el.textContent);
        if (lessonTitle) break;
    }

    const video = document.querySelector('video');
    const currentTime = video ? Number(video.currentTime || 0) : null;
    const duration = video ? Number(video.duration || 0) : null;
    const percent = video && duration > 0 ? Math.floor((currentTime / duration) * 100) : null;
    return {
        pageTitle: normalize(document.title || ''),
        lessonTitle,
        hasVideo: !!video,
        paused: video ? !!video.paused : null,
        ended: video ? !!video.ended : null,
        currentTime,
        duration,
        percent,
        text: visibleText,
        url: location.href,
    };
}
"""

async def trigger_restart(page: Page, worker_name: str, restart_event: asyncio.Event | None = None) -> None:
    if restart_event is not None and restart_event.is_set():
        return
    if restart_event is not None:
        restart_event.set()
    logger.warn(f"[{worker_name}] 触发强制重建，立即关闭当前上下文.", shift=True)
    try:
        await page.context.close()
    except Exception:
        pass

async def status_ocr_stream(
    page: Page,
    worker_name: str,
    interval_sec: int = 5,
    restart_event: asyncio.Event | None = None,
) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(interval_sec)
            data = await page.evaluate(STATUS_PROBE_JS)
            if data["hasVideo"]:
                cur = 0 if data["currentTime"] is None else data["currentTime"]
                dur = 0 if data["duration"] is None else data["duration"]
                logger.info(
                    f"[{worker_name}/OCR] {data['lessonTitle'] or data['pageTitle']} | "
                    f"{data['percent'] if data['percent'] is not None else 0}% | "
                    f"paused={data['paused']} | ended={data['ended']} | {cur:.0f}/{dur:.0f}s"
                )
                if data["ended"]:
                    logger.warn(f"[{worker_name}/OCR] 检测到 ended=True，等待 worker 重建.", shift=True)
                    await trigger_restart(page, worker_name, restart_event)
            elif data["text"]:
                logger.info(f"[{worker_name}/OCR] {' | '.join(data['text'][:3])}")
        except TargetClosedError:
            logger.write_log(f"{worker_name} status stream offline.\n")
            return
        except Exception:
            continue

# 题库查询实现已拆分至 modules.question_bank_client；本模块顶部保留兼容入口。




# 视频后台任务已拆分至 modules.video_tasks，并在本模块顶部兼容导出。




async def _extract_question_title(page: Page) -> str:
    """从页面提取当前题目文本"""
    try:
        title_el = await page.query_selector(".topic-title")
        if title_el:
            text = await title_el.text_content()
            return text.strip() if text else ""
    except Exception:
        pass
    # 备选: 尝试 .subject-title / .question-title
    for sel in (".subject-title", ".question-title", ".el-dialog__body"):
        try:
            el = await page.query_selector(sel)
            if el:
                text = await el.text_content()
                return text.strip()[:500] if text else ""
        except Exception:
            continue
    return ""


async def _extract_options(page: Page):
    """从页面提取选项文本列表"""
    options = []
    try:
        items = await page.query_selector_all(".topic-item")
        for item in items:
            text = await item.text_content()
            if text:
                options.append(text.strip())
    except Exception:
        pass
    return options


async def _extract_wisdom_question(page: Page):
    """提取智慧课题目信息"""
    try:
        question_info = page.locator(".question-info")
        if await question_info.count() > 0:
            text = await question_info.first.text_content()
            return text.strip() if text else ""
    except Exception:
        pass
    return ""

async def _extract_wisdom_options(page: Page):
    """提取智慧课选项"""
    options = []
    try:
        items = page.locator(".option")
        count = await items.count()
        for i in range(count):
            item = items.nth(i)
            letter_el = item.locator(".class-question-select")
            answer_el = item.locator(".answer")
            if await letter_el.count() > 0 and await answer_el.count() > 0:
                letter = await letter_el.text_content()
                answer = await answer_el.text_content()
                options.append(f"{letter}. {answer}")
    except Exception:
        pass
    return options

async def skip_questions(page: Page, event_loop) -> None:
    await page.wait_for_load_state("domcontentloaded")
    last_question_hash = 0
    while True:
        try:
            profile = CourseProfile.from_url(page.url)
            if profile.kind is CourseKind.NATIONAL_WISDOM:
                await asyncio.sleep(2)
                try:
                    ai_dialog = page.locator(".ai-class-exercise-dialog")
                    if await ai_dialog.count() > 0 and await ai_dialog.is_visible():
                        logger.info("智慧课 - 检测到AI随堂练习弹窗")

                        try:
                            close_btn = ai_dialog.locator(".header-icon").first
                            if await close_btn.count() > 0 and await close_btn.is_visible():
                                await close_btn.click(timeout=1000)
                                await page.wait_for_timeout(800)
                                if await ai_dialog.count() == 0 or not await ai_dialog.is_visible():
                                    logger.info("智慧课 - 直接关闭弹窗成功")
                                    event_loop.set()
                                    continue
                        except Exception:
                            pass

                        logger.info("智慧课 - 弹窗无法直接关闭，随机作答")
                        try:
                            options = ai_dialog.locator(".option")
                            opt_count = await options.count()
                            if opt_count > 0:
                                rand_idx = random.randint(0, opt_count - 1)
                                await options.nth(rand_idx).click(timeout=1000)
                                logger.info(f"智慧课 - 随机选择第 {rand_idx + 1} 个选项")
                                await page.wait_for_timeout(500)
                        except Exception as e:
                            logger.warn(f"智慧课 - 选择选项失败: {repr(e)[:60]}")

                        try:
                            submit_btn = ai_dialog.locator("button.btn, .dialog-footer button").first
                            if await submit_btn.count() > 0 and await submit_btn.is_visible():
                                await submit_btn.click(timeout=2000)
                                logger.info("智慧课 - 已点击提交作答")
                                await page.wait_for_timeout(1000)
                        except Exception as e:
                            logger.warn(f"智慧课 - 提交作答失败: {repr(e)[:60]}")

                        try:
                            await page.wait_for_timeout(500)
                            if await ai_dialog.count() > 0 and await ai_dialog.is_visible():
                                close_btn = ai_dialog.locator(".header-icon").first
                                if await close_btn.count() > 0:
                                    await close_btn.click(timeout=1000)
                                    logger.info("智慧课 - 提交后关闭弹窗")
                                else:
                                    await page.keyboard.press("Escape")
                                    logger.info("智慧课 - ESC关闭弹窗")
                        except Exception:
                            await page.keyboard.press("Escape")

                        event_loop.set()
                except TargetClosedError:
                    logger.write_log("浏览器已关闭,答题模块已下线.\n")
                    return
                except Exception:
                    pass
                continue
            
            if profile.kind is CourseKind.HIKE:
                logger.warn("当前课程为新版本,不支持自动答题.", shift=True)
                return
            await asyncio.sleep(2)
            ques_element = await page.wait_for_selector(".el-scrollbar__view", state="attached", timeout=1000)
            total_ques = await ques_element.query_selector_all(".number")
            if total_ques:
                logger.write_log(f"检测到{len(total_ques)}道题目.\n")

            for ques in total_ques:
                await ques.click(timeout=500)
                await page.wait_for_timeout(300)

                if await page.query_selector(".answer"):
                    continue  # 已作答

                # 提取题目和选项
                title = await _extract_question_title(page)
                option_texts = await _extract_options(page)

                if title and option_texts:
                    options_joined = "\n".join(option_texts)
                    answer, is_ai = query_question_bank(title, options_joined)

                    if answer:
                        source = "AI" if is_ai else "题库"
                        logger.write_log(f"[{source}] 匹配到答案: {answer[:80]}\n")
                        matched = _match_option(answer, option_texts)

                        if matched:
                            choices = await page.query_selector_all(".topic-item")
                            for idx in matched:
                                if idx < len(choices):
                                    await choices[idx].click(timeout=500)
                                    await page.wait_for_timeout(100)
                            continue

                # 题库未命中或无答案，回退到盲点前2个
                logger.write_log("未匹配到答案,使用默认策略.\n")
                choices = await page.query_selector_all(".topic-item")
                for each in choices[:2]:
                    await each.click(timeout=500)
                    await page.wait_for_timeout(100)

            await page.press(".el-dialog", "Escape", timeout=1000)
            event_loop.set()
        except TargetClosedError:
            logger.write_log("浏览器已关闭,答题模块已下线.\n")
            return
        except Exception as e:
            profile = CourseProfile.from_url(page.url)
            if profile.kind is CourseKind.FUSION:
                not_finish_close = await page.query_selector(".el-dialog")
                if not_finish_close:
                    await page.press(".el-dialog", "Escape", timeout=1000)
            elif profile.kind is CourseKind.HIKE:
                logger.warn("当前课程为新版本,不支持自动答题.", shift=True)
                return
            else:
                not_finish_close = await page.query_selector(".el-message-box__headerbtn")
                if not_finish_close:
                    await not_finish_close.click()
            continue


async def wait_for_verify(page: Page, config, event_loop) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(3)
            await page.wait_for_selector(".yidun_modal__title", state="attached", timeout=1000)
            logger.warn("检测到安全验证,请手动完成验证...", shift=True)
            if config.enableHideWindow:
                await display_window(page)
            await page.wait_for_selector(".yidun_modal__title", state="hidden", timeout=24 * 3600 * 1000)
            event_loop.set()
            if config.enableHideWindow:
                await hide_window(page)
            logger.info("安全验证已完成.", shift=True)
            await asyncio.sleep(30)  # 较长时间内不会再次触发验证
        except TargetClosedError:
            logger.write_log("浏览器已关闭,安全验证模块已下线.\n")
            return
        except Exception as e:
            continue


# 测验响应监听器已拆分至 modules.test_capture，并在本模块顶部兼容导出。


async def handle_test_page(page: Page, questions_data: list, auto_submit: bool = False, manual_submit: bool = False) -> bool:
    """
    处理答题页面
    questions_data: 从 doHomework 获取的题目数据
    auto_submit: 是否自动提交（默认False，用户手动点击提交）
    
    注意：调用前 practice_loop 已确保试卷 DOM 已渲染
    """
    await page.wait_for_load_state("domcontentloaded")
    
    if not questions_data:
        logger.warn("[WARN] 没有题目数据")
        return False
    
    try:
        await inject_widget(page)
        logger.info("[OK] 浮动答题助手已注入")
    except Exception as e:
        logger.warn("[WARN] 浮动组件注入失败: %s" % str(e)[:50])
    
    option_selectors = [
        '.nodeLab',
        '.topic-item',
        '.option-item',
        '.el-radio',
        '.el-checkbox',
        '.exam-option',
        '[class*="option"]',
        '[class*="topic"]',
    ]
    max_retries = 3
    option_sel = None
    for retry in range(max_retries):
        for sel in option_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000, state='visible')
                option_sel = sel
                logger.info(f"[OK] 选项元素已加载: {sel}")
                break
            except Exception:
                continue
        if option_sel:
            break
        if retry < max_retries - 1:
            logger.warn(f"[RETRY] 等待选项加载失败，重试 {retry+1}/{max_retries}")
            await page.wait_for_timeout(3000)
        else:
            page_html = await page.content()
            logger.warn(f"页面HTML前500字符: {page_html[:500]}")
            logger.error(f"[ERROR] 无法加载选项元素")
            return False
    
    total = len(questions_data)
    logger.info("="*60)
    logger.info("[START] 开始答题，共 %d 道题" % total)
    logger.info("="*60)
    
    type_map = {"单选题": "single", "多选题": "multiple", "判断题": "judgement",
                "填空题": "completion", "简答题": "completion", "名词解释": "completion"}
    
    # 【阶段1】批量查询答案
    logger.info("\n[STEP1] 批量查询答案")
    for i, q in enumerate(questions_data):
        q_name = clean_html_tags(q["name"])
        q_type = q["type"]
        options = [clean_html_tags(o[1]) for o in q["options"]]
        options_joined = "\n".join(options)

        qtype_hint = type_map.get(q_type, None) or ("single" if q.get("type_id") == 1 else
                    "multiple" if q.get("type_id") == 2 else "judgement" if q.get("type_id") == 14 else None)

        answer, is_ai = query_question_bank(q_name, options_joined, qtype_hint)
        
        if answer:
            source = "AI" if is_ai else "题库"
            logger.info("\n[%s] 第%d题 [%s]" % (source, i+1, q_type))
            logger.info("题目: %s..." % q_name[:80])
            logger.info("答案: %s" % answer[:60])
            q["answer"] = answer
            q["is_ai"] = is_ai
        else:
            logger.info("\n[NO MATCH] 第%d题 [%s]" % (i+1, q_type))
            logger.info("题目: %s..." % q_name[:80])
            q["answer"] = None
            q["is_ai"] = False
    
    # 【阶段2】逐题答题
    logger.info("\n" + "="*60)
    logger.info("[STEP2] 逐题答题")
    logger.info("="*60)
    
    current_index = 0
    
    while current_index < total:
        i = current_index
        q = questions_data[i]
        q_name = clean_html_tags(q["name"])
        q_type = q["type"]
        answer = q.get("answer", "")
        raw_options = q["options"]
        options = [clean_html_tags(o[1]) for o in raw_options]
        is_ai = q.get("is_ai", False)
        
        logger.info("\n--- 第%d/%d题 [%s] ---" % (i+1, total, q_type))
        logger.info("题目: %s..." % q_name[:60])
        
        # 更新浮动组件显示
        try:
            widget_data = {
                "question": q_name,
                "answer": answer or "未找到答案",
                "options": options,
                "type": q_type,
                "index": i,
                "total": total,
                "isAI": is_ai
            }
            await page.evaluate(f'''
                if (window.AIAnswerWidget) {{
                    AIAnswerWidget.updateQuestion({json.dumps(widget_data, ensure_ascii=False)});
                }}
            ''')
        except Exception as e:
            pass
        
        if not auto_submit:
            # 手动模式：等待用户操作
            logger.info("[手动模式] 请在页面上选择答案")
            
            # 检测是否为多选题
            is_multiple = '多选' in q_type
            
            # 同时监听页面点击和浮动窗口按钮
            action = await wait_for_user_action(page, is_multiple, timeout=180)
            
            if action == "next":
                logger.info("[手动模式] 用户点击下一题")
                # 点击页面上的下一题按钮
                if i < total - 1:
                    await click_next_button(page)
                    await page.wait_for_timeout(300)
                current_index += 1
            elif action == "prev":
                logger.info("[手动模式] 用户点击上一题")
                # 点击页面上的上一题按钮（如果有）
                if i > 0:
                    await click_prev_button(page)
                    await page.wait_for_timeout(300)
                    current_index -= 1
            elif action == "option_click":
                logger.info("[手动模式] 用户选择了选项")
                # 单选题：选择后自动下一题
                if not is_multiple:
                    await page.wait_for_timeout(500)
                    if i < total - 1:
                        await click_next_button(page)
                        await page.wait_for_timeout(300)
                    current_index += 1
                else:
                    # 多选题：继续等待
                    pass
            elif action == "submit":
                logger.info("[手动模式] 用户点击提交试卷")
                break
            else:
                # 超时
                logger.warn("[手动模式] 超时，自动下一题")
                if i < total - 1:
                    await click_next_button(page)
                    await page.wait_for_timeout(300)
                current_index += 1
        else:
            # 自动模式：自动点击答案
            logger.info("[自动模式] 自动选择答案")
            if answer and answer.strip():
                logger.info("答案: %s" % answer[:40])
                applied = await answer_question(page, answer, options, raw_options)
                q["answer_applied"] = applied
                if not applied:
                    logger.warn("[WARN] 答案存在，但页面选项点击失败")
            else:
                q["answer_applied"] = False
                logger.warn("[WARN] 答案为空或无法匹配，留空跳过（可手动补充）")
            
            await page.wait_for_timeout(500)
            if i < total - 1:
                await click_next_button(page)
                await page.wait_for_timeout(300)
            current_index += 1
    
    # 【阶段3】提交
    logger.info("\n" + "="*60)
    logger.info("[STEP3] 提交试卷")
    logger.info("="*60)

    if manual_submit:
        logger.info("[手动提交] 已跳过自动提交，请在页面上手动点击提交按钮")
        logger.info("[手动提交] 页面保持打开状态，请检查答案后手动提交")
        return True

    # 检查未答题比例，防止无答案自动交卷
    if auto_submit:
        answered = sum(1 for q in questions_data if q.get("answer_applied"))
    else:
        answered = sum(
            1 for q in questions_data if q.get("answer") and q["answer"].strip()
        )
    unanswered = total - answered
    if unanswered > total * 0.5:
        logger.warn(f"\n[WARN] 未答题数 {unanswered}/{total} 超过 50%，跳过自动交卷，请手动检查后提交")
        logger.warn("[WARN] 如需强制自动交卷，请调整答题策略或配置 AI")
        return False

    success = await submit_exam(page)
    
    if success:
        logger.info("\n" + "="*60)
        logger.info("[DONE] 答题完成，准备继续课程学习")
        logger.info("="*60)
        
        # 关闭当前页面（测验窗口）
        try:
            await page.wait_for_timeout(1500)
            # 尝试关闭页面
            await page.evaluate('window.close()')
            logger.info("[OK] 已关闭测验页面")
        except Exception as e:
            logger.warn("[WARN] 关闭页面: %s" % str(e)[:30])
    else:
        logger.warn("\n" + "="*60)
        logger.warn("[FAIL] 答题提交失败")
        logger.warn("="*60)
    
    return success


async def wait_for_page_option_click(page: Page, timeout: float = 120) -> bool:
    """等待用户在页面上点击选项"""
    try:
        # 注入点击监听器
        await page.evaluate('''
            window._optionClicked = false;
            document.querySelectorAll('.nodeLab, .examquestions-answer, .flagChecked').forEach(el => {
                el.addEventListener('click', () => {
                    window._optionClicked = true;
                    console.log('选项被点击');
                }, { once: false });
            });
        ''')
        
        # 轮询检测点击
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            clicked = await page.evaluate('window._optionClicked')
            if clicked:
                # 重置状态
                await page.evaluate('window._optionClicked = false')
                await page.wait_for_timeout(300)  # 等待点击效果
                return True
            await asyncio.sleep(0.2)
        return False
    except:
        return False


async def wait_for_user_action(page: Page, is_multiple: bool = False, timeout: float = 180) -> str:
    """等待用户操作，返回操作类型
    
    返回值：
    - "next": 用户点击了下一题按钮
    - "prev": 用户点击了上一题按钮
    - "option_click": 用户点击了页面选项
    - "submit": 用户点击了提交试卷
    - "timeout": 超时
    """
    try:
        # 重置所有状态
        await page.evaluate('''
            window._optionClicked = false;
            window._widgetNextClicked = false;
            window._widgetPrevClicked = false;
            
            // 注入选项点击监听
            document.querySelectorAll('.nodeLab, .examquestions-answer, .flagChecked').forEach(el => {
                el.addEventListener('click', () => {
                    window._optionClicked = true;
                }, { once: false });
            });
        ''')
        
        # 轮询检测各种操作
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            # 检查浮动窗口按钮
            widget_next = await page.evaluate('window._widgetNextClicked')
            if widget_next:
                await page.evaluate('window._widgetNextClicked = false')
                # 判断是最后一题（提交）还是下一题
                is_last = await page.evaluate('''
                    const widget = document.getElementById('ai-answer-widget');
                    if (widget) {
                        const btn = widget.querySelector('.nav-btn.next');
                        return btn && btn.textContent.includes('提交');
                    }
                    return false;
                ''')
                return "submit" if is_last else "next"
            
            widget_prev = await page.evaluate('window._widgetPrevClicked')
            if widget_prev:
                await page.evaluate('window._widgetPrevClicked = false')
                return "prev"
            
            # 检查页面选项点击（仅单选题有效）
            if not is_multiple:
                option_clicked = await page.evaluate('window._optionClicked')
                if option_clicked:
                    await page.evaluate('window._optionClicked = false')
                    await page.wait_for_timeout(300)
                    return "option_click"
            
            await asyncio.sleep(0.2)
        
        return "timeout"
    except:
        return "timeout"


async def click_prev_button(page: Page):
    """点击上一题按钮"""
    try:
        # 尝试多种选择器
        selectors = [
            "text='上一题'",
            "text*='上一题'",
            ".prev-btn",
            "button:has-text('上一题')",
            ".exam-btn-prev"
        ]
        
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=2000)
                    logger.info("[OK] 点击上一题")
                    return
            except:
                continue
        
        logger.warn("[FAIL] 未找到上一题按钮")
    except Exception as e:
        logger.warn("[FAIL] 点击上一题异常: %s" % str(e)[:30])


async def wait_for_widget_next_button(page: Page, timeout: float = 180) -> bool:
    """等待用户点击悬浮窗的下一题按钮"""
    try:
        # 重置状态
        await page.evaluate('window._widgetNextClicked = false')
        
        # 轮询检测点击
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            clicked = await page.evaluate('window._widgetNextClicked')
            if clicked:
                await page.evaluate('window._widgetNextClicked = false')
                return True
            await asyncio.sleep(0.3)
        return False
    except:
        return False


async def wait_for_widget_submit(page: Page, timeout: float = 60) -> bool:
    """等待浮动组件的提交事件"""
    # ⚠️ 注意: answerSubmitted 事件在项目任何地方均未被触发（floating_widget.py 未 dispatch 此事件）
    # 保留此函数以备第三方扩展使用，但默认不会返回 True
    try:
        # 创建一个 Promise 等待提交事件
        result = await page.evaluate(f'''
            new Promise((resolve) => {{
                if (window.AIAnswerWidget) {{
                    const handler = () => {{
                        resolve(true);
                    }};
                    document.getElementById('ai-answer-widget').addEventListener('answerSubmitted', handler, {{ once: true }});
                    setTimeout(() => resolve(false), {timeout * 1000});
                }} else {{
                    resolve(false);
                }}
            }})
        ''')
        return result
    except:
        return False


async def _force_click_locator(locator):
    """对 locator 执行多重点击策略：click → click(force) → dispatchEvent"""
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
        await locator.dispatchEvent('click')
        return "dispatchEvent-click"
    except Exception:
        pass
    try:
        await locator.dispatchEvent('mousedown')
        await locator.dispatchEvent('mouseup')
        await locator.dispatchEvent('click')
        return "dispatchEvent-chain"
    except Exception:
        pass
    return None


async def click_option_by_index(page: Page, index: int, option_value: str = None):
    """通过索引点击选项（Playwright locator 层级兜底 + dispatchEvent 最终兜底）"""
    try:
        if option_value:
            # 方案A: 通过 input[value] 定位 → 点击 .label 或 .nodeLab
            sel_info = await page.evaluate(f'''
                (function() {{
                    const input = document.querySelector('input[value="{option_value}"]');
                    if (!input) return null;
                    const nodeLab = input.closest('.nodeLab');
                    const allLabs = document.querySelectorAll('.nodeLab');
                    if (nodeLab) {{
                        const gIdx = Array.from(allLabs).indexOf(nodeLab);
                        const lab = nodeLab.querySelector('.label');
                        if (lab) return {{ what: "label", idx: gIdx }};
                        return {{ what: "nodeLab", idx: gIdx }};
                    }}
                    return null;
                }})()
            ''')
            if sel_info:
                target = page.locator('.nodeLab').nth(sel_info["idx"])
                if sel_info["what"] == "label":
                    target = target.locator('.label')
                r = await _force_click_locator(target)
                if r:
                    logger.info("[OK] 通过value点击: value=%s (%s)" % (option_value, r))
                    await page.wait_for_timeout(200)
                    return True

            # 方案A兜底: 直接 force 点 input
            try:
                loc = page.locator('input[value="%s"]' % option_value)
                if await loc.count() > 0:
                    r = await _force_click_locator(loc.first)
                    if r:
                        logger.info("[OK] 通过value点input: value=%s (%s)" % (option_value, r))
                        await page.wait_for_timeout(200)
                        return True
            except Exception:
                pass

        # 方案B: 按索引定位
        idx_info = await page.evaluate(f'''
            (function() {{
                const subjects = document.querySelectorAll('.examPaper_subject');
                for (const subject of subjects) {{
                    const p = subject.parentElement;
                    if (p && p.style.display === 'none') continue;
                    const rect = subject.getBoundingClientRect();
                    if (rect.height > 0 && rect.width > 0) {{
                        const nodeLabs = subject.querySelectorAll('.nodeLab');
                        if (nodeLabs[{index}]) {{
                            const allLabs = document.querySelectorAll('.nodeLab');
                            const gIdx = Array.from(allLabs).indexOf(nodeLabs[{index}]);
                            return {{ idx: gIdx }};
                        }}
                    }}
                }}
                const allLabs = document.querySelectorAll('.nodeLab');
                const visLabs = [];
                for (const nl of allLabs) {{
                    const sub = nl.closest('.examPaper_subject');
                    if (sub) {{
                        const p = sub.parentElement;
                        if (p && p.style.display === 'none') continue;
                    }}
                    if (nl.offsetParent !== null) visLabs.push(nl);
                }}
                if (visLabs[{index}]) {{
                    return {{ idx: Array.from(allLabs).indexOf(visLabs[{index}]) }};
                }}
                if (allLabs[{index}]) {{
                    return {{ idx: {index} }};
                }}
                return null;
            }})()
        ''')
        if idx_info:
            main = page.locator('.nodeLab').nth(idx_info["idx"])
            label = main.locator('.label')
            r = await _force_click_locator(label)
            if r:
                logger.info("[OK] 通过索引点击label: 选项%d (%s)" % (index+1, r))
                await page.wait_for_timeout(200)
                return True
            r = await _force_click_locator(main)
            if r:
                logger.info("[OK] 通过索引点击nodeLab: 选项%d (%s)" % (index+1, r))
                await page.wait_for_timeout(200)
                return True

        # 方案C: .topic-item / .option-item / .el-radio / .el-checkbox
        for sel in ['.topic-item', '.option-item', '.el-radio', '.el-checkbox']:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0 and index < cnt:
                r = await _force_click_locator(loc.nth(index))
                if r:
                    logger.info("[OK] 通过%s点击: 选项%d (%s)" % (sel, index+1, r))
                    return True

        # 方案D: input[type=radio/checkbox] 全局
        for sel in ['input[type="radio"]', 'input[type="checkbox"]']:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0 and index < cnt:
                r = await _force_click_locator(loc.nth(index))
                if r:
                    logger.info("[OK] 通过%s点击: 选项%d (%s)" % (sel, index+1, r))
                    return True

        logger.warn("[FAIL] 所有方案失败: index=%d" % index)
        return False
    except Exception as e:
        logger.warn("[FAIL] 点击异常: %s" % str(e)[:80])
        return False


async def click_option_by_text(page: Page, text: str):
    """通过文本匹配点击选项（_force_click_locator 多重点击策略）"""
    try:
        escaped = text.replace("'", "\\'")
        # 方案A: .nodeLab 按文本匹配
        idx_info = await page.evaluate(f'''
            (function() {{
                const allLabs = document.querySelectorAll('.nodeLab');
                for (let i = 0; i < allLabs.length; i++) {{
                    const lab = allLabs[i];
                    if (!lab.textContent.includes('{escaped}')) continue;
                    const sub = lab.closest('.examPaper_subject');
                    if (sub) {{
                        const p = sub.parentElement;
                        if (p && p.style.display === 'none') continue;
                    }}
                    if (lab.offsetParent === null) continue;
                    return {{ idx: i }};
                }}
                for (let i = 0; i < allLabs.length; i++) {{
                    const lab = allLabs[i];
                    if (!lab.textContent.includes('{escaped}')) continue;
                    const sub = lab.closest('.examPaper_subject');
                    if (sub) {{
                        const p = sub.parentElement;
                        if (p && p.style.display === 'none') continue;
                    }}
                    return {{ idx: i, hidden: true }};
                }}
                return null;
            }})()
        ''')
        if idx_info:
            main = page.locator('.nodeLab').nth(idx_info["idx"])
            label = main.locator('.label')
            for target, name in [(label, "label"), (main, "nodeLab")]:
                r = await _force_click_locator(target)
                if r:
                    logger.info("[OK] 通过文本点击%s: text=%s (%s)" % (name, text, r))
                    await page.wait_for_timeout(200)
                    return True

        # 方案B: .topic-item / .option-item 按文本匹配
        for sel in ['.topic-item', '.option-item']:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(count):
                el_text = await locs.nth(i).text_content()
                if el_text and text in el_text:
                    r = await _force_click_locator(locs.nth(i))
                    if r:
                        logger.info("[OK] 通过文本点击%s: text=%s (%s)" % (sel, text, r))
                        return True

        logger.warn("[FAIL] 通过文本所有方法失败: text=%s" % text)
        return False
    except Exception as e:
        logger.warn("[FAIL] 通过文本点击异常: %s" % str(e)[:80])
        return False


async def answer_question(page: Page, answer: str, options: list = None, raw_options: list = None):
    """
    答题主函数
    answer: 答案文本（如 "对"、"A"、"正确" 等），多选题答案用 ### 分隔
    options: 选项文本列表（用于索引匹配）
    raw_options: 原始选项列表 [(id, text), ...] 用于 input[value] 定位
    """
    actions = build_answer_actions(answer, options, raw_options)
    if not actions:
        logger.info("[ANS] 答题: 答案为空，跳过")
        return False

    answer = str(answer).strip()
    logger.info("[ANS] 答题: %s" % answer)
    if options:
        logger.info("[ANS] 选项列表: %s" % str(options[:5]))

    all_succeeded = True
    for action_index, action in enumerate(actions):
        if action.kind == "index":
            logger.info("[ANS] 按索引点击选项: %s" % action.value)
            succeeded = await click_option_by_index(
                page,
                int(action.value),
                action.option_value,
            )
        else:
            logger.info("[ANS] 按文本点击选项: %s" % action.value)
            succeeded = await click_option_by_text(page, str(action.value))
        all_succeeded = bool(succeeded) and all_succeeded
        if action_index < len(actions) - 1:
            await page.wait_for_timeout(300)
    return all_succeeded


async def click_next_button(page: Page):
    """点击下一题按钮"""
    try:
        # 方法1：通过按钮文本
        next_btn = page.locator("button:has-text('下一题')").first
        await next_btn.click(timeout=3000)
        logger.info("[OK] 点击下一题")
        return
    except:
        pass
    
    try:
        # 方法2：通过 class
        next_btn = page.locator(".switch-btn-box button").last
        await next_btn.click(timeout=3000)
        logger.info("[OK] 点击下一题（备用）")
        return
    except:
        pass
    
    try:
        # 方法3：其他选择器
        next_btn = page.locator(".next-btn, [class*='next']").first
        await next_btn.click(timeout=3000)
        logger.info("[OK] 点击下一题（选择器）")
        return
    except:
        pass
    
    try:
        # 方法4：键盘右键
        await page.keyboard.press("ArrowRight")
        logger.info("[OK] 使用键盘下一题")
    except Exception as e:
        logger.warn("[FAIL] 点击下一题失败: %s" % str(e)[:50])


async def submit_exam(page: Page) -> bool:
    """提交作业
    
    返回值：
    - True: 提交成功
    - False: 提交失败
    """
    try:
        # 1. 点击提交按钮
        submit_btn = page.locator("button:has-text('提交作业'), .btnStyleXSumit").first
        await submit_btn.click(timeout=5000)
        logger.info("[OK] 点击提交作业按钮")
        
        # 2. 等待确认对话框出现并点击确定
        await page.wait_for_timeout(1000)
        
        # 尝试多种确认按钮选择器
        confirm_selectors = [
            "button.el-button--primary:has-text('确定')",
            ".el-message-box__btns button.el-button--primary",
            "button.el-button.el-button--default.el-button--small.el-button--primary",
            ".el-message-box__confirm",
            ".confirm-btn"
        ]
        
        confirmed = False
        for selector in confirm_selectors:
            try:
                confirm_btn = page.locator(selector).first
                if await confirm_btn.count() > 0:
                    await confirm_btn.click(timeout=3000)
                    logger.info("[OK] 点击确认按钮")
                    confirmed = True
                    break
            except:
                continue
        
        if not confirmed:
            logger.warn("[WARN] 未找到确认按钮，可能已自动提交")
        
        # 3. 等待提交完成
        await page.wait_for_timeout(2000)
        logger.info("[OK] 作业提交完成")
        
        # 4. 关闭当前页面（测验窗口）
        try:
            # 检查是否有关闭按钮
            close_selectors = [
                ".close-btn",
                "button:has-text('关闭')",
                ".el-dialog__close",
                ".exam-close-btn"
            ]
            
            for selector in close_selectors:
                try:
                    close_btn = page.locator(selector).first
                    if await close_btn.count() > 0:
                        await close_btn.click(timeout=2000)
                        logger.info("[OK] 关闭测验窗口")
                        break
                except:
                    continue
        except Exception as e:
            logger.warn("[WARN] 关闭窗口失败: %s" % str(e)[:30])
        
        return True
        
    except Exception as e:
        logger.warn("[FAIL] 提交失败：%s" % str(e)[:50])
        return False


# 章节学习辅助流程已拆分至 modules.chapter_learning，并在本模块顶部兼容导出。
