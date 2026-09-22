# encoding=utf-8
"""契约测试：Yatori 学习时长与章节任务开关必须能读、能保存且不被默认值覆盖。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _frontend() -> str:
    return (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _block(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_xxt_course_settings_are_rendered_with_real_fields():
    source = _frontend()
    renderer = _block(
        source,
        "function renderYatoriXxtSettings(user, isXXT)",
        "function normalizeYatoriUser",
    )
    for field in (
        "studyTime",
        "cxNode",
        "cxChapterTestSw",
        "cxWorkSw",
        "cxExamSw",
    ):
        assert 'data-field="%s"' % field in renderer
    assert "renderYatoriXxtSettings(user, isXXT)" in source


def test_saved_course_switches_are_not_clobbered_by_defaults():
    gather = _block(
        _frontend(),
        "function gatherYatoriSettings()",
        "function gatherAutovisorSettings()",
    )
    for field in (
        "studyTime",
        "cxNode",
        "cxChapterTestSw",
        "cxWorkSw",
        "cxExamSw",
    ):
        assert "existingUser?.coursesCustom?.%s" % field in gather

    assert "studyTime:st?st.value.trim():''" not in gather
    assert "cxChapterTestSw:cct?(cct.checked?1:0):1," not in gather
    assert "cxWorkSw:cw?(cw.checked?1:0):1," not in gather
    assert "cxExamSw:ce?(ce.checked?1:0):1," not in gather


def test_normalization_keeps_course_switches_round_trip():
    model = _block(
        _frontend(),
        "function normalizeYatoriUser(",
        "function normalizeAutovisorAccount(",
    )
    for field in (
        "studyTime",
        "cxNode",
        "cxChapterTestSw",
        "cxWorkSw",
        "cxExamSw",
    ):
        assert field in model


def test_platform_switch_toggles_xxt_settings_visibility():
    sync = _block(
        _frontend(),
        "function syncYatoriPlatformCard(",
        "function updateYatoriCourseFilterUI(",
    )
    assert 'data-role="xxt-course-settings"' in sync
    assert "pc==='XUEXITONG'" in sync


def _settings_html() -> str:
    for path in (PROJECT_ROOT / "web").glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'id="y-fetch-models-btn"' in text:
            return text
    raise AssertionError("settings page not found")


def test_model_name_and_api_key_fields_are_vertically_aligned():
    """模型名称的“获取模型”按钮不能再撑高标题行，否则和 API Key 错位。"""
    html = _settings_html()

    assert html.count('<span class="input-label">模型名称</span>') == 2
    assert 'style="display: flex; align-items: center; gap: 0.5rem;"' not in html

    for model_id, button_id in (
        ("y-ai-model", "y-fetch-models-btn"),
        ("tianyi-ai-model", "tianyi-fetch-models-btn"),
    ):
        input_at = html.index('id="%s"' % model_id)
        start = html.rindex('<div class="input-group">', 0, input_at)
        row = html[start : html.index("</div>", input_at)]
        assert 'display: flex; gap: 0.5rem;' in row, model_id
        assert button_id in row, model_id
