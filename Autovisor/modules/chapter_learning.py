# encoding=utf-8
"""章节视频识别、播放等待与测验编排辅助流程。"""

from __future__ import annotations

import asyncio

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page

from modules.logger import Logger


logger = Logger()

VIDEO_ITEM_SELECTOR = (
    ".catalogue_title, .lesson-item, .video-item, [class*='video']"
)


async def get_chapter_videos_status(
    page: Page,
    *,
    logger_instance=None,
) -> list:
    """返回章节内真正的视频条目及其完成状态。"""
    active_logger = logger_instance or logger
    videos = []
    try:
        video_items = page.locator(VIDEO_ITEM_SELECTOR)
        item_count = await video_items.count()
    except TargetClosedError:
        raise
    except Exception as exc:
        active_logger.warn(
            "[WARN] 获取章节视频列表失败: %s"
            % repr(exc)[:100]
        )
        raise

    item_errors = 0
    for index in range(item_count):
        try:
            item = video_items.nth(index)
            text = await item.text_content()
            if not text:
                continue

            normalized_text = text.strip()
            lowered_text = normalized_text.lower()
            item_class = (await item.get_attribute("class") or "").lower()
            has_video_child = (
                await item.locator(
                    "video, .video-icon, [class*='play']"
                ).count()
                > 0
            )
            is_video = (
                "video" in item_class
                or "video" in lowered_text
                or "视频" in normalized_text
                or "观看" in normalized_text
                or has_video_child
            )
            if not is_video:
                continue

            completed = (
                "已完成" in normalized_text
                or "100%" in normalized_text
                or "complete" in lowered_text
                or "done" in lowered_text
            )
            videos.append(
                {
                    "name": normalized_text[:50],
                    "completed": completed,
                    "index": index,
                }
            )
        except TargetClosedError:
            raise
        except Exception as exc:
            item_errors += 1
            if item_errors == 1 or item_errors % 5 == 0:
                active_logger.warn(
                    "[WARN] 读取章节条目失败（累计%d项，索引%d）: %s"
                    % (item_errors, index, repr(exc)[:100])
                )

    if item_count and item_errors == item_count:
        raise RuntimeError("所有章节条目均读取失败，无法确认视频完成状态")
    return videos


async def wait_for_video_completion(
    page: Page,
    timeout: float = 3600,
    *,
    logger_instance=None,
    poll_interval: float = 3,
    missing_video_interval: float = 2,
    sleep_func=asyncio.sleep,
) -> bool:
    """等待当前视频播放完成，并在意外暂停时尝试恢复。"""
    active_logger = logger_instance or logger
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    last_progress = -1

    try:
        while loop.time() - start_time < timeout:
            status = await page.evaluate(
                """() => {
                    const video = document.querySelector('video');
                    if (!video) return null;
                    return {
                        currentTime: video.currentTime,
                        duration: video.duration,
                        ended: video.ended,
                        paused: video.paused
                    };
                }"""
            )
            if not status:
                await sleep_func(missing_video_interval)
                continue

            current_time = float(status.get("currentTime") or 0)
            duration = float(status.get("duration") or 0)
            ended = bool(status.get("ended"))
            paused = bool(status.get("paused"))

            if ended or (duration > 0 and current_time >= duration - 1):
                active_logger.info("[OK] 视频播放完成")
                return True

            if duration > 0:
                progress = int(current_time / duration * 100)
                if progress != last_progress and progress % 10 == 0:
                    active_logger.info("[VIDEO] 播放进度: %d%%" % progress)
                    last_progress = progress

            if paused and not ended:
                await page.evaluate(
                    """() => {
                        const video = document.querySelector('video');
                        if (video && !video.ended) {
                            video.play().catch(() => {});
                        }
                    }"""
                )
            await sleep_func(poll_interval)

        active_logger.warn("[WARN] 视频播放超时")
        return False
    except Exception as exc:
        active_logger.warn(
            "[WARN] 视频播放异常: %s" % str(exc)[:30]
        )
        return False


async def chapter_learning_flow(
    page: Page,
    test_handler=None,
    auto_submit: bool = False,
    force_video_first: bool = True,
    *,
    logger_instance=None,
    answer_handler=None,
    video_status_getter=None,
    completion_waiter=None,
) -> bool:
    """依次完成章节视频、测验并复核视频完成状态。"""
    active_logger = logger_instance or logger
    status_getter = video_status_getter or get_chapter_videos_status
    completion_handler = completion_waiter or wait_for_video_completion

    active_logger.info("\n" + "=" * 60)
    active_logger.info("[CHAPTER] 开始章节学习流程")
    active_logger.info("=" * 60)

    try:
        if force_video_first:
            active_logger.info("\n[CHAPTER] 步骤1: 学习未完成视频（强制）")
            videos = await status_getter(
                page,
                logger_instance=active_logger,
            )
            incomplete_videos = [
                video for video in videos if not video["completed"]
            ]
            if incomplete_videos:
                active_logger.info(
                    "[CHAPTER] 发现 %d 个未完成视频，必须先完成"
                    % len(incomplete_videos)
                )
                for item_number, video in enumerate(incomplete_videos, 1):
                    active_logger.info(
                        "\n[CHAPTER] 播放视频 %d/%d: %s"
                        % (
                            item_number,
                            len(incomplete_videos),
                            video["name"][:30],
                        )
                    )
                    try:
                        video_items = page.locator(VIDEO_ITEM_SELECTOR)
                        if video["index"] >= await video_items.count():
                            active_logger.warn(
                                "[CHAPTER] 视频条目已变化，无法定位"
                            )
                            return False
                        await video_items.nth(video["index"]).click()
                        await page.wait_for_timeout(1000)
                        await page.wait_for_selector(
                            "video",
                            state="attached",
                            timeout=10000,
                        )
                        await page.evaluate(
                            """() => {
                                const video = document.querySelector('video');
                                if (video) {
                                    video.muted = true;
                                    video.play().catch(() => {});
                                }
                            }"""
                        )
                        completed = await completion_handler(
                            page,
                            logger_instance=active_logger,
                        )
                        if not completed:
                            active_logger.warn(
                                "[CHAPTER] 视频未完成，无法进入测验"
                            )
                            return False
                        await page.go_back()
                        await page.wait_for_timeout(1000)
                    except Exception as exc:
                        active_logger.warn(
                            "[CHAPTER] 视频播放失败: %s"
                            % str(exc)[:30]
                        )
                        return False
            elif videos:
                active_logger.info("[CHAPTER] 所有视频已完成")
            else:
                active_logger.info("[CHAPTER] 未找到视频内容")

        if test_handler and test_handler.questions_data:
            active_logger.info("\n[CHAPTER] 步骤2: 完成章节测验")
            if answer_handler is None:
                # 延迟导入避免 tasks 为兼容导出本模块时产生循环依赖。
                from modules.tasks import handle_test_page

                answer_handler = handle_test_page
            test_success = await answer_handler(
                page,
                test_handler.questions_data,
                auto_submit,
            )
            if not test_success:
                active_logger.warn("[CHAPTER] 测验失败")
                return False
            active_logger.info("[CHAPTER] 测验完成")
        else:
            active_logger.info(
                "\n[CHAPTER] 步骤2: 无测验或测验已完成，跳过"
            )

        active_logger.info("\n[CHAPTER] 步骤3: 验证完成状态")
        videos = await status_getter(
            page,
            logger_instance=active_logger,
        )
        incomplete = [video for video in videos if not video["completed"]]
        if incomplete:
            active_logger.warn(
                "[CHAPTER] 仍有 %d 个视频未完成，无法进入下一章"
                % len(incomplete)
            )
            return False

        active_logger.info("\n" + "=" * 60)
        active_logger.info("[CHAPTER] 章节学习完成，可进入下一章")
        active_logger.info("=" * 60)
        return True
    except Exception as exc:
        active_logger.error(
            "[CHAPTER] 章节学习异常: %s" % str(exc)[:50]
        )
        return False
