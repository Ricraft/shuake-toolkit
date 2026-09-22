# encoding=utf-8
"""课程名解析与单课程启动编排。"""

from types import SimpleNamespace

import pytest
import yaml

from src.course_overlay_service import CourseOverlayService
from src.course_plan_service import CoursePlanService
from src.course_run_service import CourseRunService
from src.runtime_coordinator import RuntimeCoordinator


GOOD_URL = "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=abc"


class FakeLauncher:
    def __init__(
        self,
        tmp_path,
        *,
        yatori_users=None,
        autovisor_accounts=None,
        start_ok=True,
        catalog_cache=None,
    ):
        self.running = {"yatori": False, "autovisor": False, "practice": False}
        self.starting = {"yatori": False, "autovisor": False, "practice": False}
        self._last_start_error = {}
        self.logs = []
        self.started = []
        self.autovisor_calls = []
        self.start_ok = start_ok
        self._yatori_users = (
            yatori_users
            if yatori_users is not None
            else [{"account": "10001", "password": "pw"}]
        )
        self._autovisor_accounts = (
            autovisor_accounts
            if autovisor_accounts is not None
            else [{"account_id": 1, "username": "u", "password": "p"}]
        )
        self._yatori_path = tmp_path / "config.yaml"
        self.catalog_cache = catalog_cache or {}

    def log_system(self, message):
        self.logs.append(str(message))

    def _load_yatori_config_data(self):
        return {"users": self._yatori_users}

    def _load_autovisor_config_data(self):
        return {"accounts": self._autovisor_accounts}

    def _get_course_catalog_service(self):
        cache = self.catalog_cache

        class _CatalogService:
            @staticmethod
            def get_cached(provider, index, identity):
                return cache.get((provider, index, identity))

        return _CatalogService()

    def _get_yatori_config_path(self):
        return str(self._yatori_path)

    def start_yatori(self):
        if not self.start_ok:
            self._last_start_error["yatori"] = "启动失败：没有可用账号"
            return False
        self.started.append("yatori")
        return True

    def start_autovisor_course(self, *, course_url, account_id, max_minutes=None):
        self.autovisor_calls.append(
            {
                "course_url": course_url,
                "account_id": account_id,
                "max_minutes": max_minutes,
            }
        )
        if not self.start_ok:
            self._last_start_error["autovisor"] = "启动失败"
            return False
        self.started.append("autovisor")
        return True


def _write_yatori_config(tmp_path, *, include=None):
    path = tmp_path / "config.yaml"
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


def _service(tmp_path, launcher, *, registry=None, autovisor_catalog=None, yatori_catalog=None):
    plans = CoursePlanService(tmp_path / "course_plans.json")
    if registry is not None:
        assert plans.save({"courses": registry})["ok"] is True
    overlays = CourseOverlayService(tmp_path / "overlay.json")
    return (
        CourseRunService(
            launcher,
            plans=plans,
            overlays=overlays,
            autovisor_catalog=autovisor_catalog,
            yatori_catalog=yatori_catalog,
        ),
        plans,
        overlays,
    )


def test_resolve_prefers_prefilled_registry(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        registry=[
            {"name": "高等数学A2", "aliases": "高数A2", "core": "yatori"},
        ],
        autovisor_catalog=lambda _index: pytest.fail("不应拉取账号课程"),
        yatori_catalog=lambda _index: pytest.fail("不应拉取账号课程"),
    )
    result = service.resolve("高数 A2")
    assert result["ok"] is True
    assert result["source"] == "registry"
    assert result["entry"]["core"] == "yatori"


def test_resolve_falls_back_to_account_catalog(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        autovisor_catalog=lambda _index: {
            "ok": True,
            "courses": [{"name": "大学物理", "url": GOOD_URL}],
        },
        yatori_catalog=lambda _index: {"ok": True, "courses": []},
    )
    result = service.resolve("大学物理", allow_fetch=True)
    assert result["ok"] is True
    assert result["source"] == "catalog"
    assert result["entry"]["core"] == "autovisor"
    assert result["entry"]["courseUrl"] == GOOD_URL


def test_resolve_asks_for_account_when_credentials_missing(tmp_path):
    launcher = FakeLauncher(
        tmp_path,
        yatori_users=[{"account": "", "password": ""}],
        autovisor_accounts=[{"account_id": 1, "username": "", "password": ""}],
    )
    service, _plans, _overlays = _service(tmp_path, launcher)
    result = service.resolve("高等数学A2", allow_fetch=True)
    assert result["ok"] is False
    assert result["code"] == "account_missing"
    assert "还没填账号或密码" in result["message"]


def test_resolve_reports_course_not_configured(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        autovisor_catalog=lambda _index: {"ok": True, "courses": []},
        yatori_catalog=lambda _index: {"ok": True, "courses": []},
    )
    result = service.resolve("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "course_not_configured"


def test_start_yatori_writes_single_course_overlay(tmp_path):
    config = _write_yatori_config(tmp_path, include=["旧课程"])
    launcher = FakeLauncher(tmp_path)
    service, _plans, overlays = _service(
        tmp_path,
        launcher,
        registry=[
            {
                "name": "高等数学A2",
                "aliases": "高数A2",
                "core": "yatori",
                "skipQuestions": True,
                "maxMinutes": "45",
            }
        ],
    )

    result = service.start("高数A2")
    assert result["ok"] is True
    assert launcher.started == ["yatori"]
    custom = yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]
    assert custom["includeCourses"] == ["高等数学A2"]
    assert custom["excludeCourses"] == []
    assert custom["cxChapterTestSw"] == 0
    assert custom["cxWorkSw"] == 0
    assert custom["cxExamSw"] == 0
    assert custom["studyTime"] == "45"
    assert overlays.pending("yatori") == ["yatori"]

    assert overlays.release("yatori") is True
    restored = yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]
    assert restored["includeCourses"] == ["旧课程"]
    assert restored["cxChapterTestSw"] == 1
    assert restored["studyTime"] == ""


def test_start_yatori_releases_overlay_when_launch_fails(tmp_path):
    config = _write_yatori_config(tmp_path)
    launcher = FakeLauncher(tmp_path, start_ok=False)
    service, _plans, overlays = _service(
        tmp_path,
        launcher,
        registry=[{"name": "高等数学A2", "core": "yatori"}],
    )

    result = service.start("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "start_failed"
    assert overlays.pending() == []
    restored = yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]
    assert restored["includeCourses"] == []


def test_start_yatori_refuses_while_running(tmp_path):
    _write_yatori_config(tmp_path)
    launcher = FakeLauncher(tmp_path)
    launcher.running["yatori"] = True
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        registry=[{"name": "高等数学A2", "core": "yatori"}],
    )
    result = service.start("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "already_running"


def test_start_autovisor_uses_registry_url_without_touching_config(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        registry=[
            {"name": "高等数学A2", "core": "autovisor", "courseUrl": GOOD_URL},
        ],
    )
    result = service.start("高等数学A2", max_minutes="30")
    assert result["ok"] is True
    assert launcher.autovisor_calls == [
        {"course_url": GOOD_URL, "account_id": 1, "max_minutes": "30"}
    ]


def test_start_autovisor_fills_missing_url_from_local_cache(tmp_path):
    launcher = FakeLauncher(
        tmp_path,
        catalog_cache={
            ("zhs", 0, "u"): {
                "ok": True,
                "courses": [{"name": "高等数学A2", "url": GOOD_URL}],
            }
        },
    )
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        registry=[{"name": "高等数学A2", "core": "autovisor"}],
        autovisor_catalog=lambda _index: pytest.fail("不应联网拉取课程"),
    )
    result = service.start("高等数学A2")
    assert result["ok"] is True
    assert launcher.autovisor_calls[0]["course_url"] == GOOD_URL


def test_start_autovisor_without_url_or_cache_asks_user_to_fetch(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        registry=[{"name": "高等数学A2", "core": "autovisor"}],
        autovisor_catalog=lambda _index: pytest.fail("不应联网拉取课程"),
    )
    result = service.start("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "course_not_configured"
    assert launcher.autovisor_calls == []


def test_start_autovisor_reports_missing_url(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        registry=[{"name": "高等数学A2", "core": "autovisor"}],
        autovisor_catalog=lambda _index: {
            "ok": True,
            "courses": [{"name": "线性代数", "url": GOOD_URL}],
        },
    )
    result = service.start("高等数学A2")
    assert result["ok"] is False
    assert result["code"] == "course_not_configured"
    assert launcher.autovisor_calls == []


def test_coordinator_releases_overlay_on_normal_exit_and_stop():
    released = []
    launcher = SimpleNamespace(
        _runtime_failure_since_batch=False,
        log_system=lambda _message: None,
        _maybe_shutdown_after_completion=lambda: None,
        _maybe_close_launcher_after_completion=lambda: None,
        _release_course_run_overlay=lambda core: released.append(core),
        _record_runtime_failure=lambda *_args: None,
        _notify_runtime_event=lambda *_args, **_kwargs: None,
    )
    coordinator = RuntimeCoordinator(launcher)

    coordinator.handle_runtime_exit("yatori", 0, False)
    coordinator.handle_runtime_exit("yatori", 0, True)
    coordinator.handle_runtime_failure("autovisor", "t", "m")

    assert released == ["yatori", "yatori", "autovisor"]


def _yatori_with_configured(tmp_path, names):
    return FakeLauncher(
        tmp_path,
        yatori_users=[
            {
                "account": "10001",
                "password": "pw",
                "coursesCustom": {"includeCourses": list(names)},
            }
        ],
    )


def test_resolve_reads_yatori_configured_courses_without_fetching(tmp_path):
    """用户在核心设置里填的课应当直接命中，且不联网。"""
    launcher = _yatori_with_configured(tmp_path, ["毛概"])
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        autovisor_catalog=lambda _index: pytest.fail("不应拉取智慧树课程"),
        yatori_catalog=lambda _index: pytest.fail("不应拉取学习通课程"),
    )
    result = service.resolve("毛概")
    assert result["ok"] is True
    assert result["entry"]["name"] == "毛概"
    assert result["entry"]["core"] == "yatori"
    assert result["source"] == "configured"
    assert launcher.started == []


def test_resolve_lists_available_courses_on_miss(tmp_path):
    launcher = _yatori_with_configured(tmp_path, ["毛概", "线性代数"])
    service, _plans, _overlays = _service(tmp_path, launcher)
    result = service.resolve("大学物理")
    assert result["ok"] is False
    assert result["code"] == "course_not_configured"
    assert result["available"] == ["毛概", "线性代数"]


def test_resolve_only_fetches_when_explicitly_allowed(tmp_path):
    calls = []
    launcher = FakeLauncher(tmp_path)

    def yatori_catalog(_index):
        calls.append("yatori")
        return {"ok": True, "courses": [{"name": "大学物理"}]}

    def autovisor_catalog(_index):
        calls.append("autovisor")
        return {"ok": True, "courses": []}

    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=yatori_catalog,
        autovisor_catalog=autovisor_catalog,
    )

    assert service.resolve("大学物理")["ok"] is False
    assert calls == []

    fetched = service.resolve("大学物理", allow_fetch=True)
    assert fetched["ok"] is True
    assert fetched["source"] == "catalog"
    assert calls == ["yatori", "autovisor"]


def test_start_configured_yatori_course_writes_overlay(tmp_path):
    config = _write_yatori_config(tmp_path)
    launcher = _yatori_with_configured(tmp_path, ["毛概"])
    service, _plans, overlays = _service(tmp_path, launcher)

    result = service.start("毛概", skip_questions=True)

    assert result["ok"] is True
    assert launcher.started == ["yatori"]
    custom = yaml.safe_load(config.read_text(encoding="utf-8"))["users"][0][
        "coursesCustom"
    ]
    assert custom["includeCourses"] == ["毛概"]
    assert custom["cxChapterTestSw"] == 0
    assert custom["cxWorkSw"] == 0
    assert custom["cxExamSw"] == 0
    assert overlays.release("yatori") is True


def test_fetch_catalog_covers_both_cores(tmp_path):
    calls = []
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=lambda _index: (
            calls.append("yatori")
            or {"ok": True, "courses": [{"name": "\u6bdb\u6982"}]}
        ),
        autovisor_catalog=lambda _index: (
            calls.append("autovisor")
            or {"ok": True, "courses": [{"name": "\u5927\u5b66\u7269\u7406", "url": GOOD_URL}]}
        ),
    )

    result = service.fetch_catalog(None)

    assert result["ok"] is True
    assert calls == ["yatori", "autovisor"]
    assert {item["name"] for item in result["courses"]} == {"\u6bdb\u6982", "\u5927\u5b66\u7269\u7406"}


def test_fetch_catalog_reports_explicit_core_failure(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=lambda _index: {"ok": False, "message": "\u5b66\u4e60\u901a\u767b\u5f55\u5931\u8d25"},
        autovisor_catalog=lambda _index: pytest.fail("\u6307\u5b9a\u4e86 yatori \u5c31\u4e0d\u5e94\u62c9 autovisor"),
    )

    result = service.fetch_catalog("yatori")

    assert result["ok"] is False
    assert "\u767b\u5f55\u5931\u8d25" in result["message"]


def test_fetch_catalog_keeps_partial_success_visible(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=lambda _index: {"ok": False, "message": "\u8d26\u53f7\u7c7b\u578b\u4e0d\u662f\u5b66\u4e60\u901a"},
        autovisor_catalog=lambda _index: {
            "ok": True,
            "courses": [{"name": "\u5927\u5b66\u7269\u7406", "url": GOOD_URL}],
        },
    )

    result = service.fetch_catalog(None)

    assert result["ok"] is True
    assert [item["name"] for item in result["courses"]] == ["\u5927\u5b66\u7269\u7406"]
    assert result["failed"][0]["core"] == "yatori"
    assert "\u4e0d\u662f\u5b66\u4e60\u901a" in result["message"]


def test_fetched_courses_become_resolvable_from_cache(tmp_path):
    launcher = FakeLauncher(tmp_path)
    cache = launcher.catalog_cache

    def yatori_catalog(_index):
        result = {"ok": True, "courses": [{"name": "\u5927\u5b66\u7269\u7406"}]}
        cache[("xxt", 0, "10001")] = result
        return result

    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=yatori_catalog,
        autovisor_catalog=lambda _index: {"ok": True, "courses": []},
    )

    assert service.resolve("\u5927\u5b66\u7269\u7406")["ok"] is False

    assert service.fetch_catalog("yatori")["ok"] is True

    again = service.resolve("\u5927\u5b66\u7269\u7406")
    assert again["ok"] is True
    assert again["source"] == "cache"


def test_resolve_smart_auto_fetches_yatori_then_finds_course(tmp_path):
    launcher = FakeLauncher(tmp_path)
    cache = launcher.catalog_cache
    calls = []

    def yatori_catalog(_index):
        calls.append("yatori")
        result = {"ok": True, "courses": [{"name": "\u5927\u5b66\u7269\u7406"}]}
        cache[("xxt", 0, "10001")] = result
        return result

    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=yatori_catalog,
        autovisor_catalog=lambda _index: pytest.fail("\u4e0d\u5e94\u81ea\u52a8\u62c9 Autovisor"),
    )

    result = service.resolve_smart("\u5927\u5b66\u7269\u7406")

    assert result["ok"] is True
    assert result["source"] == "cache"
    assert calls == ["yatori"]


def test_resolve_smart_needs_confirmation_before_autovisor(tmp_path):
    launcher = FakeLauncher(tmp_path)
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=lambda _index: {"ok": True, "courses": []},
        autovisor_catalog=lambda _index: pytest.fail("\u4e0d\u5e94\u81ea\u52a8\u62c9 Autovisor"),
    )

    result = service.resolve_smart("\u5927\u5b66\u7269\u7406")

    assert result["ok"] is False
    assert result["code"] == "needs_autovisor_fetch"
    assert "\u6d4f\u89c8\u5668" in result["message"]


def test_resolve_smart_prompts_for_missing_yatori_account(tmp_path):
    launcher = FakeLauncher(
        tmp_path,
        yatori_users=[{"account": "", "password": ""}],
    )
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=lambda _index: pytest.fail("\u8d26\u53f7\u672a\u586b\u4e0d\u5e94\u53d1\u8d77\u767b\u5f55"),
        autovisor_catalog=lambda _index: pytest.fail("\u4e0d\u5e94\u81ea\u52a8\u62c9 Autovisor"),
    )

    result = service.resolve_smart("\u5927\u5b66\u7269\u7406")

    assert result["ok"] is False
    assert result["code"] == "account_missing"
    assert "\u5148\u5728\u6838\u5fc3\u8bbe\u7f6e\u91cc\u8865\u9f50" in result["message"]


def test_resolve_smart_does_not_force_refetch_when_cache_exists(tmp_path):
    launcher = FakeLauncher(
        tmp_path,
        catalog_cache={
            ("xxt", 0, "10001"): {
                "ok": True,
                "courses": [{"name": "\u7ebf\u6027\u4ee3\u6570"}],
            }
        },
    )
    service, _plans, _overlays = _service(
        tmp_path,
        launcher,
        yatori_catalog=lambda _index: pytest.fail("\u5df2\u6709\u7f13\u5b58\u4e0d\u5e94\u53cd\u590d\u767b\u5f55"),
        autovisor_catalog=lambda _index: pytest.fail("\u4e0d\u5e94\u81ea\u52a8\u62c9 Autovisor"),
    )

    result = service.resolve_smart("\u5927\u5b66\u7269\u7406")

    assert result["ok"] is False
    assert result["code"] == "needs_autovisor_fetch"
