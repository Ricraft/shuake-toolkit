# encoding=utf-8
"""端到端：课程表 → 对话 → 二次确认 → 真正改配置 → 核心退出后恢复。"""

import configparser
import json

import yaml

from src.course_overlay_service import CourseOverlayService
from src.course_plan_service import CoursePlanService
from src.course_run_service import CourseRunService
from src.runtime_coordinator import RuntimeCoordinator
from src.tianyi_agent_service import TianyiAgentService


def _write_yatori_config(tmp_path, *, include=None):
    path = tmp_path / "Yatori" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "users": [
                    {
                        "accountType": "XUEXITONG",
                        "account": "10001",
                        "password": "pw",
                        "coursesCustom": {
                            "studyTime": "",
                            "cxChapterTestSw": 1,
                            "cxWorkSw": 1,
                            "cxExamSw": 1,
                            "includeCourses": list(include or []),
                            "excludeCourses": [],
                        },
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_autovisor_config(tmp_path):
    path = tmp_path / "Autovisor" / "configs.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[user-account]\nusername = 10001\npassword = pw\n"
        "\n[course-option]\nlimitMaxTime = 30\nlimitSpeed = 1.0\n",
        encoding="utf-8",
    )
    return path


class FakeAI:
    def __init__(self, script):
        self.script = list(script)

    def chat_with_tools(self, config, messages, tools):
        return self.script.pop(0) if self.script else {
            "ok": True,
            "reply": "好了。",
            "tool_calls": [],
        }


class FakeLauncher:
    """真实组合 CoursePlanService / CourseOverlayService / RuntimeCoordinator。"""

    def __init__(self, tmp_path, *, registry, ai_script):
        self.running = {"yatori": False, "autovisor": False, "practice": False}
        self.starting = {"yatori": False, "autovisor": False, "practice": False}
        self._last_start_error = {}
        self._shutdown_pending = False
        self._runtime_failure_since_batch = False
        self.logs = []
        self.started = []
        self.autovisor_calls = []
        self.performed = []
        self.preferences = []
        self._yatori_config_path = _write_yatori_config(tmp_path, include=["旧课程"])
        self._autovisor_config_path = _write_autovisor_config(tmp_path)
        self._plans = CoursePlanService(tmp_path / "course_plans.json")
        assert self._plans.save({"courses": registry})["ok"] is True
        self._overlays = CourseOverlayService(tmp_path / "overlay.json")
        self._course_run = CourseRunService(
            self,
            plans=self._plans,
            overlays=self._overlays,
        )
        self._ai = FakeAI(ai_script)

    # --- launcher-ish surface ---
    def log_system(self, message):
        self.logs.append(str(message))

    def _load_yatori_config_data(self):
        return yaml.safe_load(self._yatori_config_path.read_text(encoding="utf-8"))

    def _load_autovisor_config_data(self):
        return {"accounts": [{"account_id": 1, "username": "u", "password": "p"}]}

    def _get_yatori_config_path(self):
        return str(self._yatori_config_path)

    def _get_course_catalog_service(self):
        raise AssertionError("resolve 不应读取缓存，更不应联网")

    def _get_course_plan_service(self):
        return self._plans

    def _get_course_overlay_service(self):
        return self._overlays

    def _get_course_run_service(self):
        return self._course_run

    def _release_course_run_overlay(self, core):
        return self._overlays.release(core)

    def _apply_course_limit_overlay(self, minutes):
        return self._overlays.apply_ini(
            core="autovisor",
            config_path=self._autovisor_config_path,
            section="course-option",
            option="limitMaxTime",
            value=str(minutes),
        )

    def start_yatori(self):
        self.started.append("yatori")
        return True

    def start_autovisor_course(self, *, course_url, account_id, max_minutes=None):
        self.autovisor_calls.append(
            {"course_url": course_url, "account_id": account_id, "max_minutes": max_minutes}
        )
        if max_minutes:
            self._apply_course_limit_overlay(max_minutes)
        self.started.append("autovisor")
        return True

    def _maybe_shutdown_after_completion(self):
        return False

    def _maybe_close_launcher_after_completion(self):
        return False

    def _record_runtime_failure(self, script_type, message):
        self.logs.append(str(message))

    def _notify_runtime_event(self, title, message, error=False):
        self.logs.append(str(title))

    def perform_web_action(self, action, script_type=None):
        self.performed.append((action, script_type))
        return {"ok": True}

    def save_web_preference(self, payload):
        self.preferences.append(dict(payload))
        return {"ok": True}

    # --- helpers ---
    def agent(self):
        return TianyiAgentService(self, ai_service=self._ai, logger=self.log_system)

    def coordinator(self):
        return RuntimeCoordinator(self)


def _call(name, arguments):
    return {"id": "c1", "name": name, "arguments": json.dumps(arguments)}


def test_chat_single_course_confirm_changes_then_restores_yatori(tmp_path):
    launcher = FakeLauncher(
        tmp_path,
        registry=[
            {
                "name": "高等数学A2",
                "aliases": ["高数A2"],
                "core": "yatori",
                "skipQuestions": True,
                "maxMinutes": "45",
            }
        ],
        ai_script=[
            {
                "ok": True,
                "reply": "好呀，我先确认一下。",
                "tool_calls": [
                    _call(
                        "start_course",
                        {
                            "course": "高数A2",
                            "skip_questions": True,
                            "max_minutes": "45",
                        },
                    )
                ],
            }
        ],
    )
    agent = launcher.agent()

    result = agent.chat(
        {"api_key": "k"},
        [{"role": "user", "content": "帮我刷高等数学A2，不用做题"}],
    )
    assert result["pending"] is not None
    assert launcher.started == []

    executed = agent.confirm(result["pending"]["id"])
    assert executed["ok"] is True
    assert launcher.started == ["yatori"]

    custom = yaml.safe_load(
        launcher._yatori_config_path.read_text(encoding="utf-8")
    )["users"][0]["coursesCustom"]
    assert custom["includeCourses"] == ["高等数学A2"]
    assert custom["cxChapterTestSw"] == 0
    assert custom["cxWorkSw"] == 0
    assert custom["cxExamSw"] == 0
    assert custom["studyTime"] == "45"
    assert launcher._overlays.pending("yatori") == ["yatori"]

    # 核心退出后自动恢复
    launcher.coordinator().handle_runtime_exit("yatori", 0, False)
    restored = yaml.safe_load(
        launcher._yatori_config_path.read_text(encoding="utf-8")
    )["users"][0]["coursesCustom"]
    assert restored["includeCourses"] == ["旧课程"]
    assert restored["cxChapterTestSw"] == 1
    assert restored["studyTime"] == ""
    assert launcher._overlays.pending() == []


def test_chat_autovisor_course_uses_cli_url_and_restores_limit(tmp_path):
    url = "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=abc"
    launcher = FakeLauncher(
        tmp_path,
        registry=[
            {"name": "大学物理", "core": "autovisor", "courseUrl": url}
        ],
        ai_script=[
            {
                "ok": True,
                "reply": "",
                "tool_calls": [
                    _call(
                        "start_course",
                        {"course": "大学物理", "max_minutes": "20"},
                    )
                ],
            }
        ],
    )
    agent = launcher.agent()

    result = agent.chat(
        {"api_key": "k"},
        [{"role": "user", "content": "帮我刷大学物理，刷 20 分钟"}],
    )
    executed = agent.confirm(result["pending"]["id"])
    assert executed["ok"] is True
    assert launcher.autovisor_calls == [
        {"course_url": url, "account_id": 1, "max_minutes": "20"}
    ]
    assert "limitMaxTime = 20" in launcher._autovisor_config_path.read_text(encoding="utf-8")

    launcher.coordinator().handle_runtime_exit("autovisor", 0, False)
    restored = launcher._autovisor_config_path.read_text(encoding="utf-8")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(restored)
    assert parser.get("course-option", "limitMaxTime") == "30"
    assert "limitSpeed = 1.0" in restored


def test_chat_missing_course_asks_user_to_configure(tmp_path):
    launcher = FakeLauncher(
        tmp_path,
        registry=[{"name": "线性代数", "core": "yatori"}],
        ai_script=[
            {
                "ok": True,
                "reply": "",
                "tool_calls": [
                    _call("find_course", {"query": "大学物理"})
                ],
            },
            {
                "ok": True,
                "reply": "课程表里还没有这门课，要先配置一下哦。",
                "tool_calls": [],
            },
        ],
    )
    agent = launcher.agent()
    result = agent.chat(
        {"api_key": "k"},
        [{"role": "user", "content": "帮我刷大学物理"}],
    )
    assert result["pending"] is None
    assert "配置" in result["reply"]
    assert launcher.started == []


def test_user_report_yatori_configured_course_found_without_opening_browser(tmp_path):
    """复现用户反馈：在 Yatori 核心设置里填的「毛概」应当直接命中且不弹浏览器。"""
    launcher = FakeLauncher(
        tmp_path,
        registry=[],
        ai_script=[
            {
                "ok": True,
                "reply": "好的，我先确认一下。",
                "tool_calls": [
                    _call("start_course", {"course": "毛概", "skip_questions": True})
                ],
            }
        ],
    )
    # 模拟用户在核心设置里填了“只刷这些课程”
    config = yaml.safe_load(launcher._yatori_config_path.read_text(encoding="utf-8"))
    config["users"][0]["coursesCustom"]["includeCourses"] = ["毛概"]
    launcher._yatori_config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    agent = launcher.agent()
    result = agent.chat(
        {"api_key": "k"},
        [{"role": "user", "content": "天依，可以帮我刷一下毛概吗，不用做题"}],
    )

    assert result.get("code") is None
    assert result["pending"] is not None
    assert "毛概" in result["pending"]["summary"]
    assert "不做题" in result["pending"]["summary"]
    assert launcher.started == []

    executed = agent.confirm(result["pending"]["id"])
    assert executed["ok"] is True
    assert launcher.started == ["yatori"]
    custom = yaml.safe_load(
        launcher._yatori_config_path.read_text(encoding="utf-8")
    )["users"][0]["coursesCustom"]
    assert custom["includeCourses"] == ["毛概"]
    assert custom["cxChapterTestSw"] == 0
