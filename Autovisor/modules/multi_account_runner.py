# encoding=utf-8
"""
多账号并发运行模块
支持同时运行多个 Autovisor 实例

注意: Windows 下多进程必须使用 spawn 模式
"""
import asyncio
import multiprocessing
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional, Dict, Any

# Windows 多进程设置
if sys.platform == 'win32':
    multiprocessing.set_start_method('spawn', force=True)

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from runtime_bootstrap import activate_runtime_dependencies

activate_runtime_dependencies()

from playwright.async_api import async_playwright, Playwright, Page, BrowserContext
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright._impl._errors import TargetClosedError

from modules.logger import Logger
from modules.multi_config import AccountConfig
from modules.progress import get_course_progress, show_course_progress
from modules.utils import optimize_page, get_lesson_name, get_filtered_class, get_video_attr, hide_window, \
    get_browser_window, bring_console_to_front, save_cookies, load_cookies
from modules.slider import slider_verify
from modules.async_utils import cancel_background_tasks
from modules.tasks import video_optimize, play_video, skip_questions, wait_for_verify, activate_window, task_monitor
from modules import installer


def ensure_directories():
    """确保必要的目录存在"""
    dirs = ['logs', 'res']
    for d in dirs:
        os.makedirs(d, exist_ok=True)


class SingleAccountRunner:
    """单个账号运行器"""
    
    def __init__(self, account_config: AccountConfig, log_queue=None):
        self.config = account_config
        self.log_queue = log_queue
        self.event_loop_verify = asyncio.Event()
        self.event_loop_answer = asyncio.Event()
        self.log_text = ""
        
        # 每个账号独立的日志文件
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/Log_Account_{account_config.account_id}.txt"
        
    def log(self, message: str, level: str = "info"):
        """输出日志 - 多进程安全版本"""
        timestamp = time.strftime('%H:%M:%S')
        prefix = f"[账号{self.config.account_id}]"
        full_message = f"{prefix} {message}"
        
        # 根据级别设置颜色
        color_map = {
            'info': '\033[32m',    # 绿色
            'warn': '\033[33m',    # 黄色
            'error': '\033[31m'    # 红色
        }
        color = color_map.get(level, '\033[32m')
        reset = '\033[0m'
        
        # 打印到控制台
        print(f"\r{color}[{level.upper()}]{reset} {full_message}".ljust(80))
        
        # 写入日志文件
        self.log_text += f"[{timestamp}] [{level.upper()}] {full_message}\n"
        
        # 立即写入文件（多进程安全）
        try:
            with open(self.log_filename, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [{level.upper()}] {full_message}\n")
        except Exception as e:
            print(f"[账号{self.config.account_id}] 日志写入失败: {e}")
        
        # 如果有队列，也发送到队列
        if self.log_queue:
            try:
                self.log_queue.put({
                    'account_id': self.config.account_id,
                    'level': level,
                    'message': message,
                    'timestamp': timestamp
                })
            except:
                pass  # 队列可能已关闭
    
    async def init_page(self, p: Playwright) -> tuple[Page, BrowserContext]:
        """初始化浏览器页面"""
        driver = "msedge" if self.config.driver == "edge" else self.config.driver
        self.log(f"正在启动{self.config.driver}浏览器...")
        
        # 计算窗口位置（错开显示，避免重叠）
        base_x = 50 + (self.config.account_id - 1) * 80
        base_y = 50 + (self.config.account_id - 1) * 40
        
        try:
            browser = await p.chromium.launch(
                channel=driver,
                headless=False,
                executable_path=self.config.exe_path if self.config.exe_path else None,
                args=[
                    f'--window-size={self.config.window_width},{self.config.window_height}',
                    f'--window-position={base_x},{base_y}',
                ],
            )
        except Exception as e:
            self.log(f"浏览器启动失败: {e}", "error")
            raise
            
        context = await browser.new_context()
        
        # 确保 cookie 目录存在
        os.makedirs(os.path.dirname(self.config.cookies_file) or '.', exist_ok=True)
        
        # 加载 Cookies
        try:
            cookies = load_cookies(self.config.cookies_file)
            if cookies:
                await context.add_cookies(cookies)
                self.log("已加载 Cookies!")
            else:
                self.log("未找到 Cookies,将跳转至登录页.")
        except Exception as e:
            self.log(f"加载 Cookies 失败: {e}", "warn")
        
        page = await context.new_page()
        self.log(f"{self.config.driver}浏览器启动完成.")
        
        # 抹去特征
        try:
            stealth_path = project_root / 'res' / 'stealth.min.js'
            if stealth_path.exists():
                with open(stealth_path, 'r', encoding='utf-8') as f:
                    js = f.read()
                await page.add_init_script(js)
                self.log("stealth.js执行完成.")
        except Exception as e:
            self.log(f"stealth.js执行失败: {e}", "warn")
        
        page.set_default_timeout(24 * 3600 * 1000)
        return page, context
    
    async def auto_login(self, context: BrowserContext, page: Page, modules=None):
        """自动登录"""
        login_url = "https://passport.zhihuishu.com/login"
        
        async def request_handler(request):
            if "https://www.zhihuishu.com" in request.url:
                cookies = await context.cookies()
                save_cookies(cookies, self.config.cookies_file)
                self.log(f"已保存登录凭证到: {self.config.cookies_file}")
                page.remove_listener('request', request_handler)
        
        await page.goto(login_url, wait_until="commit")
        if "login" not in page.url:
            self.log("检测到已登录,跳过登录步骤.")
            return
        
        await page.wait_for_selector(".wall-main", state='attached')
        page.on('request', request_handler)
        
        if self.config.username and self.config.password:
            await page.wait_for_selector("#lUsername", state="attached")
            await page.wait_for_selector("#lPassword", state="attached")
            await page.locator('#lUsername').fill(self.config.username)
            await page.locator('#lPassword').fill(self.config.password)
            await page.wait_for_selector(".wall-sub-btn", state="attached")
            await page.wait_for_timeout(500)
            await page.locator(".wall-sub-btn").first.click()
        
        if self.config.enable_auto_captcha and modules:
            await slider_verify(page)
        
        await page.wait_for_selector(".wall-main", state='hidden')
    
    async def learning_loop(self, page: Page, start_time, is_new_version=False, is_hike_class=False):
        """学习循环"""
        cur_time = await get_course_progress(page, is_new_version, is_hike_class)
        while cur_time != "100%":
            try:
                limit_time = self.config.limit_max_time
                time_period = (time.time() - start_time) / 60
                if 0 < limit_time <= time_period:
                    break
                cur_time = await get_course_progress(page, is_new_version, is_hike_class)
                show_course_progress(desc=f"[账号{self.config.account_id}] 完成进度:", cur_time=cur_time)
                await asyncio.sleep(0.5)
            except PlaywrightTimeoutError as e:
                if await page.query_selector(".yidun_modal__title"):
                    await self.event_loop_verify.wait()
                elif await page.query_selector(".topic-title"):
                    await self.event_loop_answer.wait()
                else:
                    self.log(repr(e), "warn")
    
    async def review_loop(self, page: Page, start_time, is_hike_class=False):
        """复习循环"""
        total_time = await get_video_attr(page, "duration")
        await page.evaluate("document.querySelector('video').currentTime=0;")
        
        while True:
            limit_time = self.config.limit_max_time
            cur_time = await get_video_attr(page, "currentTime")
            if cur_time >= total_time:
                break
            try:
                time_period = (time.time() - start_time) / 60
                if 0 < limit_time <= time_period:
                    break
                show_course_progress(
                    desc=f"[账号{self.config.account_id}] 完成进度:", 
                    cur_time=time_period, 
                    limit_time=limit_time
                )
                await asyncio.sleep(0.5)
            except PlaywrightTimeoutError as e:
                if await page.query_selector(".yidun_modal__title"):
                    await self.event_loop_verify.wait()
                elif await page.query_selector(".topic-title"):
                    await self.event_loop_answer.wait()
                else:
                    self.log(repr(e), "warn")
    
    async def working_loop(self, page: Page, is_new_version=False, is_hike_class=False):
        """工作循环"""
        # 获取所有课程元素
        if is_hike_class:
            await page.wait_for_selector(".file-item", state="attached")
        else:
            await page.wait_for_selector(".clearfix.video", state="attached")
        
        to_learn_class = await get_filtered_class(page, is_new_version, is_hike_class)
        learning = True if len(to_learn_class) > 0 else False
        
        if learning:
            all_class = to_learn_class
        else:
            all_class = await get_filtered_class(page, is_new_version, is_hike_class, include_all=True)
        
        start_time = time.time()
        cur_index = 0
        
        while cur_index < len(all_class):
            await all_class[cur_index].click()
            if is_hike_class:
                await page.wait_for_selector(".file-item.active", state="attached")
            else:
                await page.wait_for_selector(".current_play", state="attached")
            await page.wait_for_timeout(1000)
            
            title = await get_lesson_name(page, is_hike_class)
            self.log(f"正在学习:{title}")
            
            page.set_default_timeout(10000)
            await page.wait_for_selector("video", state="attached")
            await page.evaluate("document.querySelector('video').pause = ()=>{}")
            
            if learning:
                await self.learning_loop(page, start_time, is_new_version, is_hike_class)
            else:
                await self.review_loop(page, start_time, is_hike_class)
            
            if is_hike_class is False:
                if "current_play" in await all_class[cur_index].get_attribute('class'):
                    cur_index += 1
            else:
                if "active" in await all_class[cur_index].get_attribute('class'):
                    cur_index += 1
            
            reach_time_limit = await self.check_time_limit(page, start_time, all_class, title, is_hike_class)
            if reach_time_limit:
                return
    
    async def check_time_limit(self, page: Page, start_time, all_class, title, is_hike_class) -> bool:
        """检查时间限制"""
        reach_time_limit = False
        page.set_default_timeout(24 * 3600 * 1000)
        time_period = (time.time() - start_time) / 60
        
        if 0 < self.config.limit_max_time <= time_period:
            self.log(f"当前课程已达时限:{self.config.limit_max_time}min", "info")
            self.log("即将进入下门课程!")
            reach_time_limit = True
        else:
            class_name = await all_class[-1].get_attribute('class')
            if is_hike_class:
                if "active" in class_name:
                    self.log("已学完本课程全部内容!", "info")
                else:
                    self.log(f"\"{title}\" 已完成!", "info")
                    self.log(f"本次课程已学习:{time_period:.1f} min")
            else:
                if "current_play" in class_name:
                    self.log("已学完本课程全部内容!", "info")
                else:
                    self.log(f"\"{title}\" 已完成!", "info")
                    self.log(f"本次课程已学习:{time_period:.1f} min")
        
        return reach_time_limit
    
    async def run(self):
        """运行单个账号"""
        modules, tasks = [], []
        
        if self.config.enable_auto_captcha:
            self.log("正在检查依赖库...")
            modules = installer.start()
            self.log("所有依赖库安装完成!")
        
        self.log("开始运行...")
        
        # 检查隐藏窗口时是否填写了账号密码
        if self.config.enable_hide_window and (not self.config.username or not self.config.password):
            self.log("错误: 启用隐藏窗口时必须填写账号和密码!", "error")
            self.log("请修改配置: enableHideWindow = False 或填写 username 和 password", "error")
            return
        
        try:
            async with async_playwright() as p:
                page, context = await self.init_page(p)
                
                # 登录
                if not self.config.username or not self.config.password:
                    self.log("未配置账号密码，请手动在浏览器中登录...")
                else:
                    self.log("正在自动登录...")
                
                verify_task = asyncio.create_task(
                    wait_for_verify(page, self._create_mock_config(), self.event_loop_verify)
                )
                await self.auto_login(context, page, modules)
                
                # 启动协程任务
                video_optimize_task = asyncio.create_task(
                    video_optimize(page, self._create_mock_config())
                )
                skip_ques_task = asyncio.create_task(
                    skip_questions(page, self.event_loop_answer)
                )
                play_video_task = asyncio.create_task(play_video(page))
                tasks.extend([verify_task, video_optimize_task, skip_ques_task, play_video_task])
                
                # 隐藏窗口
                if self.config.enable_hide_window:
                    window = await get_browser_window(page)
                    activate_window_task = asyncio.create_task(activate_window(page))
                    tasks.append(activate_window_task)
                    await hide_window(page)
                
                # 任务监视器
                monitor_task = asyncio.create_task(task_monitor(tasks))
                
                # 遍历所有课程
                for course_url in self.config.course_urls:
                    self.log(f"正在加载课程: {course_url[:50]}...")
                    is_new_version = "fusioncourseh5" in course_url
                    is_hike_class = "hike.zhihuishu.com" in course_url
                    
                    await page.goto(course_url, wait_until="commit")
                    await optimize_page(page, self._create_mock_config(), is_new_version, is_hike_class)
                    self.log("页面优化完成!")
                    
                    # 获取课程标题
                    try:
                        if not is_new_version and is_hike_class is False:
                            title_selector = await page.wait_for_selector(".source-name")
                            course_title = await title_selector.text_content()
                            self.log(f"当前课程:<<{course_title}>>")
                        if is_hike_class:
                            title_selector = await page.wait_for_selector(".course-name")
                            course_title = await title_selector.text_content()
                            self.log(f"当前课程:<<{course_title}>>， 是翻转课")
                    except:
                        pass
                    
                    # 启动课程主循环
                    await self.working_loop(page, is_new_version=is_new_version, is_hike_class=is_hike_class)
                
                self.log("所有课程已学习完毕!")
                
                # 这些协程是长期监听器；正常完成后主动取消，避免永久等待。
                await cancel_background_tasks(tasks)
                await cancel_background_tasks([monitor_task])
                
        except TargetClosedError as e:
            self.log(traceback.format_exc(), "error")
            if "BrowserType.launch" in repr(e):
                self.log("浏览器启动失败,请尝试重新启动!", "error")
            else:
                self.log("浏览器被关闭,程序退出.", "error")
        except Exception as e:
            self.log(f"运行错误: {repr(e)}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            self.log("账号运行结束", "info")
    
    def _create_mock_config(self):
        """创建兼容的配置对象（用于兼容现有模块）"""
        class MockConfig:
            def __init__(self, account_config: AccountConfig):
                self.driver = account_config.driver
                self.username = account_config.username
                self.password = account_config.password
                self.exe_path = account_config.exe_path
                self.enableAutoCaptcha = account_config.enable_auto_captcha
                self.enableHideWindow = account_config.enable_hide_window
                self.soundOff = account_config.sound_off
                self.limitMaxTime = account_config.limit_max_time
                self.limitSpeed = account_config.limit_speed
                self.course_urls = account_config.course_urls
                self.login_url = "https://passport.zhihuishu.com/login"
                self.block_js = '''return document.getElementsByClassName("yidun_jigsaw")[0].src'''
                self.bg_js = '''return document.getElementsByClassName("yidun_bg-img")[0].src'''
                self.pop_js = '''document.getElementsByClassName("iconfont iconguanbi")[0].click();'''
                self.close_ques = '''document.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, keyCode: 27 }));'''
                self.remove_pause = "document.querySelector('video').pause = ()=>{}"
                self.play_video = '''const video = document.querySelector('video');video.play();'''
                self.volume_none = "document.querySelector('video').volume=0;"
                self.set_none_icon = '''document.querySelector(".volumeBox").classList.add("volumeNone")'''
                self.reset_curtime = '''document.querySelector('video').currentTime=0;'''
                self.night_js = '''document.getElementsByClassName("Patternbtn-div")[0].click()'''
                
                @property
                def revise_speed(self):
                    return f"document.querySelector('video').playbackRate={self.limitSpeed};"
                
                @property
                def revise_speed_name(self):
                    return f'''document.querySelector(".speedBox span").innerText = "X {self.limitSpeed}";'''
        
        return MockConfig(self.config)


def run_single_account_process(account_config_dict: dict, log_queue=None):
    """在独立进程中运行单个账号（用于多进程）
    
    注意：此函数在子进程中执行，必须是模块级别的函数
    """
    try:
        # 子进程导入 logger 触发 sys.stdout 安全替换
        import modules.logger
        
        # 重建 AccountConfig 对象
        from modules.multi_config import AccountConfig
        account_config = AccountConfig(**account_config_dict)
        
        # 确保目录存在
        ensure_directories()
        
        # 创建并运行
        runner = SingleAccountRunner(account_config, log_queue)
        asyncio.run(runner.run())
    except Exception as e:
        print(f"[账号{account_config_dict.get('account_id', '?')}] 进程异常: {e}")
        import traceback
        traceback.print_exc()


class MultiAccountManager:
    """多账号管理器"""
    
    def __init__(self, config_path: str = "configs.ini"):
        from modules.multi_config import MultiAccountConfig
        self.multi_config = MultiAccountConfig(config_path)
        self.processes: List[multiprocessing.Process] = []
        self.log_queue = multiprocessing.Queue()
    
    def run_all(self, max_concurrent: Optional[int] = None):
        """运行所有账号"""
        if max_concurrent is None:
            max_concurrent = len(self.multi_config.accounts)
        
        print(f"共 {len(self.multi_config.accounts)} 个账号，同时运行 {max_concurrent} 个")
        print("=" * 50)
        
        # 将 AccountConfig 转换为字典（用于多进程序列化）
        account_dicts = []
        for acc in self.multi_config.accounts:
            account_dicts.append({
                'account_id': acc.account_id,
                'username': acc.username,
                'password': acc.password,
                'driver': acc.driver,
                'exe_path': acc.exe_path,
                'enable_auto_captcha': acc.enable_auto_captcha,
                'enable_hide_window': acc.enable_hide_window,
                'sound_off': acc.sound_off,
                'limit_max_time': acc.limit_max_time,
                'limit_speed': acc.limit_speed,
                'course_urls': acc.course_urls,
                'cookies_file': acc.cookies_file
            })
        
        # 创建并启动进程
        for acc_dict in account_dicts[:max_concurrent]:
            p = multiprocessing.Process(
                target=run_single_account_process,
                args=(acc_dict, self.log_queue)
            )
            p.start()
            self.processes.append(p)
            time.sleep(2)  # 错开启动时间，避免同时启动导致资源冲突
        
        # 等待所有进程完成
        try:
            for p in self.processes:
                p.join()
        except KeyboardInterrupt:
            print("\n收到中断信号，正在停止所有账号...")
            for p in self.processes:
                p.terminate()
            for p in self.processes:
                p.join()
        
        print("=" * 50)
        print("所有账号运行完毕!")


if __name__ == "__main__":
    # 测试多账号运行
    manager = MultiAccountManager("../configs.ini")
    manager.run_all()
