import asyncio
import json
import random
from playwright.async_api import async_playwright
import configparser
import os
import sys
from datetime import datetime

if sys.stdout and sys.stdout.encoding and "gbk" in sys.stdout.encoding.lower():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and sys.stderr.encoding and "gbk" in sys.stderr.encoding.lower():
    import io
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
AUTOVISOR_DIR = os.path.join(SCRIPT_DIR, "Autovisor")
if AUTOVISOR_DIR not in sys.path:
    sys.path.insert(0, AUTOVISOR_DIR)

from src.atomic_io import atomic_dump_json
from modules.course_portal import is_login_url, navigate_to_my_course
from modules.login_flow import wait_for_login_completion
from modules.login_selectors import (
    LOGIN_AGREEMENT_CHECKBOX,
    LOGIN_PANEL,
    LOGIN_SUBMIT,
    LOGIN_URL,
    PASSWORD_INPUT,
    USERNAME_INPUT,
)


class _ConsoleLogger:
    """Adapt the shared course portal navigator to script output."""

    @staticmethod
    def info(message, **_kwargs):
        print(message, flush=True)

    @staticmethod
    def warn(message, **_kwargs):
        print(f"警告: {message}", flush=True)

    @staticmethod
    def error(message, **_kwargs):
        print(f"错误: {message}", flush=True)


def _mask_identifier(value):
    text = str(value or "")
    if len(text) <= 2:
        return "*" * len(text)
    if len(text) <= 6:
        return f"{text[0]}***{text[-1]}"
    return f"{text[:3]}****{text[-2:]}"


def _credential_status(value):
    return "已获取" if value else "未提供"


def _detect_browser_path():
    config_path = os.path.join(SCRIPT_DIR, "Autovisor", "configs.ini")
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(config_path, encoding='utf-8')
    except UnicodeDecodeError:
        cfg.read(config_path, encoding='gbk')
    exe_path = cfg.get('browser-option', 'EXE_PATH', fallback='')
    if exe_path and os.path.isfile(exe_path):
        return exe_path
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.extend([
            os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ])
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_config(account_index=1):
    try:
        account_index = int(account_index)
    except (TypeError, ValueError):
        print("账号索引格式错误", flush=True)
        return None, None
    if account_index < 1:
        print("账号索引必须从 1 开始", flush=True)
        return None, None
    section = (
        "user-account"
        if account_index == 1
        else f"user-account-{account_index}"
    )

    config_path = os.path.join(SCRIPT_DIR, "Autovisor", "configs.ini")
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(config_path, encoding='utf-8')
    except UnicodeDecodeError:
        config.read(config_path, encoding='gbk')

    try:
        username = config.get(section, 'username', raw=True)
        password = config.get(section, 'password', raw=True)
        print(
            f"已从配置文件({section})读取账号: {_mask_identifier(username)}",
            flush=True,
        )
        return username, password
    except Exception as e:
        print(f"读取配置文件失败: {e}", flush=True)
        return None, None


def format_time(timestamp):
    if not timestamp:
        return '未知'
    try:
        dt = datetime.fromtimestamp(timestamp / 1000)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(timestamp)


def try_import_cv2():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError:
        return None, None


def download_image(url, np=None, cv2_module=None):
    if np is None or cv2_module is None:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=15)
        image_array = np.frombuffer(resp.read(), np.uint8)
        return cv2_module.imdecode(image_array, cv2_module.IMREAD_COLOR)
    except Exception as e:
        print(f"下载图片失败: {e}", flush=True)
        return None


def process_background_image(image, cv2_module, np_module):
    gray = cv2_module.cvtColor(image, cv2_module.COLOR_BGR2GRAY)
    denoised = cv2_module.fastNlMeansDenoising(gray, None, 10, 7, 21)
    _, binary = cv2_module.threshold(denoised, 0, 255, cv2_module.THRESH_BINARY + cv2_module.THRESH_OTSU)
    edges = cv2_module.Canny(binary, 500, 900, apertureSize=3)
    return edges


def process_block_image(image, cv2_module, np_module):
    gray = cv2_module.cvtColor(image, cv2_module.COLOR_BGR2GRAY)
    inverted = cv2_module.bitwise_not(gray)
    _, binary = cv2_module.threshold(inverted, 240, 255, cv2_module.THRESH_BINARY_INV)
    edges = cv2_module.Canny(binary, 500, 900, apertureSize=3)
    return edges


def gen_movelist(sum_n, steps=30):
    move_list = []
    for x in range(steps - 1):
        if sum_n <= 1.5:
            break
        temp = random.uniform(1, sum_n / 2)
        move_list.append(round(temp, 3))
        sum_n -= temp
    move_list.append(round(sum_n, 3))
    return move_list


async def auto_slider_verify(page, cv2_module=None, np_module=None):
    if not cv2_module or not np_module:
        print("OpenCV未安装，无法自动过滑块验证", flush=True)
        return False

    try:
        print("检测到滑块验证，开始自动验证...", flush=True)

        try:
            if await page.locator("div.yidun--loading").is_visible():
                await page.wait_for_selector("div.yidun--loading", state="detached", timeout=5000)
        except Exception:
            pass

        bg_url = await page.locator('img.yidun_bg-img').get_attribute('src')
        block_url = await page.locator('img.yidun_jigsaw').get_attribute('src')

        if not bg_url or not block_url:
            print("无法获取滑块图片URL", flush=True)
            return False

        bg_img = download_image(bg_url, np_module, cv2_module)
        block_img = download_image(block_url, np_module, cv2_module)

        if bg_img is None or block_img is None:
            print("图片下载失败", flush=True)
            return False

        bg_edges = process_background_image(bg_img, cv2_module, np_module)
        block_edges = process_block_image(block_img, cv2_module, np_module)

        result = cv2_module.matchTemplate(bg_edges, block_edges, cv2_module.TM_CCOEFF_NORMED)
        _, _, _, max_loc = cv2_module.minMaxLoc(result)
        distance = max_loc[0]

        await page.locator('div.yidun_slider').hover()
        box = await page.locator('div.yidun_slider').bounding_box()

        move_list = gen_movelist(distance)
        await page.mouse.down()
        for i in range(0, len(move_list)):
            await page.mouse.move(box["x"] + sum(move_list[:i]) + 32, box["y"])
        await page.mouse.up()

        print("滑块验证已通过", flush=True)
        return True

    except Exception as e:
        print(f"自动滑块验证失败: {e}", flush=True)
        return False


async def handle_slider_with_retry(page, max_retries=3):
    cv2_module, np_module = try_import_cv2()

    for attempt in range(max_retries):
        try:
            slider_visible = False
            try:
                slider_visible = await page.locator(".yidun_bgimg").is_visible(timeout=3000)
            except Exception:
                pass

            if slider_visible:
                print(f"第{attempt + 1}次尝试过滑块验证...", flush=True)
                success = await auto_slider_verify(page, cv2_module, np_module)
                if success:
                    return True
                await page.wait_for_timeout(1000)
            else:
                print("未检测到滑块验证，跳过", flush=True)
                return True

        except Exception as e:
            print(f"滑块验证处理异常: {e}", flush=True)
            if attempt < max_retries - 1:
                await page.wait_for_timeout(1000)
            else:
                return False

    return False


def load_existing_course_data(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取已有数据失败: {e}", flush=True)
    return {}


def _safe_print(text):
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode('utf-8', errors='replace').decode('utf-8'), flush=True)


def save_course_data(file_path, username, courses, notices):
    data = load_existing_course_data(file_path)

    data[username] = {
        "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "courses": courses,
        "notices": notices
    }

    try:
        atomic_dump_json(file_path, data)
        _safe_print(f"数据已保存到 {file_path}")
    except Exception as e:
        _safe_print(f"保存数据失败: {e}")


async def main():
    account_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    username, password = load_config(account_index)

    if not username:
        print("无法获取账号信息，退出", flush=True)
        return

    output_file = os.path.join(SCRIPT_DIR, "data", "zhs_course.json")

    async with async_playwright() as p:
        browser_path = _detect_browser_path()
        if browser_path:
            print(f"使用浏览器: {browser_path}", flush=True)
        browser = await p.chromium.launch(
            headless=True,
            executable_path=browser_path,
        )
        page = await browser.new_page()

        all_lessons = []
        notices_data = None
        notices_captured = False

        async def handle_response(response):
            nonlocal notices_captured, notices_data

            url = response.url

            if 'queryShareCourseInfo' in url:
                try:
                    data = await response.json()
                    if data.get('code') == 200:
                        courses = data.get('result', {}).get('courseOpenDtos', [])
                        if courses:
                            print(f"捕获到 {len(courses)} 门课程", flush=True)
                            for course in courses:
                                course_name = course.get('courseName', '未知课程')
                                lesson_name = course.get('lessonName')
                                lesson_num = course.get('lessonNum')
                                progress = course.get('progress', '0%')
                                secret = course.get('secret', '')
                                course_type = course.get('courseType')
                                course_start_time = course.get('courseStartTime')
                                course_end_time = course.get('courseEndTime')

                                lesson_info = {
                                    'courseName': course_name,
                                    'lessonName': lesson_name if lesson_name else '(未选择课时)',
                                    'lessonNum': lesson_num if lesson_num else '-',
                                    'progress': progress,
                                    'secret': secret,
                                    'courseType': course_type if course_type else '-',
                                    'courseStartTime': course_start_time if course_start_time else '-',
                                    'courseEndTime': course_end_time if course_end_time else '-'
                                }
                                all_lessons.append(lesson_info)

                                if lesson_name:
                                    print(f"  {course_name}: {lesson_name} ({progress})", flush=True)
                                else:
                                    print(f"  {course_name}: {progress}", flush=True)
                except Exception as e:
                    print(f"解析课程响应失败: {e}", flush=True)

            if 'getImportantNoticeList' in url and not notices_captured:
                try:
                    notices_data = await response.json()
                    notices_captured = True
                    result = notices_data.get('result', [])
                    print(f"捕获到待办任务通知: {len(result)} 条", flush=True)
                    for notice in result:
                        task_name = notice.get('taskName', '无标题')
                        course_name = notice.get('courseName', '未知')
                        status = notice.get('status', 0)
                        start_time = format_time(notice.get('startTime'))
                        end_time = format_time(notice.get('endTime'))
                        print(f"  {task_name} | {course_name} | 状态:{status} | {start_time} ~ {end_time}", flush=True)
                except Exception as e:
                    print(f"解析通知响应失败: {e}", flush=True)

        page.on('response', handle_response)

        login_url = LOGIN_URL

        print("正在访问智慧树登录页...", flush=True)
        await page.goto(login_url, wait_until="commit")
        await page.wait_for_timeout(2000)

        if not is_login_url(page.url):
            print("检测到已登录（Cookie生效），跳过登录步骤", flush=True)
        else:
            await page.wait_for_selector(LOGIN_PANEL, state="attached")
            print("检测到登录表单", flush=True)

            if username and password:
                print("正在自动填入账号密码...", flush=True)
                try:
                    await page.wait_for_selector(USERNAME_INPUT, state="attached")
                    await page.wait_for_selector(PASSWORD_INPUT, state="attached")

                    await page.locator(USERNAME_INPUT).fill(username)
                    await page.wait_for_timeout(500)
                    await page.locator(PASSWORD_INPUT).fill(password)
                    await page.wait_for_timeout(500)

                    agreement = page.locator(LOGIN_AGREEMENT_CHECKBOX)
                    agreement_count = await agreement.count()
                    if agreement_count > 1:
                        raise RuntimeError("登录协议勾选框不唯一")
                    if agreement_count == 1 and not await agreement.is_checked():
                        await agreement.check(force=True)

                    await page.wait_for_selector(LOGIN_SUBMIT, state="attached")
                    await page.wait_for_timeout(500)
                    await page.locator(LOGIN_SUBMIT).click()
                    print("已提交登录信息", flush=True)

                    await page.wait_for_timeout(1500)

                    print("正在检查是否需要滑块验证...", flush=True)
                    await handle_slider_with_retry(page)

                except Exception as e:
                    print(f"自动填入失败: {e}", flush=True)
                    print("请手动登录...", flush=True)
                    try:
                        await wait_for_login_completion(page, 60_000)
                        print("手动登录完成", flush=True)
                    except Exception:
                        print("等待登录超时", flush=True)
                        await browser.close()
                        return
            else:
                print("请手动登录...", flush=True)
                try:
                    await wait_for_login_completion(page, 60_000)
                    print("手动登录完成", flush=True)
                except Exception:
                    print("等待登录超时", flush=True)
                    await browser.close()
                    return

            try:
                await wait_for_login_completion(page, 8_000)
                print("登录成功", flush=True)
            except Exception:
                print("等待登录表单消失超时，请检查是否需要手动操作", flush=True)

        print("正在进入我的学堂并等待课程列表...", flush=True)
        if not await navigate_to_my_course(page, _ConsoleLogger()):
            print("未能进入我的学堂，课程数据未更新", flush=True)
            await browser.close()
            return
        await page.wait_for_timeout(3000)

        print(f"\n{'='*60}", flush=True)
        print(f"课程信息统计: 共 {len(all_lessons)} 节课时", flush=True)
        print(f"{'='*60}\n", flush=True)

        if all_lessons:
            for i, lesson in enumerate(all_lessons, 1):
                print(f"{i}. 【{lesson['courseName']}】", flush=True)
                print(f"   课时名称: {lesson['lessonName']}", flush=True)
                print(f"   课时编号: {lesson['lessonNum']}", flush=True)
                print(f"   完成进度: {lesson['progress']}", flush=True)
                secret_status = _credential_status(lesson["secret"])
                print(f"   课程访问凭据: {secret_status}", flush=True)
                print(f"   课程类型: {lesson['courseType']}", flush=True)
                print(f"   开始时间: {format_time(lesson['courseStartTime'])}", flush=True)
                print(f"   截止时间: {format_time(lesson['courseEndTime'])}", flush=True)
                print("-" * 60, flush=True)

        notices_list = []
        if notices_data:
            notices_list = notices_data.get('result', [])
            print(f"\n{'='*60}", flush=True)
            print(f"待办任务通知: 共 {len(notices_list)} 条", flush=True)
            print(f"{'='*60}\n", flush=True)

            for i, notice in enumerate(notices_list, 1):
                print(f"{i}. 【{notice.get('taskName', '无标题')}】", flush=True)
                print(f"   课程ID: {notice.get('courseId')}", flush=True)
                print(f"   课程名称: {notice.get('courseName')}", flush=True)
                print(f"   任务ID: {notice.get('taskId')}", flush=True)
                print(f"   视频ID: {notice.get('videoId')}", flush=True)
                print(f"   直播课程ID: {notice.get('liveCourseId')}", flush=True)
                print(f"   开始时间: {format_time(notice.get('startTime'))}", flush=True)
                print(f"   结束时间: {format_time(notice.get('endTime'))}", flush=True)
                print(f"   状态: {notice.get('status')}", flush=True)
                print(f"   招募ID: {notice.get('recruitId')}", flush=True)
                print("-" * 60, flush=True)

        save_course_data(output_file, username, all_lessons, notices_list)

        await browser.close()
        print("课程读取完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
