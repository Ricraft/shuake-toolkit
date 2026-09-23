from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'web' / '现代启动器_UI_预览.html').read_text(encoding='utf-8')
JS = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')


def test_runtime_limit_setting_explains_scope_and_default():
    assert 'id="pref-yatori-max-runtime"' in HTML
    assert 'type="number" min="0" max="10080" step="1" value="0"' in HTML
    assert 'onchange="saveYatoriMaxRuntime(this)"' in HTML
    for text in ('进入运行状态后开始计时', '提前完成或手动停止会取消计时',
                 '仅停止 Yatori', '不保证学习完成', '刷完自动关机',
                 '运行中修改仅下次启动生效', '学习时长设置不同'):
        assert text in HTML
    assert 'yatoriMaxRuntimeMinutes: 0' in JS
    assert "preferences?.yatoriMaxRuntimeMinutes ?? 0" in JS
    assert "return savePreference('yatoriMaxRuntimeMinutes', minutes)" in JS


def test_runtime_limit_accepts_12_and_zero_but_rejects_invalid_values():
    handler = JS.split('        function saveYatoriMaxRuntime(input) {', 1)[1].split(
        '\n        function markLocalPreferenceMutation', 1)[0]
    script = '''
        const calls = [];
        const state = { preferences: { yatoriMaxRuntimeMinutes: 7 } };
        function showToast(message) { calls.push(['error', message]); }
        function savePreference(key, value) { calls.push([key, value]); }
        function saveYatoriMaxRuntime(input) {''' + handler + '''
        for (const value of ['12', '0', '-1', '1.5', '10081', '']) {
            const input = { value };
            saveYatoriMaxRuntime(input);
            calls.push(['display', input.value]);
        }
        console.log(JSON.stringify(calls));
    '''
    result = subprocess.run(['node', '-e', script], text=True, encoding="utf-8", capture_output=True, check=True)
    calls = json.loads(result.stdout)
    assert calls[:4] == [
        ['yatoriMaxRuntimeMinutes', 12], ['display', '12'],
        ['yatoriMaxRuntimeMinutes', 0], ['display', '0'],
    ]
    assert [item for item in calls if item[0] == 'yatoriMaxRuntimeMinutes'] == [
        ['yatoriMaxRuntimeMinutes', 12], ['yatoriMaxRuntimeMinutes', 0],
    ]
    assert [item[1] for item in calls if item[0] == 'display'] == [
        '12', '0', '7', '7', '7', '7',
    ]


def test_runtime_limit_control_hydrates_remote_value_without_saving():
    sync = JS.split('        function syncPreferenceControls(preferences = state.preferences) {', 1)[1].split(
        '\n        function saveYatoriMaxRuntime', 1)[0]
    script = '''
        const PREFERENCE_CONTROL_IDS = {};
        const field = { value: '0' };
        const document = { getElementById: id => id === 'pref-yatori-max-runtime' ? field : null };
        const state = { preferences: {} };
        function syncPreferenceControls(preferences = state.preferences) {''' + sync + '''
        syncPreferenceControls({ yatoriMaxRuntimeMinutes: 12 });
        const restored = field.value;
        syncPreferenceControls({ yatoriMaxRuntimeMinutes: 0 });
        console.log(JSON.stringify([restored, field.value]));
    '''
    result = subprocess.run(['node', '-e', script], text=True, encoding='utf-8',
                            capture_output=True, check=True)
    assert json.loads(result.stdout) == ['12', '0']
