# encoding=utf-8
"""前端契约：课程表编辑器 + 工具调用确认卡片 + 回退路径。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _frontend() -> str:
    return (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _page() -> str:
    for path in (PROJECT_ROOT / "web").glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "view-tianyi" in text:
            return text
    raise AssertionError("tianyi page not found")


def test_course_plan_editor_is_present():
    html = _page()
    assert 'id="course-plan-list"' in html
    assert 'id="course-plan-save-btn"' in html
    assert "addCoursePlanRow()" in html
    assert "saveCoursePlans(true)" in html

    frontend = _frontend()
    for name in (
        "normalizeCoursePlanEntry",
        "coursePlanRowHtml",
        "renderCoursePlans",
        "addCoursePlanRow",
        "removeCoursePlanRow",
        "gatherCoursePlans",
        "loadCoursePlans",
        "saveCoursePlans",
    ):
        assert f"function {name}" in frontend
    assert "apiCall('get_course_plans')" in frontend
    assert "apiCall('save_course_plans', { courses })" in frontend
    assert "void loadCoursePlans();" in frontend


def test_agent_chat_uses_tools_first_then_text_fallback():
    frontend = _frontend()
    assert "async function requestTianyiAgent(" in frontend
    assert "apiCall('tianyi_agent_chat', { config: cfg, messages })" in frontend
    assert "async function runTianyiTextChat(" in frontend
    assert "apiCall('chat_with_ai'" in frontend
    assert "result.mode === 'fallback'" in frontend

    sender = frontend.split("async function sendTianyiMessage()", 1)[1].split(
        "async function requestTianyiAgent(", 1
    )[0]
    assert "detectTianyiCommand(text)" in sender
    assert "requestTianyiAgent(cfg, text)" in sender
    assert "renderTianyiPending(agentResult.pending)" in sender


def test_pending_confirmation_card_flow():
    frontend = _frontend()
    for name in (
        "renderTianyiPending",
        "confirmTianyiAction",
        "cancelTianyiAction",
    ):
        assert f"function {name}" in frontend
    assert "apiCall('confirm_tianyi_action', { id })" in frontend
    assert "apiCall('cancel_tianyi_action', { id })" in frontend
    assert "tianyi-pending" in frontend


def test_launcher_wires_catalog_providers_as_forced_refresh():
    source = (PROJECT_ROOT / "\u7edf\u4e00\u542f\u52a8\u5668.py").read_text(
        encoding="utf-8"
    )
    assert "force_refresh=True" in source
    assert source.count("force_refresh=True") >= 2


def test_help_text_and_examples_cover_new_capabilities():
    frontend = _frontend()
    help_text = frontend.split("function tianyiHelpText() {", 1)[1].split(
        "\n        }", 1
    )[0]
    for keyword in (
        "单刷一门课",
        "课程表",
        "拉取账号课程",
        "刷完关掉程序",
        "人格提示词",
    ):
        assert keyword in help_text

    html = _page()
    assert "你可以这样对我说" in html
    for example in (
        "帮我刷毛概，不用做题",
        "课程表里有哪些课",
        "重新拉一下学习通课程",
        "刷完关掉程序",
    ):
        assert example in html
    assert "tianyiQuickSend('帮我刷毛概，不用做题')" in html


def test_confirm_result_detail_is_rendered():
    frontend = _frontend()
    confirmer = frontend.split("async function confirmTianyiAction(", 1)[1].split(
        "async function cancelTianyiAction(", 1
    )[0]
    assert "result?.detail" in confirmer
    assert "已执行：" in confirmer


def test_bridge_exposes_new_agent_and_course_methods():
    api = (PROJECT_ROOT / "src" / "launcher_api.py").read_text(encoding="utf-8")
    for name in (
        "get_course_plans",
        "save_course_plans",
        "resolve_course",
        "start_course",
        "tianyi_agent_chat",
        "confirm_tianyi_action",
        "cancel_tianyi_action",
    ):
        assert f"def {name}(" in api
