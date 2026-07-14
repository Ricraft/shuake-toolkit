# encoding=utf-8

import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.lesson_navigation import (
    LessonNavigationState,
    SelectionReason,
    find_course_card,
    pending_course_cards,
)

sys.path.remove(_AUTOVISOR_ROOT)


def _card(key: str, *, progress: int = 0, card_type: str = "video") -> dict:
    return {"key": key, "progress": progress, "type": card_type}


def test_pending_cards_preserve_page_order():
    cards = [_card("done", progress=100), _card("a", progress=20), _card("b")]
    assert [card["key"] for card in pending_course_cards(cards)] == ["a", "b"]


def test_find_course_card_returns_matching_key():
    cards = [_card("a"), _card("b")]
    assert find_course_card(cards, "b") is cards[1]
    assert find_course_card(cards, "missing") is None


def test_navigation_chooses_first_untried_card():
    state = LessonNavigationState()
    state.mark_attempted("a")
    choice = state.choose([_card("a"), _card("b")])
    assert choice.lesson["key"] == "b"
    assert choice.reason is SelectionReason.SELECTED


def test_navigation_resets_round_when_video_is_still_pending():
    state = LessonNavigationState()
    state.mark_attempted("video")
    choice = state.choose([_card("video")])
    assert choice.lesson["key"] == "video"
    assert choice.reason is SelectionReason.ROUND_RESET
    assert state.tried_keys == set()


def test_test_card_can_retry_until_configured_limit():
    state = LessonNavigationState(test_retry_limit=2)
    test_card = _card("test", card_type="test")

    assert state.begin_test_attempt("test") == 1
    state.mark_attempted("test")
    second = state.choose([test_card])
    assert second.lesson is test_card
    assert second.reason is SelectionReason.ROUND_RESET

    assert state.begin_test_attempt("test") == 2
    state.mark_attempted("test")
    exhausted = state.choose([test_card])
    assert exhausted.lesson is None
    assert exhausted.reason is SelectionReason.TEST_RETRY_EXHAUSTED


def test_skipped_cards_are_not_selected():
    state = LessonNavigationState(test_retry_limit=1)
    assert state.begin_test_attempt("test") == 1
    assert state.begin_test_attempt("test") is None
    choice = state.choose([_card("test", card_type="test")])
    assert choice.lesson is None
    assert choice.reason is SelectionReason.NO_CANDIDATES


def test_completed_test_clears_retry_counter():
    state = LessonNavigationState()
    state.begin_test_attempt("test")
    state.mark_attempted("test", test_completed=True)
    assert "test" not in state.test_attempts
    assert "test" in state.completed_keys
    choice = state.choose([_card("test", card_type="test")])
    assert choice.lesson is None
    assert choice.reason is SelectionReason.NO_CANDIDATES
