"""Shared background lifecycle for launcher-managed core processes."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any


class RuntimeProcessService:
    """Start a core process, stream its output, and always reconcile state."""

    def __init__(
        self,
        launcher,
        *,
        process_factory: Callable[..., Any] | None = None,
        thread_factory: Callable[..., Any] | None = None,
    ):
        self.launcher = launcher
        self.process_factory = process_factory or subprocess.Popen
        self.thread_factory = thread_factory or threading.Thread

    def start(
        self,
        *,
        core: str,
        label: str,
        command: Sequence[str],
        cwd: str,
        encodings: Iterable[str],
        before_launch: Callable[[], None] | None = None,
        env_factory: Callable[[], dict[str, str] | None] | None = None,
    ) -> bool:
        """Run one already-claimed core in a daemon thread."""
        command = list(command)
        encodings = tuple(encodings)

        def worker() -> None:
            process = None
            try:
                if self._cancelled(core, label):
                    return
                if before_launch is not None:
                    before_launch()
                if self._cancelled(core, label):
                    return

                env = env_factory() if env_factory is not None else None
                creationflags, startupinfo = (
                    self.launcher._get_subprocess_window_kwargs()
                )
                process = self.process_factory(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    text=False,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
                self.launcher._mark_runtime_running(core, process)
                if self.launcher.stop_requested.get(core):
                    self.launcher._terminate_process_tree(process, label)

                self.launcher._stream_process_output(process, core, encodings)
                return_code = process.wait()
                stop_requested = self.launcher.stop_requested.get(core, False)
                self.launcher._mark_runtime_stopped(core, process)
                if return_code:
                    self.launcher.log_system(
                        f"{label} 已退出，返回码: {return_code}"
                    )
                else:
                    self.launcher.log_system(f"{label} 已停止")
                self.launcher._handle_runtime_exit(
                    core,
                    return_code,
                    stop_requested,
                )
            except Exception as exc:
                self._cleanup_failed_process(process, label)
                self.launcher.log_system(f"{label} 启动失败: {exc}")
                self.launcher._mark_runtime_stopped(core, process)
                self._report_runtime_failure(
                    core,
                    f"{label} 启动失败",
                    f"{label} 启动失败: {exc}",
                    notification_message=str(exc),
                )

        try:
            thread = self.thread_factory(target=worker, daemon=True)
            thread.start()
        except Exception as exc:
            self.launcher._mark_runtime_stopped(core)
            message = f"{label} 启动失败: {exc}"
            self.launcher.log_system(message)
            self._report_runtime_failure(
                core,
                f"{label} 启动失败",
                message,
                notification_message=str(exc),
            )
            raise
        return True

    def monitor(
        self,
        *,
        core: str,
        label: str,
        process,
        encodings: Iterable[str],
        output_source: str | None = None,
        exit_message: str | None = None,
    ) -> bool:
        """Monitor a synchronously-created process in a daemon thread."""
        encodings = tuple(encodings)

        def worker() -> None:
            try:
                self.launcher._stream_process_output(
                    process,
                    output_source or core,
                    encodings,
                )
                return_code = process.wait()
                stop_requested = self.launcher.stop_requested.get(
                    core,
                    False,
                )
                self.launcher._mark_runtime_stopped(core, process)
                if return_code:
                    message = f"{label} 已退出，返回码: {return_code}"
                    self.launcher.log_system(message)
                    if not stop_requested:
                        self._record_failure(
                            output_source or core,
                            message,
                        )
                        self.launcher._notify_runtime_event(
                            f"{label} 运行异常",
                            message,
                            error=True,
                        )
                else:
                    self.launcher.log_system(
                        exit_message or f"{label} 已退出"
                    )
            except Exception as exc:
                self._cleanup_failed_process(process, label)
                self.launcher._mark_runtime_stopped(core, process)
                self.launcher.log_system(f"{label} 运行失败: {exc}")
                self._record_failure(
                    output_source or core,
                    f"{label} 运行失败: {exc}",
                )
                self.launcher._notify_runtime_event(
                    f"{label} 运行失败",
                    str(exc),
                    error=True,
                )

        try:
            thread = self.thread_factory(target=worker, daemon=True)
            thread.start()
        except Exception:
            self._cleanup_failed_process(process, label)
            self.launcher._mark_runtime_stopped(core, process)
            raise
        return True

    def _record_failure(self, core: str, message: str) -> None:
        recorder = getattr(self.launcher, "_record_runtime_failure", None)
        if callable(recorder):
            recorder(core, message)

    def _report_runtime_failure(
        self,
        core: str,
        title: str,
        message: str,
        *,
        notification_message: str | None = None,
    ) -> None:
        handler = getattr(self.launcher, "_handle_runtime_failure", None)
        if callable(handler):
            handler(
                core,
                title,
                message,
                notification_message=notification_message,
            )
            return
        self._record_failure(core, message)
        self.launcher._notify_runtime_event(
            title,
            notification_message or message,
            error=True,
        )

    def _cancelled(self, core: str, label: str) -> bool:
        if not self.launcher.stop_requested.get(core):
            return False
        self.launcher.log_system(f"{label} 启动已取消")
        self.launcher._mark_runtime_stopped(core)
        return True

    def _cleanup_failed_process(self, process, label: str) -> None:
        if process is None:
            return
        poll = getattr(process, "poll", None)
        try:
            is_running = poll is None or poll() is None
        except Exception:
            is_running = True
        if not is_running:
            return
        try:
            self.launcher._terminate_process_tree(process, label)
        except Exception as cleanup_error:
            self.launcher.log_system(
                f"{label} 异常清理失败: {cleanup_error}"
            )
