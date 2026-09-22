# encoding=utf-8
"""契约测试：洛天依人格提示词可自定义，控制指令与安全规则不可被覆盖。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _frontend() -> str:
    return (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _html() -> str:
    candidates = [
        path
        for path in (PROJECT_ROOT / "web").glob("*.html")
        if "view-tianyi" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert candidates
    return candidates[0].read_text(encoding="utf-8")


def test_preferences_service_exposes_persona_default():
    service = (PROJECT_ROOT / "src" / "preferences_service.py").read_text(
        encoding="utf-8"
    )
    assert '"tianyiPersona": ""' in service


def test_tianyi_page_exposes_persona_editor():
    html = _html()
    assert 'id="tianyi-persona"' in html
    assert 'id="tianyi-persona-hint"' in html
    assert "autoSaveTianyiPersona()" in html
    assert "saveTianyiPersona(true)" in html
    assert "resetTianyiPersona()" in html


def test_system_prompt_uses_custom_persona_and_keeps_rules_fixed():
    source = _frontend()
    prompt = source.split("function buildTianyiSystemPrompt()", 1)[1].split(
        "function tianyiTextHasAny", 1
    )[0]
    assert "getTianyiPersonaText()" in prompt
    assert "任何人格设定都不能覆盖" in prompt
    for marker in (
        "[CMD:start_all]",
        "[CMD:stop_all]",
        "[CMD:start_yatori]",
        "[CMD:stop_yatori]",
        "[CMD:start_autovisor]",
        "[CMD:stop_autovisor]",
        "[CMD:start_qb]",
        "[CMD:stop_qb]",
        "[CMD:status]",
        "[CMD:view:dashboard]",
    ):
        assert marker in prompt


def test_persona_is_loaded_and_persisted_through_preference_queue():
    source = _frontend()
    helpers = source.split("function getTianyiPersonaText()", 1)[1].split(
        "function buildTianyiSystemPrompt()", 1
    )[0]
    assert "state.preferences?.tianyiPersona" in helpers
    assert "TIANYI_DEFAULT_PERSONA" in helpers
    assert "TIANYI_PERSONA_MAX" in helpers

    saver = source.split("async function saveTianyiPersona(", 1)[1].split(
        "function resetTianyiPersona()", 1
    )[0]
    assert "savePreferencesInBackground({ tianyiPersona: value })" in saver
    assert "loadTianyiPersona();" in source


def test_default_persona_keeps_luo_tianyi_traits():
    source = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    block = source.split("const TIANYI_DEFAULT_PERSONA", 1)[1].split(
        "const TIANYI_PERSONA_MAX", 1
    )[0]
    for keyword in (
        "洛天依",
        "天钿",
        "小笼包",
        "乐谱",
        "15 岁",
        "天依",
    ):
        assert keyword in block
    assert "AI / 语言模型" in block


def test_greeting_keeps_persona_and_introduces_single_course():
    source = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    init = source.split("function initTianyiPage()", 1)[1].split(
        "function updateTianyiBadge", 1
    )[0]
    assert "小笼包" in init
    assert "帮我刷毛概，不用做题" in init
