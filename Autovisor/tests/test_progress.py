import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.progress import show_course_progress

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
