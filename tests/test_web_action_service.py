from types import SimpleNamespace

from src.web_action_service import WebActionService


def make_launcher(**overrides):
    errors = []
    values = {
        "get_web_initial_state": lambda: {"runtime": {"yatori": False}},
        "_show_error": lambda title, message: errors.append((title, message)),
    }
    values.update(overrides)
    launcher = SimpleNamespace(**values)
    launcher.errors = errors
    return launcher


def test_start_rejection_keeps_specific_runtime_error_and_state():
    launcher = make_launcher(
        start_script=lambda _script_type: False,
        _last_start_error={"yatori": "Yatori 配置缺失"},
    )

    result = WebActionService(launcher).perform("start", "yatori")

    assert result == {
        "ok": False,
        "message": "Yatori 配置缺失",
        "state": {"runtime": {"yatori": False}},
    }

def test_start_rejection_exposes_configuration_failure_kind():
    launcher = make_launcher(
        start_script=lambda _script_type: False,
        _last_start_error={"yatori": "Yatori 尚未配置可运行的账号"},
        _last_start_failure_kind={"yatori": "configuration"},
    )

    result = WebActionService(launcher).perform("start", "yatori")

    assert result["failureKind"] == "configuration"


def test_structured_batch_result_gets_fresh_state_without_mutating_source():
    source = {"ok": False, "message": "部分启动失败"}
    launcher = make_launcher(start_all=lambda: source)

    result = WebActionService(launcher).perform("start_all")

    assert result["state"] == {"runtime": {"yatori": False}}
    assert result["message"] == "部分启动失败"
    assert "state" not in source


def test_question_bank_stop_toggle_is_success_even_when_toggle_returns_false():
    launcher = make_launcher(
        question_bank=SimpleNamespace(running=True),
        toggle_question_bank=lambda: False,
    )

    result = WebActionService(launcher).perform("toggle_question_bank")

    assert result["ok"] is True
    assert "state" in result


def test_deduplicate_only_adds_success_toast_after_success():
    failed = make_launcher(
        _deduplicate_question_bank=lambda: (False, "题库文件被占用")
    )
    succeeded = make_launcher(
        _deduplicate_question_bank=lambda: (True, "已删除 3 条重复题目")
    )

    failed_result = WebActionService(failed).perform("deduplicate_question_bank")
    succeeded_result = WebActionService(succeeded).perform(
        "deduplicate_question_bank"
    )

    assert failed_result["ok"] is False
    assert "toast" not in failed_result
    assert succeeded_result["toast"] == "已删除 3 条重复题目"
    assert succeeded_result["toastType"] == "success"


def test_yatori_update_button_returns_confirmation_data_without_installing():
    calls = []
    launcher = make_launcher(
        show_update_dialog=lambda: {
            "ok": True,
            "updateDialog": {
                "latestVersion": "v2.6.2-beta.11",
                "releaseNotes": "版本介绍",
            },
        },
        install_yatori_update_async=lambda: calls.append("install") or True,
    )

    result = WebActionService(launcher).perform("show_update_dialog")

    assert result["ok"] is True
    assert result["updateDialog"]["latestVersion"] == "v2.6.2-beta.11"
    assert calls == []


def test_yatori_update_confirm_action_starts_install():
    calls = []
    launcher = make_launcher(
        install_yatori_update_async=lambda: calls.append("install") or True
    )

    result = WebActionService(launcher).perform("install_yatori_update")

    assert result["ok"] is True
    assert calls == ["install"]


def test_action_exception_is_shown_and_returned_to_web():
    def fail():
        raise OSError("disk unavailable")

    launcher = make_launcher(start_all=fail)

    result = WebActionService(launcher).perform("start_all")

    assert result == {"ok": False, "message": "disk unavailable"}
    assert launcher.errors == [("操作失败", "disk unavailable")]
