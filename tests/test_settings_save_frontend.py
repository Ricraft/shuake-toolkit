from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_identical_settings_saves_share_one_in_flight_promise():
    save_source = function_source("saveSettings", "cancelShutdown")

    assert "const settingsSaveInFlight = new Map()" in FRONTEND
    assert "settingsSaveInFlight.get(key)" in save_source
    assert "if (!record)" in save_source
    assert "settingsSaveInFlight.set(key, record)" in save_source
    assert "const result = await record.promise" in save_source
    assert "return result" in save_source
    assert save_source.index("settingsSaveInFlight.get(key)") < (
        save_source.index("if (!record)")
    )


def test_distinct_settings_saves_are_serialized_and_only_latest_renders():
    save_source = function_source("saveSettings", "cancelShutdown")

    assert "let settingsSaveTail = Promise.resolve()" in FRONTEND
    assert ".then(() => executeSettingsSave(payload, sequence))" in save_source
    assert "settingsSaveTail = promise.catch(() => {})" in save_source
    assert "latestSettingsSaveSequence = sequence" in save_source
    assert "record.sequence === latestSettingsSaveSequence" in save_source
    assert "renderSettings(result.state.settings)" in save_source


def test_save_button_and_success_feedback_follow_unique_save_lifecycle():
    button_source = function_source(
        "syncSettingsSaveButton",
        "executeSettingsSave",
    )
    save_source = function_source("saveSettings", "cancelShutdown")

    assert "settingsSavePendingCount > 0" in button_source
    assert "button.disabled = busy" in button_source
    assert "正在保存" in button_source
    assert "record.successToastShown" in save_source
    assert "record.sequence === latestSettingsSaveSequence" in save_source
    assert "配置已保存" in save_source
    assert "settingsSavePendingCount += 1" in save_source
    assert "settingsSavePendingCount - 1" in save_source
    assert "syncSettingsSaveButton()" in save_source
