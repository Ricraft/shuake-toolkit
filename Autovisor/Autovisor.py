# encoding=utf-8
import argparse
import asyncio
import json
import os
import time
import traceback
import sys
import types
from typing import Optional

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
from modules.diagnostics import RateLimitedDiagnostics
from modules.configs import Config
from modules.course_queue import run_course_queue
from modules.course_session import (
    CourseAuthenticationError,
    ensure_course_authenticated,
    wait_for_authenticated_selector,
)
from modules.hike_course_flow import run_hike_course
from modules.login_flow import login_to_zhihuishu
from modules.meeting_course_flow import run_meeting_course
from modules.national_course_flow import run_national_course
from modules.normal_course_flow import run_normal_course
from modules.progress import get_course_progress, show_course_progress
from modules.utils import get_video_attr, hide_window, \
    get_browser_window, bring_console_to_front, load_cookies, \
    is_playwright_window
from modules.slider import slider_verify
from modules.async_utils import background_task_scope
from modules.tasks import (
    handle_test_page,
    skip_questions,
    wait_for_question_resolution,
    wait_for_verification_resolution,
    wait_for_verify,
)
from modules.test_capture import TestResponseHandler
from modules.video_tasks import (
    activate_window,
    play_video,
    task_monitor,
    video_optimize,
)
from modules import installer

# 获取全局事件循环
event_loop_verify = asyncio.Event()
event_loop_answer = asyncio.Event()

async def init_page(p: Playwright) -> tuple[Page, BrowserContext]:
    driver = "msedge" if config.driver == "edge" else config.driver
    logger.info(f"正在启动{config.driver}浏览器...")
    account_offset = max((config.account_id or 1) - 1, 0)
    browser = await p.chromium.launch(
        channel=driver,
        headless=False,
        executable_path=config.exe_path if config.exe_path else None,
        args=[
            f'--window-size={config.windowWidth},{config.windowHeight}',
            f'--window-position={100 + account_offset * 80},{100 + account_offset * 40}',
        ],
    )
    context = await browser.new_context()
    # 加载 Cookies
    os.makedirs(os.path.dirname(config.cookies_file) or ".", exist_ok=True)
    cookies = load_cookies(config.cookies_file)
    if cookies:
        await context.add_cookies(cookies)
        logger.info("已加载 Cookies!")
    else:
        logger.info("未找到 Cookies,将跳转至登录页.")
    page = await context.new_page()
    logger.write_log(f"{config.driver}浏览器启动完成.\n")
    page.set_default_timeout(30_000)

    return page, context

async def auto_login(context: BrowserContext, page: Page, modules=None):
    return await login_to_zhihuishu(
        context,
        page,
        config,
        logger,
        modules=modules,
        cookie_path=config.cookies_file,
        slider_handler=slider_verify,
    )


async def close_popup(page: Page, logger_instance=None, diagnostics=None):
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
                    except TargetClosedError:
                        raise
                    except Exception:
                        try:
                            await close_btn.click(timeout=2000, force=True)
                            if logger_instance:
                                context_name = "iframe" if context != page else "主页面"
                                logger_instance.info(f"已关闭弹窗 (强制点击, {context_name}, {selector})")
                            await page.wait_for_timeout(500)
                            return True
                        except TargetClosedError:
                            raise
                        except TimeoutError:
                            continue
                        except Exception as error:
                            if diagnostics:
                                diagnostics.warn(
                                    "popup-force-click",
                                    "关闭课程弹窗失败",
                                    error,
                                )
                            continue
            except TargetClosedError:
                raise
            except TimeoutError:
                continue
            except Exception as error:
                if diagnostics:
                    diagnostics.warn(
                        "popup-selector",
                        "扫描课程弹窗失败",
                        error,
                    )
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
        except TargetClosedError:
            raise
        except TimeoutError:
            pass
        except Exception as error:
            if diagnostics:
                diagnostics.warn(
                    "popup-dialog-read",
                    "关闭课程说明弹窗失败",
                    error,
                )
    
    return False


async def learning_loop(
    page: Page,
    start_time,
    is_new_version=False,
    is_hike_class=False,
    is_national_wisdom=False,
    is_meeting_class=False,
    *,
    clock=time.time,
    sleep_func=asyncio.sleep,
    minimum_watch_seconds=10,
    stuck_timeout=60,
    max_recovery_attempts=3,
    health_check_interval=30,
    auth_check_interval=4,
    position_check_interval=10,
    diagnostic_clock=time.monotonic,
) -> bool:
    diagnostics = RateLimitedDiagnostics(logger, clock=diagnostic_clock)
    auth_check_interval = max(1, int(auth_check_interval))
    await ensure_course_authenticated(
        page,
        "视频播放启动时登录状态失效",
    )
    # 见面课完成阈值：80%（签到进度达到80%即完成签到）
    completion_threshold = 0.8 if is_meeting_class else (0.98 if is_national_wisdom else 1.0)

    async def read_authenticated_progress(message):
        await ensure_course_authenticated(page, message)
        try:
            progress = await get_course_progress(
                page,
                is_new_version,
                is_hike_class,
                is_national_wisdom,
                is_meeting_class,
                completion_threshold,
                diagnostics=diagnostics,
            )
        except (CourseAuthenticationError, TargetClosedError):
            raise
        except Exception as exc:
            try:
                await ensure_course_authenticated(page, message)
            except CourseAuthenticationError as auth_error:
                raise auth_error from exc
            raise
        await ensure_course_authenticated(page, message)
        return progress
    
    # 对于全国智慧共享课和见面课，额外等待视频真正开始播放
    if is_national_wisdom or is_meeting_class:
        await page.wait_for_timeout(2000)
        await ensure_course_authenticated(
            page,
            "等待视频开始播放时登录状态失效",
        )
        # 等待视频开始播放（paused 变为 false）
        for _ in range(10):
            try:
                await ensure_course_authenticated(
                    page,
                    "等待视频开始播放时登录状态失效",
                )
                paused = await page.evaluate('document.querySelector("video")?.paused ?? true')
                if not paused:
                    break
            except CourseAuthenticationError:
                raise
            except TargetClosedError:
                raise
            except Exception as error:
                try:
                    await ensure_course_authenticated(
                        page,
                        "等待视频开始播放时登录状态失效",
                    )
                except CourseAuthenticationError as auth_error:
                    raise auth_error from error
                diagnostics.warn(
                    "initial-video-state",
                    "等待视频开始播放时读取状态失败",
                    error,
                )
            await page.wait_for_timeout(500)
    
    await page.wait_for_timeout(1000)
    cur_time = await read_authenticated_progress(
        "读取视频初始进度时登录状态失效",
    )
    
    # 如果一开始就显示100%，对于智慧共享课和见面课需要额外等待和验证
    if cur_time == "100%" and (is_hike_class or is_national_wisdom or is_meeting_class):
        await page.wait_for_timeout(5000)
        cur_time = await read_authenticated_progress(
            "复核视频完成状态时登录状态失效",
        )
        
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
            except (CourseAuthenticationError, TargetClosedError):
                raise
            except Exception as e:
                try:
                    await ensure_course_authenticated(
                        page,
                        "修复视频进度状态时登录状态失效",
                    )
                except CourseAuthenticationError as auth_error:
                    raise auth_error from e
                logger.warn(f"重置视频失败: {repr(e)}", shift=True)
    
    min_duration = clock() + minimum_watch_seconds
    loop_counter = 0
    last_progress = cur_time
    stuck_since = clock()
    last_video_position = None
    recovery_attempts = 0
    while cur_time != "100%" or clock() < min_duration:
        try:
            loop_counter += 1

            if loop_counter == 1 or loop_counter % auth_check_interval == 0:
                await ensure_course_authenticated(
                    page,
                    "视频播放期间被重定向到首页或登录页",
                )
            
            limit_time = config.limitMaxTime
            time_period = (clock() - start_time) / 60
            if 0 < limit_time <= time_period:
                logger.info(f"已达学习时限 {limit_time}min，退出学习循环", shift=True)
                break

            if loop_counter % position_check_interval == 0:
                try:
                    position = await page.evaluate(
                        'document.querySelector("video")?.currentTime ?? null'
                    )
                    if isinstance(position, (int, float)) and (
                        last_video_position is None
                        or position > last_video_position + 0.5
                    ):
                        stuck_since = clock()
                    if isinstance(position, (int, float)):
                        last_video_position = position
                except TargetClosedError:
                    raise
                except Exception as error:
                    diagnostics.warn(
                        "video-position",
                        "读取视频播放位置失败",
                        error,
                    )
            
            # P0-1: 每30次循环检查一次视频加载状态
            if loop_counter % health_check_interval == 0:
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
                    if recovery_attempts >= max_recovery_attempts:
                        logger.error(
                            f"视频加载持续失败，已达到 {max_recovery_attempts} 次恢复上限",
                            shift=True,
                        )
                        return False
                    recovery_attempts += 1
                    logger.warn(f"视频加载错误(code={video_state['error']})，刷新页面重试", shift=True)
                    try:
                        await page.reload(wait_until="domcontentloaded")
                        await wait_for_authenticated_selector(
                            page,
                            "video",
                            "恢复视频页面时登录状态失效",
                            timeout=15000,
                        )
                        await page.evaluate(config.remove_pause)
                        last_progress = "0%"
                        last_video_position = None
                        stuck_since = clock()
                        cur_time = "0%"
                        logger.info(
                            f"页面已刷新，继续播放（恢复 {recovery_attempts}/{max_recovery_attempts}）",
                            shift=True,
                        )
                        continue
                    except (CourseAuthenticationError, TargetClosedError):
                        raise
                    except Exception as reload_e:
                        logger.error(f"刷新页面失败: {str(reload_e)[:50]}", shift=True)
                        break
            
            cur_time = await read_authenticated_progress(
                "读取视频播放进度时登录状态失效",
            )
            
            # 【修复】检测进度停滞：如果连续60秒进度没有变化，刷新页面
            if cur_time == last_progress:
                if clock() - stuck_since > stuck_timeout:
                    if recovery_attempts >= max_recovery_attempts:
                        logger.error(
                            f"进度连续停滞，已达到 {max_recovery_attempts} 次恢复上限",
                            shift=True,
                        )
                        return False
                    recovery_attempts += 1
                    logger.warn(
                        f"进度已停滞超过{stuck_timeout}秒({cur_time})，刷新页面重试",
                        shift=True,
                    )
                    try:
                        await page.reload(wait_until="domcontentloaded")
                        await wait_for_authenticated_selector(
                            page,
                            "video",
                            "恢复视频页面时登录状态失效",
                            timeout=15000,
                        )
                        await page.evaluate(config.remove_pause)
                        last_progress = "0%"
                        last_video_position = None
                        stuck_since = clock()
                        cur_time = "0%"
                        logger.info(
                            f"页面已刷新，继续播放（恢复 {recovery_attempts}/{max_recovery_attempts}）",
                            shift=True,
                        )
                        continue
                    except (CourseAuthenticationError, TargetClosedError):
                        raise
                    except Exception as reload_e:
                        logger.error(f"刷新页面失败: {str(reload_e)[:50]}", shift=True)
                        break
            else:
                last_progress = cur_time
                stuck_since = clock()
            
            # 见面课特殊提示
            if is_meeting_class and cur_time == "100%":
                print()
                logger.info("见面课签到进度已达80%，自动完成签到！", shift=True)
            
            show_course_progress(desc="完成进度:", cur_time=cur_time, is_meeting_class=is_meeting_class)
            
            # 检测并关闭弹窗
            await close_popup(page, logger, diagnostics=diagnostics)
            
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
            except TargetClosedError:
                raise
            except Exception as error:
                diagnostics.warn(
                    "resume-video",
                    "检测或恢复视频播放状态失败",
                    error,
                )
            
            await sleep_func(0.5)
        except TimeoutError as e:
            try:
                await ensure_course_authenticated(
                    page,
                    "视频播放等待超时时登录状态失效",
                )
            except CourseAuthenticationError as auth_error:
                raise auth_error from e
            if await page.query_selector(".yidun_modal__title"):
                resolved = await wait_for_verification_resolution(
                    page,
                    event_loop_verify,
                    logger_instance=logger,
                )
                if not resolved:
                    raise RuntimeError("安全验证等待超时，视频学习已停止")
            elif await page.query_selector(".topic-title"):
                resolved = await wait_for_question_resolution(
                    page,
                    event_loop_answer,
                    (".topic-title",),
                    logger_instance=logger,
                )
                if not resolved:
                    raise RuntimeError("随堂题等待超时，视频学习已停止")
            elif is_hike_class and await page.query_selector(".question-info"):
                resolved = await wait_for_question_resolution(
                    page,
                    event_loop_answer,
                    (".question-info",),
                    logger_instance=logger,
                )
                if not resolved:
                    raise RuntimeError("翻转课随堂题等待超时，视频学习已停止")
            else:
                logger.warn(repr(e))

    await ensure_course_authenticated(
        page,
        "视频播放结束时登录状态失效",
    )
    completed = cur_time == "100%"
    if not completed:
        logger.warn(f"视频未确认完成，最终进度: {cur_time}", shift=True)
    return completed


async def review_loop(page: Page, start_time, is_hike_class=False):
    await ensure_course_authenticated(page, "复习模式启动时登录状态失效")
    total_time = await get_video_attr(page, "duration")
    await page.evaluate(config.reset_curtime)  # 重置视频播放时间
    while True:
        limit_time = config.limitMaxTime
        cur_time = await get_video_attr(page, "currentTime")
        await ensure_course_authenticated(page, "复习模式播放期间登录状态失效")
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
                resolved = await wait_for_verification_resolution(
                    page,
                    event_loop_verify,
                    logger_instance=logger,
                )
                if not resolved:
                    raise RuntimeError("安全验证等待超时，复习模式已停止")
            elif await page.query_selector(".topic-title"):
                resolved = await wait_for_question_resolution(
                    page,
                    event_loop_answer,
                    (".topic-title",),
                    logger_instance=logger,
                )
                if not resolved:
                    raise RuntimeError("随堂题等待超时，复习模式已停止")
            else:
                logger.warn(repr(e))
    await ensure_course_authenticated(page, "复习模式结束时登录状态失效")


async def working_loop(page: Page, is_new_version=False, is_hike_class=False, is_national_wisdom=False, is_meeting_class=False, course_url=None):
    # 智慧共享课（翻转课）使用深度扫描
    if is_hike_class:
        return await run_hike_course(
            page,
            config,
            logger,
            close_popup=close_popup,
            learning_loop=learning_loop,
            course_url=course_url,
            is_new_version=is_new_version,
            is_national_wisdom=is_national_wisdom,
        )
    
    # 全国智慧共享课
    elif is_national_wisdom:
        return await run_national_course(
            page,
            config,
            logger,
            close_popup=close_popup,
            learning_loop=learning_loop,
            handler_factory=TestResponseHandler,
            answer_handler=handle_test_page,
            course_url=course_url,
            is_new_version=is_new_version,
        )
    # 见面课使用特殊逻辑（80%完成阈值）
    elif is_meeting_class:
        return await run_meeting_course(
            page,
            config,
            logger,
            close_popup=close_popup,
            learning_loop=learning_loop,
            is_new_version=is_new_version,
            is_hike_class=is_hike_class,
            is_national_wisdom=is_national_wisdom,
        )
    # 普通课程
    else:
        return await run_normal_course(
            page,
            config,
            logger,
            close_popup=close_popup,
            learning_loop=learning_loop,
            review_loop=review_loop,
            handler_factory=TestResponseHandler,
            answer_handler=handle_test_page,
            course_url=course_url,
            is_new_version=is_new_version,
        )




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
        async with background_task_scope(tasks):
            # 进行登录
            if not config.username or not config.password:
                logger.info("请手动填写账号密码...")
            logger.info("正在等待登录完成...")
            verify_task = asyncio.create_task(
                wait_for_verify(page, config, event_loop_verify)
            )
            tasks.append(verify_task)
            if not await auto_login(context, page, modules):
                raise RuntimeError("登录未完成，已停止刷课任务")

            video_optimize_task = asyncio.create_task(video_optimize(page, config))
            skip_ques_task = asyncio.create_task(skip_questions(page, event_loop_answer))
            play_video_task = asyncio.create_task(play_video(page, config))
            tasks.extend([video_optimize_task, skip_ques_task, play_video_task])
            if config.enableHideWindow:
                window = await hide_window(page)
                if window:
                    tasks.append(asyncio.create_task(activate_window(page)))

            monitored_tasks = list(tasks)
            tasks.append(asyncio.create_task(task_monitor(monitored_tasks)))
            summary = await run_course_queue(
                page,
                config,
                logger,
                course_worker=working_loop,
            )
    print("==" * 10)
    if summary.failed:
        raise RuntimeError(
            f"课程队列部分失败: 成功 {summary.completed} 门，失败 {summary.failed} 门"
        )
    logger.info("所有课程已学习完毕!")


def run(
    config_path: str = "configs.ini",
    account_id: Optional[int] = None,
    course_url: Optional[str] = None,
) -> int:
    """运行一个账号；单账号入口和多账号子进程共同调用。"""
    global config, logger, event_loop_verify, event_loop_answer

    logger = Logger()
    # 每次显式运行都使用独立文件，避免同一解释器重复运行时混入旧会话。
    logger.configure(account_id, force=True, clear=True)
    event_loop_verify = asyncio.Event()
    event_loop_answer = asyncio.Event()
    exit_code = 0

    try:
        logger.info("程序启动中...")
        config = Config(config_path, account_id=account_id)
        if course_url and course_url.strip():
            config.course_urls = [course_url.strip()]
        if not config.course_urls:
            logger.info("未检测到有效网址或不支持此类网页,请检查配置文件!")
            return 2
        asyncio.run(main())
    except TargetClosedError as e:
        exit_code = 1
        logger.write_log(traceback.format_exc())
        if "BrowserType.launch" in repr(e):
            logger.error("浏览器启动失败,请尝试重新启动!")
            logger.info("如果仍然无法启动,请修改配置文件并使用Chrome浏览器")
        else:
            logger.error("浏览器在课程完成前被关闭,任务已中断.")
    except Exception as e:
        exit_code = 1
        logger.error(repr(e), shift=True)
        logger.write_log(traceback.format_exc())
        if isinstance(e, KeyError):
            logger.error("配置文件错误!")
        elif isinstance(e, FileNotFoundError):
            missing_path = e.filename or str(e)
            logger.error(f"依赖文件缺失: {missing_path},请重新安装程序!")
        elif isinstance(e, UnicodeDecodeError):
            logger.error("配置文件编码错误,保存时请选择UTF-8或GBK编码!")
        elif isinstance(e, CourseAuthenticationError):
            logger.error("登录状态已失效，请重新登录后再启动任务!")
        else:
            logger.error("系统出错,请检查后重新启动!")
    finally:
        logger.save()

    return exit_code


def _parse_args():
    parser = argparse.ArgumentParser(description="Autovisor 单账号运行入口")
    parser.add_argument("--config", "-c", default="configs.ini", help="配置文件路径")
    parser.add_argument("--account-id", type=int, default=None, help="运行指定编号账号")
    parser.add_argument("--course-url", default=None, help="仅运行指定课程网址")
    return parser.parse_args()


if __name__ == "__main__":
    print("Github:CXRunfree All Rights Reserved.")
    args = _parse_args()
    result = run(
        config_path=args.config,
        account_id=args.account_id,
        course_url=args.course_url,
    )
    try:
        if sys.stdin and sys.stdin.isatty():
            try:
                input("程序已结束,按Enter退出...")
            except (OSError, ValueError):
                pass
    except EOFError:
        pass
    raise SystemExit(result)
