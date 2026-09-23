"""Lifecycle orchestration for the Web-only practice-mode process."""

from __future__ import annotations

import locale
import os
import subprocess
from typing import Any


class PracticeModeService:
    """Resolve an account and supervise the optional practice-mode process.

    The launcher remains the owner of shared runtime state. This service keeps
    account validation, command construction, process creation and failure
    cleanup out of the already-large Web launcher composition root.
    """

    def __init__(self, launcher: Any):
        self.launcher = launcher

    def stop(self) -> None:
        launcher = self.launcher
        if launcher.starting.get("practice") and not launcher.processes.get("practice"):
            launcher.stop_requested["practice"] = True
            launcher.log_system("正在取消刷题模式启动...")
            return

        process = launcher.processes.get("practice")
        if process and process.poll() is None:
            launcher.log_system("正在停止刷题模式...")
            launcher.stop_requested["practice"] = True
            launcher._terminate_process_tree(process, "刷题模式")
        launcher._mark_runtime_stopped("practice", process)

    def stop_from_web(self) -> dict[str, object]:
        launcher = self.launcher
        if not (
            launcher.running.get("practice")
            or launcher.starting.get("practice")
        ):
            return {"ok": False, "message": "刷题模式当前未运行"}
        self.stop()
        return {"ok": True, "message": "已请求停止刷题模式"}

    def _resolve_account_id(self, account_index: object) -> tuple[int | None, str | None]:
        try:
            normalized_index = int(account_index)
        except (TypeError, ValueError):
            return None, "刷题模式账号序号无效"

        accounts = self.launcher._load_autovisor_config_data().get("accounts", [])
        if normalized_index < 0 or normalized_index >= len(accounts):
            return None, "刷题模式账号不存在，请刷新配置后重试"

        account_id = self.launcher._as_int(
            accounts[normalized_index].get("account_id"),
            normalized_index + 1,
        )
        if account_id < 1:
            return None, "刷题模式账号编号无效"
        return account_id, None

    def _cleanup_failed_start(self, process: object | None) -> None:
        launcher = self.launcher
        if process is not None:
            try:
                if process.poll() is None:
                    launcher._terminate_process_tree(process, "刷题模式")
            except Exception as cleanup_error:
                launcher.log_system(f"[刷题模式] 异常清理失败: {cleanup_error}")
        launcher._mark_runtime_stopped("practice", process)

    def start(self, account_index: object = 0) -> dict[str, object]:
        launcher = self.launcher
        existing = launcher.processes.get("practice")
        if existing and existing.poll() is None:
            return {"ok": False, "message": "刷题模式已经在运行中"}
        if existing:
            launcher._mark_runtime_stopped("practice", existing)

        coordinator = launcher._get_runtime_coordinator()
        if not coordinator.prepare_start("practice"):
            return {
                "ok": False,
                "message": getattr(launcher, "_last_start_error", {}).get(
                    "practice",
                    "无法取消待执行的自动关机，未启动刷题模式",
                ),
            }

        script_path = os.path.join(launcher.autovisor_path, "Practice_Mode.py")
        if not os.path.exists(script_path):
            launcher.log_system(f"[刷题模式] 未找到 {script_path}")
            return {
                "ok": False,
                "message": "未找到刷题模式入口: Autovisor/Practice_Mode.py",
            }

        python_executable = launcher.get_python_executable()
        if not python_executable:
            return {"ok": False, "message": "未找到 Python 解释器"}

        account_id, error = self._resolve_account_id(account_index)
        if error:
            return {"ok": False, "message": error}

        if not launcher._claim_runtime_start("practice"):
            if getattr(launcher, "_last_start_failure_kind", {}).get(
                "practice"
            ) == "system":
                return {
                    "ok": False,
                    "message": getattr(launcher, "_last_start_error", {}).get(
                        "practice",
                        "无法取消待执行的自动关机，未启动刷题模式",
                    ),
                }
            return {"ok": False, "message": "刷题模式已经在运行或启动中"}
        launcher.practice_account_id = account_id

        process = None
        try:
            if not launcher.question_bank.running:
                launcher.log_system("[刷题模式] 正在启动题库服务器...")
                started = launcher.start_question_bank(silent=True)
                if started is False and not launcher.question_bank.running:
                    self._cleanup_failed_start(None)
                    return {"ok": False, "message": "题库服务器启动失败，未启动刷题模式"}

            if launcher.stop_requested.get("practice"):
                launcher.log_system("[刷题模式] 启动已取消")
                self._cleanup_failed_start(None)
                return {"ok": False, "message": "刷题模式启动已取消"}

            launcher.log_system("[刷题模式] 正在启动刷题模式...")
            creationflags, startupinfo = launcher._get_subprocess_window_kwargs()
            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"

            process = subprocess.Popen(
                [
                    python_executable,
                    script_path,
                    "--account-id",
                    str(account_id),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=launcher.autovisor_path,
                text=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
                env=environment,
            )
            launcher._mark_runtime_running("practice", process)
            if launcher.stop_requested.get("practice"):
                launcher.log_system("[刷题模式] 启动已取消")
                self._cleanup_failed_start(process)
                return {"ok": False, "message": "刷题模式启动已取消"}

            encodings = launcher._build_encoding_candidates(
                locale.getpreferredencoding(False),
                "utf-8",
                "gb18030",
                "gbk",
            )
            launcher._get_runtime_process_service().monitor(
                core="practice",
                label="刷题模式",
                process=process,
                encodings=encodings,
                output_source="autovisor",
                exit_message="[刷题模式] 已退出",
            )

            launcher.log_system("[刷题模式] 刷题模式已启动，请在打开的浏览器中操作")
            return {
                "ok": True,
                "message": "刷题模式已启动，系统将自动登录并导航到课程页，请手动点击测验",
            }
        except Exception as exc:
            self._cleanup_failed_start(process)
            launcher.log_system(f"[刷题模式] 启动失败: {exc}")
            return {"ok": False, "message": f"启动刷题模式失败: {exc}"}
