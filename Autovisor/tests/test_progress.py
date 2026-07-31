import asyncio
import sys
from pathlib import Path

import pytest
from playwright._impl._errors import TargetClosedError


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.diagnostics import RateLimitedDiagnostics
from modules.progress import (
    _parse_percentage,
    get_course_progress,
    show_course_progress,
    show_progress,
)

sys.path.remove(_AUTOVISOR_ROOT)


def test_normal_course_progress_does_not_turn_eighty_percent_into_complete(capsys):
    show_course_progress("完成进度:", "80%")
    output = capsys.readouterr().out
    assert " 80%" in output
    assert " 100%" not in output


def test_progress_display_clamps_invalid_ranges(capsys):
    show_course_progress("完成进度:", "150%")
    assert " 100%" in capsys.readouterr().out

    show_course_progress("完成进度:", "not-ready")
    assert " 0%" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("已签到 100/1000，进度 10%", 10),
        ("签到 8/10", 80),
        ("签到进度 80", 80),
        ("完成 120%", 100),
    ],
)
def test_percentage_parser_prefers_explicit_units_and_ratios(
    text,
    expected,
):
    assert _parse_percentage(text) == expected


def test_percentage_parser_rejects_ambiguous_bare_numbers():
    with pytest.raises(ValueError, match="多个无单位数字"):
        _parse_percentage("总计 100 人，已签到 80 人")


def test_unknown_download_size_displays_bytes_without_division_by_zero(capsys):
    show_progress("下载进度:", current=1234, total=0)

    output = capsys.readouterr().out
    assert "已下载 1234 bytes" in output


@pytest.mark.parametrize(
    ("current", "expected"),
    [(200, "100%"), (-10, "0%")],
)
def test_known_download_progress_clamps_percentage(
    current,
    expected,
    capsys,
):
    show_progress("下载进度:", current=current, total=100)

    assert expected in capsys.readouterr().out


class _TextLocator:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    @property
    def first(self):
        return self

    async def text_content(self, **_kwargs):
        if self.error:
            raise self.error
        return self.value


class _MeetingProgressPage:
    def __init__(self, text):
        self.text = text
        self.evaluate_calls = 0

    def locator(self, selector):
        assert selector == ".qiandao-num"
        return _TextLocator(self.text)

    async def evaluate(self, _script):
        self.evaluate_calls += 1
        raise AssertionError("valid sign-in text must not use video fallback")


def test_meeting_progress_accepts_decorated_percentage_text():
    page = _MeetingProgressPage("签到进度 80%")

    result = asyncio.run(
        get_course_progress(page, is_meeting_class=True)
    )

    assert result == "100%"
    assert page.evaluate_calls == 0


class _AmbiguousMeetingProgressPage:
    def locator(self, _selector):
        return _TextLocator("总计 100 人，已签到 80 人")

    async def evaluate(self, _script):
        return {
            "duration": 100,
            "currentTime": 50,
            "ended": False,
            "paused": False,
            "readyState": 4,
        }


def test_meeting_progress_uses_video_when_sign_in_text_is_ambiguous():
    result = asyncio.run(
        get_course_progress(
            _AmbiguousMeetingProgressPage(),
            is_meeting_class=True,
        )
    )

    assert result == "50%"


class _BrokenMeetingProgressPage:
    def locator(self, _selector):
        return _TextLocator(error=ValueError("sign-in changed"))

    async def evaluate(self, _script):
        raise RuntimeError("video state changed")


class _WarningLogger:
    def __init__(self):
        self.warnings = []

    def warn(self, message):
        self.warnings.append(message)


def test_meeting_progress_logs_repeated_fallback_failure_once():
    warning_logger = _WarningLogger()
    diagnostics = RateLimitedDiagnostics(
        warning_logger,
        interval_seconds=30,
        clock=lambda: 10,
    )
    page = _BrokenMeetingProgressPage()

    first = asyncio.run(
        get_course_progress(
            page,
            is_meeting_class=True,
            diagnostics=diagnostics,
        )
    )
    second = asyncio.run(
        get_course_progress(
            page,
            is_meeting_class=True,
            diagnostics=diagnostics,
        )
    )

    assert first == second == "0%"
    assert len(warning_logger.warnings) == 1
    assert "读取见面课进度失败" in warning_logger.warnings[0]
    assert "RuntimeError: video state changed" in warning_logger.warnings[0]


class _SharedVideoPage:
    def __init__(self, state):
        self.state = state

    async def evaluate(self, _script):
        return self.state


def test_shared_video_progress_rejects_transition_state_and_honors_threshold():
    transition = _SharedVideoPage(
        {
            "duration": 100,
            "currentTime": 0,
            "ended": True,
            "paused": True,
            "readyState": 4,
        }
    )
    completed = _SharedVideoPage(
        {
            "duration": 100,
            "currentTime": 98,
            "ended": False,
            "paused": False,
            "readyState": 4,
        }
    )

    assert asyncio.run(
        get_course_progress(transition, is_national_wisdom=True)
    ) == "0%"
    assert asyncio.run(
        get_course_progress(
            completed,
            is_national_wisdom=True,
            completion_threshold=0.98,
        )
    ) == "100%"


class _ClosedVideoPage:
    async def evaluate(self, _script):
        raise TargetClosedError("page closed")


def test_progress_reader_propagates_closed_page_instead_of_returning_zero():
    with pytest.raises(TargetClosedError, match="page closed"):
        asyncio.run(
            get_course_progress(
                _ClosedVideoPage(),
                is_national_wisdom=True,
            )
        )


class _HoverArea:
    async def hover(self, **_kwargs):
        return None

    async def bounding_box(self):
        return {"x": 0, "y": 0}


class _Mouse:
    async def move(self, _x, _y):
        return None


class _CurrentLesson:
    async def query_selector(self, selector):
        if selector == ".progress-num":
            return None
        if selector == ".time_icofinish":
            return object()
        raise AssertionError(selector)


class _NormalProgressPage:
    def __init__(self):
        self.mouse = _Mouse()

    async def wait_for_selector(self, selector, **_kwargs):
        assert selector == ".videoArea"
        return object()

    def locator(self, selector):
        assert selector == ".videoArea"
        return _HoverArea()

    async def query_selector(self, selector):
        assert selector == ".current_play"
        return _CurrentLesson()


def test_new_normal_page_uses_completion_icon_when_progress_text_is_absent():
    result = asyncio.run(
        get_course_progress(_NormalProgressPage(), is_new_version=True)
    )

    assert result == "100%"
