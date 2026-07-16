# encoding=utf-8
"""Sequential course execution with per-course failure isolation."""

from __future__ import annotations

from dataclasses import dataclass
import traceback
from urllib.parse import urlsplit

from playwright._impl._errors import TargetClosedError

from modules.course_session import CourseAuthenticationError, CourseSession


@dataclass(frozen=True)
class CourseRunSummary:
    total: int
    completed: int
    failed: int

    @property
    def is_complete(self) -> bool:
        return self.failed == 0 and self.completed == self.total


def _course_label(url: str, index: int, total: int) -> str:
    hostname = (urlsplit(url).hostname or "未知站点").lower()
    return f"第 {index}/{total} 门课程（{hostname}）"


async def run_course_queue(
    page,
    config,
    logger,
    *,
    course_worker,
    session_factory=CourseSession.from_url,
) -> CourseRunSummary:
    """Run every configured course, continuing after course-local failures."""
    course_urls = list(config.course_urls)
    completed = 0
    failed = 0

    for index, course_url in enumerate(course_urls, 1):
        print("==" * 10)
        label = _course_label(course_url, index, len(course_urls))
        logger.info(f"开始处理{label}")
        try:
            session = session_factory(course_url)
            await session.open(page, config, logger)
            await course_worker(
                page,
                course_url=course_url,
                **session.profile.working_loop_options(),
            )
        except (CourseAuthenticationError, TargetClosedError):
            raise
        except Exception as exc:
            failed += 1
            logger.error(
                f"{label}执行失败: {type(exc).__name__}: {str(exc)[:120]}"
            )
            logger.write_log(traceback.format_exc())
            continue

        completed += 1
        logger.info(f"{label}执行完成")

    summary = CourseRunSummary(
        total=len(course_urls),
        completed=completed,
        failed=failed,
    )
    if failed:
        logger.warn(
            f"课程队列结束: 成功 {completed} 门，失败 {failed} 门，共 {summary.total} 门"
        )
    return summary
