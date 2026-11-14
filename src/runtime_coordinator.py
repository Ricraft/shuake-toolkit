"""Batch starts, runtime events, and completion safety for launcher cores."""

from __future__ import annotations

import threading
from contextlib import nullcontext
from datetime import datetime
from src.preferences_service import DEFAULT_PREFERENCES, normalize_batch_targets


class RuntimeCoordinator:
    """Own cross-core state that must remain consistent across worker threads."""

    CORE_LABELS = {
        "yatori": "Yatori",
        "autovisor": "Autovisor",
        "practice": "刷题模式",
        "zhs": "ZHS",
    }

    def __init__(self, launcher):
        self.launcher = launcher
        self._state_lock = threading.RLock()
        self._claim_lock = threading.Lock()
        self._batch_lock = threading.Lock()

    def notify_runtime_event(self, title, message, *, error=False):
        launcher = self.launcher
        pref_key = "notifyOnError" if error else "notifyOnComplete"
        if not launcher._preference_enabled(pref_key, True):
            return False
        launcher._play_feedback_sound(error=error)
        launcher._after(
            0,
            lambda: (
                launcher._show_error(title, message)
                if error
                else launcher._show_info(title, message)
            ),
        )
        return True

    def _runtime_is_idle(self) -> bool:
        launcher = self.launcher
        runtime_lock = getattr(launcher, "_runtime_lock", None)
        if runtime_lock is None:
            return not any(getattr(launcher, "running", {}).values()) and not any(
                getattr(launcher, "starting", {}).values()
            )
        with runtime_lock:
            return not any(launcher.running.values()) and not any(
                launcher.starting.values()
            )

    def reset_failures_for_new_round(self) -> None:
        with self._state_lock:
            self.launcher._runtime_failure_since_batch = False

    def mark_batch_failed(self) -> None:
        with self._state_lock:
            self.launcher._runtime_failure_since_batch = True

    def record_runtime_failure(self, script_type, message):
        launcher = self.launcher
        self.mark_batch_failed()
        target = "autovisor" if script_type == "practice" else script_type
        if target in getattr(launcher, "log_history", {}):
            launcher.log(target, f"[ERROR] {message}")

    def _cancel_pending_shutdown_locked(self, script_type) -> bool:
        launcher = self.launcher
        if not getattr(launcher, "_shutdown_pending", False):
            return True
        result = launcher._cancel_shutdown()
        if result.get("ok"):
            return True
        message = (
            "无法取消待执行的自动关机，已阻止启动: "
            + (result.get("message") or "未知错误")
        )
        reject = getattr(launcher, "_reject_runtime_start", None)
        if callable(reject):
            reject(script_type, message, failure_kind="system")
        launcher.log_system(message)
        return False

    def prepare_start(self, script_type) -> bool:
        """Cancel a pending shutdown before any new runtime work begins."""
        with self._claim_lock:
            return self._cancel_pending_shutdown_locked(script_type)

    def claim_runtime_start(self, script_type) -> bool:
        launcher = self.launcher
        with self._claim_lock:
            if not self._cancel_pending_shutdown_locked(script_type):
                return False
            was_idle = self._runtime_is_idle()
            updates = getattr(launcher, "update_controller", None)
            guard = updates.core_start_guard(script_type) if updates else nullcontext(True)
            with guard as allowed:
                if not allowed:
                    message = "核心正在更新，请等待更新完成后再启动任务"
                    launcher.log_system(message)
                    return launcher._reject_runtime_start(
                        script_type, message, failure_kind="system",
                    )
                claimed = launcher._get_process_supervisor().claim_start(script_type)
            if (
                claimed
                and was_idle
                and not getattr(launcher, "_runtime_batch_starting", False)
            ):
                self.reset_failures_for_new_round()
            return claimed

    def _release_course_overlay(self, script_type) -> None:
        """恢复单课程临时写入的核心配置。"""
        release = getattr(self.launcher, "_release_course_run_overlay", None)
        if not callable(release):
            return
        try:
            release(script_type)
        except Exception as exc:
            try:
                self.launcher.log_system(f"单课程配置恢复失败: {exc}")
            except Exception:
                pass

    def _finalize_if_idle(self, *, allow_shutdown: bool) -> None:
        launcher = self.launcher
        if getattr(launcher, "_runtime_batch_starting", False):
            return
        runtime_lock = getattr(launcher, "_runtime_lock", None)
        with self._claim_lock:
            if runtime_lock is None:
                if not self._runtime_is_idle():
                    return
                self._finish_idle_round(allow_shutdown=allow_shutdown)
                return
            with runtime_lock:
                if any(launcher.running.values()) or any(
                    launcher.starting.values()
                ):
                    return
                self._finish_idle_round(allow_shutdown=allow_shutdown)

    def _finish_idle_round(self, *, allow_shutdown: bool) -> None:
        launcher = self.launcher
        with self._state_lock:
            had_failure = bool(
                getattr(launcher, "_runtime_failure_since_batch", False)
            )
            launcher._runtime_failure_since_batch = False
        if had_failure:
            launcher.log_system("本轮任务存在异常退出，已跳过自动关机")
            launcher.log_system("本轮任务存在异常退出，已跳过自动关闭启动器")
        elif allow_shutdown:
            launcher._maybe_shutdown_after_completion()
            close_launcher = getattr(
                launcher, "_maybe_close_launcher_after_completion", None
            )
            if callable(close_launcher):
                close_launcher()

    def handle_runtime_exit(self, script_type, return_code, stop_requested):
        launcher = self.launcher
        self._release_course_overlay(script_type)
        if stop_requested:
            self._finalize_if_idle(allow_shutdown=False)
            return

        label = self.CORE_LABELS.get(script_type, script_type)
        # zhs 在单门课失败/验证码中断时仍以 0 退出（异常被逐门 try/except 吞掉），
        # 这里用日志标记补一次判定，避免课程没刷完就自动关机。
        interrupted = (
            script_type == "zhs"
            and not return_code
            and bool(getattr(launcher, "_zhs_run_had_failure", False))
        )
        message = None
        if return_code:
            message = f"{label} 已退出，返回码: {return_code}"
        elif interrupted:
            message = f"{label} 本轮有课程未完成（验证码中断或处理失败），已跳过自动关机"
        if message:
            with self._state_lock:
                sequence = (
                    getattr(launcher, "_runtime_event_sequence", 0) + 1
                )
                launcher._runtime_event_sequence = sequence
                launcher._last_runtime_event = {
                    "id": sequence,
                    "kind": "crash" if return_code else "runtime_failure",
                    "core": script_type,
                    "label": label,
                    "return_code": return_code,
                    "message": message,
                    "occurred_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                }
            launcher._record_runtime_failure(script_type, message)
            launcher._notify_runtime_event(
                f"{label} 任务未完成" if interrupted else f"{label} 运行异常",
                message,
                error=True,
            )
        else:
            launcher._notify_runtime_event(
                f"{label} 任务结束",
                f"{label} 已正常停止或完成任务。",
                error=False,
            )

        # Failure and success both converge here so exit order cannot change
        # the shutdown decision.
        self._finalize_if_idle(allow_shutdown=True)

    def handle_runtime_failure(
        self,
        script_type,
        title,
        message,
        *,
        notification_message=None,
        allow_shutdown=True,
    ):
        """Record an exception path and reconcile the round if it is now idle."""
        launcher = self.launcher
        self._release_course_overlay(script_type)
        label = self.CORE_LABELS.get(script_type, script_type)
        with self._state_lock:
            sequence = getattr(launcher, "_runtime_event_sequence", 0) + 1
            launcher._runtime_event_sequence = sequence
            launcher._last_runtime_event = {
                "id": sequence,
                "kind": "runtime_failure",
                "core": script_type,
                "label": label,
                "return_code": None,
                "message": message,
                "occurred_at": datetime.now().isoformat(timespec="seconds"),
            }
        launcher._record_runtime_failure(script_type, message)
        launcher._notify_runtime_event(
            title,
            notification_message or message,
            error=True,
        )
        self._finalize_if_idle(allow_shutdown=allow_shutdown)

    def start_all(self) -> dict:
        launcher = self.launcher
        if not self._batch_lock.acquire(blocking=False):
            message = "一键启动正在进行中，请勿重复操作"
            launcher.log_system(message)
            return {"ok": False, "message": message}

        previous_batch_state = getattr(
            launcher,
            "_runtime_batch_starting",
            False,
        )
        launcher._runtime_batch_starting = True
        try:
            preferences = launcher.get_web_preferences() if hasattr(launcher, "get_web_preferences") else {}
            targets = normalize_batch_targets(preferences.get("batchTargets", DEFAULT_PREFERENCES["batchTargets"]))
            if not targets:
                return {"ok": False, "message": "未选择启动目标，请在软件设置中选择核心", "skipped": [], "submitted": []}
            launcher.log_system("正在启动所选核心: " + "、".join(self.CORE_LABELS[core] for core in targets))
            if not self.prepare_start("batch"):
                message = getattr(launcher, "_last_start_error", {}).get(
                    "batch",
                    "无法取消待执行的自动关机",
                )
                return {
                    "ok": False,
                    "message": message,
                    "failureKinds": ["system"],
                }

            if self._runtime_is_idle():
                self.reset_failures_for_new_round()

            failures = []
            failure_kinds = []
            skipped, submitted, already_running = [], [], []
            eligible = []
            running = getattr(launcher, "running", {})
            starting = getattr(launcher, "starting", {})
            for core in targets:
                if running.get(core) or starting.get(core):
                    already_running.append(core)
                    continue
                checker = getattr(launcher, '_batch_start_skip_reason', None)
                try:
                    reason = checker(core) if callable(checker) else ""
                except Exception:
                    reason = "无法读取配置，请打开核心设置检查"
                if reason:
                    skipped.append({"core": core, "message": reason})
                    launcher.log_system(f"跳过 {self.CORE_LABELS[core]}: {reason}")
                else:
                    eligible.append(core)
            if not hasattr(launcher, "_last_start_error"):
                launcher._last_start_error = {}
            if not hasattr(launcher, "_last_start_failure_kind"):
                launcher._last_start_failure_kind = {}

            question_bank = launcher.question_bank
            needs_question_bank = any(core in ("yatori", "autovisor") for core in eligible)
            if needs_question_bank and not question_bank.available:
                failures.append("题库服务器模块不可用")
            elif needs_question_bank and not question_bank.running:
                try:
                    started = launcher.start_question_bank(silent=True)
                except Exception as exc:
                    launcher.log_system(f"题库服务器启动异常: {exc}")
                    started = False
                if not started:
                    failures.append("题库服务器启动失败")

            running = getattr(launcher, "running", {})
            starting = getattr(launcher, "starting", {})
            for core in eligible:
                if running.get(core) or starting.get(core):
                    continue
                launcher._last_start_failure_kind.pop(core, None)
                try:
                    started = getattr(launcher, f"start_{core}")()
                except Exception as exc:
                    launcher.log_system(
                        f"{self.CORE_LABELS[core]} 启动请求异常: {exc}"
                    )
                    started = False
                    launcher._last_start_error[core] = (
                        f"{self.CORE_LABELS[core]} 启动异常: {exc}"
                    )
                if started:
                    submitted.append(core)
                    continue
                failures.append(
                    getattr(launcher, "_last_start_error", {}).get(
                        core,
                        f"{self.CORE_LABELS[core]} 启动失败",
                    )
                )
                kind = launcher._last_start_failure_kind.get(core)
                if kind:
                    failure_kinds.append(kind)

            if failures:
                self.mark_batch_failed()
                message = "；".join(dict.fromkeys(failures))
                launcher.log_system(f"一键启动未全部成功: {message}")
                return {
                    "ok": False,
                    "message": message,
                    "failureKinds": list(dict.fromkeys(failure_kinds)),
                    "submitted": submitted, "skipped": skipped, "alreadyRunning": already_running,
                }
            message = "启动请求已提交" if submitted else "所选核心已在运行" if already_running else "没有可启动的核心"
            if skipped:
                message += "；" + "；".join(f"跳过 {self.CORE_LABELS[item['core']]}：{item['message']}" for item in skipped)
            return {
                "ok": bool(submitted or already_running),
                "message": message,
                "toast": message,
                "toastType": "warning" if skipped else "success",
                "submitted": submitted, "skipped": skipped, "alreadyRunning": already_running,
            }
        finally:
            launcher._runtime_batch_starting = previous_batch_state
            if not previous_batch_state:
                self._finalize_if_idle(allow_shutdown=False)
            self._batch_lock.release()
