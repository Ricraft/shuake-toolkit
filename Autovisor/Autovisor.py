# encoding=utf-8
import asyncio
import json
import os
import time
import traceback
import sys
import types

from runtime_bootstrap import activate_runtime_dependencies

activate_runtime_dependencies()

from playwright.async_api import async_playwright, Playwright, Page, BrowserContext
from playwright.async_api import TimeoutError
from playwright._impl._errors import TargetClosedError


def _ensure_simplejson_compat():
    try:
        import simplejson as simplejson_module
        if hasattr(simplejson_module, "JSONDecodeError"):
            return
    except Exception:
        pass

    shim = types.ModuleType("simplejson")
    shim.JSONDecodeError = json.JSONDecodeError
    shim.dumps = json.dumps
    shim.dump = json.dump
    shim.loads = json.loads
    shim.load = json.load
    sys.modules["simplejson"] = shim


_ensure_simplejson_compat()

from modules.logger import Logger
from modules.configs import Config
from modules.progress import get_course_progress, show_course_progress, move_mouse_meeting_class
from modules.utils import optimize_page, get_lesson_name, get_filtered_class, get_video_attr, hide_window, \
    get_browser_window, bring_console_to_front, save_cookies, load_cookies, \
    scan_pending_lessons_deep, scan_national_wisdom_cards, click_card_by_id, APPLY_VIDEO_SETTINGS_JS, \
    scan_normal_class_tests, detect_test_in_current_lesson, scan_meeting_class_videos
from modules.slider import slider_verify
from modules.async_utils import cancel_background_tasks
from modules.tasks import video_optimize, play_video, skip_questions, wait_for_verify, activate_window, task_monitor, handle_test_page, TestResponseHandler
from modules import installer

# 获取全局事件循环
event_loop_verify = asyncio.Event()
event_loop_answer = asyncio.Event()

async def init_page(p: Playwright) -> tuple[Page, BrowserContext]:
    driver = "msedge" if config.driver == "edge" else config.driver
    logger.info(f"正在启动{config.driver}浏览器...")
    browser = await p.chromium.launch(
        channel=driver,
        headless=False,
        executable_path=config.exe_path if config.exe_path else None,
        args=[
            f'--window-size={1600},{900}',
            '--window-position=100,100',  # 窗口位置
        ],
    )
    context = await browser.new_context()
    # 加载 Cookies
    cookies = load_cookies("res/cookies.json")
    if cookies:
        await context.add_cookies(cookies)
        logger.info("已加载 Cookies!")
    else:
        logger.info("未找到 Cookies,将跳转至登录页.")
    page = await context.new_page()
    logger.write_log(f"{config.driver}浏览器启动完成.\n")
    page.set_default_timeout(24 * 3600 * 1000)

    return page, context

async def auto_login(context: BrowserContext, page: Page, modules=None):
    async def request_handler(request):
        if "https://www.zhihuishu.com" in request.url:
            cookies = await context.cookies()
            save_cookies(cookies, "res/cookies.json")
            logger.info(f"已保存登录凭证到: res/cookies.json,下次可免密登录.")
            # 停止监听
            page.remove_listener('request', request_handler)

    await page.goto(config.login_url, wait_until="commit")
    if "login" not in page.url:
        logger.info("检测到已登录,跳过登录步骤.")
        return
    await page.wait_for_selector(".wall-main", state='attached')  # 等待登陆界面加载
    page.on('request', request_handler)
    if config.username and config.password:
        await page.wait_for_selector("#lUsername", state="attached")
        await page.wait_for_selector("#lPassword", state="attached")
        await page.locator('#lUsername').fill(config.username)
        await page.locator('#lPassword').fill(config.password)
        await page.wait_for_selector(".wall-sub-btn", state="attached")
        await page.wait_for_timeout(500)
        await page.locator(".wall-sub-btn").first.click()
    if config.enableAutoCaptcha and modules:
        await slider_verify(page)
    await page.wait_for_selector(".wall-main", state='hidden')


async def close_popup(page: Page, logger_instance=None):
    """关闭课程弹窗（学前必读等），支持 iframe 内弹窗"""
    await page.wait_for_timeout(500)
    
    popup_close_selectors = [
        "i.iconfont.iconguanbi",
        ".iconfont.iconguanbi",
        "i.iconguanbi",
        ".iconguanbi",
        ".el-dialog__header i.iconfont",
        ".dialog-read i.iconfont",
        ".el-dialog__close",
        ".close-btn",
        "button:has-text('关闭')",
        # 见面课签到弹窗
        "#qiandao_rule .iconfont",
        "#qiandao_rule i",
        ".tm_dialog .dialog-close",
        ".popboxes .close",
    ]
    
    contexts = [page]
    for frame in page.frames:
        contexts.append(frame)
    
    for context in contexts:
        for selector in popup_close_selectors:
            try:
                close_btn = context.locator(selector).first
                if await close_btn.count() > 0:
                    try:
                        if await close_btn.is_visible():
                            await close_btn.click(timeout=2000, force=True)
                            if logger_instance:
                                context_name = "iframe" if context != page else "主页面"
                                logger_instance.info(f"已关闭弹窗 ({context_name}, 选择器: {selector})")
                            await page.wait_for_timeout(500)
                            return True
                    except Exception:
                        try:
                            await close_btn.click(timeout=2000, force=True)
                            if logger_instance:
                                context_name = "iframe" if context != page else "主页面"
                                logger_instance.info(f"已关闭弹窗 (强制点击, {context_name}, {selector})")
                            await page.wait_for_timeout(500)
                            return True
                        except Exception:
                            continue
            except Exception:
                continue
        
        try:
            dialog_read = context.locator(".dialog-read").first
            if await dialog_read.count() > 0:
                close_icon = dialog_read.locator("i.iconfont, i.iconguanbi").first
                if await close_icon.count() > 0:
                    await close_icon.click(timeout=2000, force=True)
                    if logger_instance:
                        context_name = "iframe" if context != page else "主页面"
                        logger_instance.info(f"已关闭弹窗 ({context_name}, dialog-read 容器内)")
                    await page.wait_for_timeout(500)
                    return True
        except Exception:
            pass
    
    return False


async def learning_loop(page: Page, start_time, is_new_version=False, is_hike_class=False, is_national_wisdom=False, is_meeting_class=False):
    # 见面课完成阈值：80%（签到进度达到80%即完成签到）
    completion_threshold = 0.8 if is_meeting_class else (0.98 if is_national_wisdom else 1.0)
    
    # 对于全国智慧共享课和见面课，额外等待视频真正开始播放
    if is_national_wisdom or is_meeting_class:
        await page.wait_for_timeout(2000)
        # 等待视频开始播放（paused 变为 false）
        for _ in range(10):
            try:
                paused = await page.evaluate('document.querySelector("video")?.paused ?? true')
                if not paused:
                    break
            except Exception:
                pass
            await page.wait_for_timeout(500)
    
    await page.wait_for_timeout(1000)
    cur_time = await get_course_progress(page, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class, completion_threshold)
    
    # 如果一开始就显示100%，对于智慧共享课和见面课需要额外等待和验证
    if cur_time == "100%" and (is_hike_class or is_national_wisdom or is_meeting_class):
        await page.wait_for_timeout(5000)
        cur_time = await get_course_progress(page, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class, completion_threshold)
        
        # 再次检查，如果还是100%但视频时间很短，说明可能是误判，需要重置视频
        if cur_time == "100%":
            try:
                result = await page.evaluate('''() => {
                    const video = document.querySelector('video');
                    if (!video) return {currentTime: 0, duration: 0};
                    return {currentTime: video.currentTime, duration: video.duration};
                }''')
                if result and result['duration'] > 0 and result['currentTime'] < result['duration'] * 0.2:
                    logger.warn("检测到进度误判：视频时间很短但显示100%，重置视频并重新播放", shift=True)
                    await page.evaluate("document.querySelector('video').currentTime = 0;")
                    await page.evaluate("Object.defineProperty(document.querySelector('video'), 'ended', { value: false, writable: true });")
                    await page.evaluate("document.querySelector('video').play();")
                    cur_time = "0%"
            except Exception as e:
                logger.warn(f"重置视频失败: {repr(e)}", shift=True)
    
    min_duration = time.time() + 10
    loop_counter = 0
    last_progress = cur_time
    stuck_since = time.time()
    while cur_time != "100%" or time.time() < min_duration:
        try:
            loop_counter += 1
            
            limit_time = config.limitMaxTime
            time_period = (time.time() - start_time) / 60
            if 0 < limit_time <= time_period:
                logger.info(f"已达学习时限 {limit_time}min，退出学习循环", shift=True)
                break
            
            # P0-1: 每30次循环检查一次视频加载状态
            if loop_counter % 30 == 0:
                video_state = await page.evaluate('''() => {
                    const v = document.querySelector('video');
                    if (!v) return { error: 'no_video' };
                    return {
                        error: v.error ? v.error.code : null,
                        networkState: v.networkState,
                        readyState: v.readyState,
                        paused: v.paused,
                        currentTime: v.currentTime,
                        duration: v.duration
                    };
                }''')
                if video_state.get('error') is not None:
                    logger.warn(f"视频加载错误(code={video_state['error']})，刷新页面重试", shift=True)
                    try:
                        await page.reload(wait_until="domcontentloaded")
                        await page.wait_for_selector("video", timeout=15000)
                        await page.evaluate(config.remove_pause)
                        logger.info("页面已刷新，继续播放", shift=True)
                    except Exception as reload_e:
                        logger.error(f"刷新页面失败: {str(reload_e)[:50]}", shift=True)
                        break
            
            # P0-2: 每30次循环检查登录态是否过期
            if loop_counter % 30 == 0:
                current_url = page.url
                if "www.zhihuishu.com" in current_url or "login" in current_url:
                    logger.error("检测到被重定向到首页或登录页，登录态可能已过期，退出", shift=True)
                    break
            
            cur_time = await get_course_progress(page, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class, completion_threshold)
            
            # 【修复】检测进度停滞：如果连续60秒进度没有变化，刷新页面
            if cur_time == last_progress:
                if time.time() - stuck_since > 60:
                    logger.warn(f"进度已停滞超过60秒({cur_time})，刷新页面重试", shift=True)
                    try:
                        await page.reload(wait_until="domcontentloaded")
                        await page.wait_for_selector("video", timeout=15000)
                        await page.evaluate(config.remove_pause)
                        last_progress = "0%"
                        stuck_since = time.time()
                        cur_time = "0%"
                        logger.info("页面已刷新，继续播放", shift=True)
                        continue
                    except Exception as reload_e:
                        logger.error(f"刷新页面失败: {str(reload_e)[:50]}", shift=True)
                        break
            else:
                last_progress = cur_time
                stuck_since = time.time()
            
            # 见面课特殊提示
            if is_meeting_class and cur_time == "100%":
                print()
                logger.info("见面课签到进度已达80%，自动完成签到！", shift=True)
            
            show_course_progress(desc="完成进度:", cur_time=cur_time, is_meeting_class=is_meeting_class)
            
            # 检测并关闭弹窗
            await close_popup(page, logger)
            
            # 检测视频是否暂停，如果暂停则继续播放
            try:
                paused = await page.evaluate('document.querySelector("video")?.paused ?? true')
                if paused:
                    logger.info("检测到视频暂停，尝试继续播放...")
                    play_result = await page.evaluate('''async () => {
                        const video = document.querySelector('video');
                        if (!video) return { success: false, error: 'video not found' };
                        try {
                            await video.play();
                            return { success: true, paused: video.paused, error: null };
                        } catch (e) {
                            return { success: false, error: e.message || 'play failed' };
                        }
                    }''')
                    if play_result.get('success'):
                        logger.info("视频已继续播放")
                    else:
                        logger.warn(f"继续播放失败: {play_result.get('error', 'unknown')}")
            except Exception as e:
                pass
            
            await asyncio.sleep(0.5)
        except TimeoutError as e:
            if await page.query_selector(".yidun_modal__title"):
                await event_loop_verify.wait()
            elif await page.query_selector(".topic-title"):
                await event_loop_answer.wait()
            elif is_hike_class and await page.query_selector(".question-info"):
                await event_loop_answer.wait()
            else:
                logger.warn(repr(e))


async def review_loop(page: Page, start_time, is_hike_class=False):
    total_time = await get_video_attr(page, "duration")
    await page.evaluate(config.reset_curtime)  # 重置视频播放时间
    while True:
        limit_time = config.limitMaxTime
        cur_time = await get_video_attr(page, "currentTime")
        if cur_time >= total_time:
            break
        try:
            time_period = (time.time() - start_time) / 60
            if 0 < limit_time <= time_period:
                break
            show_course_progress(desc="完成进度:", cur_time=time_period, limit_time=limit_time)
            await asyncio.sleep(0.5)
        except TimeoutError as e:
            if await page.query_selector(".yidun_modal__title"):
                await event_loop_verify.wait()
            elif await page.query_selector(".topic-title"):
                await event_loop_answer.wait()
            else:
                logger.warn(repr(e))


async def working_loop(page: Page, is_new_version=False, is_hike_class=False, is_national_wisdom=False, is_meeting_class=False):
    # 智慧共享课（翻转课）使用深度扫描
    if is_hike_class:
        logger.info("开始运行时滚动扫描... (智慧共享课)", shift=True)
        pending_lessons, summary = await scan_pending_lessons_deep(page)
        logger.info(
            f"整页统计: 总卡片 {summary['total']} | 未完成 {summary['pending']} | 已完成 {summary['done']}",
            shift=True,
        )
        if summary["sections"]:
            top_sections = []
            for section, stats in summary["sections"].items():
                top_sections.append(f"{section}:{stats['pending']}/{stats['total']}")
            logger.info(f"分组统计: {' | '.join(top_sections[:6])}")
        
        if not pending_lessons:
            logger.info("没有未完成的课程，本轮结束。")
            return
        
        start_time = time.time()
        for lesson in pending_lessons:
            title = lesson["title"]
            card_id = lesson["card_id"]
            scope_id = lesson.get("scope_id")
            top = lesson.get("top")
            
            # 检查时间限制
            time_period = (time.time() - start_time) / 60
            if 0 < config.limitMaxTime <= time_period:
                logger.info(f"当前课程已达时限:{config.limitMaxTime}min", shift=True)
                return
            
            logger.info(
                f"锁定蓝条未满节次: {lesson.get('section', '')} / {title} ({lesson['progress']}%)"
            )
            
            if not await click_card_by_id(page, card_id, title, scope_id, top):
                logger.warn(f"未能定位卡片:{title}, 本轮跳过.", shift=True)
                continue
            
            await page.wait_for_timeout(1000)
            
            # 关闭可能出现的弹窗（学前必读等）
            await close_popup(page, logger)
            
            try:
                current_title = await get_lesson_name(page, is_hike_class) or title
            except Exception:
                current_title = title
            logger.info(f"开始观看:{current_title}")
            
            try:
                await page.wait_for_selector("video", state="attached", timeout=15000)
                await page.evaluate(config.remove_pause)
            except Exception:
                logger.warn("未及时检测到视频元素,进入宽松等待模式.", shift=True)
            
            # 学习循环
            await learning_loop(page, start_time, is_new_version, is_hike_class, is_national_wisdom)
            
            # 返回课程母页继续下一个
            await page.goto(config.course_urls[0], wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            await optimize_page(page, config, is_new_version, is_hike_class, is_national_wisdom)
            
            # 重新扫描（课程状态可能已更新）
            pending_lessons, summary = await scan_pending_lessons_deep(page)
            if not pending_lessons:
                logger.info("所有课程已完成!", shift=True)
                return
    
    # 全国智慧共享课使用专门的处理逻辑
    elif is_national_wisdom:
        logger.info("开始运行时滚动扫描... (全国智慧共享课)", shift=True)
        
        # 等待页面加载并关闭可能出现的弹窗
        await page.wait_for_timeout(2000)
        for _ in range(5):
            closed = await close_popup(page, logger)
            if closed:
                await page.wait_for_timeout(500)
            else:
                break
        
        start_time = time.time()
        tried_keys: set = set()
        skipped_tests: set = set()
        test_retry_map: dict[str, int] = {}
        test_retry_limit = 5
        consecutive_lessons = 0
        loop_count = 0
        
        while True:
            loop_count += 1
            if loop_count > 200:
                logger.warn("循环次数超限(200)，强制退出", shift=True)
                break

            # 先尝试关闭弹窗（学前必读等）
            await close_popup(page, logger)
            
            all_cards, summary, is_in_iframe = await scan_national_wisdom_cards(page)
            pending_lessons = [c for c in all_cards if c["progress"] < 100]
            
            logger.info(
                f"整页统计: 总卡片 {summary['total']} | 未完成 {summary['pending']} | 已完成 {summary['done']}",
                shift=True,
            )
            
            if not pending_lessons:
                if summary["total"] == 0:
                    logger.info("未检测到课程卡片，可能页面还在加载，重试中...", shift=True)
                    await page.wait_for_timeout(3000)
                    continue
                logger.info("所有课程已完成!", shift=True)
                break
            
            # 调试: 打印 test 类型卡片
            test_cards = [c for c in all_cards if c.get('type') == 'test']
            if test_cards:
                logger.info(f"检测到 {len(test_cards)} 个测试项: {[t['title'] for t in test_cards]}")
            
            time_period = (time.time() - start_time) / 60
            if 0 < config.limitMaxTime <= time_period:
                logger.info(f"当前课程已达时限:{config.limitMaxTime}min", shift=True)
                return
            
            lesson = None
            for candidate in pending_lessons:
                if candidate["key"] not in tried_keys and candidate["key"] not in skipped_tests:
                    lesson = candidate
                    break
            if lesson is None:
                # 检查是否只剩被永久跳过的测验，若是则退出
                non_skipped = [c for c in pending_lessons if c["key"] not in skipped_tests]
                if not non_skipped:
                    logger.info("所有未完成项均已跳过，退出", shift=True)
                    break
                # 还有视频项未完成，清空 tried_keys 重试
                non_test = [c for c in non_skipped if c.get('type') != 'test']
                if not non_test:
                    # 只剩测验且全部尝试过，退出
                    logger.info("所有剩余项均为测验且已尝试，退出", shift=True)
                    break
                logger.info("所有可尝试项均尝试过，清空记录重新开始", shift=True)
                tried_keys.clear()
                for candidate in non_skipped:
                    lesson = candidate
                    break
            
            title = lesson["title"]
            card_id = lesson["card_id"]
            lesson_key = lesson["key"]
            
            logger.info(f"锁定未完成节次: {lesson.get('section', '')} / {title} ({lesson['progress']}%)")
            
            # 测试卡片：在点击前设置监听器
            test_handler = None
            new_page = None
            original_page = page  # 保存原始页面引用
            if lesson.get('type') == 'test':
                # 测验重试次数限制（按测验key独立计数）
                test_retry_count = test_retry_map.get(lesson_key, 0) + 1
                test_retry_map[lesson_key] = test_retry_count
                if test_retry_count > test_retry_limit:
                    logger.warn(f"测验 '{title}' 重试超限({test_retry_limit})，永久跳过", shift=True)
                    skipped_tests.add(lesson_key)
                    continue
                
                test_handler = TestResponseHandler()
                test_handler.setup_listener(page.context)
                logger.info(f"已设置测试响应监听器，准备点击测试卡片: {title}")
                
                # 同时监听新页面
                async def wait_for_new_page():
                    nonlocal new_page
                    try:
                        new_page = await page.context.wait_for_event("page", timeout=8000)
                        logger.info(f"检测到新页面打开: {new_page.url}")
                    except Exception:
                        logger.write_log("未检测到新页面\n")
                
                new_page_task = asyncio.create_task(wait_for_new_page())
            else:
                new_page_task = None
            
            # 【修复】点击卡片前确保页面在课程列表页
            try:
                if "study/index" not in page.url and "wisdom-mooc" not in page.url:
                    logger.info("点击卡片前检测到页面不在课程列表，重新导航", shift=True)
                    await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                    await page.wait_for_timeout(1500)
                    # 重新扫描
                    all_cards, summary, is_in_iframe = await scan_national_wisdom_cards(page)
                    pending_lessons = [c for c in all_cards if c["progress"] < 100]
                    # 重新查找当前卡片
                    found = False
                    for c in pending_lessons:
                        if c["key"] == lesson_key:
                            lesson = c
                            card_id = c["card_id"]
                            title = c["title"]
                            is_in_iframe = is_in_iframe
                            found = True
                            break
                    if not found:
                        logger.warn(f"重新导航后未找到卡片:{title}", shift=True)
                        continue
            except Exception:
                pass
            
            if not await click_card_by_id(page, card_id, title, is_in_iframe):
                logger.warn(f"未能定位卡片:{title}, 本轮跳过.", shift=True)
                if test_handler:
                    test_handler.remove_listener()
                if new_page_task:
                    new_page_task.cancel()
                await page.wait_for_timeout(1000)
                continue
            
            # 测试卡片：等待响应并处理答题
            if lesson.get('type') == 'test':
                logger.info(f"已点击测试卡片，等待页面加载和API响应...")
                
                # 等待新页面检测完成
                if new_page_task:
                    try:
                        await new_page_task
                    except Exception:
                        pass
                
                # 确定工作页面
                work_page = new_page if new_page else page
                logger.info(f"工作页面URL: {work_page.url}")
                
                # 等待页面完全加载（不刷新，避免登录重定向）
                await work_page.wait_for_load_state("networkidle")
                logger.info(f"页面加载完成，URL: {work_page.url}")
                
                # 如果lookHomework已经返回了题目数据，检查是否已完成
                if test_handler.questions_data:
                    if test_handler.is_completed:
                        logger.info(f"测试已完成（lookHomework），跳过答题")
                        test_handler.remove_listener()
                        if new_page:
                            try:
                                await new_page.close()
                                logger.info("已关闭测试页面")
                            except Exception:
                                pass
                        page = original_page
                        # 【修复】强制 reload 清理 CDP 状态，避免触发视频页反debug
                        try:
                            await page.reload(wait_until="domcontentloaded")
                            await page.wait_for_timeout(1500)
                            if "study/index" not in page.url and "wisdom-mooc" not in page.url:
                                await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                                await page.wait_for_timeout(1500)
                        except Exception:
                            try:
                                await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                                await page.wait_for_timeout(1500)
                            except Exception:
                                pass
                        tried_keys.add(lesson_key)
                        test_retry_map.pop(lesson_key, None)
                        continue
                    else:
                        logger.info(f"doHomework已返回 {len(test_handler.questions_data)} 道题目")
                else:
                    # 尝试点击"开始做题"按钮触发doHomework
                    try:
                        start_btn = work_page.locator("button:has-text('开始做题'), button:has-text('开始做'), a:has-text('开始做题'), a:has-text('开始做'), .start-btn, [class*='start']").first
                        if await start_btn.count() > 0:
                            logger.info("找到开始做题按钮，点击...")
                            await start_btn.click(timeout=5000)
                            await work_page.wait_for_load_state("networkidle")
                    except Exception as e:
                        logger.write_log(f"未找到开始做题按钮: {e}\n")
                    
                    # 等待doHomework响应
                    got_questions = await test_handler.wait_for_questions(timeout=20)
                    if got_questions:
                        logger.info(f"成功捕获题目数据")
                        if test_handler.is_completed:
                            logger.info("测试已完成，跳过答题")
                            test_handler.remove_listener()
                            if new_page:
                                try:
                                    await new_page.close()
                                    logger.info("已关闭测试页面")
                                except Exception:
                                    pass
                            page = original_page
                            # 【修复】强制 reload 清理 CDP 状态，避免触发视频页反debug
                            try:
                                await page.reload(wait_until="domcontentloaded")
                                await page.wait_for_timeout(1500)
                                if "study/index" not in page.url and "wisdom-mooc" not in page.url:
                                    await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                                    await page.wait_for_timeout(1500)
                            except Exception:
                                try:
                                    await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                                    await page.wait_for_timeout(1500)
                                except Exception:
                                    pass
                            tried_keys.add(lesson_key)
                            test_retry_map.pop(lesson_key, None)
                            continue

                # 处理答题（只要有题目数据就执行）
                if test_handler.questions_data:
                    await handle_test_page(work_page, test_handler.questions_data, auto_submit=True, manual_submit=True)
                else:
                    logger.warn("没有题目数据，跳过答题")
                
                # 清理监听器
                test_handler.remove_listener()
                
                # 如果打开了新页面，关闭它
                if new_page:
                    try:
                        await new_page.close()
                        logger.info("已关闭测试页面")
                    except Exception:
                        pass
                
                # 恢复原始页面引用
                page = original_page
                
                # 【修复】强制 reload 清理 CDP 状态，避免触发视频页反debug
                try:
                    logger.info("测验处理后 force reload 清理CDP状态", shift=True)
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_timeout(1500)
                    if "study/index" not in page.url and "wisdom-mooc" not in page.url:
                        await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                        await page.wait_for_timeout(1500)
                except Exception:
                    try:
                        await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                        await page.wait_for_timeout(1500)
                    except Exception:
                        pass
                
                tried_keys.add(lesson_key)
                continue
            
            await page.wait_for_timeout(1000)
            
            # 关闭可能出现的弹窗（学前必读等）
            await close_popup(page, logger)
            
            try:
                current_title = await get_lesson_name(page, is_hike_class, is_national_wisdom) or title
            except Exception:
                current_title = title
            logger.info(f"开始观看:{current_title}")
            
            # 等待视频并立即静音+倍速
            try:
                await page.wait_for_selector("video", state="attached", timeout=15000)
                await page.wait_for_timeout(500)
                if "www.zhihuishu.com" in page.url:
                    logger.error("检测到被重定向到首页，session 可能已过期，退出", shift=True)
                    return
                
                # 使用 remove_pause 防止视频被暂停
                await page.evaluate(config.remove_pause)
                
                # 全国智慧共享课强制1.0倍速（按累计墙钟时间计分，倍速会导致时间不够）
                national_wisdom_speed = 1.0
                await page.evaluate(APPLY_VIDEO_SETTINGS_JS, national_wisdom_speed)
                logger.write_log(f"静音+{national_wisdom_speed}x倍速 已应用\n")
                
                # 确保视频正在播放
                await page.wait_for_timeout(300)
                paused = await page.evaluate("document.querySelector('video')?.paused ?? true")
                
                if paused:
                    # 尝试播放视频，处理Promise
                    play_result = await page.evaluate('''async () => {
                        const video = document.querySelector('video');
                        if (!video) return { success: false, error: 'video not found' };
                        try {
                            await video.play();
                            return { success: true, paused: video.paused, error: null };
                        } catch (e) {
                            return { success: false, error: e.message || 'play failed' };
                        }
                    }''')
                    
                    if play_result['success']:
                        logger.write_log("视频已开始播放\n")
                    else:
                        logger.warn(f"视频播放失败: {play_result['error']}")
            except Exception:
                logger.warn("未及时检测到视频元素,进入宽松等待模式.", shift=True)
            
            # 学习循环
            lesson_start = time.time()
            await learning_loop(page, start_time, is_new_version, is_hike_class, is_national_wisdom)
            lesson_elapsed = (time.time() - lesson_start) / 60
            logger.write_log(f"\"{title}\" 本课学习用时: {lesson_elapsed:.1f}min\n")
            
            tried_keys.add(lesson_key)
            consecutive_lessons += 1
            
            # 检查视频是否真正播放完成
            # 全国智慧共享课：用 go_back 返回课程列表（比 goto 快数倍，利用浏览器缓存）
            if is_national_wisdom:
                try:
                    await page.go_back(wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
                    # 【修复】验证是否真正回到了课程列表
                    if "study/index" not in page.url:
                        logger.info("go_back未回到课程列表，改用goto导航", shift=True)
                        await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                        await page.wait_for_timeout(1500)
                except Exception:
                    logger.info("go_back失败，改用goto导航回课程列表", shift=True)
                    try:
                        await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                        await page.wait_for_timeout(1500)
                    except Exception:
                        pass
                logger.info("视频播放完成，已返回课程列表", shift=True)
                consecutive_lessons = 0
                continue
            else:
                await page.wait_for_timeout(1500)
                cur_progress = await get_course_progress(page, is_new_version, is_hike_class, is_national_wisdom)
                logger.write_log(f"课后进度检查: {cur_progress}\n")
                if cur_progress != "100%":
                    logger.info(f"播放未完成(进度{cur_progress})，返回课程页重试", shift=True)
                    consecutive_lessons = 0
                    await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    await optimize_page(page, config, is_new_version, is_hike_class, is_national_wisdom)
                    continue
            
            # 检查总时限
            time_period = (time.time() - start_time) / 60
            if 0 < config.limitMaxTime <= time_period:
                logger.info(f"已达总时限 {config.limitMaxTime}min，停止", shift=True)
                return
            
            # 播放完成，尝试连播或回课程页
            
            if len(tried_keys) >= len(all_cards):
                logger.info("所有课程均已尝试一轮，清空重试记录", shift=True)
                tried_keys.clear()
            
            # 每节课完成后返回课程列表重新扫描，确保进度记录正确
            consecutive_lessons = 0
            await page.goto(config.course_urls[0], wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            await optimize_page(page, config, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class)
    # 见面课使用特殊逻辑（80%完成阈值）
    elif is_meeting_class:
        logger.info("见面课模式：签到进度达到80%即完成签到")
        
        # 扫描视频列表
        logger.info("扫描视频列表...")
        videos = await scan_meeting_class_videos(page)
        
        if not videos:
            logger.warn("未找到视频列表，尝试直接播放")
            await page.wait_for_selector("video", state="attached", timeout=15000)
            start_time = time.time()
            
            # 关闭可能出现的弹窗（学前必读等）
            await close_popup(page, logger)
            
            # 移动鼠标到视频区域，显示播放控制按钮
            logger.info("移动鼠标到视频区域...")
            await move_mouse_meeting_class(page)
            await page.wait_for_timeout(1000)
            
            # 设置倍速
            if hasattr(config, 'playbackRate') and config.playbackRate:
                try:
                    await move_mouse_meeting_class(page)
                    await page.wait_for_timeout(500)
                    
                    speed_set = False
                    speed_box = page.locator(".speedBox").first
                    if await speed_box.count() > 0 and await speed_box.is_visible():
                        await speed_box.click(timeout=2000)
                        await page.wait_for_timeout(500)
                        rate_map = {1.0: ".speedTab05", 1.25: ".speedTab10", 1.5: ".speedTab15"}
                        rate_selector = rate_map.get(config.playbackRate, ".speedTab15")
                        rate_button = page.locator(rate_selector).first
                        if await rate_button.count() > 0:
                            await rate_button.click(timeout=2000)
                            logger.info(f"倍数已设置为 {config.playbackRate}x")
                            speed_set = True
                            await page.wait_for_timeout(500)
                    
                    if not speed_set:
                        rate_box = page.locator(".vjs-playback-rate").first
                        if await rate_box.count() > 0 and await rate_box.is_visible():
                            await rate_box.click(timeout=2000)
                            await page.wait_for_timeout(500)
                            vjs_rate_map = {1.0: "1x", 1.25: "1.25x", 1.5: "1.5x"}
                            rate_text = vjs_rate_map.get(config.playbackRate, f"{config.playbackRate}x")
                            rate_item = rate_box.locator(f".vjs-menu-item:has-text('{rate_text}')").first
                            if await rate_item.count() > 0:
                                await rate_item.click(timeout=2000)
                                logger.info(f"倍数已设置为 {config.playbackRate}x (video.js)")
                                speed_set = True
                                await page.wait_for_timeout(500)
                    
                    if not speed_set:
                        await page.evaluate(f"document.querySelector('video').playbackRate = {config.playbackRate};")
                        logger.info(f"通过JS设置倍速为 {config.playbackRate}x")
                except Exception as e:
                    logger.warn(f"设置倍速失败: {str(e)[:50]}")
            
            # 尝试自动播放
            try:
                await move_mouse_meeting_class(page)
                await page.wait_for_timeout(1000)
                
                play_clicked = False
                big_play = page.locator(".bigPlayButton").first
                if await big_play.count() > 0 and await big_play.is_visible():
                    try:
                        await big_play.click(timeout=5000)
                        logger.info("点击播放按钮(.bigPlayButton)成功")
                        play_clicked = True
                        await page.wait_for_timeout(2000)
                    except Exception:
                        pass
                
                if not play_clicked:
                    vjs_play = page.locator(".vjs-big-play-button").first
                    if await vjs_play.count() > 0 and await vjs_play.is_visible():
                        try:
                            await vjs_play.click(timeout=5000)
                            logger.info("点击播放按钮(.vjs-big-play-button)成功")
                            play_clicked = True
                            await page.wait_for_timeout(2000)
                        except Exception:
                            pass
                
                if not play_clicked:
                    paused = await page.evaluate('document.querySelector("video")?.paused ?? true')
                    if paused:
                        logger.info("尝试直接播放视频...")
                        await page.evaluate("document.querySelector('video').play();")
                        await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warn(f"自动播放失败: {str(e)[:100]}")
            
            await learning_loop(page, start_time, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class)
            logger.info("见面课已完成！", shift=True)
        else:
            # 有视频列表，逐个播放未完成的视频
            incomplete_videos = [v for v in videos if not v['completed']]
            
            if not incomplete_videos:
                logger.info("所有视频已完成！", shift=True)
            else:
                logger.info(f"发现 {len(incomplete_videos)} 个未完成的视频")
                
                for video_idx, video in enumerate(incomplete_videos):
                    if video_idx >= 50:
                        logger.warn(f"视频播放次数超限(50)，强制停止", shift=True)
                        break
                    logger.info(f"\n开始播放 ({video_idx+1}/{len(incomplete_videos)}): {video['title']} ({video['duration']})")
                    
                    # 点击视频项
                    try:
                        await video['element'].click(timeout=5000)
                        await page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.warn(f"点击视频失败: {str(e)[:50]}")
                        continue
                    
                    # 等待视频加载
                    await page.wait_for_selector("video", state="attached", timeout=15000)
                    start_time = time.time()
                    
                    # 关闭可能出现的弹窗（学前必读等）
                    await close_popup(page, logger)
                    
                    # 移动鼠标到视频区域，显示播放控制按钮
                    logger.info("移动鼠标到视频区域...")
                    await move_mouse_meeting_class(page)
                    await page.wait_for_timeout(1000)
                    
                    # 设置倍速（仅第一个视频设置）
                    if video == incomplete_videos[0] and hasattr(config, 'playbackRate') and config.playbackRate:
                        try:
                            await move_mouse_meeting_class(page)
                            await page.wait_for_timeout(500)
                            
                            speed_set = False
                            speed_box = page.locator(".speedBox").first
                            if await speed_box.count() > 0 and await speed_box.is_visible():
                                await speed_box.click(timeout=2000)
                                await page.wait_for_timeout(500)
                                rate_map = {1.0: ".speedTab05", 1.25: ".speedTab10", 1.5: ".speedTab15"}
                                rate_selector = rate_map.get(config.playbackRate, ".speedTab15")
                                rate_button = page.locator(rate_selector).first
                                if await rate_button.count() > 0:
                                    await rate_button.click(timeout=2000)
                                    logger.info(f"倍数已设置为 {config.playbackRate}x")
                                    speed_set = True
                                    await page.wait_for_timeout(500)
                            
                            if not speed_set:
                                rate_box = page.locator(".vjs-playback-rate").first
                                if await rate_box.count() > 0 and await rate_box.is_visible():
                                    await rate_box.click(timeout=2000)
                                    await page.wait_for_timeout(500)
                                    vjs_rate_map = {1.0: "1x", 1.25: "1.25x", 1.5: "1.5x"}
                                    rate_text = vjs_rate_map.get(config.playbackRate, f"{config.playbackRate}x")
                                    rate_item = rate_box.locator(f".vjs-menu-item:has-text('{rate_text}')").first
                                    if await rate_item.count() > 0:
                                        await rate_item.click(timeout=2000)
                                        logger.info(f"倍数已设置为 {config.playbackRate}x (video.js)")
                                        speed_set = True
                                        await page.wait_for_timeout(500)
                            
                            if not speed_set:
                                await page.evaluate(f"document.querySelector('video').playbackRate = {config.playbackRate};")
                                logger.info(f"通过JS设置倍速为 {config.playbackRate}x")
                        except Exception as e:
                            logger.warn(f"设置倍速失败: {str(e)[:50]}")
                    
                    # 尝试自动播放
                    try:
                        await move_mouse_meeting_class(page)
                        await page.wait_for_timeout(1000)
                        
                        play_clicked = False
                        big_play = page.locator(".bigPlayButton").first
                        if await big_play.count() > 0 and await big_play.is_visible():
                            try:
                                await big_play.click(timeout=5000)
                                logger.info("点击播放按钮(.bigPlayButton)成功")
                                play_clicked = True
                                await page.wait_for_timeout(2000)
                            except Exception:
                                pass
                        
                        if not play_clicked:
                            vjs_play = page.locator(".vjs-big-play-button").first
                            if await vjs_play.count() > 0 and await vjs_play.is_visible():
                                try:
                                    await vjs_play.click(timeout=5000)
                                    logger.info("点击播放按钮(.vjs-big-play-button)成功")
                                    play_clicked = True
                                    await page.wait_for_timeout(2000)
                                except Exception:
                                    pass
                        
                        if not play_clicked:
                            paused = await page.evaluate('document.querySelector("video")?.paused ?? true')
                            if paused:
                                logger.info("尝试直接播放视频...")
                                await page.evaluate("document.querySelector('video').play();")
                                await page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.warn(f"自动播放失败: {str(e)[:100]}")
                    
                    # 进入学习循环（90%阈值）
                    await learning_loop(page, start_time, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class)
                    
                    logger.info(f"视频 '{video['title']}' 已完成！", shift=True)
                
                logger.info("\n所有视频已完成！", shift=True)
    # 普通课程使用原有逻辑
    else:
        await page.wait_for_selector(".clearfix.video, .chapter-test", state="attached")
        
        # 等待页面加载并关闭可能出现的弹窗（学前必读等）
        await page.wait_for_timeout(2000)
        for _ in range(5):
            closed = await close_popup(page, logger)
            if closed:
                await page.wait_for_timeout(500)
            else:
                break
        
        # 【新增】扫描测验项
        logger.info("扫描课程列表中的测验项...")
        test_items = await scan_normal_class_tests(page, is_new_version)
        incomplete_tests = [t for t in test_items if not t['completed']]
        
        if incomplete_tests:
            logger.info(f"发现 {len(incomplete_tests)} 个未完成的测验")
        
        to_learn_class = await get_filtered_class(page, is_new_version, is_hike_class, is_national_wisdom)
        learning = True if len(to_learn_class) > 0 else False
        start_time = time.time()
        cur_index = 0
        loop_count = 0
        max_loop = 200
        test_retry_count = 0
        test_retry_limit = 5

        while True:
            loop_count += 1
            if loop_count > max_loop:
                logger.warn(f"循环次数超限({max_loop})，强制退出")
                break

            # 先尝试关闭弹窗（学前必读等）
            await close_popup(page, logger)
            
            # 【关键修改】：每次循环都重新获取最新的节点列表，防止 DOM 刷新导致旧节点 detached
            all_class = await get_filtered_class(page, is_new_version, is_hike_class, is_national_wisdom, include_all=not learning)
            
            if cur_index >= len(all_class):
                logger.info("本页课程列表已遍历完毕。")
                break

            course = all_class[cur_index]
            
            # 【新增】检测当前项是否是测验（通过class判断）
            try:
                course_class = await course.get_attribute('class')
            except Exception:
                logger.warn("获取课程class属性失败，跳过该课程")
                cur_index += 1
                continue
            is_test_item = 'chapter-test' in (course_class or '')
            
            if is_test_item:
                # 获取测验标题
                test_title_el = course.locator(".name").first
                test_title = await test_title_el.text_content() if await test_title_el.count() > 0 else "平时测试"
                logger.info(f"检测到测验项: {test_title.strip()}")
                
                # 检查是否已完成
                is_completed = await course.locator("b.finish").count() > 0
                if is_completed:
                    logger.info("测验已完成，跳过")
                    cur_index += 1
                    test_retry_count = 0
                    continue
                
                # 测验重试次数限制
                test_retry_count += 1
                if test_retry_count > test_retry_limit:
                    logger.warn(f"测验重试超限({test_retry_limit})，强制跳过")
                    test_retry_count = 0
                    cur_index += 1
                    continue
                
                # 设置响应监听器
                test_handler = TestResponseHandler()
                test_handler.setup_listener(page.context)
                
                # 监听新页面打开
                new_page = None
                original_page = page
                
                async def wait_for_new_page():
                    nonlocal new_page
                    try:
                        new_page = await page.context.wait_for_event("page", timeout=8000)
                        logger.info(f"检测到新页面打开: {new_page.url}")
                    except Exception:
                        logger.write_log("未检测到新页面\n")
                
                new_page_task = asyncio.create_task(wait_for_new_page())
                
                # 点击测验项
                logger.info("点击测验项...")
                try:
                    await course.click()
                except Exception as e:
                    logger.warn(f"点击测验项失败: {str(e)[:50]}，跳过")
                    test_handler.remove_listener()
                    cur_index += 1
                    continue
                
                # 等待新页面检测完成
                try:
                    await new_page_task
                except Exception:
                    pass
                
                # 确定工作页面
                work_page = new_page if new_page else page
                logger.info(f"工作页面URL: {work_page.url}")
                
                # 等待页面加载
                await work_page.wait_for_load_state("domcontentloaded")
                await work_page.wait_for_timeout(2000)
                
                # 等待题目数据
                logger.info("等待题目数据...")
                got_questions = await test_handler.wait_for_questions(timeout=20)
                
                if got_questions and test_handler.questions_data:
                    if test_handler.is_completed:
                        logger.info("测验已完成（API确认），跳过")
                        test_handler.remove_listener()
                        if new_page:
                            try:
                                await new_page.close()
                            except Exception:
                                pass
                        cur_index += 1
                        test_retry_count = 0
                        continue
                    else:
                        logger.info(f"开始处理测验，共 {len(test_handler.questions_data)} 题")
                        # 处理答题
                        await handle_test_page(work_page, test_handler.questions_data, auto_submit=True)
                        test_handler.remove_listener()
                        
                        # 如果打开了新页面，关闭它
                        if new_page:
                            try:
                                await new_page.close()
                                logger.info("已关闭测验页面")
                            except Exception:
                                pass
                        
                        # 恢复原始页面引用
                        page = original_page
                        
                        # 返回课程列表
                        logger.info("答题完成，返回课程列表...")
                        try:
                            await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                            logger.info("已返回课程列表页面")
                        except Exception as e:
                            logger.warn(f"返回课程列表失败: {str(e)[:50]}")
                        
                        await page.wait_for_timeout(2000)
                        
                        try:
                            await optimize_page(page, config, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class)
                            logger.info("页面优化完成")
                        except Exception as e:
                            logger.warn(f"页面优化失败: {str(e)[:50]}")
                        
                        # 答题成功后重置测验重试计数
                        test_retry_count = 0
                        
                        # 不增加 cur_index，直接 continue 重新扫描
                        # 这样可以确保课程状态是最新的
                        logger.info("继续处理下一个课程...")
                        continue
                else:
                    logger.warn("未能获取测验题目数据，尝试手动处理")
                    test_handler.remove_listener()
                    if new_page:
                        try:
                            await new_page.close()
                        except Exception:
                            pass
                    page = original_page
                    await page.goto(config.course_urls[0], wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                    await optimize_page(page, config, is_new_version, is_hike_class, is_national_wisdom)
                
                cur_index += 1
                continue
            
            # 测验重试计数复位（遇到正常视频项说明测验已处理完毕）
            test_retry_count = 0
            
            # 原有的视频处理逻辑
            try:
                await course.click()
            except Exception as e:
                logger.warn(f"点击课程失败: {str(e)[:50]}，跳过该课程")
                cur_index += 1
                continue

            await page.wait_for_selector(".current_play", state="attached")
            await page.wait_for_timeout(500)
            
            # 关闭可能出现的弹窗（学前必读等）
            await close_popup(page, logger)

            title = await get_lesson_name(page, is_hike_class, is_national_wisdom)
            logger.info(f"正在学习:{title}")
            page.set_default_timeout(10000)
            await page.wait_for_selector("video", state="attached")
            await page.evaluate(config.remove_pause)
            if learning:
                await learning_loop(page, start_time, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class)
            else:
                await review_loop(page, start_time, is_hike_class)

            try:
                if "current_play" in await all_class[cur_index].get_attribute('class'):
                    cur_index += 1
            except Exception as e:
                logger.warn(f"获取课程状态失败: {str(e)[:50]}，强制推进")
                cur_index += 1
            reachTimeLimit = await check_time_limit(page, start_time, all_class, title, is_hike_class)
            if reachTimeLimit:
                return


async def check_time_limit(page: Page, start_time, all_class, title, is_hike_class) -> bool:
    reachTimeLimit = False
    page.set_default_timeout(24 * 3600 * 1000)
    time_period = (time.time() - start_time) / 60
    if 0 < config.limitMaxTime <= time_period:
        logger.info(f"当前课程已达时限:{config.limitMaxTime}min", shift=True)
        logger.info("即将进入下门课程!")
        reachTimeLimit = True
    else:
        if is_hike_class:
            class_name = await all_class[-1].get_attribute('class')
            if "active" in class_name:
                logger.info("已学完本课程全部内容!", shift=True)
                print("==" * 10)
            else:
                logger.info(f"\"{title}\" 已完成!", shift=True)
                logger.info(f"本次课程已学习:{time_period:.1f} min")
        else:
            class_name = await all_class[-1].get_attribute('class')
            if "current_play" in class_name:
                logger.info("已学完本课程全部内容!", shift=True)
                print("==" * 10)
            else:
                logger.info(f"\"{title}\" 已完成!", shift=True)
                logger.info(f"本次课程已学习:{time_period:.1f} min")
    return reachTimeLimit


async def main():
    modules, tasks = [], []
    if config.enableAutoCaptcha:
        print("===== Install log =====")
        logger.info("正在检查依赖库...")
        modules = installer.start()
        logger.info("所有依赖库安装完成!")
    print("===== Runtime Log =====")
    async with async_playwright() as p:
        page, context = await init_page(p)
        # 进行登录
        if not config.username or not config.password:
            logger.info("请手动填写账号密码...")
        logger.info("正在等待登录完成...")
        # 先启动人机验证协程
        verify_task = asyncio.create_task(wait_for_verify(page, config, event_loop_verify))
        await auto_login(context, page, modules)

        # 启动协程任务
        video_optimize_task = asyncio.create_task(video_optimize(page, config))
        skip_ques_task = asyncio.create_task(skip_questions(page, event_loop_answer))
        play_video_task = asyncio.create_task(play_video(page))
        tasks.extend([verify_task, video_optimize_task, skip_ques_task, play_video_task])
        # 隐藏窗口
        if config.enableHideWindow:
            window = await hide_window(page)
            if window:
                activate_window_task = asyncio.create_task(activate_window(page))
                tasks.append(activate_window_task)

        # 任务监视器
        monitor_task = asyncio.create_task(task_monitor(tasks))
        # 遍历所有课程,加载网页
        for course_url in config.course_urls:
            print("==" * 10)
            is_new_version = "fusioncourseh5" in course_url
            # 判断课程类型
            is_hike_class = "hike.zhihuishu.com" in course_url  # 翻转课
            is_national_wisdom = "wisdom-mooc.zhihuishu.com" in course_url  # 全国智慧共享课
            is_meeting_class = "lc.zhihuishu.com" in course_url or "live.zhihuishu.com" in course_url  # 见面课
            
            logger.info("正在加载播放页...")
            await page.goto(course_url, wait_until="commit")
            await optimize_page(page, config, is_new_version, is_hike_class, is_national_wisdom, is_meeting_class)
            logger.info("页面优化完成!")
            # 获取课程标题
            if is_meeting_class:
                try:
                    title_selectors = [".course-name", ".source-name", ".title", "h1", "h2", ".header-title", ".meeting-title"]
                    course_title = "见面课"
                    for selector in title_selectors:
                        try:
                            title_selector = await page.wait_for_selector(selector, timeout=3000)
                            course_title = await title_selector.text_content()
                            if course_title and course_title.strip():
                                break
                        except TimeoutError:
                            continue
                    logger.info(f"当前课程:<<{course_title}>>，是见面课")
                except Exception as e:
                    logger.warn(f"见面课获取课程标题失败: {repr(e)}")
                    logger.info("当前课程:<<见面课>>")
            elif is_national_wisdom:
                try:
                    title_selectors = [".course-name", ".source-name", ".title", "h1", "h2", ".header-title"]
                    course_title = "全国智慧共享课"
                    for selector in title_selectors:
                        try:
                            title_selector = await page.wait_for_selector(selector, timeout=3000)
                            course_title = await title_selector.text_content()
                            if course_title and course_title.strip():
                                break
                        except TimeoutError:
                            continue
                    logger.info(f"当前课程:<<{course_title}>>，是全国智慧共享课")
                except Exception as e:
                    logger.warn(f"全国智慧共享课获取课程标题失败: {repr(e)}")
                    logger.info("当前课程:<<全国智慧共享课>>")
            elif not is_new_version and not is_hike_class:
                title_selector = await page.wait_for_selector(".source-name")
                course_title = await title_selector.text_content()
                logger.info(f"当前课程:<<{course_title}>>")
            if is_hike_class:
                try:
                    title_selectors = [".course-name", ".source-name", ".title", "h1", "h2"]
                    course_title = "智慧共享课"
                    for selector in title_selectors:
                        try:
                            title_selector = await page.wait_for_selector(selector, timeout=3000)
                            course_title = await title_selector.text_content()
                            if course_title and course_title.strip():
                                break
                        except TimeoutError:
                            continue
                    logger.info(f"当前课程:<<{course_title}>>， 是智慧共享课")
                except Exception as e:
                    logger.warn(f"智慧共享课获取课程标题失败: {repr(e)}")
                    logger.info("当前课程:<<智慧共享课>>")
            # 启动课程主循环
            await working_loop(page, is_new_version=is_new_version, is_hike_class=is_hike_class, is_national_wisdom=is_national_wisdom, is_meeting_class=is_meeting_class)
    print("==" * 10)
    logger.info("所有课程已学习完毕!")
    # 后台协程均为长期监听任务；课程完成后必须主动取消，否则程序不会退出。
    await cancel_background_tasks(tasks)
    await cancel_background_tasks([monitor_task])


if __name__ == "__main__":
    print("Github:CXRunfree All Rights Reserved.")
    logger = Logger()
    try:
        logger.info("程序启动中...")
        config = Config("configs.ini")
        if not config.course_urls:
            logger.info("未检测到有效网址或不支持此类网页,请检查配置文件!")
            time.sleep(2)
            sys.exit(-1)
        asyncio.run(main())
    except TargetClosedError as e:
        logger.write_log(traceback.format_exc())
        if "BrowserType.launch" in repr(e):
            logger.error("浏览器启动失败,请尝试重新启动!")
            logger.info("如果仍然无法启动,请修改配置文件并使用Chrome浏览器")
        else:
            logger.error("浏览器被关闭,程序退出.")
    except Exception as e:
        logger.error(repr(e), shift=True)
        logger.write_log(traceback.format_exc())
        if isinstance(e, KeyError):
            logger.error(f"配置文件错误!")
        elif isinstance(e, FileNotFoundError):
            logger.error(f"依赖文件缺失: {e.filename},请重新安装程序!")
        elif isinstance(e, UnicodeDecodeError):
            logger.error("配置文件编码错误,保存时请选择UTF-8或GBK编码!")
        else:
            logger.error("系统出错,请检查后重新启动!")
    finally:
        logger.save()
        try:
            if sys.stdin and sys.stdin.isatty():
                try:
                    input("程序已结束,按Enter退出...")
                except (OSError, ValueError):
                    pass
        except EOFError:
            pass
