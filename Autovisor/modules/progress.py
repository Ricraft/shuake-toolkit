# encoding=utf-8
import random
from playwright.async_api import Page, TimeoutError
from modules.logger import Logger

logger = Logger()


# 视频区域内移动鼠标
async def move_mouse(page: Page):
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
    except TimeoutError:
        return


# 见面课视频区域内移动鼠标（显示播放控制按钮）
async def move_mouse_meeting_class(page: Page):
    """移动鼠标到见面课视频区域，显示播放控制按钮
    
    播放控制按钮（.controlsBar）需要鼠标悬停在视频区域才能显示
    同时通过JS强制显示控制条，避免鼠标移开后控制条自动隐藏
    """
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
    except Exception:
        pass
    
    try:
        video_area_selectors = [".videoArea", ".video-box", ".player-box", "#vjs_forFollowBackDiv", "video"]
        
        for selector in video_area_selectors:
            try:
                elem = page.locator(selector).first
                if await elem.count() > 0:
                    await elem.hover(timeout=3000)
                    pos = await elem.bounding_box()
                    if pos:
                        target_x = pos['x'] + pos['width'] / 2 + random.uniform(-50, 50)
                        target_y = pos['y'] + pos['height'] / 2 + random.uniform(-50, 50)
                        await page.mouse.move(target_x, target_y)
                        await page.wait_for_timeout(500)
                        return
            except Exception:
                continue
    except Exception:
        pass


# 获取课程进度
async def get_course_progress(page: Page, is_new_version=False, is_hike_class=False, is_national_wisdom=False, is_meeting_class=False, completion_threshold=1.0):
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
    curtime = "0%"
    
    # 见面课：读取签到进度
    if is_meeting_class:
        try:
            # 读取签到进度数字
            qiandao_num = await page.locator(".qiandao-num").first.text_content(timeout=2000)
            if qiandao_num:
                progress = int(qiandao_num.strip())
                curtime = f"{progress}%"
                
                # 检查是否已完成签到
                if progress >= 80:
                    curtime = "100%"
        except Exception as e:
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
                
                if result and result['duration'] > 0:
                    progress = int((result['currentTime'] / result['duration']) * 100)
                    curtime = f"{progress}%"
                    
                    if progress >= 80:
                        curtime = "100%"
            except Exception:
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
            
            if not result:
                curtime = "0%"
            elif not isinstance(result['duration'], (int, float)) or result['duration'] <= 0 or result['duration'] >= 1000000:
                curtime = "0%"
            elif result['duration'] < 5:
                # 太短的视频可能是广告或加载中的占位，不进行进度判定
                curtime = "0%"
            elif result['readyState'] < 2:
                # readyState < 2 表示视频尚未加载足够数据
                curtime = "0%"
            elif result['currentTime'] < 0.1 and (result['ended'] or result['paused']):
                # 视频刚开始或尚未播放，即使 ended 为 true 也判定为 0%
                curtime = "0%"
            elif result['ended'] and result['currentTime'] < result['duration'] * 0.1:
                # 页面跳转时 video.ended 可能为 true 但 currentTime 很低，是过渡态
                curtime = "0%"
            elif result['ended'] or result['currentTime'] >= result['duration'] * completion_threshold:
                # 见面课：进度达到阈值（默认90%）即视为完成
                # 其他课程：100%完成
                curtime = "100%"
            else:
                progress = int((result['currentTime'] / result['duration']) * 100)
                curtime = f"{progress}%"
        except Exception as e:
            # 【修复】记录异常原因，便于调试
            logger.write_log(f"获取进度异常: {repr(e)[:80]}\n")
            curtime = "0%"
    else:
        await move_mouse(page)
        cur_play = await page.query_selector(".current_play")
        if not cur_play:
            return "0%"
        progress = await cur_play.query_selector(".progress-num")
        if not progress:
            if is_new_version:
                progress_ele = await cur_play.query_selector(".progress-num")
                if progress_ele:
                    progress = await progress_ele.text_content()
                    finish = progress == "100%"
                else:
                    finish = False
            else:
                finish = await cur_play.query_selector(".time_icofinish")
            if finish:
                curtime = "100%"
        else:
            curtime = await progress.text_content()

    return curtime


# 打印课程播放进度
def show_course_progress(desc, cur_time=None, limit_time=0, is_meeting_class=False):
    assert limit_time >= 0, "limit_time 必须为非负数!"
    if limit_time == 0:
        cur_time = "0%" if cur_time == '' else cur_time
        percent = int(cur_time.split("%")[0]) + 1  # Handles a 1% rendering error
        
        # 见面课：80%即视为完成，但显示实际进度
        # 其他课程：80%进度即视为完成
        if not is_meeting_class and percent >= 80:
            percent = 100
        
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
