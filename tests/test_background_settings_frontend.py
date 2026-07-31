from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_custom_background_sources_are_bounded_and_protocol_checked():
    validate_source = function_source(
        "validateCustomBackgroundSource",
        "applyBackground",
    )

    assert "CUSTOM_BACKGROUND_MAX_FILE_BYTES = 2 * 1024 * 1024" in FRONTEND
    assert "CUSTOM_BACKGROUND_DATA_URL_PATTERN" in validate_source
    assert "CUSTOM_BACKGROUND_MAX_DATA_URL_LENGTH" in validate_source
    assert "CUSTOM_BACKGROUND_MAX_REMOTE_URL_LENGTH" in validate_source
    assert "parsed.protocol !== 'https:'" in validate_source
    assert "parsed.protocol !== 'http:'" in validate_source


def test_custom_background_entry_points_validate_before_mutating_preferences():
    main_source = function_source("applyCustomBackground", "triggerBgFileInput")
    modal_source = function_source("applyModalCustomBg", "handleBgFileUpload")

    for source in (main_source, modal_source):
        assert "validateCustomBackgroundSource" in source
        assert source.index("if (!source.ok)") < source.index(
            "state.preferences.bgUrl = source.url"
        )


def test_local_background_upload_has_size_and_read_error_guards():
    upload_source = function_source("handleBgFileUpload", "resetBackground")

    assert "file.size > CUSTOM_BACKGROUND_MAX_FILE_BYTES" in upload_source
    assert "本地背景图片不能超过 2 MB" in upload_source
    assert "reader.onerror" in upload_source
    assert "读取背景图片失败" in upload_source
    assert "validateCustomBackgroundSource(e.target.result)" in upload_source


def test_reset_background_restores_all_visual_defaults_and_persists_them():
    reset_source = function_source("resetBackground", "markBackendDisconnected")

    assert "state.preferences.glassBlur = 16" in reset_source
    assert "state.preferences.overlayStrength = 70" in reset_source
    assert "strengthSlider.value = 70" in reset_source
    assert "strengthValueEl.textContent = '70%'" in reset_source
    assert "overlayStrength: 70" in reset_source
