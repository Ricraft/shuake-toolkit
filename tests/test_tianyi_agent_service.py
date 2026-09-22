# encoding=utf-8
"""洛天依工具调用 Agent：只读自动执行、写操作二次确认、失败回退。"""

import json

from src.course_plan_service import CoursePlanService
from src.tianyi_agent_service import (
    AGENT_SYSTEM_PROMPT,
    TianyiAgentService,
)


class FakeAI:
    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def chat_with_tools(self, config, messages, tools):
        self.calls.append(
            {
                "config": config,
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "tools": list(tools),
            }
        )
        if not self.script:
            return {
                "ok": True,
                "success": True,
                "reply": "好了。",
                "tool_calls": [],
            }
        return self.script.pop(0)


class FakeCourseRun:
    def __init__(self):
        self.started = []
        self.fetched = []
        self.fetch_result = {
            "ok": True,
            "courses": [{"name": "毛概", "core": "yatori"}],
        }
        self.resolve_result = {
            "ok": True,
            "entry": {"name": "高等数学A2", "core": "yatori"},
            "source": "registry",
        }

    def resolve(self, query, **kwargs):
        return dict(self.resolve_result)

    def resolve_smart(self, query, **kwargs):
        return self.resolve(query, **kwargs)

    def fetch_catalog(self, core, account_index=0):
        self.fetched.append((core, account_index))
        return dict(self.fetch_result)

    def start(self, query, **kwargs):
        self.started.append({"query": query, **kwargs})
        return {"ok": True, "message": f"已开始单刷《{query}》"}


class FakeLauncher:
    def __init__(self, *, course_run=None, plans=None):
        self.running = {"yatori": False, "autovisor": False, "practice": False}
        self._shutdown_pending = False
        self.logs = []
        self.performed = []
        self.preferences = []
        self.course_run = course_run or FakeCourseRun()
        self.plans = plans

    def log_system(self, message):
        self.logs.append(str(message))

    def _get_course_run_service(self):
        return self.course_run

    def _get_course_plan_service(self):
        return self.plans

    def perform_web_action(self, action, script_type=None):
        self.performed.append((action, script_type))
        return {"ok": True, "message": f"{action} 已提交"}

    def save_web_preference(self, payload):
        self.preferences.append(dict(payload))
        return {"ok": True, "preferences": dict(payload)}


def _call(name, arguments):
    return {
        "id": "call-1",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def _service(tmp_path, script):
    plans = CoursePlanService(tmp_path / "course_plans.json")
    plans.save({"courses": [{"name": "高等数学A2", "core": "yatori"}]})
    launcher = FakeLauncher(plans=plans)
    ai = FakeAI(script)
    agent = TianyiAgentService(
        launcher,
        ai_service=ai,
        logger=launcher.log_system,
    )
    return agent, launcher, ai


def test_tool_specs_expose_course_and_runtime_tools():
    names = {
        spec["function"]["name"]
        for spec in TianyiAgentService.__new__(TianyiAgentService).tool_specs()
    }
    assert {
        "get_status",
        "list_courses",
        "find_course",
        "start_course",
        "control_runtime",
        "set_preference",
        "refresh_account_courses",
    } <= names


def test_list_courses_includes_configured_core_courses(tmp_path):
    launcher = FakeLauncher(
        plans=CoursePlanService(tmp_path / "course_plans.json"),
    )
    launcher.configured = [
        {"name": "\u6bdb\u6982", "core": "yatori", "source": "configured"}
    ]

    class _Run:
        @staticmethod
        def list_known_courses(core=None):
            return launcher.configured

    launcher.course_run = _Run()
    agent = TianyiAgentService(
        launcher, ai_service=FakeAI([]), logger=launcher.log_system
    )

    result = agent._list_courses({})

    assert result["ok"] is True
    assert result["courses"] == launcher.configured


def test_read_tool_runs_then_returns_final_reply(tmp_path):
    agent, launcher, ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "我看一下。",
                "tool_calls": [_call("get_status", {})],
            },
            {"ok": True, "reply": "现在都没在跑哦。", "tool_calls": []},
        ],
    )
    launcher.running["yatori"] = True

    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "现在什么状态"}])

    assert result["ok"] is True
    assert result["mode"] == "tools"
    assert result["pending"] is None
    assert result["reply"] == "现在都没在跑哦。"
    assert result["actions"] == [
        {
            "yatori_running": True,
            "autovisor_running": False,
            "practice_running": False,
            "question_bank_running": False,
            "shutdown_pending": False,
        }
    ]
    assert len(ai.calls) == 2
    tool_messages = [m for m in ai.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages and "yatori_running" in tool_messages[0]["content"]


def test_write_tool_waits_for_confirmation_then_executes(tmp_path):
    agent, launcher, _ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "好呀。",
                "tool_calls": [
                    _call(
                        "start_course",
                        {
                            "course": "高等数学A2",
                            "skip_questions": True,
                            "max_minutes": "45",
                        },
                    )
                ],
            }
        ],
    )

    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "帮我刷高数，不用做题"}])

    assert result["pending"] is not None
    assert "高等数学A2" in result["pending"]["summary"]
    assert "不做题" in result["pending"]["summary"]
    assert launcher.course_run.started == []

    confirmed = agent.confirm(result["pending"]["id"])
    assert confirmed["ok"] is True
    assert launcher.course_run.started == [
        {
            "query": "高等数学A2",
            "core": None,
            "account_index": None,
            "skip_questions": True,
            "max_minutes": "45",
        }
    ]
    assert agent.pending_ids() == []


def test_confirm_after_cancel_reports_expired(tmp_path):
    agent, _launcher, _ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "",
                "tool_calls": [_call("control_runtime", {"action": "stop_all"})],
            }
        ],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "停止全部"}])
    pending_id = result["pending"]["id"]

    assert agent.cancel(pending_id) == {"ok": True, "cancelled": True}
    expired = agent.confirm(pending_id)
    assert expired["ok"] is False
    assert expired["code"] == "pending_expired"


def test_control_runtime_confirmation_calls_web_action(tmp_path):
    agent, launcher, _ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "",
                "tool_calls": [
                    _call("control_runtime", {"action": "start", "core": "autovisor"})
                ],
            }
        ],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "启动智慧树"}])
    agent.confirm(result["pending"]["id"])
    assert launcher.performed == [("start", "autovisor")]


def test_set_preference_confirmation_persists(tmp_path):
    agent, launcher, _ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "",
                "tool_calls": [
                    _call(
                        "set_preference",
                        {"key": "closeLauncherOnComplete", "value": True},
                    )
                ],
            }
        ],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "刷完关掉程序"}])
    assert "刷完自动关闭程序" in result["pending"]["summary"]
    agent.confirm(result["pending"]["id"])
    assert launcher.preferences == [{"closeLauncherOnComplete": True}]


def test_invalid_write_arguments_do_not_create_pending(tmp_path):
    agent, launcher, ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "",
                "tool_calls": [
                    _call("set_preference", {"key": "apiKey", "value": True})
                ],
            },
            {"ok": True, "reply": "这个改不了哦。", "tool_calls": []},
        ],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "改一下 key"}])
    assert result["pending"] is None
    assert agent.pending_ids() == []
    assert launcher.preferences == []
    tool_messages = [m for m in ai.calls[1]["messages"] if m.get("role") == "tool"]
    assert "不支持修改偏好项" in tool_messages[0]["content"]


def test_unsupported_tools_falls_back_to_text_protocol(tmp_path):
    agent, _launcher, ai = _service(
        tmp_path,
        [{"ok": False, "message": "当前模型或接口不支持工具调用"}],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "一键刷课"}])
    assert result["mode"] == "fallback"
    assert result["reply"] == ""
    assert "不支持工具调用" in result["fallbackReason"]
    assert len(ai.calls) == 1


def test_agent_prompt_is_appended_to_existing_system_message(tmp_path):
    agent, _launcher, ai = _service(
        tmp_path,
        [{"ok": True, "reply": "嗯。", "tool_calls": []}],
    )
    agent.chat(
        {"api_key": "k"},
        [
            {"role": "system", "content": "你是洛天依。"},
            {"role": "user", "content": "你好"},
        ],
    )
    system = ai.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "你是洛天依。" in system["content"]
    assert AGENT_SYSTEM_PROMPT in system["content"]


def test_pending_action_expires_with_injected_clock(tmp_path):
    now = [1000.0]
    plans = CoursePlanService(tmp_path / "course_plans.json")
    launcher = FakeLauncher(plans=plans)
    agent = TianyiAgentService(
        launcher,
        ai_service=FakeAI(
            [
                {
                    "ok": True,
                    "reply": "",
                    "tool_calls": [_call("control_runtime", {"action": "stop_all"})],
                }
            ]
        ),
        logger=launcher.log_system,
        clock=lambda: now[0],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "停止全部"}])
    pending_id = result["pending"]["id"]

    now[0] += TianyiAgentService.PENDING_TTL + 1
    expired = agent.confirm(pending_id)
    assert expired["ok"] is False
    assert expired["code"] == "pending_expired"


def test_find_course_reports_missing_course_without_starting(tmp_path):
    plans = CoursePlanService(tmp_path / "course_plans.json")
    launcher = FakeLauncher(plans=plans)
    launcher.course_run.resolve_result = {
        "ok": False,
        "code": "course_not_configured",
        "message": "课程表里没有这门课",
        "candidates": [],
    }
    agent = TianyiAgentService(
        launcher,
        ai_service=FakeAI(
            [
                {
                    "ok": True,
                    "reply": "",
                    "tool_calls": [_call("find_course", {"query": "大学物理"})],
                },
                {"ok": True, "reply": "课程表里还没这门课。", "tool_calls": []},
            ]
        ),
        logger=launcher.log_system,
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "有大学物理吗"}])
    assert result["reply"] == "课程表里还没这门课。"
    assert launcher.course_run.started == []


def test_refresh_account_courses_needs_confirmation_before_opening_browser(tmp_path):
    agent, launcher, _ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "",
                "tool_calls": [_call("refresh_account_courses", {"core": "yatori"})],
            }
        ],
    )

    result = agent.chat(
        {"api_key": "k"},
        [{"role": "user", "content": "去拉一下我的学习通课程"}],
    )

    assert result["pending"] is not None
    assert "拉取" in result["pending"]["summary"]
    assert launcher.course_run.fetched == []

    confirmed = agent.confirm(result["pending"]["id"])
    assert confirmed["ok"] is True
    assert launcher.course_run.fetched == [("yatori", 0)]


def test_refresh_summary_distinguishes_core_login_modes():
    agent = TianyiAgentService(
        FakeLauncher(),
        ai_service=FakeAI([]),
        logger=lambda _message: None,
    )
    assert "\u4e0d\u5f39\u7a97" in agent._summarize(
        "refresh_account_courses", {"core": "yatori"}
    )
    assert "\u6253\u5f00\u6d4f\u89c8\u5668" in agent._summarize(
        "refresh_account_courses", {"core": "autovisor"}
    )
    both = agent._summarize("refresh_account_courses", {})
    assert "Yatori" in both and "Autovisor" in both


def _refresh_agent(tmp_path):
    agent, launcher, _ai = _service(
        tmp_path,
        [
            {
                "ok": True,
                "reply": "",
                "tool_calls": [
                    _call("refresh_account_courses", {"core": "yatori", "for_course": "毛概"})
                ],
            }
        ],
    )
    result = agent.chat({"api_key": "k"}, [{"role": "user", "content": "拉一下课程"}])
    return agent, launcher, result["pending"]["id"]


def test_refresh_confirm_reports_pulled_courses_and_target_hit(tmp_path):
    agent, _launcher, pending_id = _refresh_agent(tmp_path)

    result = agent.confirm(pending_id)

    assert result["ok"] is True
    assert "共 1 门课程" in result["detail"]
    assert "《毛概》" in result["detail"]
    assert "要我现在单刷这一门吗" in result["detail"]


def test_refresh_confirm_reports_target_missing(tmp_path):
    agent, launcher, pending_id = _refresh_agent(tmp_path)
    launcher.course_run.fetch_result = {
        "ok": True,
        "courses": [{"name": "线性代数", "core": "yatori"}],
    }

    result = agent.confirm(pending_id)

    assert "共 1 门课程" in result["detail"]
    assert "里面没有《毛概》" in result["detail"]


def test_refresh_confirm_reports_empty_result(tmp_path):
    agent, launcher, pending_id = _refresh_agent(tmp_path)
    launcher.course_run.fetch_result = {"ok": True, "courses": []}

    result = agent.confirm(pending_id)

    assert "没有返回任何课程" in result["detail"]
    assert "暂时没找到《毛概》" in result["detail"]


def test_refresh_confirm_reports_failure_message(tmp_path):
    agent, launcher, pending_id = _refresh_agent(tmp_path)
    launcher.course_run.fetch_result = {"ok": False, "message": "学习通登录失败"}

    result = agent.confirm(pending_id)

    assert result["ok"] is False
    assert result["detail"] == "学习通登录失败"
