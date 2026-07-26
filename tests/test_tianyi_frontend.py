from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _function_source(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_tianyi_control_actions_propagate_backend_failures():
    frontend = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    core_action = _function_source(
        frontend,
        "async function handleCoreAction",
        "async function toggleQuestionBank",
    )
    qb_action = _function_source(
        frontend,
        "async function toggleQuestionBank",
        "async function saveAndPerform",
    )
    tianyi_action = _function_source(
        frontend,
        "async function execTianyiAction",
        "/* ---------- 发送与 AI 对话 ---------- */",
    )

    assert "return result" in core_action
    assert "return { ok: false" in core_action
    assert "return result" in qb_action
    assert "return { ok: false" in qb_action
    assert "const r = await handleCoreAction('yatori')" in tianyi_action
    assert "const r = await handleCoreAction('autovisor')" in tianyi_action
    assert "const r = await toggleQuestionBank()" in tianyi_action
    assert "ok: !!r?.ok" in tianyi_action
    assert "启动失败：" in tianyi_action
    assert "停止失败：" in tianyi_action


def test_tianyi_local_assets_referenced_by_ui_exist():
    frontend = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = next(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "web").glob("*.html")
        if "view-tianyi" in path.read_text(encoding="utf-8", errors="ignore")
    )
    styles = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    referenced = set(
        re.findall(
            r"assets/([A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp|svg))",
            "\n".join((frontend, html, styles)),
            flags=re.IGNORECASE,
        )
    )

    assert referenced
    missing = [
        name
        for name in referenced
        if not (PROJECT_ROOT / "web" / "assets" / name).is_file()
    ]
    assert missing == []


def test_achievement_merge_accepts_legacy_backend_formats():
    frontend = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    parser = _function_source(
        frontend,
        "function parseAchievementStore",
        "function normalizeAchievementStore",
    )
    merger = _function_source(
        frontend,
        "function mergePreferencesWithAchievements",
        "function achievementIconMarkup",
    )

    assert "Array.isArray(raw)" in parser
    assert "includeLegacyTianyi" in parser
    assert "parseAchievementStore(" in merger
    assert "incoming?.achievements" in merger
    assert "!!incoming?.tianyiAchievementShown" in merger
    assert "remoteResetToken > localResetToken" in merger
