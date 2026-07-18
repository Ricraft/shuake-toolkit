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
                self.launcher._notify_runtime_event(
                    f"{label} 启动失败",
                    str(exc),
                    error=True,
                )

        try:
            thread = self.thread_factory(target=worker, daemon=True)
            thread.start()
        except Exception:
            self.launcher._mark_runtime_stopped(core)
            raise
        return True

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
