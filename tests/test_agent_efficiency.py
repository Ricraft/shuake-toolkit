"""Local intent safety, ordered plans, and measurable request-size reductions."""
import json
import subprocess
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent_intent import recognize
from src.agent_settings_service import AgentSettingsService
from src.tianyi_agent_service import TianyiAgentService


class Model:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def chat_with_tools(self, config, messages, tools):
        self.calls.append(deepcopy({'messages': messages, 'tools': tools}))
        return self.replies.pop(0) if self.replies else {'ok': True, 'reply': '已查询', 'tool_calls': []}


def setup_agent():
    prefs = {'theme': 'dark', 'autoShutdown': True}
    performed = []
    launcher = SimpleNamespace(
        running={}, get_web_preferences=lambda: deepcopy(prefs), log_system=lambda message: None,
        save_web_preference=lambda patch: prefs.update(patch) or {'ok': True},
        perform_web_action=lambda action, core: performed.append((action, core)) or {'ok': True},
    )
    model = Model()
    return TianyiAgentService(launcher, ai_service=model), model, prefs, performed


@pytest.mark.parametrize('text,core,action', [
    ('请启动 Yatori', 'yatori', 'start'), ('帮我打开学习通', 'yatori', 'start'),
    ('暂停ZHS', 'zhs', 'stop'), ('关掉Autovisor', 'autovisor', 'stop'),
    ('启动新版智慧树', 'zhs', 'start'), ('停止旧版智慧树', 'autovisor', 'stop'),
])
def test_common_aliases_create_confirmations_without_any_model_request(text, core, action):
    agent, model, prefs, calls = setup_agent()
    result = agent.chat({}, [{'role': 'user', 'content': text}])
    assert not model.calls and not calls
    assert result['pending']['action'] == 'control_runtime'
    assert agent.confirm(result['pending']['id'])['ok']
    assert calls == [(action, core)]


@pytest.mark.parametrize('text', ['不要启动Yatori', '别关闭ZHS', '先不修改设置', '停止智慧树', '修改智慧树倍速', '启动Yatori还是ZHS', '启动Yatori但不要ZHS'])
def test_negated_or_ambiguous_commands_never_execute_or_call_a_model(text):
    agent, model, prefs, calls = setup_agent()
    result = agent.chat({}, [{'role': 'user', 'content': text}])
    assert not model.calls and not calls and not result['pending']
    assert result['reply']


def test_ordered_multi_step_request_is_one_confirmation_and_keeps_every_step():
    agent, model, prefs, calls = setup_agent()
    response = agent.chat({}, [{'role': 'user', 'content': '先关闭自动关机，再启动Yatori'}])
    assert not model.calls and prefs['autoShutdown'] is True and not calls
    assert response['pending']['action'] == 'batch'
    result = agent.confirm(response['pending']['id'])
    assert result['ok'] and prefs['autoShutdown'] is False
    assert calls == [('start', 'yatori')]
    assert agent.confirm(response['pending']['id'])['ok'] is False


def test_batch_stops_on_failure_and_reports_partial_settings_change():
    agent, model, prefs, calls = setup_agent()
    def failure(action, core):
        calls.append((action, core))
        return {'ok': False, 'message': '缺少环境'}
    agent.launcher.perform_web_action = failure
    response = agent.chat({}, [{'role': 'user', 'content': '关闭自动关机，然后启动ZHS，然后启动Yatori'}])
    result = agent.confirm(response['pending']['id'])
    assert not result['ok'] and result['settingsChanged'] == 'launcher'
    assert len(result['completed']) == 1 and calls == [('start', 'zhs')]
    assert prefs['autoShutdown'] is False


def test_question_cannot_be_upgraded_to_a_write_even_by_a_model_tool_call():
    agent, model, prefs, calls = setup_agent()
    model.replies = [{'ok': True, 'tool_calls': [{'name': 'control_runtime', 'arguments': {'action': 'start', 'core': 'yatori'}}]}]
    response = agent.chat({}, [{'role': 'user', 'content': '怎么启动Yatori？'}])
    assert not response['pending'] and not calls
    assert 'control_runtime' not in {s['function']['name'] for s in model.calls[0]['tools']}


def test_context_and_tool_schema_reduction_is_measured_without_claiming_token_counts():
    agent, model, prefs, calls = setup_agent()
    messages = [{'role': 'system', 'content': '人格' * 3000}]
    messages += [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(i) + '历史' * 5000} for i in range(30)]
    messages += [{'role': 'user', 'content': '查看启动器设置'}]
    baseline = len(json.dumps({'messages': messages, 'tools': agent.tool_specs()}, ensure_ascii=False))
    agent.chat({}, messages)
    sent = model.calls[0]
    actual = len(json.dumps(sent, ensure_ascii=False))
    assert actual < baseline * 0.2
    assert len(sent['messages']) <= 9
    assert sent['messages'][-1]['content'] == '查看启动器设置'
    assert sum(len(m['content']) for m in sent['messages']) < 10000
    assert len(sent['tools']) < len(agent.tool_specs())


def test_repeated_read_calls_are_memoized_and_large_results_are_bounded():
    agent, model, prefs, calls = setup_agent()
    reads = []
    agent.launcher.get_web_preferences = lambda: reads.append(1) or deepcopy(prefs)
    call = {'name': 'get_settings', 'arguments': {'scope': 'launcher', 'paths': ['theme']}}
    model.replies = [{'ok': True, 'tool_calls': [dict(call, id='a'), dict(call, id='b')]}, {'ok': True, 'reply': '已查询', 'tool_calls': []}]
    agent.chat({}, [{'role': 'user', 'content': '查看启动器设置'}])
    assert len(reads) == 1
    assert len([m for m in model.calls[1]['messages'] if m['role'] == 'tool']) == 2
    large = {'ok': True, 'settings': {'accounts': [{'name': 'x' * 5000} for i in range(1000)]}}
    assert len(json.dumps(agent._compact_result(large), ensure_ascii=False)) <= 6000


def test_projected_settings_are_small_and_remain_redacted():
    agent, model, prefs, calls = setup_agent()
    prefs['qb_external_url'] = 'https://host.test/query?token=secret'
    read = AgentSettingsService(agent.launcher).read({'scope': 'launcher', 'paths': ['theme', 'qb_external_url']})
    assert set(read['settings']) == {'theme', 'qb_external_url'}
    assert 'secret' not in json.dumps(read)


def test_model_multi_write_calls_are_grouped_instead_of_dropping_the_second():
    agent, model, prefs, calls = setup_agent()
    model.replies = [{'ok': True, 'tool_calls': [
        {'name': 'set_preference', 'arguments': {'key': 'autoShutdown', 'value': False}},
        {'name': 'control_runtime', 'arguments': {'action': 'start', 'core': 'zhs'}},
    ]}]
    response = agent.chat({}, [{'role': 'user', 'content': '帮我调整启动设置并启动新核心'}])
    assert response['pending']['action'] == 'batch'
    assert prefs['autoShutdown'] is True and not calls
    assert agent.confirm(response['pending']['id'])['ok']
    assert prefs['autoShutdown'] is False and calls == [('start', 'zhs')]


def test_course_skip_questions_phrase_does_not_become_a_negation():
    assert recognize('帮我刷毛概，不用做题') is None


def test_polite_course_request_keeps_write_tools_but_how_to_question_does_not():
    agent, model, prefs, calls = setup_agent()
    names = {t['function']['name'] for t in agent._tools_for('可以帮我刷一下毛概吗，不用做题')}
    assert 'start_course' in names
    assert 'start_course' not in {t['function']['name'] for t in agent._tools_for('请告诉我怎么刷毛概？')}


def test_confirmed_unsupported_model_is_cached_but_network_errors_are_not():
    agent, model, prefs, calls = setup_agent()
    now = [1000]
    agent._clock = lambda: now[0]
    model.replies = [{'ok': False, 'message': '当前模型不支持工具调用'}]
    assert agent.chat({}, [{'role': 'user', 'content': '你好'}])['mode'] == 'fallback'
    assert agent.chat({}, [{'role': 'user', 'content': '你好'}])['mode'] == 'fallback'
    assert len(model.calls) == 1
    now[0] += 301
    model.replies = [{'ok': False, 'message': '网络连接失败'}]
    agent.chat({}, [{'role': 'user', 'content': '你好'}])
    agent.chat({}, [{'role': 'user', 'content': '你好'}])
    assert len(model.calls) == 3


def test_no_key_global_and_questionbank_commands_still_create_confirmations():
    agent, model, prefs, calls = setup_agent()
    response = agent.chat({}, [{'role': 'user', 'content': '开启题库'}])
    assert agent.confirm(response['pending']['id'])['ok']
    assert calls == [('start_question_bank', None)]
    calls.clear()
    response = agent.chat({}, [{'role': 'user', 'content': '一键刷课'}])
    assert agent.confirm(response['pending']['id'])['ok']
    assert calls == [('start_all', None)]
    assert model.calls == []


def test_frontend_tool_request_omits_legacy_cmd_prompt_and_bounds_history():
    source = (Path(__file__).resolve().parents[1] / 'web/app.js').read_text(encoding='utf-8')
    source = source[source.index('        async function requestTianyiAgent('):source.index('        async function runTianyiTextChat(')]
    script = r'''
const assert=require('assert');
const tianyiState={history:Array.from({length:12},(_,i)=>({role:i%2?'user':'assistant',content:'long'.repeat(3000)}))};
const getTianyiPersonaText=()=> 'persona';
let sent;
const apiCall=async (name,payload)=>{sent=payload;return {ok:true,mode:'tools'};};
eval(SOURCE);
(async()=>{
 await requestTianyiAgent({},'查看启动器设置');
 assert(sent.messages.length<=7);
 assert(!JSON.stringify(sent.messages).includes('[CMD:'));
 assert.equal(sent.messages.at(-1).content,'查看启动器设置');
 assert(JSON.stringify(sent.messages).length<8000);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('SOURCE', json.dumps(source))
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_setting_parser_does_not_drop_unknown_followup_operation():
    assert recognize('关闭自动关机并修改 ZHS 倍速') is None
    agent, model, prefs, calls = setup_agent()
    result = agent.chat({}, [{'role': 'user', 'content': '关掉声音'}])
    assert not model.calls
    assert agent.confirm(result['pending']['id'])['ok']
    assert prefs['soundEnabled'] is False


def test_joined_core_and_theme_commands_keep_order_and_need_one_confirmation():
    agent, model, prefs, calls = setup_agent()
    response = agent.chat({}, [{'role': 'user', 'content': '关闭ZHS并切到浅色'}])
    assert response['pending']['action'] == 'batch' and not model.calls
    assert agent.confirm(response['pending']['id'])['ok']
    assert calls == [('stop', 'zhs')] and prefs['theme'] == 'light'
