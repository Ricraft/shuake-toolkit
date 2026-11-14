"""Assistant edits exercise real config persistence without accounts/network."""
from copy import deepcopy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from 统一启动器 import UnifiedLauncher
from src.agent_settings_service import AgentSettingsService
from src.preferences_service import PreferencesService
from src.tianyi_agent_service import TianyiAgentService


def launcher_at(root):
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher.get_base_dir = lambda: str(root)
    launcher.yatori_path = str(root / 'Yatori')
    launcher.autovisor_path = str(root / 'Autovisor')
    (root / 'Yatori').mkdir()
    (root / 'Autovisor').mkdir()
    launcher.log_system = lambda message: None
    launcher._find_browser_executable = lambda name: ''
    launcher.autovisor_multi_mode = False
    launcher._get_autovisor_multi_mode = lambda: launcher.autovisor_multi_mode
    launcher._set_autovisor_multi_mode = lambda mode: setattr(launcher, 'autovisor_multi_mode', mode)
    launcher.get_web_initial_state = lambda: {'runtime': {}, 'private_state': 'not-for-model'}
    launcher._preferences_service = PreferencesService(root / 'preferences.json')
    launcher._get_preferences_service = lambda: launcher._preferences_service
    launcher.question_bank = SimpleNamespace(config_path=root / 'qb.json')
    launcher.qb = {'port': 8083, 'ai_api_key': 'qb-secret', 'ai_enabled': True}
    launcher.get_qb_settings_from_web = lambda: deepcopy(launcher.qb)
    def save_qb(data):
        launcher.qb = deepcopy(data)
        (root / 'qb.json').write_text(json.dumps(data), encoding='utf-8')
        return {'ok': True, 'state': {'secret': 'do-not-return'}}
    launcher.save_qb_settings_from_web = save_qb
    return launcher


def pending(agent, scope, path, value):
    return agent._dispatch({'name': 'update_settings', 'arguments': {
        'scope': scope, 'changes': [{'path': path, 'value': value}],
    }})


def test_real_yatori_patch_requires_confirmation_and_preserves_newer_unrelated_edits(tmp_path):
    launcher = launcher_at(tmp_path)
    config = launcher._load_yatori_config_data()
    config['setting']['aiSetting']['API_KEY'] = 'existing-secret'
    launcher._save_yatori_config_data(config)
    agent = TianyiAgentService(launcher)
    outcome = pending(agent, 'yatori', 'setting.basicSetting.logLevel', 'DEBUG')
    assert 'pending' in outcome
    assert launcher._load_yatori_config_data()['setting']['basicSetting']['logLevel'] == 'INFO'
    newer = launcher._load_yatori_config_data()
    newer['setting']['basicSetting']['completionTone'] = 0
    launcher._save_yatori_config_data(newer)
    result = agent.confirm(outcome['pending']['id'])
    assert result['ok'], result
    saved = launcher._load_yatori_config_data()
    assert saved['setting']['basicSetting']['logLevel'] == 'DEBUG'
    assert saved['setting']['basicSetting']['completionTone'] == 0
    assert saved['setting']['aiSetting']['API_KEY'] == 'existing-secret'
    assert 'existing-secret' not in json.dumps(result)


@pytest.mark.parametrize('scope,path,value', [
    ('autovisor', 'accounts.0.limit_speed', '1.5'),
    ('zhs', 'video.zhidao_speed', 2.0),
    ('questionbank', 'port', 8099),
    ('launcher', 'theme', 'light'),
    ('launcher', 'yatoriMaxRuntimeMinutes', 25),
    ('launcher', 'autoStart', True),
])
def test_all_public_settings_scopes_persist(scope, path, value, tmp_path):
    launcher = launcher_at(tmp_path)
    agent = TianyiAgentService(launcher)
    item = pending(agent, scope, path, value)['pending']
    assert agent.confirm(item['id'])['ok']
    data = AgentSettingsService(launcher)._load(scope)
    for part in path.split('.'):
        data = data[int(part)] if isinstance(data, list) else data[part]
    assert data == value


def test_read_settings_and_confirmation_never_disclose_secret_values(tmp_path):
    launcher = launcher_at(tmp_path)
    config = launcher._load_yatori_config_data()
    config['users'][0]['password'] = 'login-secret'
    config['setting']['aiSetting']['API_KEY'] = 'model-secret'
    config['setting']['apiQueSetting']['url'] = 'https://user:pass@host.test/query?token=query-secret'
    launcher._save_yatori_config_data(config)
    agent = TianyiAgentService(launcher)
    result = agent._dispatch({'name': 'get_settings', 'arguments': {'scope': 'yatori'}})
    encoded = json.dumps(result)
    for secret in ['login-secret', 'model-secret', 'query-secret', 'user:pass']:
        assert secret not in encoded
    item = pending(agent, 'yatori', 'setting.aiSetting.API_KEY', 'replacement-secret')['pending']
    assert 'replacement-secret' not in json.dumps(item)
    item['args']['changes'][0]['value'] = 'tampered'
    assert agent.confirm(item['id'])['ok']
    assert launcher._load_yatori_config_data()['setting']['aiSetting']['API_KEY'] == 'replacement-secret'


@pytest.mark.parametrize('scope,path,value', [
    ('launcher', 'achievements', {}), ('launcher', 'tianyiThemeUnlocked', True),
    ('launcher', 'autoShutdown', 'yes'), ('launcher', 'yatoriMaxRuntimeMinutes', -1),
    ('launcher', 'bgUrl', 'javascript:alert(1)'), ('launcher', 'theme', 'tianyi'),
    ('yatori', 'users', []), ('yatori', 'users.99.password', 'x'),
    ('yatori', 'setting.aiSetting.API_KEY', '[已隐藏]'),
    ('questionbank', 'port', 0),
])
def test_invalid_changes_do_not_create_confirmation_or_modify_settings(scope, path, value, tmp_path):
    launcher = launcher_at(tmp_path)
    agent = TianyiAgentService(launcher)
    result = pending(agent, scope, path, value)
    assert not result.get('pending')
    assert agent.pending_ids() == []
    assert not (tmp_path / 'preferences.json').exists()


def test_cancelled_change_is_never_written_and_zhs_control_requires_confirmation(tmp_path):
    launcher = launcher_at(tmp_path)
    calls = []
    launcher.perform_web_action = lambda action, core: calls.append((action, core)) or {'ok': True}
    agent = TianyiAgentService(launcher)
    change = pending(agent, 'launcher', 'theme', 'light')['pending']
    agent.cancel(change['id'])
    assert agent.confirm(change['id'])['ok'] is False
    assert launcher.get_web_preferences()['theme'] == 'dark'
    start = agent._dispatch({'name': 'control_runtime', 'arguments': {'action': 'start', 'core': 'zhs'}})['pending']
    assert calls == []
    assert agent.confirm(start['id'])['ok']
    assert calls == [('start', 'zhs')]


def test_about_update_buttons_use_cloud_icons_and_the_same_primary_style():
    html = (Path(__file__).resolve().parents[1] / 'web/现代启动器_UI_预览.html').read_text(encoding='utf-8')
    about = html.split('id="about-version"', 1)[1].split('id="changelog-list"', 1)[0]
    for action, label in [('openYatoriUpdateDialog()', '安装 Yatori 更新'), ('checkZhsUpdate()', '安装 ZHS 更新')]:
        button = re.search(r'<button class="([^"]+)" onclick="' + re.escape(action) + r'">([\s\S]*?)</button>', about)
        assert button and 'btn-primary btn-pill btn-glow btn-magnetic' in button[1]
        assert 'fa-cloud-arrow-down' in button[2] and label in button[2]
    assert 'openAutovisorUpdateDialog()' not in about


def test_normalized_value_is_reported_instead_of_claiming_requested_speed(tmp_path):
    launcher = launcher_at(tmp_path)
    agent = TianyiAgentService(launcher)
    item = pending(agent, 'autovisor', 'accounts.0.limit_speed', '999')['pending']
    result = agent.confirm(item['id'])
    assert result['ok']
    assert '按核心规则调整' in result['message']
    assert launcher._load_autovisor_config_data()['accounts'][0]['limit_speed'] == '1.0'


def test_model_reads_redacted_settings_then_requests_and_confirms_a_real_edit(tmp_path):
    launcher = launcher_at(tmp_path)
    config = launcher._load_yatori_config_data()
    config['setting']['aiSetting']['API_KEY'] = 'never-send-this-secret'
    launcher._save_yatori_config_data(config)
    messages_seen = []

    def model(config, messages, tools):
        messages_seen.append(deepcopy(messages))
        names = {tool['function']['name'] for tool in tools}
        assert {'get_settings', 'update_settings'} <= names
        if len(messages_seen) == 1:
            call = {'name': 'get_settings', 'arguments': {'scope': 'yatori'}}
        else:
            assert 'never-send-this-secret' not in json.dumps(messages)
            assert any(message.get('role') == 'tool' for message in messages)
            call = {'name': 'update_settings', 'arguments': {
                'scope': 'yatori', 'changes': [{'path': 'setting.basicSetting.logLevel', 'value': 'DEBUG'}],
            }}
        return {'ok': True, 'reply': '', 'tool_calls': [dict(call, id='call-settings')]}

    agent = TianyiAgentService(launcher, ai_service=SimpleNamespace(chat_with_tools=model))
    response = agent.chat({}, [{'role': 'user', 'content': '把 Yatori 日志级别改成 DEBUG'}])
    assert len(messages_seen) == 2
    assert response['pending']['action'] == 'update_settings'
    assert launcher._load_yatori_config_data()['setting']['basicSetting']['logLevel'] == 'INFO'
    assert agent.confirm(response['pending']['id'])['ok']
    assert launcher._load_yatori_config_data()['setting']['basicSetting']['logLevel'] == 'DEBUG'
