import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.question_bank_action_service import QuestionBankActionService


class _Window:
    def __init__(self, selection=None, error=None):
        self.selection = selection
        self.error = error
        self.calls = []

    def create_file_dialog(self, dialog_type, **kwargs):
        self.calls.append((dialog_type, kwargs))
        if self.error:
            raise self.error
        return self.selection


def make_service(controller, window, *, dialog_available=True):
    messages = []
    service = QuestionBankActionService(
        get_controller=lambda: controller,
        get_window=lambda: window,
        dialog_available=lambda: dialog_available,
        open_dialog_type="open",
        save_dialog_type="save",
        show_info=lambda title, message: messages.append(
            ("info", title, message)
        ),
        show_error=lambda title, message: messages.append(
            ("error", title, message)
        ),
    )
    return service, messages


def test_cancelled_dialog_is_a_successful_no_op():
    controller = SimpleNamespace(
        available=True,
        import_questions=lambda _path: (_ for _ in ()).throw(
            AssertionError("cancelled import must not reach controller")
        ),
    )
    service, messages = make_service(controller, _Window(selection=None))

    result = service.import_questions()

    assert result == {"ok": True, "cancelled": True}
    assert messages == []


def test_unavailable_dialog_is_reported_instead_of_silent_cancellation():
    controller = SimpleNamespace(available=True)
    service, messages = make_service(
        controller,
        None,
        dialog_available=False,
    )

    result = service.import_questions()

    assert result["ok"] is False
    assert "文件选择窗口不可用" in result["message"]
    assert messages == [("error", "导入失败", result["message"])]


def test_string_selection_is_used_as_the_complete_import_path():
    selected_path = r"D:\Question Banks\questions.json"
    imported_paths = []
    controller = SimpleNamespace(
        available=True,
        import_questions=lambda path: imported_paths.append(path)
        or {"ok": True, "count": 3, "message": "已导入 3 条题目"},
    )
    window = _Window(selection=selected_path)
    service, messages = make_service(controller, window)

    result = service.import_questions()

    assert imported_paths == [selected_path]
    assert result["toast"] == "已导入 3 条题目"
    assert result["toastType"] == "success"
    assert messages == [("info", "导入成功", "已导入 3 条题目")]


def test_export_appends_json_extension_for_tuple_selection():
    exported_paths = []
    controller = SimpleNamespace(
        available=True,
        export_questions=lambda path: exported_paths.append(path)
        or {"ok": True, "count": 2, "message": "已导出 2 条题目"},
    )
    service, _messages = make_service(
        controller,
        _Window(selection=(r"D:\backup\questions",)),
    )

    result = service.export_questions()

    assert result["ok"] is True
    assert exported_paths == [r"D:\backup\questions.json"]


def test_file_dialog_exception_returns_operation_specific_failure():
    controller = SimpleNamespace(available=True)
    service, messages = make_service(
        controller,
        _Window(error=RuntimeError("renderer unavailable")),
    )

    result = service.export_questions()

    assert result["ok"] is False
    assert "renderer unavailable" in result["message"]
    assert messages == [("error", "导出失败", result["message"])]


def test_import_rejects_unavailable_question_bank_before_opening_dialog():
    window = _Window(selection=(r"D:\questions.json",))
    service, messages = make_service(
        SimpleNamespace(available=False),
        window,
    )

    result = service.import_questions()

    assert result == {"ok": False, "message": "题库服务器不可用"}
    assert window.calls == []
    assert messages == [("error", "导入失败", "题库服务器不可用")]


def test_clear_success_and_failure_have_consistent_feedback():
    successful, success_messages = make_service(
        SimpleNamespace(
            clear_questions=lambda: {
                "ok": True,
                "count": 4,
                "message": "已清空 4 条题目",
            }
        ),
        None,
    )
    failed, failure_messages = make_service(
        SimpleNamespace(
            clear_questions=lambda: {
                "ok": False,
                "message": "题库文件被占用",
            }
        ),
        None,
    )

    success_result = successful.clear_questions()
    failure_result = failed.clear_questions()

    assert success_result["toast"] == "已清空 4 条题目"
    assert success_messages == [("info", "清空成功", "已清空 4 条题目")]
    assert failure_result == {"ok": False, "message": "题库文件被占用"}
    assert failure_messages == [("error", "清空失败", "题库文件被占用")]


def test_deduplicate_preserves_tuple_contract_and_catches_backend_errors():
    successful, success_messages = make_service(
        SimpleNamespace(
            deduplicate_questions=lambda: {
                "ok": True,
                "message": "已清理 2 条重复记录",
            }
        ),
        None,
    )
    failed, failure_messages = make_service(
        SimpleNamespace(
            deduplicate_questions=lambda: (_ for _ in ()).throw(
                OSError("database is locked")
            )
        ),
        None,
    )

    assert successful.deduplicate_questions() == (
        True,
        "已清理 2 条重复记录",
    )
    assert success_messages == [
        ("info", "去重完成", "已清理 2 条重复记录")
    ]
    assert failed.deduplicate_questions() == (False, "database is locked")
    assert failure_messages == [
        ("error", "去重失败", "database is locked")
    ]


def test_real_file_path_selection_accepts_pathlike_values():
    with tempfile.TemporaryDirectory() as temp_dir:
        selected = Path(temp_dir) / "questions.json"
        imported_paths = []
        controller = SimpleNamespace(
            available=True,
            import_questions=lambda path: imported_paths.append(path)
            or {"ok": True, "message": "完成"},
        )
        service, _messages = make_service(
            controller,
            _Window(selection=[selected]),
        )

        assert service.import_questions()["ok"] is True
        assert imported_paths == [str(selected)]


def test_success_without_backend_message_uses_success_fallback():
    service, messages = make_service(
        SimpleNamespace(clear_questions=lambda: {"ok": True}),
        None,
    )

    result = service.clear_questions()

    assert result["message"] == "题库清空完成"
    assert result["toast"] == "题库清空完成"
    assert messages == [("info", "清空成功", "题库清空完成")]
