from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (PROJECT_ROOT / "web" / "现代启动器_UI_预览.html").read_text(
    encoding="utf-8"
)


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_direct_preference_controls_use_the_shared_serial_queue():
    save_source = function_source("savePreference", "savePreferenceSilent")
    silent_source = function_source(
        "savePreferenceSilent",
        "flushBackgroundPreferenceSaves",
    )

    assert "savePreferencesInBackground({ [key]: value })" in save_source
    assert "savePreferencesInBackground({ [key]: value })" in silent_source
    assert "apiCall('save_preference'" not in save_source
    assert "apiCall('save_preference'" not in silent_source
    assert "state.preferences[key] = previous" not in save_source


def test_tianyi_auto_save_stages_locally_before_starting_debounce():
    stage_source = function_source(
        "stageTianyiConfig",
        "cancelPendingTianyiConfigSave",
    )
    auto_source = function_source("autoSaveTianyiConfig", "syncTianyiAiField")

    assert "state.preferences[TIANYI_PREF_KEY] = cfg" in stage_source
    assert "persistStoredPreferences()" in stage_source
    assert auto_source.index("stageTianyiConfig()") < auto_source.index("setTimeout(")
    assert "cancelPendingTianyiConfigSave()" in auto_source
    assert "window._tianyiSaveTimer = null" in auto_source


def test_manual_tianyi_save_cancels_debounce_and_tracks_button_lifecycle():
    save_source = function_source("saveTianyiConfig", "autoSaveTianyiConfig")
    button_source = function_source(
        "syncTianyiConfigSaveButton",
        "saveTianyiConfig",
    )

    assert "if (show) cancelPendingTianyiConfigSave()" in save_source
    assert "savePreferencesInBackground({ [TIANYI_PREF_KEY]: cfg })" in save_source
    assert "tianyiConfigSavePendingCount += 1" in save_source
    assert "tianyiConfigSavePendingCount - 1" in save_source
    assert "button.disabled = busy" in button_source
    assert "保存中..." in button_source
    assert "id=\"tianyi-save-config-btn\"" in HTML


def test_tianyi_chat_history_uses_the_same_queue_and_clears_old_timer():
    persist_source = function_source(
        "persistTianyiChatHistoryNow",
        "cancelPendingTianyiChatSave",
    )
    schedule_source = function_source(
        "scheduleTianyiChatSave",
        "initTianyiPage",
    )

    assert "cancelPendingTianyiChatSave()" in persist_source
    assert "savePreferenceSilent(TIANYI_CHAT_PREF_KEY, history)" in persist_source
    assert "cancelPendingTianyiChatSave()" in schedule_source
    assert "window._tianyiChatSaveTimer = null" in schedule_source
    assert "void persistTianyiChatHistoryNow()" in schedule_source
