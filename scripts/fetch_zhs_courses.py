import asyncio
import json
import random
from playwright.async_api import async_playwright
import configparser
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

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
from src.course_catalog import get_zhs_course_access_id
from modules.configs import Config
from modules.course_portal import navigate_to_my_course
from modules.login_flow import login_to_zhihuishu
from modules.utils import load_cookies


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_LOGIN = 3
EXIT_PORTAL = 4
EXIT_COURSE_RESPONSE = 5
EXIT_SAVE = 6
COURSE_RESPONSE_TIMEOUT_SECONDS = 20
COURSE_RESPONSE_SETTLE_SECONDS = 0.75


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


def extract_share_course_rows(payload):
    """Return course rows for a successful known API response.

    ``None`` means the response is invalid or reports failure, while an empty
    list is a valid response for an account with no current courses.
    """
    if not isinstance(payload, dict) or str(payload.get("code")) != "200":
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    rows = result.get("courseOpenDtos")
    if not isinstance(rows, list):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    rows_with_access_id = [
        row for row in rows if get_zhs_course_access_id(row)
    ]
    if rows and not rows_with_access_id:
        return None
    return rows_with_access_id


def extract_notice_rows(payload):
    """Return valid Zhihuishu notice rows, or ``None`` for unknown shapes."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("result")
    if not isinstance(rows, list):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    return rows


def is_course_api_candidate(url):
    """Limit structural response discovery to official course API routes."""
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return (
        hostname == "onlineservice-api.zhihuishu.com"
        and "course" in parsed.path.lower()
    )


def normalize_share_course(row):
    course_name = row.get("courseName", "未知课程")
    lesson_name = row.get("lessonName")
    lesson_num = row.get("lessonNum")
    course_type = row.get("courseType")
    course_start_time = row.get("courseStartTime")
    course_end_time = row.get("courseEndTime")
    return {
        "courseName": course_name,
        "lessonName": lesson_name if lesson_name else "(未选择课时)",
        "lessonNum": lesson_num if lesson_num else "-",
        "progress": row.get("progress", "0%"),
        "secret": get_zhs_course_access_id(row),
        "courseType": course_type if course_type is not None else "-",
        "courseStartTime": (
            course_start_time if course_start_time is not None else "-"
        ),
        "courseEndTime": course_end_time if course_end_time is not None else "-",
    }


def merge_share_courses(target, rows):
    """Merge repeated portal responses without duplicating course cards."""
    for row in rows:
        record = normalize_share_course(row)
        secret = str(record["secret"] or "").strip()
        key = secret or "\0".join(
            str(record[field] or "")
            for field in ("courseName", "lessonName", "lessonNum", "courseType")
        )
        target[key] = record


async def wait_for_course_response(
    event,
    timeout=COURSE_RESPONSE_TIMEOUT_SECONDS,
    settle_timeout=0,
):
    """Wait for a valid response and optionally for a quiet aggregation window."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    while settle_timeout > 0:
        event.clear()
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=min(settle_timeout, remaining),
            )
        except asyncio.TimeoutError:
            break
    return True


def _browser_candidates(driver, local_app_data=""):
    edge_candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    if local_app_data:
        edge_candidates.append(
            os.path.join(
                local_app_data, "Microsoft", "Edge", "Application", "msedge.exe"
            )
        )
        chrome_candidates.append(
            os.path.join(
                local_app_data, "Google", "Chrome", "Application", "chrome.exe"
            )
        )
    if str(driver or "").lower() == "chrome":
        return chrome_candidates + edge_candidates
    return edge_candidates + chrome_candidates


def _detect_browser_path(preferred_driver=""):
    config_path = os.path.join(SCRIPT_DIR, "Autovisor", "configs.ini")
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(config_path, encoding='utf-8')
    except UnicodeDecodeError:
        cfg.read(config_path, encoding='gbk')
    exe_path = cfg.get('browser-option', 'EXE_PATH', fallback='')
    if exe_path and os.path.isfile(exe_path):
        return exe_path
    configured_driver = preferred_driver or cfg.get(
        'browser-option', 'driver', fallback='edge'
    )
    candidates = _browser_candidates(
        configured_driver,
        os.environ.get("LOCALAPPDATA", ""),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_runtime_config(account_index=1):
    try:
        account_index = int(account_index)
    except (TypeError, ValueError):
        raise ValueError("账号索引格式错误")
    if account_index < 1:
        raise ValueError("账号索引必须从 1 开始")

    config_path = os.path.join(SCRIPT_DIR, "Autovisor", "configs.ini")
    return Config(config_path, account_id=account_index)


def load_config(account_index=1):
    try:
        config = load_runtime_config(account_index)
        print(
            f"已读取账号配置 {config.account_id}: {_mask_identifier(config.username)}",
            flush=True,
        )
        return config.username, config.password
    except Exception as e:
        print(f"读取配置文件失败: {e}", flush=True)
        return None, None


def resolve_cookie_path(config):
    cookie_path = Path(config.cookies_file)
    if not cookie_path.is_absolute():
        cookie_path = Path(SCRIPT_DIR) / "Autovisor" / cookie_path
    return cookie_path


async def create_session_context(browser, config):
    context = await browser.new_context()
    cookie_path = resolve_cookie_path(config)
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookies = load_cookies(str(cookie_path))
    if cookies:
        try:
            await context.add_cookies(cookies)
            print("已加载该账号的登录凭证", flush=True)
        except Exception as exc:
            print(f"历史登录凭证无效，将重新登录: {exc}", flush=True)
    return context, cookie_path


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
        # CourseAPIService uses the file content as the refresh fingerprint.
        # Keep sub-second precision so two legitimate refreshes in the same
        # second (especially repeated empty results) are still distinguishable.
        "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'),
        "courses": courses,
        "notices": notices
    }

    try:
        atomic_dump_json(file_path, data)
        _safe_print(f"数据已保存到 {file_path}")
        return True
    except Exception as e:
        _safe_print(f"保存数据失败: {e}")
        return False


async def main():
    account_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    try:
        runtime_config = load_runtime_config(account_index)
    except Exception as exc:
        print(f"读取配置文件失败: {exc}", flush=True)
        return EXIT_CONFIG
    username = runtime_config.username

    if not username:
        print("无法获取账号信息，退出", flush=True)
        return EXIT_CONFIG

    output_file = os.path.join(SCRIPT_DIR, "data", "zhs_course.json")

    async with async_playwright() as p:
        browser_path = (
            runtime_config.exe_path
            if runtime_config.exe_path and os.path.isfile(runtime_config.exe_path)
            else _detect_browser_path(runtime_config.driver)
        )
        if browser_path:
            print(f"使用浏览器: {browser_path}", flush=True)
        print("课程获取将打开可见浏览器，便于完成登录或安全验证", flush=True)
        browser = await p.chromium.launch(
            headless=False,
            executable_path=browser_path,
        )
        context, cookie_path = await create_session_context(browser, runtime_config)
        page = await context.new_page()
        page.set_default_timeout(30_000)

        lessons_by_key = {}
        courses_response_event = asyncio.Event()
        notices_data = None
        notices_captured = False

        async def handle_response(response):
            nonlocal notices_captured, notices_data

            url = response.url

            if is_course_api_candidate(url):
                known_course_endpoint = 'queryShareCourseInfo' in url
                try:
                    data = await response.json()
                    courses = extract_share_course_rows(data)
                    if courses is None:
                        if known_course_endpoint:
                            print(
                                "课程接口返回了无法识别的数据，本次不会覆盖旧课程",
                                flush=True,
                            )
                        return
                    merge_share_courses(lessons_by_key, courses)
                    courses_response_event.set()
                    print(f"捕获到 {len(courses)} 门课程", flush=True)
                    for course in courses:
                        course_name = course.get('courseName', '未知课程')
                        lesson_name = course.get('lessonName')
                        progress = course.get('progress', '0%')
                        if lesson_name:
                            print(f"  {course_name}: {lesson_name} ({progress})", flush=True)
                        else:
                            print(f"  {course_name}: {progress}", flush=True)
                except Exception as e:
                    if known_course_endpoint:
                        print(f"解析课程响应失败: {e}", flush=True)

            if 'getImportantNoticeList' in url and not notices_captured:
                try:
                    rows = extract_notice_rows(await response.json())
                    if rows is None:
                        print(
                            "通知接口返回了无法识别的数据，本次不会写入见面课",
                            flush=True,
                        )
                        return
                    notices_data = rows
                    notices_captured = True
                    print(f"捕获到待办任务通知: {len(rows)} 条", flush=True)
                    for notice in rows:
                        task_name = notice.get('taskName', '无标题')
                        course_name = notice.get('courseName', '未知')
                        status = notice.get('status', 0)
                        start_time = format_time(notice.get('startTime'))
                        end_time = format_time(notice.get('endTime'))
                        print(f"  {task_name} | {course_name} | 状态:{status} | {start_time} ~ {end_time}", flush=True)
                except Exception as e:
                    print(f"解析通知响应失败: {e}", flush=True)

        page.on('response', handle_response)

        print("正在检查智慧树登录状态...", flush=True)
        try:
            login_ok = await asyncio.wait_for(
                login_to_zhihuishu(
                    context,
                    page,
                    runtime_config,
                    _ConsoleLogger(),
                    modules=[object()] if runtime_config.enableAutoCaptcha else None,
                    cookie_path=str(cookie_path),
                    slider_handler=handle_slider_with_retry,
                ),
                timeout=180,
            )
        except asyncio.TimeoutError:
            login_ok = False
            print("等待登录或安全验证超时", flush=True)
        if not login_ok:
            print("登录未完成，课程数据未更新", flush=True)
            await browser.close()
            return EXIT_LOGIN

        print("正在进入我的学堂并等待课程列表...", flush=True)
        if not await navigate_to_my_course(page, _ConsoleLogger()):
            print("未能进入我的学堂，课程数据未更新", flush=True)
            await browser.close()
            return EXIT_PORTAL
        if not await wait_for_course_response(
            courses_response_event,
            settle_timeout=COURSE_RESPONSE_SETTLE_SECONDS,
        ):
            print(
                "20 秒内未捕获到有效课程接口响应，可能是网络缓慢或页面接口已变化；"
                "旧课程数据保持不变",
                flush=True,
            )
            await browser.close()
            return EXIT_COURSE_RESPONSE

        all_lessons = list(lessons_by_key.values())

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
        if notices_data is not None:
            notices_list = notices_data
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

        if not save_course_data(output_file, username, all_lessons, notices_list):
            await browser.close()
            return EXIT_SAVE

        await browser.close()
        print("课程读取完成", flush=True)
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
