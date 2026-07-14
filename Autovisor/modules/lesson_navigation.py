# encoding=utf-8
"""课程卡片过滤与导航状态，不依赖 Playwright。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


CourseCard = Mapping[str, Any]


class SelectionReason(str, Enum):
    SELECTED = "selected"
    ROUND_RESET = "round_reset"
    NO_CANDIDATES = "no_candidates"
    TEST_RETRY_EXHAUSTED = "test_retry_exhausted"


@dataclass(frozen=True)
class LessonSelection:
    lesson: CourseCard | None
    reason: SelectionReason


def pending_course_cards(cards: Iterable[CourseCard]) -> list[CourseCard]:
    """返回进度未达到 100% 的卡片，并保留页面顺序。"""
    return [card for card in cards if card["progress"] < 100]


def find_course_card(cards: Iterable[CourseCard], key: str) -> CourseCard | None:
    return next((card for card in cards if card["key"] == key), None)


@dataclass
class LessonNavigationState:
    """跟踪一轮扫描中已经尝试、跳过及测验重试的卡片。"""

    test_retry_limit: int = 5
    tried_keys: set[str] = field(default_factory=set)
    skipped_keys: set[str] = field(default_factory=set)
    completed_keys: set[str] = field(default_factory=set)
    test_attempts: dict[str, int] = field(default_factory=dict)

    def choose(self, pending_lessons: Iterable[CourseCard]) -> LessonSelection:
        candidates = [
            lesson
            for lesson in pending_lessons
            if lesson["key"] not in self.skipped_keys
            and lesson["key"] not in self.completed_keys
        ]
        if not candidates:
            return LessonSelection(None, SelectionReason.NO_CANDIDATES)

        for lesson in candidates:
            if lesson["key"] not in self.tried_keys:
                return LessonSelection(lesson, SelectionReason.SELECTED)

        retryable = [
            lesson
            for lesson in candidates
            if lesson.get("type") != "test"
            or self.test_attempts.get(lesson["key"], 0) < self.test_retry_limit
        ]
        if not retryable:
            return LessonSelection(None, SelectionReason.TEST_RETRY_EXHAUSTED)

        self.tried_keys.clear()
        return LessonSelection(retryable[0], SelectionReason.ROUND_RESET)

    def begin_test_attempt(self, key: str) -> int | None:
        attempt = self.test_attempts.get(key, 0) + 1
        self.test_attempts[key] = attempt
        if attempt > self.test_retry_limit:
            self.skipped_keys.add(key)
            return None
        return attempt

    def mark_attempted(self, key: str, *, test_completed: bool = False) -> None:
        self.tried_keys.add(key)
        if test_completed:
            self.test_attempts.pop(key, None)
            self.completed_keys.add(key)

    def skip(self, key: str) -> None:
        self.skipped_keys.add(key)
        self.tried_keys.discard(key)
