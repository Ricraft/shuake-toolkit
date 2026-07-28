"""Consistent, fault-isolated state snapshots for the WebView frontend."""

from __future__ import annotations

import threading
import time
from contextlib import nullcontext
from typing import Any, Callable

from src.runtime_activity import summarize_autovisor_activity


EMPTY_QB_STATS = {
    "total": 0,
    "local": 0,
    "ai_cached": 0,
}


def _empty_autovisor_activity() -> dict[str, Any]:
    return {
        "phase": "idle",
        "label": "等待启动",
        "progress_percent": None,
        "course": None,
        "course_index": None,
        "course_total": None,
        "last_error": None,
    }


class WebStateService:
    """Build JSON-friendly launcher state without one optional probe taking it down."""

    def __init__(
        self,
        launcher,
        *,
        clock: Callable[[], float] = time.monotonic,
        error_log_interval: float = 30.0,
    ):
        self.launcher = launcher
        self.clock = clock
        self.error_log_interval = max(float(error_log_interval), 0.0)
        self._last_error_log: dict[str, float] = {}
        self._error_log_lock = threading.Lock()

    @staticmethod
    def _fallback(value):
        return value() if callable(value) else value

    def _report_issue(self, component: str, exc: Exception) -> None:
        now = self.clock()
        with self._error_log_lock:
            last = self._last_error_log.get(component)
            if last is not None and now - last < self.error_log_interval:
                return
            self._last_error_log[component] = now
        message = f"{type(exc).__name__}: {str(exc)[:160]}"
        try:
            self.launcher.log_system(
                f"[状态快照] {component}读取失败，已使用安全默认值: {message}"
            )
        except Exception:
            pass

    def _safe(
        self,
        component: str,
        callback: Callable[[], Any],
        fallback,
        issues: list[str],
        *,
        validator: Callable[[Any], bool] | None = None,
    ):
        try:
            value = callback()
            if validator is not None and not validator(value):
                raise TypeError(f"返回类型无效: {type(value).__name__}")
            return value
        except Exception as exc:
            issues.append(f"{component}: {type(exc).__name__}: {str(exc)[:120]}")
            self._report_issue(component, exc)
            return self._fallback(fallback)

    def _runtime_flags(self, issues: list[str]) -> tuple[dict, dict]:
        launcher = self.launcher
        lock = getattr(launcher, "_runtime_lock", None)

        def capture():
            context = lock if hasattr(lock, "__enter__") else nullcontext()
            with context:
                running = dict(getattr(launcher, "running", {}) or {})
                starting = dict(getattr(launcher, "starting", {}) or {})
            return (
                {str(key): bool(value) for key, value in running.items()},
                {str(key): bool(value) for key, value in starting.items()},
            )

        return self._safe(
            "核心运行标记",
            capture,
            lambda: ({}, {}),
            issues,
            validator=lambda value: (
                isinstance(value, tuple)
                and len(value) == 2
                and all(isinstance(item, dict) for item in value)
            ),
        )

    def _logs(self, issues: list[str]) -> dict[str, list[str]]:
        def capture():
            history = getattr(self.launcher, "log_history", {}) or {}
            if not isinstance(history, dict):
                raise TypeError("日志历史不是对象")
            result = {}
            for name, raw_lines in list(history.items()):
                if isinstance(raw_lines, str):
                    lines = raw_lines.splitlines()
                elif raw_lines is None:
                    lines = []
                else:
                    try:
                        lines = list(raw_lines)
                    except TypeError:
                        lines = [raw_lines]
                result[str(name)] = [str(line) for line in lines]
            for name in ("yatori", "autovisor", "system"):
                result.setdefault(name, [])
            return result

        return self._safe(
            "运行日志",
            capture,
            lambda: {"yatori": [], "autovisor": [], "system": []},
            issues,
            validator=lambda value: isinstance(value, dict),
        )

    def runtime_state(self) -> dict[str, Any]:
        launcher = self.launcher
        issues: list[str] = []
        running, starting = self._runtime_flags(issues)

        yatori_version = self._safe(
            "Yatori 版本",
            launcher._get_yatori_display_version,
            getattr(launcher, "YATORI_DISPLAY_VERSION", "未知"),
            issues,
        )
        autovisor_version = self._safe(
            "Autovisor 版本",
            launcher._get_autovisor_display_version,
            getattr(launcher, "AUTOVISOR_DISPLAY_VERSION", "未知"),
            issues,
        )
        yatori_version = str(yatori_version or "未知")
        autovisor_version = str(autovisor_version or "未知")

        yatori_path = str(getattr(launcher, "yatori_path", "") or "")
        autovisor_path = str(getattr(launcher, "autovisor_path", "") or "")
        logs = self._logs(issues)
        autovisor_activity = self._safe(
            "Autovisor 活动摘要",
            lambda: summarize_autovisor_activity(
                logs.get("autovisor"),
                running=bool(running.get("autovisor")),
                starting=bool(starting.get("autovisor")),
            ),
            _empty_autovisor_activity,
            issues,
            validator=lambda value: isinstance(value, dict),
        )

        installed = {
            "yatori": bool(
                self._safe(
                    "Yatori 安装状态",
                    lambda: launcher._directory_has_any_file(
                        yatori_path,
                        launcher.YATORI_ENTRY_FILES,
                    ),
                    False,
                    issues,
                )
            ),
            "autovisor": bool(
                self._safe(
                    "Autovisor 安装状态",
                    lambda: launcher._directory_has_any_file(
                        autovisor_path,
                        launcher.AUTOVISOR_ENTRY_FILES,
                    ),
                    False,
                    issues,
                )
            ),
        }

        question_bank = getattr(launcher, "question_bank", None)
        qb_running = bool(
            self._safe(
                "题库运行状态",
                lambda: getattr(question_bank, "running", False),
                False,
                issues,
            )
        )
        qb_port = self._safe(
            "题库端口",
            lambda: getattr(question_bank, "port", 8083) or 8083,
            8083,
            issues,
        )
        qb_stats = self._safe(
            "题库统计",
            lambda: dict(question_bank.get_stats()),
            lambda: dict(EMPTY_QB_STATS),
            issues,
            validator=lambda value: isinstance(value, dict),
        )

        dependency_installing = bool(
            self._safe(
                "Autovisor 依赖状态",
                lambda: launcher.autovisor_installing,
                False,
                issues,
            )
        )
        multi_mode = bool(
            self._safe(
                "Autovisor 多账号状态",
                launcher._get_autovisor_multi_mode,
                False,
                issues,
            )
        )
        runtime_event = getattr(launcher, "_last_runtime_event", None)
        runtime_event = dict(runtime_event) if isinstance(runtime_event, dict) else None

        core_paths = "\n".join(
            [
                f"Yatori: {yatori_path}",
                f"Autovisor: {autovisor_path}",
            ]
        )
        return {
            "backend_ready": True,
            "yatori_running": bool(running.get("yatori")),
            "autovisor_running": bool(running.get("autovisor")),
            "yatori_version": yatori_version,
            "autovisor_version": autovisor_version,
            "about_version": (
                f"统一启动器 {launcher.LAUNCHER_VERSION} | "
                f"Yatori {yatori_version} | Autovisor {autovisor_version}"
            ),
            "core_paths": core_paths,
            "versions": {
                "yatori": yatori_version,
                "autovisor": autovisor_version,
            },
            "paths": {
                "yatori": yatori_path,
                "autovisor": autovisor_path,
            },
            "installed": installed,
            "running": running,
            "starting": starting,
            "dependency_installing": {
                "autovisor": dependency_installing,
            },
            "practice_account_id": getattr(launcher, "practice_account_id", None),
            "qb_running": qb_running,
            "qb_port": qb_port,
            "qb_stats": qb_stats,
            "logs": logs,
            "autovisor_activity": autovisor_activity,
            "shutdown_pending": bool(
                getattr(launcher, "_shutdown_pending", False)
            ),
            "autovisor_multi_mode": multi_mode,
            "runtime_event": runtime_event,
            "state_issues": issues,
        }

    def initial_state(self) -> dict[str, Any]:
        launcher = self.launcher
        return {
            "runtime": self.runtime_state(),
            "preferences": launcher.get_web_preferences(),
            "settings": {
                "yatori": launcher._load_yatori_config_data(),
                "autovisor": launcher._load_autovisor_config_data(),
                "questionbank": launcher.get_qb_settings_from_web(),
            },
        }
