# encoding=utf-8
"""新功能可执行测试：用 Node 真实跑一遍课程表渲染函数。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="本机没有 node")


def _extract_render_functions() -> str:
    source = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function normalizeCoursePlanEntry(")
    end = source.index("function renderCoursePlans(")
    return source[start:end]


def _run_node(body: str) -> str:
    prelude = (
        "const escapeHtml = (value) => String(value ?? '').replace("
        "/[&<>\"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"
        "'\"':'&quot;',\"'\":'&#39;'}[ch]));\n"
    )
    script = prelude + _extract_render_functions() + "\n" + body
    completed = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_course_plan_row_renders_all_fields_and_states():
    html = _run_node(
        "process.stdout.write(coursePlanRowHtml({"
        "name: '高等数学A2',"
        "aliases: ['高数A2', '高数2'],"
        "core: 'autovisor',"
        "accountIndex: 2,"
        "courseUrl: 'https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=abc',"
        "skipQuestions: true,"
        "maxMinutes: '45'"
        "}, 0));"
    )
    assert 'data-field="name"' in html
    assert 'value="高等数学A2"' in html
    assert 'data-field="aliases"' in html
    assert 'value="高数A2, 高数2"' in html
    assert '<option value="autovisor" selected>' in html
    assert 'data-field="accountIndex"' in html
    assert 'value="2"' in html
    assert 'data-field="courseUrl"' in html
    assert "recruitAndCourseId=abc" in html
    assert 'data-field="skipQuestions" type="checkbox" checked' in html
    assert 'data-field="maxMinutes"' in html
    assert 'value="45"' in html


def test_course_plan_row_escapes_user_input():
    html = _run_node(
        "process.stdout.write(coursePlanRowHtml({"
        "name: '<script>alert(1)</script>',"
        "aliases: [],"
        "core: 'yatori'"
        "}, 0));"
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_normalize_course_plan_entry_defaults_and_alias_join():
    output = _run_node(
        "process.stdout.write(JSON.stringify({"
        "empty: normalizeCoursePlanEntry({}),"
        "joined: normalizeCoursePlanEntry({aliases: ['a', 'b'], core: 'autovisor'})"
        "}));"
    )
    data = json.loads(output)
    assert data["empty"]["core"] == "yatori"
    assert data["empty"]["accountIndex"] == 0
    assert data["empty"]["skipQuestions"] is False
    assert data["empty"]["name"] == ""
    assert data["joined"]["aliases"] == "a, b"
    assert data["joined"]["core"] == "autovisor"
