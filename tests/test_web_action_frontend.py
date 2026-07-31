from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_web_action_lock_coalesces_same_action_and_releases_in_finally():
    lock_source = function_source(
        "getWebActionKey",
        "requestWebAction",
    )

    assert "const webActionInFlight = new Set()" in FRONTEND
    assert "webActionInFlight.has(key)" in lock_source
    assert "webActionInFlight.add(key)" in lock_source
    assert "busy: true" in lock_source
    assert "该操作正在执行，请稍候" in lock_source
    assert "finally" in lock_source
    assert "webActionInFlight.delete(key)" in lock_source
    assert lock_source.index("webActionInFlight.add(key)") < lock_source.index(
        "return await callback()"
    )
    assert lock_source.index("return await callback()") < lock_source.index(
        "webActionInFlight.delete(key)"
    )


def test_direct_web_action_entry_points_share_the_lock():
    core_source = function_source("handleCoreAction", "toggleQuestionBank")
    toggle_source = function_source("toggleQuestionBank", "saveAndPerform")
    save_source = function_source("saveAndPerform", "handleWebActionResult")
    perform_source = function_source("performAction", "openYatoriUpdateDialog")
    update_source = function_source(
        "openYatoriUpdateDialog",
        "setText",
    )

    assert "runWebActionLocked(action, [core]" in core_source
    assert "requestWebAction(" in toggle_source
    assert "runWebActionLocked(action, []" in save_source
    assert "requestWebAction(action, args)" in perform_source
    assert "runWebActionLocked('show_update_dialog', []" in update_source
