# encoding=utf-8
"""
刷题模式 — 由用户手动导航到智慧树课程，自动监听 doHomework API
自动调用题库答题并提交，提交后返回我的课堂页等待用户下一步操作

流程：
  自动登录 → 点击我的学堂 → 设置 doHomework 监听器
  → 等待用户手动点击测验 → 监听 doHomework 响应
  → 批量查询题库 → 逐题自动作答 → 自动提交
  → 返回我的课堂页 → 等待用户下一个操作（循环）
"""

import sys
import os
import asyncio
import time
import traceback

if sys.stdout and sys.stdout.encoding and "gbk" in sys.stdout.encoding.lower():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and sys.stderr.encoding and "gbk" in sys.stderr.encoding.lower():
    import io
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright, Playwright, Page, BrowserContext

from modules.logger import Logger
from modules.configs import Config
from modules.login_flow import login_to_zhihuishu
from modules.course_portal import navigate_to_my_course as navigate_course_portal
from modules.tasks import handle_test_page
from modules.test_capture import TestResponseHandler
from modules.utils import (
    load_cookies,
)
from modules.slider import slider_verify
from modules import installer

logger = Logger()

async def init_page(p: Playwright, config: Config):
    """初始化浏览器页面"""
    driver = "msedge" if config.driver == "edge" else config.driver
    logger.info(f"正在启动 {config.driver} 浏览器...")
    browser = await p.chromium.launch(
        channel=driver,
        headless=False,
        executable_path=config.exe_path if config.exe_path else None,
        args=[
            "--window-size=1400,900",
            "--window-position=100,100",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1400, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    cookies = load_cookies("res/cookies.json")
    if cookies:
        await context.add_cookies(cookies)
        logger.info("已加载 Cookies，可免密登录")
    else:
        logger.info("未找到 Cookies，将跳转至登录页")
    page = await context.new_page()
    logger.write_log(f"{config.driver} 浏览器启动完成.\n")
    
    page.set_default_timeout(30_000)
    return browser, page, context


async def auto_login(context: BrowserContext, page: Page, config: Config, modules=None):
    """Run the same bounded login flow used by the normal course worker."""
    return await login_to_zhihuishu(
        context,
        page,
        config,
        logger,
        modules=modules,
        cookie_path="res/cookies.json",
        slider_handler=slider_verify,
    )


async def navigate_to_my_course(page: Page, config: Config):
    """Compatibility wrapper around the shared, verified portal navigator."""
    return await navigate_course_portal(page, logger)


def _find_test_page(context: BrowserContext, main_page: Page):
    """查找测验所在的页面（优先 exam/doHomework，排除主页面）"""
    candidates = []
    for p in context.pages:
        if p == main_page:
            continue
        try:
            url = p.url
            if "exam" in url or "doHomework" in url or "lookHomework" in url:
                candidates.append((0, p))
            elif "zhihuishu" in url:
                candidates.append((1, p))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[0])
    if candidates:
        logger.info(f"找到测验页面: {candidates[0][1].url[:80]}")
        return candidates[0][1]
    return None


async def _close_extra_pages(context: BrowserContext, main_page: Page):
    """关闭除主页面外的多余页面"""
    all_pages = context.pages
    for p in all_pages:
        if p != main_page:
            try:
                await p.close()
                logger.info("已关闭测验标签页")
            except Exception:
                pass


async def _return_to_my_course(page: Page, context: BrowserContext, main_page: Page, config: Config):
    """提交后返回我的课堂页（关闭测验标签，保留主页面）"""
    await _close_extra_pages(context, main_page)
    try:
        if await navigate_to_my_course(main_page, config):
            return True
        logger.warn("未能返回我的学堂，等待后续恢复")
        return False
    except Exception as e:
        logger.warn(f"返回我的课堂页时出错: {str(e)[:50]}")
        try:
            return await navigate_to_my_course(main_page, config)
        except Exception:
            return False


async def practice_loop(page: Page, context: BrowserContext, config: Config):
    """
    刷题主循环

    1. 导航到我的课堂页
    2. 设置 context 级别的 doHomework 监听器（跨页面保持）
    3. 等待用户手动点击测验
    4. doHomework 触发 → 自动答题提交
    5. 返回我的课堂页 → 继续等待用户操作
    """
    if not await navigate_to_my_course(page, config):
        raise RuntimeError("未能进入我的学堂，刷题模式已停止")
    await page.wait_for_timeout(1500)

    main_page = page
    test_handler = TestResponseHandler()
    test_handler.setup_listener(context, clear_data=True)

    logger.info("=" * 60)
    logger.info("刷题模式已启动")
    logger.info("请在我的课堂页点击课程进入，然后点击章节测验 / 平时测试")
    logger.info("系统将自动监听 doHomework API")
    logger.info("检测到测验后 → 自动调用题库答题 → 自动提交 → 返回我的课堂页")
    logger.info("=" * 60)

    idle_report_counter = 0

    while True:
        try:
            await main_page.title()
        except Exception:
            logger.info("浏览器页面已关闭，刷题模式结束")
            break

        has_questions = await test_handler.wait_for_questions(timeout=10)

        if has_questions and test_handler.questions_data:
            total = len(test_handler.questions_data)
            logger.info(f"\n{'=' * 60}")
            logger.info(f"检测到 doHomework，共 {total} 道题目")
            logger.info(f"{'=' * 60}")

            logger.info("等待测验页面打开...")
            test_page = None
            for attempt in range(20):
                test_page = _find_test_page(context, main_page)
                if test_page:
                    break
                await asyncio.sleep(2)
            work_page = test_page if test_page else main_page
            if not test_page:
                logger.warn("未找到测验页面，将在当前页面尝试答题")

            logger.info("等待找到题目页（/dohomework/ 子路由）...")
            for attempt in range(30):
                work_page = None
                for p in context.pages:
                    try:
                        u = p.url
                        if "dohomework" in u or "lookHomework" in u:
                            work_page = p
                            logger.info(f"[OK] 找到题目页: {u[:100]}")
                            break
                    except Exception:
                        continue
                if work_page:
                    break
                if attempt % 3 == 0:
                    logger.info(f"  尚未找到题目页... ({attempt*2}s)")
                    for p in context.pages:
                        try:
                            logger.info(f"    页面: {p.url[:100]}")
                        except Exception:
                            pass
                await asyncio.sleep(2)
            else:
                work_page = test_page or main_page
                logger.warn("未找到题目页 URL，使用当前页面尝试...")

            logger.info("等待试卷 DOM 渲染...")
            exam_rendered = False
            for attempt in range(30):
                try:
                    await work_page.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass
                for sel in ('.examPaper_box', '.examPaper_subject', '.subject_node', '.nodeLab'):
                    try:
                        if await work_page.locator(sel).first.count() > 0:
                            logger.info(f"[OK] 试卷已渲染: {sel} (等待 {attempt*2}s)")
                            exam_rendered = True
                            break
                    except Exception:
                        continue
                if exam_rendered:
                    break
                if attempt % 5 == 0 and attempt > 0:
                    try:
                        logger.info(f"试卷尚未渲染 ({attempt*2}s)，当前URL: {work_page.url[:100]}")
                    except Exception:
                        pass
                await asyncio.sleep(2)

            if not exam_rendered:
                logger.error("[ERROR] 试卷超时未渲染，跳过本题")
                test_handler.remove_listener()
                test_handler = TestResponseHandler()
                test_handler.setup_listener(context, clear_data=True)
                await _return_to_my_course(main_page, context, main_page, config)
                continue

            try:
                success = await handle_test_page(
                    work_page,
                    test_handler.questions_data,
                    auto_submit=True,
                )
            except Exception as e:
                logger.error(f"答题过程异常: {str(e)[:100]}")
                logger.write_log(traceback.format_exc())
                success = False

            test_handler.remove_listener()
            test_handler = TestResponseHandler()
            test_handler.setup_listener(context, clear_data=True)

            if success:
                logger.info("答题并提交成功！")
            else:
                logger.warn("答题提交可能未完全成功")

            returned = await _return_to_my_course(
                main_page, context, main_page, config
            )

            logger.info(f"\n{'=' * 60}")
            if returned:
                logger.info("已返回我的课堂页，您可以点击下一个测验")
                logger.info("系统将持续监听 doHomework API...")
            else:
                logger.warn("未能返回我的课堂页，请检查网络或登录状态")
            logger.info(f"{'=' * 60}")

            idle_report_counter = 0

        else:
            idle_report_counter += 1
            if idle_report_counter % 6 == 0:
                try:
                    logger.info(f"等待用户操作... 当前页面: {main_page.url[:70]}")
                except Exception:
                    pass

            try:
                if "passport" in main_page.url and "login" in main_page.url:
                    logger.warn("检测到登录页，session 已过期，正在重新登录...")
                    if not await auto_login(context, main_page, config):
                        logger.error("重新登录失败，保持当前页面等待下一次重试")
                        await asyncio.sleep(5)
                        continue
                    if not await navigate_to_my_course(main_page, config):
                        logger.error("重新登录成功，但未能进入我的学堂")
                        await asyncio.sleep(5)
                        continue
                    test_handler.remove_listener()
                    test_handler = TestResponseHandler()
                    test_handler.setup_listener(context, clear_data=True)
                    logger.info("已重新登录并返回我的课堂页")
            except Exception:
                pass

        await asyncio.sleep(1)


async def main():
    """刷题模式主入口"""
    print("=" * 60)
    print("智慧树刷题模式 — 自动登录后进入我的课堂，等待用户操作")
    print("用户点击测验后系统自动答题并提交，然后返回我的课堂页")
    print("=" * 60)

    config = Config("configs.ini")

    modules = []
    if config.enableAutoCaptcha:
        logger.info("正在检查依赖库...")
        modules = installer.start()
        logger.info("依赖库检查完成")

    async with async_playwright() as p:
        browser, page, context = await init_page(p, config)

        if not config.username or not config.password:
            logger.info("请手动填写账号密码...")
        logger.info("正在登录...")
        if not await auto_login(context, page, config, modules):
            raise RuntimeError("登录未完成，刷题模式已停止")

        await practice_loop(page, context, config)
