from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_background_preference_queue_catches_and_coalesces_updates():
    flush_source = function_source(
        "flushBackgroundPreferenceSaves",
        "savePreferencesInBackground",
    )
    queue_source = function_source(
        "savePreferencesInBackground",
        "getCurrentTheme",
    )

    assert "pendingBackgroundPreferences = {}" in FRONTEND
    assert "backgroundPreferenceSavePromise = null" in FRONTEND
    assert "while (Object.keys(pendingBackgroundPreferences).length)" in flush_source
    assert "const payload = pendingBackgroundPreferences" in flush_source
    assert "pendingBackgroundPreferences = {}" in flush_source
    assert "await apiCall('save_preference', payload)" in flush_source
    assert "catch (error)" in flush_source
    assert "后端暂时无法同步偏好" in flush_source
    assert "...pendingBackgroundPreferences" in queue_source
    assert "...payload" in queue_source
    assert ".then(flushBackgroundPreferenceSaves)" in queue_source
    assert ".finally(" in queue_source


def test_fire_and_forget_preferences_use_the_tracked_queue():
    unsafe_pattern = "try { apiCall('save_preference'"

    assert unsafe_pattern not in FRONTEND
    assert "achievementSaveQueue" not in FRONTEND
    assert FRONTEND.count("savePreferencesInBackground(") >= 8
    assert "savePreferencesInBackground({ theme: normalized })" in FRONTEND
    assert "savePreferencesInBackground({ glassBlur: blurValue })" in FRONTEND
    assert "savePreferencesInBackground({ overlayStrength: strength })" in FRONTEND


def test_log_export_handles_rejected_api_promises():
    export_source = function_source("exportLogs", "renderAutovisorActivity")

    assert "apiCall('export_logs'" in export_source
    assert ".catch(error =>" in export_source
    assert "showToast(error?.message||'导出失败', 'error')" in export_source
