import threading
from copy import deepcopy
from types import SimpleNamespace

from src.config_service import ConfigService
from src.web_settings_service import WebSettingsService


def make_launcher(tmp_path):
    yatori_path = tmp_path / "config.yaml"
    autovisor_path = tmp_path / "configs.ini"
    qb_path = tmp_path / "question_bank.json"
    yatori_path.write_text("old-yatori", encoding="utf-8")
    autovisor_path.write_text("old-autovisor", encoding="utf-8")
    qb_path.write_text("old-qb", encoding="utf-8")
    logs = []
    calls = []
    saved = {}
    mode = {"value": False}

    def set_mode(value):
        calls.append(("mode", bool(value)))
        mode["value"] = bool(value)

    def save_yatori(data):
        calls.append(("yatori",))
        saved["yatori"] = deepcopy(data)
        yatori_path.write_text("new-yatori", encoding="utf-8")

    def save_autovisor(data):
        calls.append(("autovisor",))
        saved["autovisor"] = deepcopy(data)
        autovisor_path.write_text("new-autovisor", encoding="utf-8")

    def save_qb(data):
        calls.append(("questionbank",))
        saved["questionbank"] = deepcopy(data)
        qb_path.write_text("new-qb", encoding="utf-8")
        return {"ok": True}

    launcher = SimpleNamespace(
        question_bank=SimpleNamespace(config_path=qb_path),
        _load_yatori_config_data=lambda: {
            "setting": {
                "basicSetting": {"unknown": "keep"},
                "emailInform": {},
                "aiSetting": {},
                "apiQueSetting": {},
            },
            "users": [
                {
                    "account": "old",
                    "legacy": "keep",
                    "coursesCustom": {"legacyCourse": "keep"},
                }
            ],
        },
        _load_autovisor_config_data=lambda: {
            "multi_mode": False,
            "accounts": [],
        },
        _default_yatori_config=ConfigService.default_yatori_config,
        _default_yatori_user=ConfigService.default_yatori_user,
        _default_autovisor_account=lambda index: {
            "account_id": index,
            "limit_speed": "1.0",
            "course_urls": [],
        },
        _normalize_autovisor_speed=lambda value, default: (
            str(value) if str(value) in {"1.0", "1.25", "1.5", "1.8"} else default
        ),
        _validate_autovisor_accounts=ConfigService.validate_autovisor_accounts,
        _get_yatori_config_path=lambda: str(yatori_path),
        _get_autovisor_config_path=lambda: str(autovisor_path),
        _save_yatori_config_data=save_yatori,
        _save_autovisor_config_data=save_autovisor,
        save_qb_settings_from_web=save_qb,
        _get_autovisor_multi_mode=lambda: mode["value"],
        _set_autovisor_multi_mode=set_mode,
        get_web_initial_state=lambda: {"runtime": {"ready": True}},
        log_system=logs.append,
    )
    launcher.paths = (yatori_path, autovisor_path, qb_path)
    launcher.calls = calls
    launcher.saved = saved
    launcher.logs = logs
    launcher.mode = mode
    return launcher


def valid_payload():
    return {
        "yatori": {
            "setting": {
                "basicSetting": {"logLevel": "DEBUG"},
            },
            "users": [
                {
                    "account": "new",
                    "coursesCustom": {"studyTime": "30"},
                }
            ],
        },
        "autovisor": {
            "multi_mode": False,
            "browser_driver": "Chrome",
            "browser_path": None,
            "accounts": [
                {
                    "username": "alice",
                    "limit_speed": "9.9",
                    "course_urls": [],
                }
            ],
        },
        "questionbank": {"port": 8084},
    }


def test_malformed_autovisor_account_is_rejected_before_any_write(tmp_path):
    launcher = make_launcher(tmp_path)
    payload = valid_payload()
    payload["autovisor"]["accounts"] = ["not-an-object"]

    result = WebSettingsService(launcher).save(payload)

    assert result["ok"] is False
    assert result["message"] == "Autovisor 账号 1 的配置格式无效"
    assert result["state"] == {"runtime": {"ready": True}}
    assert launcher.calls == []
    assert [path.read_text(encoding="utf-8") for path in launcher.paths] == [
        "old-yatori",
        "old-autovisor",
        "old-qb",
    ]


def test_save_normalizes_copies_and_preserves_hidden_yatori_fields(tmp_path):
    launcher = make_launcher(tmp_path)
    payload = valid_payload()
    original = deepcopy(payload)

    result = WebSettingsService(launcher).save(payload)

    assert result["ok"] is True
    assert payload == original
    assert launcher.saved["autovisor"]["browser_path"] == ""
    assert (
        launcher.saved["autovisor"]["accounts"][0]["limit_speed"]
        == "1.0"
    )
    yatori = launcher.saved["yatori"]
    assert yatori["setting"]["basicSetting"]["unknown"] == "keep"
    assert yatori["users"][0]["legacy"] == "keep"
    assert (
        yatori["users"][0]["coursesCustom"]["legacyCourse"]
        == "keep"
    )
    assert yatori["users"][0]["coursesCustom"]["studyTime"] == "30"


def test_question_bank_runtime_update_is_the_last_fallible_save_step(
    tmp_path,
):
    launcher = make_launcher(tmp_path)

    result = WebSettingsService(launcher).save(valid_payload())

    assert result["ok"] is True
    assert launcher.calls == [
        ("yatori",),
        ("autovisor",),
        ("mode", False),
        ("questionbank",),
    ]


def test_question_bank_failure_restores_files_and_runtime_mode(tmp_path):
    launcher = make_launcher(tmp_path)

    def fail_qb(_data):
        launcher.calls.append(("questionbank",))
        launcher.paths[2].write_text("partial-qb", encoding="utf-8")
        return {"ok": False, "message": "题库重启失败"}

    launcher.save_qb_settings_from_web = fail_qb
    payload = valid_payload()
    payload["autovisor"]["multi_mode"] = True

    result = WebSettingsService(launcher).save(payload)

    assert result["ok"] is False
    assert "未保留任何部分改动" in result["message"]
    assert launcher.mode["value"] is False
    assert launcher.calls[-2:] == [
        ("questionbank",),
        ("mode", False),
    ]
    assert [path.read_text(encoding="utf-8") for path in launcher.paths] == [
        "old-yatori",
        "old-autovisor",
        "old-qb",
    ]


def test_successful_save_is_not_reclassified_when_state_refresh_fails(
    tmp_path,
):
    launcher = make_launcher(tmp_path)
    launcher.get_web_initial_state = lambda: (_ for _ in ()).throw(
        RuntimeError("runtime snapshot unavailable")
    )

    result = WebSettingsService(launcher).save(valid_payload())

    assert result["ok"] is True
    assert result["stateUnavailable"] is True
    assert result["message"] == "配置已保存，但界面状态暂未刷新"
    assert any("状态刷新失败" in message for message in launcher.logs)


def test_malformed_yatori_nested_data_returns_stable_validation_error(
    tmp_path,
):
    launcher = make_launcher(tmp_path)
    payload = valid_payload()
    payload["yatori"]["users"][0]["coursesCustom"] = "bad"

    result = WebSettingsService(launcher).save(payload)

    assert result["ok"] is False
    assert result["message"] == "Yatori 账号 1 的课程设置格式无效"
    assert launcher.calls == []


def test_failed_transaction_cannot_restore_over_a_later_save(tmp_path):
    launcher = make_launcher(tmp_path)
    first_save_started = threading.Event()
    release_first_save = threading.Event()
    second_done = threading.Event()
    results = {}
    original_save_yatori = launcher._save_yatori_config_data

    def controlled_save_yatori(data):
        account = data["users"][0]["account"]
        if account == "first":
            launcher.paths[0].write_text("first-partial", encoding="utf-8")
            first_save_started.set()
            assert release_first_save.wait(timeout=2)
            raise RuntimeError("first save failed")
        original_save_yatori(data)
        launcher.paths[0].write_text("second-yatori", encoding="utf-8")

    launcher._save_yatori_config_data = controlled_save_yatori
    first_payload = valid_payload()
    first_payload["yatori"]["users"][0]["account"] = "first"
    second_payload = valid_payload()
    second_payload["yatori"]["users"][0]["account"] = "second"
    first_service = WebSettingsService(launcher)
    second_service = WebSettingsService(launcher)

    first = threading.Thread(
        target=lambda: results.update(
            first=first_service.save(first_payload)
        )
    )

    def save_second():
        results["second"] = second_service.save(second_payload)
        second_done.set()

    second = threading.Thread(target=save_second)
    first.start()
    assert first_save_started.wait(timeout=2)
    second.start()
    assert not second_done.wait(timeout=0.05)
    release_first_save.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results["first"]["ok"] is False
    assert results["second"]["ok"] is True
    assert [path.read_text(encoding="utf-8") for path in launcher.paths] == [
        "second-yatori",
        "new-autovisor",
        "new-qb",
    ]


def test_save_uses_detached_loaded_config_and_payload_snapshots(tmp_path):
    launcher = make_launcher(tmp_path)
    shared_yatori = launcher._load_yatori_config_data()
    launcher._load_yatori_config_data = lambda: shared_yatori
    payload = valid_payload()
    payload["yatori"]["users"][0]["coursesCustom"]["includeCourses"] = [
        "original-course"
    ]
    payload["autovisor"]["accounts"][0]["course_urls"] = [
        "https://onlineweb.zhihuishu.com/onlinestuh5"
    ]
    original_payload = deepcopy(payload)
    original_save_yatori = launcher._save_yatori_config_data
    original_save_autovisor = launcher._save_autovisor_config_data

    def mutate_yatori(data):
        data["users"][0]["coursesCustom"]["includeCourses"].append(
            "callback-mutation"
        )
        original_save_yatori(data)

    def mutate_autovisor(data):
        data["accounts"][0]["course_urls"].append(
            "https://onlineweb.zhihuishu.com/callback-mutation"
        )
        original_save_autovisor(data)

    launcher._save_yatori_config_data = mutate_yatori
    launcher._save_autovisor_config_data = mutate_autovisor

    result = WebSettingsService(launcher).save(payload)

    assert result["ok"] is True
    assert payload == original_payload
    assert shared_yatori["users"][0]["account"] == "old"
    assert shared_yatori["setting"]["basicSetting"] == {"unknown": "keep"}


def test_failed_prepare_does_not_mutate_loaded_yatori_cache(tmp_path):
    launcher = make_launcher(tmp_path)
    shared_yatori = launcher._load_yatori_config_data()
    launcher._load_yatori_config_data = lambda: shared_yatori
    payload = valid_payload()
    payload["yatori"]["setting"]["basicSetting"]["logLevel"] = "TRACE"
    payload["yatori"]["users"][0]["coursesCustom"] = "invalid"

    result = WebSettingsService(launcher).save(payload)

    assert result["ok"] is False
    assert shared_yatori["setting"]["basicSetting"] == {"unknown": "keep"}
    assert shared_yatori["users"][0]["account"] == "old"
