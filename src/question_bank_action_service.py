"""Web-facing question-bank maintenance actions and file-dialog handling."""

from __future__ import annotations

import os
from collections.abc import Callable


class QuestionBankActionService:
    """Coordinate question-bank actions without coupling them to the launcher."""

    JSON_FILE_TYPES = ("JSON 文件 (*.json)", "所有文件 (*.*)")

    def __init__(
        self,
        *,
        get_controller: Callable[[], object],
        get_window: Callable[[], object],
        dialog_available: Callable[[], bool],
        open_dialog_type,
        save_dialog_type,
        show_info: Callable[[str, str], None],
        show_error: Callable[[str, str], None],
    ):
        self.get_controller = get_controller
        self.get_window = get_window
        self.dialog_available = dialog_available
        self.open_dialog_type = open_dialog_type
        self.save_dialog_type = save_dialog_type
        self.show_info = show_info
        self.show_error = show_error

    @staticmethod
    def _failure(message):
        return {"ok": False, "message": str(message)}

    def _controller(self, operation, *, require_available=False):
        try:
            controller = self.get_controller()
        except Exception as exc:
            controller = None
            message = f"读取题库服务状态失败: {exc}"
        else:
            message = "题库服务器不可用"

        if controller is None or (
            require_available and not bool(getattr(controller, "available", False))
        ):
            self.show_error(f"{operation}失败", message)
            return None, self._failure(message)
        return controller, None

    @staticmethod
    def _selection_path(selection):
        if isinstance(selection, (str, os.PathLike)):
            return os.fsdecode(os.fspath(selection))
        if isinstance(selection, (tuple, list)) and selection:
            selected = selection[0]
            if isinstance(selected, (str, os.PathLike)):
                return os.fsdecode(os.fspath(selected))
        return None

    def _choose_file(self, operation, dialog_type):
        title = f"{operation}失败"
        try:
            available = bool(self.dialog_available())
            window = self.get_window()
        except Exception as exc:
            message = f"读取文件选择窗口失败: {exc}"
            self.show_error(title, message)
            return None, self._failure(message)

        if not available or window is None:
            message = "文件选择窗口不可用，请重启启动器后重试"
            self.show_error(title, message)
            return None, self._failure(message)

        try:
            selection = window.create_file_dialog(
                dialog_type,
                file_types=self.JSON_FILE_TYPES,
            )
        except Exception as exc:
            message = f"打开文件选择窗口失败: {exc}"
            self.show_error(title, message)
            return None, self._failure(message)

        if not selection:
            return None, {"ok": True, "cancelled": True}

        file_path = self._selection_path(selection)
        if not file_path:
            message = "文件选择窗口返回了无法识别的路径"
            self.show_error(title, message)
            return None, self._failure(message)
        return file_path, None

    def _perform(
        self,
        operation,
        callback,
        *,
        success_title,
        default_success,
        default_failure,
        add_toast=True,
    ):
        try:
            result = callback()
        except Exception as exc:
            result = self._failure(str(exc))
        if not isinstance(result, dict):
            result = self._failure(f"{default_failure}：题库服务返回格式无效")
        else:
            result = dict(result)

        message = str(
            result.get("message")
            or (default_success if result.get("ok") else default_failure)
        )
        result["message"] = message
        if result.get("ok"):
            self.show_info(success_title, message)
            if add_toast:
                result.update({"toast": message, "toastType": "success"})
        else:
            self.show_error(f"{operation}失败", message)
        return result

    def import_questions(self):
        controller, failure = self._controller(
            "导入",
            require_available=True,
        )
        if failure:
            return failure
        file_path, selection_result = self._choose_file(
            "导入",
            self.open_dialog_type,
        )
        if selection_result:
            return selection_result
        return self._perform(
            "导入",
            lambda: controller.import_questions(file_path),
            success_title="导入成功",
            default_success="题库导入完成",
            default_failure="导入题库失败",
        )

    def export_questions(self):
        controller, failure = self._controller(
            "导出",
            require_available=True,
        )
        if failure:
            return failure
        file_path, selection_result = self._choose_file(
            "导出",
            self.save_dialog_type,
        )
        if selection_result:
            return selection_result
        if not file_path.lower().endswith(".json"):
            file_path += ".json"
        return self._perform(
            "导出",
            lambda: controller.export_questions(file_path),
            success_title="导出成功",
            default_success="题库导出完成",
            default_failure="导出题库失败",
        )

    def clear_questions(self):
        controller, failure = self._controller("清空")
        if failure:
            return failure
        return self._perform(
            "清空",
            controller.clear_questions,
            success_title="清空成功",
            default_success="题库清空完成",
            default_failure="清空题库失败",
        )

    def deduplicate_questions(self):
        controller, failure = self._controller("去重")
        if failure:
            return False, failure["message"]
        result = self._perform(
            "去重",
            controller.deduplicate_questions,
            success_title="去重完成",
            default_success="题库去重完成",
            default_failure="题库去重失败",
            add_toast=False,
        )
        return bool(result.get("ok")), result["message"]
