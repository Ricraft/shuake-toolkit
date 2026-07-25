# encoding=utf-8
import math
import random
import re

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page, TimeoutError

from modules.diagnostics import RateLimitedDiagnostics
from modules.logger import Logger

logger = Logger()
progress_diagnostics = RateLimitedDiagnostics(logger)


def _parse_percentage(value) -> int:
    """Extract and clamp a percentage from text such as ``签到 80%``."""
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        raise ValueError("进度文本中没有数字")
    return max(0, min(100, int(float(match.group(0)))))


def _progress_from_video_state(
    state,
    *,
    completion_threshold: float,
    minimum_duration: float = 0,
    require_ready: bool = False,
) -> str:
    """Normalize a browser video snapshot into a safe percentage string."""
    if not isinstance(state, dict):
        return "0%"

    duration = state.get("duration")
    current_time = state.get("currentTime")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
        or duration >= 1_000_000
        or duration < minimum_duration
    ):
        return "0%"
    if (
        isinstance(current_time, bool)
        or not isinstance(current_time, (int, float))
        or not math.isfinite(current_time)
        or current_time < 0
    ):
        return "0%"
    if require_ready and int(state.get("readyState") or 0) < 2:
        return "0%"

    ended = bool(state.get("ended"))
    paused = bool(state.get("paused"))
    if current_time < 0.1 and (ended or paused):
        return "0%"
    if ended and current_time < duration * 0.1:
        return "0%"
    threshold = max(0.01, min(float(completion_threshold), 1.0))
    if ended or current_time >= duration * threshold:
        return "100%"
    return f"{max(0, min(99, int(current_time / duration * 100)))}%"


# 视频区域内移动鼠标
async def move_mouse(page: Page, *, diagnostics=None):
    active_diagnostics = diagnostics or progress_diagnostics
    try:
        await page.wait_for_selector(".videoArea", state="attached", timeout=5000)
        elem = page.locator(".videoArea")
        await elem.hover(timeout=4000)
        pos = await elem.bounding_box()
        if not pos:
            return
        # Calculate the target position to move the mouse
        target_x = pos['x'] + random.uniform(-10, 10)
        target_y = pos['y'] + random.uniform(-10, 10)
        await page.mouse.move(target_x, target_y)
    except TargetClosedError:
        raise
    except TimeoutError:
        return
    except Exception as exc:
        active_diagnostics.warn(
            "normal-progress-hover",
            "显示视频进度控件失败",
            exc,
        )


# 见面课视频区域内移动鼠标（显示播放控制按钮）
async def move_mouse_meeting_class(page: Page, *, diagnostics=None):
    """移动鼠标到见面课视频区域，显示播放控制按钮
    
    播放控制按钮（.controlsBar）需要鼠标悬停在视频区域才能显示
    同时通过JS强制显示控制条，避免鼠标移开后控制条自动隐藏
    """
    active_diagnostics = diagnostics or progress_diagnostics
    try:
        await page.evaluate('''() => {
            const controlsBar = document.querySelector('.controlsBar');
            if (controlsBar) {
                controlsBar.style.setProperty('display', 'block', 'important');
                controlsBar.style.setProperty('opacity', '1', 'important');
            }
            const bigPlayBtn = document.querySelector('.bigPlayButton');
            if (bigPlayBtn) {
                bigPlayBtn.style.setProperty('display', 'block', 'important');
            }
        }''')
    except TargetClosedError:
        raise
    except Exception as exc:
        active_diagnostics.warn(
            "meeting-controls",
            "显示见面课播放控件失败",
            exc,
        )

    video_area_selectors = [
        ".videoArea",
        ".video-box",
        ".player-box",
        "#vjs_forFollowBackDiv",
        "video",
    ]
    last_error = None
    for selector in video_area_selectors:
        try:
            elem = page.locator(selector).first
            if await elem.count() > 0:
                await elem.hover(timeout=3000)
                pos = await elem.bounding_box()
                if pos:
                    target_x = (
                        pos['x'] + pos['width'] / 2 + random.uniform(-50, 50)
                    )
                    target_y = (
                        pos['y'] + pos['height'] / 2 + random.uniform(-50, 50)
                    )
                    await page.mouse.move(target_x, target_y)
                    await page.wait_for_timeout(500)
                    return
        except TargetClosedError:
            raise
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        active_diagnostics.warn(
            "meeting-progress-hover",
            "定位见面课视频区域失败",
            last_error,
        )


# 获取课程进度
async def get_course_progress(
    page: Page,
    is_new_version=False,
    is_hike_class=False,
    is_national_wisdom=False,
    is_meeting_class=False,
    completion_threshold=1.0,
    *,
    diagnostics=None,
):
    """获取课程进度
    
    参数：
        page: Page对象
        is_new_version: 是否为新版本
        is_hike_class: 是否为翻转课
        is_national_wisdom: 是否为全国智慧共享课
        is_meeting_class: 是否为见面课
        completion_threshold: 完成阈值（默认1.0即100%，见面课为0.8即80%）
    
    返回值：
        进度百分比字符串，如 "80%"
    """
    active_diagnostics = diagnostics or progress_diagnostics
    curtime = "0%"
    
    # 见面课：读取签到进度
    if is_meeting_class:
        try:
            # 读取签到进度数字
            qiandao_num = await page.locator(".qiandao-num").first.text_content(timeout=2000)
            if qiandao_num:
                progress = _parse_percentage(qiandao_num)
                curtime = f"{progress}%"
                
                # 检查是否已完成签到
                if progress >= 80:
                    curtime = "100%"
        except TargetClosedError:
            raise
        except Exception:
            # 如果读取签到进度失败，尝试读取视频播放进度
            try:
                result = await page.evaluate('''() => {
                    const video = document.querySelector('video');
                    if (!video) return null;
                    return {
                        ended: video.ended,
                        currentTime: video.currentTime,
                        duration: video.duration,
                        paused: video.paused,
                        readyState: video.readyState
                    };
                }''')
                
                curtime = _progress_from_video_state(
                    result,
                    completion_threshold=0.8,
                )
            except TargetClosedError:
                raise
            except Exception as exc:
                active_diagnostics.warn(
                    "meeting-progress",
                    "读取见面课进度失败",
                    exc,
                )
                curtime = "0%"
    # 翻转课、全国智慧共享课都用 JS 直接读取 video 属性
    elif is_hike_class or is_national_wisdom:
        # 智慧共享课用 JS 直接读取 video 属性，不依赖 DOM 文本选择器（控件需悬停才显示）
        try:
            result = await page.evaluate('''() => {
                const video = document.querySelector('video');
                if (!video) return null;
                return {
                    ended: video.ended,
                    currentTime: video.currentTime,
                    duration: video.duration,
                    paused: video.paused,
                    readyState: video.readyState
                };
            }''')
            
            curtime = _progress_from_video_state(
                result,
                completion_threshold=completion_threshold,
                minimum_duration=5,
                require_ready=True,
            )
        except TargetClosedError:
            raise
        except Exception as exc:
            active_diagnostics.warn(
                "shared-video-progress",
                "读取智慧共享课视频进度失败",
                exc,
            )
            curtime = "0%"
    else:
        try:
            await move_mouse(page, diagnostics=active_diagnostics)
            cur_play = await page.query_selector(".current_play")
            if not cur_play:
                return "0%"
            progress = await cur_play.query_selector(".progress-num")
            if not progress:
                # 新旧页面都可能在渲染切换期只保留完成图标。
                finish = await cur_play.query_selector(".time_icofinish")
                if finish:
                    curtime = "100%"
            else:
                curtime = await progress.text_content()
        except TargetClosedError:
            raise
        except Exception as exc:
            active_diagnostics.warn(
                "normal-course-progress",
                "读取普通课程进度失败",
                exc,
            )
            curtime = "0%"

    return curtime


# 打印课程播放进度
def show_course_progress(desc, cur_time=None, limit_time=0, is_meeting_class=False):
    assert limit_time >= 0, "limit_time 必须为非负数!"
    if limit_time == 0:
        cur_time = "0%" if not cur_time else cur_time
        try:
            percent = int(float(str(cur_time).split("%")[0]))
        except (TypeError, ValueError):
            percent = 0
        percent = max(0, min(100, percent))
        
        length = int(percent * 30 // 100)
        progress = ("█" * length).ljust(30, " ")
        
        # 见面课特殊标识
        suffix = " (80%签到)" if is_meeting_class else ""
        print(f"\r{desc} |{progress}| {percent}%{suffix}\t".ljust(50), end="", flush=True)
    else:
        cur_time = 0 if cur_time == '' else cur_time
        left_time = round(limit_time - cur_time, 1)
        percent = int(cur_time / limit_time * 100)
        if left_time <= 0:
            percent = 100
        length = int(percent * 20 // 100)
        progress = ("█" * length).ljust(20, " ")
        print(f"\r{desc} |{progress}| {percent}%\t剩余 {left_time} min\t".ljust(50), end="", flush=True)


# 打印通用版进度条
def show_progress(desc, current, total, suffix="", width=30):
    percent = int(current / total * 100)
    length = int(percent * width // 100)
    progress = ("█" * length).ljust(width, " ")
    print(f"\r{desc} |{progress}| {percent}%\t{suffix}".ljust(50), end="", flush=True)
