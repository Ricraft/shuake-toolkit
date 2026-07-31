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


def test_question_bank_saves_dedupe_identical_snapshots_and_serialize_changes():
    save_source = function_source("saveQbSettings", "manualSaveQbSettings")

    assert "let qbSettingsSaveTail = Promise.resolve()" in FRONTEND
    assert "const qbSettingsSaveInFlight = new Map()" in FRONTEND
    assert "qbSettingsSaveInFlight.get(key)" in save_source
    assert "qbSettingsSaveInFlight.set(key, record)" in save_source
    assert "qbSettingsSaveTail" in save_source
    assert ".then(() => apiCall('save_qb_settings', settings))" in save_source
    assert "qbSettingsSaveTail = promise.then(() => {}, () => {})" in save_source
    assert "const result = await record.promise" in save_source


def test_auto_save_registers_latest_edit_before_debounce_and_manual_cancels_it():
    save_source = function_source("saveQbSettings", "manualSaveQbSettings")
    auto_source = function_source("autoSaveQbSettings", "syncAutovisorMulti")

    assert "if (source !== 'auto') cancelPendingQbAutoSave()" in save_source
    assert "const sequence = ++qbSettingsSaveRequestSequence" in auto_source
    assert "latestQbSettingsSaveSequence = sequence" in auto_source
    assert auto_source.index("latestQbSettingsSaveSequence = sequence") < (
        auto_source.index("setTimeout(")
    )
    assert "saveQbSettings({ source: 'auto', requestSequence: sequence })" in auto_source


def test_only_latest_question_bank_request_updates_status_and_button_lifecycle():
    status_source = function_source("setQbSettingsSaveStatus", "saveQbSettings")
    button_source = function_source(
        "syncQbSettingsSaveButton",
        "setQbSettingsSaveStatus",
    )
    save_source = function_source("saveQbSettings", "manualSaveQbSettings")

    assert "sequence !== latestQbSettingsSaveSequence" in status_source
    assert "sequence === latestQbSettingsSaveSequence" in save_source
    assert "qbSettingsSavePendingCount > 0" in button_source
    assert "button.disabled" in button_source
    assert "qbSettingsSavePendingCount += 1" in save_source
    assert "qbSettingsSavePendingCount - 1" in save_source
    assert "id=\"qb-save-btn\"" in HTML
    assert "onclick=\"manualSaveQbSettings()\"" in HTML


def test_manual_save_owns_one_success_or_failure_notification():
    manual_source = function_source("manualSaveQbSettings", "autoSaveQbSettings")

    assert "题库配置已保存" in manual_source
    assert "题库设置保存失败" in manual_source
    assert "saveQbSettings().then" not in HTML
