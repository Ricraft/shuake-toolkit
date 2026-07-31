from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_remote_preference_hydration_rejects_stale_responses():
    apply_source = function_source(
        "applyRemotePreferences",
        "loadPreferences",
    )

    assert "let preferenceRequestSequence = 0" in FRONTEND
    assert "let preferenceAppliedSequence = 0" in FRONTEND
    assert "effectiveId < preferenceAppliedSequence" in apply_source
    assert "preferenceAppliedSequence = effectiveId" in apply_source
    assert "mergePreferencesWithAchievements(preferences)" in apply_source
    assert "syncPreferenceControls()" in apply_source
    assert "preferencesHydrated = true" in apply_source
    assert apply_source.index(
        "effectiveId < preferenceAppliedSequence"
    ) < apply_source.index("mergePreferencesWithAchievements(preferences)")


def test_all_remote_preference_reads_use_the_sequence_gate():
    load_source = function_source("loadPreferences", "savePreference")
    tianyi_source = function_source(
        "hydratePreferencesBeforeTianyiUnlock",
        "unlockTianyiTheme",
    )
    init_source = function_source("init", "initAboutPageEffects")

    assert "const requestId = ++preferenceRequestSequence" in load_source
    assert "applyRemotePreferences(" in load_source
    assert "const requestId = ++preferenceRequestSequence" in tianyi_source
    assert "applyRemotePreferences(prefs, requestId)" in tianyi_source
    assert "const preferenceRequestId = ++preferenceRequestSequence" in init_source
    assert "applyRemotePreferences(" in init_source
    assert "preferenceRequestId" in init_source


def test_first_local_preference_render_defers_to_initial_state():
    load_source = function_source("loadPreferences", "savePreference")

    assert "let initialPreferenceLoadPending = true" in FRONTEND
    assert "const fetchRemote = !initialPreferenceLoadPending" in load_source
    assert "initialPreferenceLoadPending = false" in load_source
    assert "if (!fetchRemote) return" in load_source
    assert load_source.index("applySavedBg()") < load_source.index(
        "if (!fetchRemote) return"
    )
    assert load_source.index("if (!fetchRemote) return") < load_source.index(
        "apiCall('get_preferences')"
    )


def test_local_preference_changes_fence_older_remote_reads():
    marker_source = function_source(
        "markLocalPreferenceMutation",
        "applyRemotePreferences",
    )
    save_source = function_source(
        "savePreference",
        "savePreferenceSilent",
    )
    background_source = function_source(
        "savePreferencesInBackground",
        "getCurrentTheme",
    )

    assert "++preferenceRequestSequence" in marker_source
    assert "preferenceAppliedSequence = requestId" in marker_source
    assert "savePreferencesInBackground({ [key]: value })" in save_source
    assert "state.preferences[key] = previous" not in save_source
    assert "markLocalPreferenceMutation()" in background_source


def test_applying_a_loaded_theme_does_not_write_it_back():
    set_theme_source = function_source("setTheme", "updateThemeIcons")
    apply_theme_source = function_source("applySavedTheme", "showExitModal")

    assert "setTheme(theme, { persist = true } = {})" in FRONTEND
    assert "if (persist)" in set_theme_source
    assert "savePreferencesInBackground({ theme: normalized })" in set_theme_source
    assert apply_theme_source.count("{ persist: false }") >= 2
