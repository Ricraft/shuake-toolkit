#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智慧树刷课流程调试脚本
支持自动登录、页面识别和HTML保存
"""

import asyncio
import sys
import os
import re

# 修复Windows终端编码问题
if os.name == 'nt':
    # 方法1: 设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # 方法2: 重新设置stdout/stderr编码
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

# 添加模块路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Autovisor'))

from playwright.async_api import async_playwright, Page, BrowserContext
from modules.logger import Logger
from modules.login_selectors import (
    LOGIN_AGREEMENT_CHECKBOX,
    LOGIN_PANEL,
    LOGIN_SUBMIT,
    LOGIN_URL,
    PASSWORD_INPUT,
    USERNAME_INPUT,
)

logger = Logger()

# 测试账号配置 — 从环境变量读取，不要硬编码
# 设置方式: set ZHS_DEBUG_USER=your_phone && set ZHS_DEBUG_PASS=your_password
TEST_USERNAME = os.environ.get("ZHS_DEBUG_USER", "")
TEST_PASSWORD = os.environ.get("ZHS_DEBUG_PASS", "")
MY_COURSES_URL = "https://onlineweb.zhihuishu.com/onlinestuh5"

# 页面类型识别模式
PAGE_PATTERNS = {
    '普通课': r'(video|learn|study)\.zhihuishu\.com|/video/|/learn/',
    '智慧共享课': r'share\.zhihuishu\.com|national',
    '见面课': r'meeting\.zhihuishu\.com|/meeting/',
    '测验': r'/exam/|/test/|doHomework',
    '课程列表': r'onlinestuh5|studentIndex',
}

saved_pages = set()

def print_info(msg):
    """打印信息"""
    print("[INFO] {}".format(msg))

def print_success(msg):
    """打印成功信息"""
    print("[SUCCESS] {}".format(msg))

def print_warning(msg):
    """打印警告信息"""
    print("[WARN] {}".format(msg))

def print_error(msg):
    """打印错误信息"""
    print("[ERROR] {}".format(msg))

async def save_page_html(page: Page, custom_name: str = None):
    """保存页面HTML到文件"""
    html_content = await page.content()
    url = page.url
    
    if custom_name:
        filename = "{}.html".format(custom_name)
    else:
        page_type = identify_page_type(url)
        course_id = re.search(r'courseId=(\d+)', url)
        if course_id:
            filename = "{}_{}.html".format(page_type, course_id.group(1))
        else:
            lesson_id = re.search(r'lessonId=(\d+)', url)
            if lesson_id:
                filename = "{}_lesson_{}.html".format(page_type, lesson_id.group(1))
            else:
                filename = "{}_{}.html".format(page_type, hash(url) % 10000)
    
    save_path = os.path.join("Autovisor", "page_snapshots", filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    success_msg = "页面已保存: {}".format(save_path)
    print_success(success_msg)
    logger.info(success_msg)
    return save_path

def identify_page_type(url: str) -> str:
    """根据URL识别页面类型"""
    url_lower = url.lower()
    for page_type, pattern in PAGE_PATTERNS.items():
        if re.search(pattern, url_lower):
            return page_type
    return '其他页面'

async def init_browser(p):
    """初始化浏览器（干净模式）"""
    print_info("正在启动Chrome浏览器...")
    browser = await p.chromium.launch(
        channel="chrome",
        headless=False,
        args=[
            '--window-size=1600,900',
            '--window-position=100,100',
        ],
    )
    context = await browser.new_context(viewport={'width': 1600, 'height': 900})
    page = await context.new_page()
    page.set_default_timeout(60000)
    print_success("浏览器初始化完成")
    return page, context, browser

async def test_login(page: Page, username: str, password: str):
    """测试登录流程"""
    print_info("正在访问登录页面: {}".format(LOGIN_URL))
    await page.goto(LOGIN_URL, wait_until="commit")
    await page.wait_for_selector(LOGIN_PANEL, state='attached')
    
    # 填写账号密码
    await page.wait_for_selector(USERNAME_INPUT, state="attached")
    await page.wait_for_selector(PASSWORD_INPUT, state="attached")
    await page.locator(USERNAME_INPUT).fill(username)
    await page.locator(PASSWORD_INPUT).fill(password)
    print_info("已填写账号: {}****{}".format(username[:4], username[-4:]))

    agreement = page.locator(LOGIN_AGREEMENT_CHECKBOX)
    agreement_count = await agreement.count()
    if agreement_count > 1:
        raise RuntimeError("登录协议勾选框不唯一")
    if agreement_count == 1 and not await agreement.is_checked():
        await agreement.check(force=True)
    
    # 点击登录
    await page.wait_for_selector(LOGIN_SUBMIT, state="attached")
    await page.wait_for_timeout(500)
    await page.locator(LOGIN_SUBMIT).click()
    print_info("已点击登录按钮")
    
    # 等待滑块验证码或登录成功
    try:
        await page.wait_for_selector(".yidun_bgimg", state="attached", timeout=5000)
        print_warning("检测到滑块验证码，请手动完成验证...")
    except:
        print_info("未检测到滑块验证码")
    
    await page.wait_for_selector(LOGIN_PANEL, state='hidden', timeout=120000)
    print_success("登录成功！")
    return True

async def monitor_navigation(page: Page):
    """监控页面导航并自动保存课程页面"""
    async def handle_navigation(request):
        if request.resource_type == 'document':
            url = request.url
            if 'zhihuishu.com' in url:
                page_type = identify_page_type(url)
                await asyncio.sleep(3)
                if url not in saved_pages:
                    saved_pages.add(url)
                    print_info("页面导航 - 类型: {}, URL: {}...".format(page_type, url[:80]))
                    await save_page_html(page)
    
    page.on('request', handle_navigation)
    print_info("页面导航监控已启动")

async def main():
    """主测试流程"""
    print("\n" + "="*80)
    print("智慧树刷课流程调试脚本")
    print("支持：自动登录、页面识别、HTML自动保存")
    print("="*80 + "\n")
    
    async with async_playwright() as p:
        page, context, browser = await init_browser(p)
        
        try:
            # 阶段1: 登录
            await test_login(page, TEST_USERNAME, TEST_PASSWORD)
            
            # 阶段2: 进入我的课程
            print_info("正在访问我的课程页面: {}".format(MY_COURSES_URL))
            await page.goto(MY_COURSES_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            await save_page_html(page, "course_list")
            
            # 启动页面导航监控
            await monitor_navigation(page)
            
            # 阶段3: 用户手动操作
            print("\n" + "="*80)
            print("【手动操作阶段】")
            print("请在浏览器中点击进入任意课程页面")
            print("系统会自动识别页面类型并保存HTML到 Autovisor/page_snapshots/")
            print("按 Ctrl+C 结束...")
            print("="*80 + "\n")
            
            # 保持运行
            while True:
                await asyncio.sleep(5)
                
        except KeyboardInterrupt:
            print("\n")
            print_info("用户中断，正在退出...")
        except Exception as e:
            print_error("发生错误: {}".format(str(e)))
            logger.error("发生错误: {}".format(str(e)))
        finally:
            await context.close()
            await browser.close()
            print("\n" + "="*80)
            print("已保存 {} 个页面快照".format(len(saved_pages)))
            print("保存目录: {}".format(os.path.abspath('Autovisor/page_snapshots')))
            print("="*80)
            logger.save()
            print("日志已保存到: {}".format(logger.filename))

if __name__ == "__main__":
    asyncio.run(main())
