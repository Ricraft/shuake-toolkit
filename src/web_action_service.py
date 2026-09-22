"""Dispatch Web launcher actions without coupling them to the UI bridge."""

from __future__ import annotations

import threading

from src.update_controller import AUTOVISOR_UPSTREAM_LINK_MAP


AUTOVISOR_UPDATE_DISABLED_MESSAGE = (
    "当前 Autovisor 包含项目本地适配修改，已禁用上游覆盖安装；"
    "可继续检查版本信息，但不会替换现有核心。"
)

# 关于页「感谢名单」外链白名单：前端只传键，URL 只存在于后端。
CONTRIBUTOR_LINKS = {
    "yatori": "https://yatori-dev.github.io/yatori-docs/",
    "autovisor": "https://github.com/CXRunfree/Autovisor",
    "zerror": "https://app.zerror.cc/",
}


class WebActionService:
    """Translate Web action names into calls on the launcher runtime."""

    def __init__(self, launcher):
        self.launcher = launcher

    def _snapshot_state(self):
        try:
            return self.launcher.get_web_initial_state()
        except Exception as exc:
            log = getattr(self.launcher, "log_system", None)
            if callable(log):
                log(f"Web 状态刷新失败: {exc}")
            return None

    def _with_state(self, result):
        if isinstance(result, dict):
            response = dict(result)
        else:
            response = {"ok": bool(result)}
        response.setdefault("ok", False)
        state = self._snapshot_state()
        if state is not None:
            response["state"] = state
        return response

    def _failure(self, message, **details):
        response = {"ok": False, "message": str(message)}
        response.update(details)
        return self._with_state(response)

    def _success(self, **details):
        return self._with_state({"ok": True, **details})

    def _exit_async(self):
        def do_exit():
            try:
                self.launcher.on_closing(confirmed=True)
            except Exception as exc:
                self.launcher.log_system(f"退出应用失败: {exc}")

        threading.Thread(target=do_exit, daemon=True).start()

    def perform(self, action, script_type=None):
        launcher = self.launcher
        try:
            if action == "start":
                if not launcher.start_script(script_type):
                    message = getattr(launcher, "_last_start_error", {}).get(
                        script_type,
                        f"未知核心类型: {script_type}",
                    )
                    failure_kind = getattr(
                        launcher,
                        "_last_start_failure_kind",
                        {},
                    ).get(script_type)
                    details = {}
                    if failure_kind:
                        details["failureKind"] = failure_kind
                    return self._failure(message, **details)
            elif action == "stop":
                if not launcher.stop_script(script_type):
                    return self._failure(f"未知核心类型: {script_type}")
            elif action == "start_all":
                return self._with_state(launcher.start_all())
            elif action == "stop_all":
                return self._with_state(launcher.stop_all())
            elif action == "open_config_dir":
                return self._with_state(launcher.open_config_dir(script_type))
            elif action == "open_config_generator":
                return self._with_state(launcher.open_config_generator())
            elif action == "show_settings_dir":
                return self._with_state(
                    launcher.open_config_dir(script_type or "yatori")
                )
            elif action == "show_update_dialog":
                return self._with_state(launcher.show_update_dialog())
            elif action == "install_yatori_update":
                if not launcher.install_yatori_update_async(script_type):
                    return self._failure(
                        "更新确认已失效，请重新点击更新按钮并核对版本说明"
                    )
            elif action == "show_autovisor_update_dialog":
                return self._with_state(launcher.show_autovisor_update_dialog())
            elif action == "open_update_link":
                url = AUTOVISOR_UPSTREAM_LINK_MAP.get(str(script_type or ""))
                if not url:
                    return self._failure(f"未知的下载链接: {script_type}")
                result = launcher.open_external_url(url)
                if not result.get("ok"):
                    return self._failure(
                        result.get("message") or "无法打开下载链接"
                    )
                return self._success(url=url, accepted=True)
            elif action == "check_autovisor_update":
                if not launcher.check_autovisor_update_async():
                    return self._failure(
                        "Autovisor 更新检查未能启动，请稍后重试"
                    )
            elif action == "install_autovisor_update":
                return self._failure(
                    AUTOVISOR_UPDATE_DISABLED_MESSAGE,
                    blockedByPolicy=True,
                )
            elif action == "check_launcher_update":
                return self._with_state(launcher.show_launcher_update_dialog())
            elif action == "install_launcher_update":
                if not launcher.install_launcher_update_async(script_type):
                    return self._failure(
                        "更新确认已失效，请重新点击「检查统一启动器更新」并核对版本说明"
                    )
            elif action == "open_contributor_link":
                url = CONTRIBUTOR_LINKS.get(str(script_type or ""))
                if not url:
                    return self._failure(f"未知的致谢链接: {script_type}")
                result = launcher.open_external_url(url)
                if not result.get("ok"):
                    return self._failure(
                        result.get("message") or "无法打开外部链接"
                    )
                return self._success(url=url, accepted=True)
            elif action == "clear_logs":
                launcher.clear_all_logs()
            elif action == "exit":
                launcher.on_closing()
            elif action == "exit_app":
                self._exit_async()
                return {"ok": True, "accepted": True}
            elif action == "toggle_question_bank":
                was_running = bool(launcher.question_bank.running)
                result = launcher.toggle_question_bank()
                if not was_running and not result:
                    return self._failure(
                        "题库服务器启动失败，请查看系统日志"
                    )
            elif action == "start_question_bank":
                if not launcher.start_question_bank():
                    return self._failure(
                        "题库服务器启动失败，请查看系统日志"
                    )
            elif action == "stop_question_bank":
                launcher.stop_question_bank()
            elif action == "clear_question_bank":
                return self._with_state(launcher._clear_question_bank())
            elif action == "export_question_bank":
                return self._with_state(launcher._export_question_bank())
            elif action == "import_question_bank":
                return self._with_state(launcher._import_question_bank())
            elif action == "deduplicate_question_bank":
                ok, message = launcher._deduplicate_question_bank()
                result = {"ok": ok, "message": message}
                if ok:
                    result.update({"toast": message, "toastType": "success"})
                return self._with_state(result)
            else:
                return self._failure(f"未知操作: {action}")
        except Exception as exc:
            launcher._show_error("操作失败", str(exc))
            return self._failure(str(exc))

        return self._success()
