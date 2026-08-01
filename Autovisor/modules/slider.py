import asyncio
import random

import requests

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from modules.logger import Logger

# 防御性导入 OpenCV 和 NumPy（由 caller 通过 runtime_deps 提供）
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None  # type: ignore
    np = None   # type: ignore

logger = Logger()
SLIDER_VISIBILITY_SELECTORS = (
    ".yidun_bgimg",
    "img.yidun_bg-img",
    "img.yidun_jigsaw",
    "div.yidun_slider",
)
SLIDER_DETECTION_TIMEOUT_MS = 3000
SLIDER_DETECTION_POLL_MS = 200
SLIDER_RESULT_SETTLE_MS = 800


# 下载图片并转换为OpenCV格式
async def download_image(url):
    if not url:
        raise ValueError("验证码图片地址为空")
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV 或 NumPy 未加载")

    def fetch():
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content

    content = await asyncio.to_thread(fetch)
    # 转换为numpy数组供cv2使用
    image_array = np.frombuffer(content, np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("验证码图片解码失败")
    return image


# 图片处理流程模块化
def process_background_image(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    edges = cv2.Canny(binary, 500, 900, apertureSize=3)
    return edges


def process_block_image(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(inverted, 240, 255, cv2.THRESH_BINARY_INV)
    edges = cv2.Canny(binary, 500, 900, apertureSize=3)
    return edges


# 主函数，结合页面加载和图片处理
async def progress_img(page: Page):
    # 等待滑块验证码图片加载完成
    if await page.locator("div.yidun--loading").is_visible():
        await page.wait_for_selector(
            "div.yidun--loading",
            state="detached",
            timeout=5000,
        )

    # 异步下载背景图片和滑块图片
    bg_url = await page.locator('img.yidun_bg-img').get_attribute('src')
    block_url = await page.locator('img.yidun_jigsaw').get_attribute('src')

    bg_img = await download_image(bg_url)
    block_img = await download_image(block_url)

    # 图片处理
    bg_edges = process_background_image(bg_img)
    block_edges = process_block_image(block_img)

    # 匹配模板
    result = cv2.matchTemplate(bg_edges, block_edges, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)

    return max_loc


# 生成随机滑动鼠标位置列表
def gen_movelist(sum_n, steps=30):
    move_list = []
    for x in range(steps - 1):
        if sum_n <= 1.5:
            break
        temp = random.uniform(1, sum_n / 2)  # 每次随机生成滑动的距离
        move_list.append(round(temp, 3))  # 添加随机滑动距离
        sum_n -= temp  # 剩余距离减少
    move_list.append(round(sum_n, 3))  # 最后一步修正剩余的距离，保证总距离正确
    return move_list


async def move_slider(page: Page, distance, offset=32):
    await page.locator('div.yidun_slider').hover()
    box = await page.locator('div.yidun_slider').bounding_box()
    if not box:
        raise RuntimeError("无法获取滑块位置")

    # 生成每次移动距离列表
    move_list = gen_movelist(distance)
    # 开始拖动
    await page.mouse.down()
    for i in range(0, len(move_list)):
        await page.mouse.move(box["x"] + sum(move_list[:i]) + offset, box["y"])
    await page.mouse.up()


async def is_slider_visible(page: Page) -> bool:
    """Recognize both legacy and current NetEase slider challenge nodes."""
    for selector in SLIDER_VISIBILITY_SELECTORS:
        locator = page.locator(selector)
        count = await locator.count()
        if count == 0:
            continue
        if count == 1:
            if await locator.is_visible():
                return True
            continue
        for item in await locator.all():
            if await item.is_visible():
                return True
    return False


async def wait_for_slider_appearance(
    page: Page,
    *,
    timeout_ms: int = SLIDER_DETECTION_TIMEOUT_MS,
    poll_ms: int = SLIDER_DETECTION_POLL_MS,
) -> bool:
    """Give a post-submit challenge a short, bounded appearance window."""
    remaining_ms = max(int(timeout_ms), 0)
    poll_ms = max(int(poll_ms), 1)
    while True:
        if await is_slider_visible(page):
            return True
        if remaining_ms <= 0:
            return False
        delay_ms = min(poll_ms, remaining_ms)
        await page.wait_for_timeout(delay_ms)
        remaining_ms -= delay_ms


async def confirm_slider_drag_result(page: Page) -> bool:
    """Treat a drag as successful only after the challenge is hidden."""
    await page.wait_for_timeout(SLIDER_RESULT_SETTLE_MS)
    return not await is_slider_visible(page)


async def slider_verify(
    page: Page,
    *,
    max_retries: int = 3,
    detection_timeout_ms: int = SLIDER_DETECTION_TIMEOUT_MS,
    logger_instance=None,
) -> bool:
    """执行滑块验证码自动破解（OpenCV 模板匹配）

    注意：cv2 和 np 在模块加载时通过防御性导入初始化，
    若导入失败（无 runtime_deps），函数会提前返回。
    """
    active_logger = logger_instance or logger
    if not cv2 or not np:
        active_logger.warn("OpenCV或Numpy导入失败,无法开启自动滑块验证.")
        return False

    try:
        challenge_visible = await wait_for_slider_appearance(
            page,
            timeout_ms=detection_timeout_ms,
        )
    except TargetClosedError:
        raise
    except Exception as exc:
        active_logger.warn(f"检测滑块验证失败，改为手动处理: {str(exc)[:100]}")
        return False

    if not challenge_visible:
        active_logger.info("未检测到滑块验证，继续等待登录结果.")
        return True

    attempts = max(1, int(max_retries))
    for attempt in range(attempts):
        try:
            active_logger.info(f"第{attempt + 1}次尝试过滑块验证...")
            max_loc = await progress_img(page)
            await move_slider(page, max_loc[0])
            if await confirm_slider_drag_result(page):
                active_logger.info("滑块验证已成功通过.")
                return True
            active_logger.warn(
                f"第{attempt + 1}次拖动后滑块仍可见，准备重试."
            )
        except TargetClosedError:
            raise
        except PlaywrightTimeoutError as exc:
            active_logger.warn(
                f"第{attempt + 1}次滑块验证等待超时: {str(exc)[:80]}"
            )
        except Exception as exc:
            active_logger.warn(
                f"第{attempt + 1}次自动滑块验证失败: {str(exc)[:100]}"
            )
        if attempt < attempts - 1:
            await page.wait_for_timeout(1000)

    active_logger.warn("自动过滑块验证失败,请手动验证!")
    return False
