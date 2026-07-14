# encoding=utf-8
"""Dashboard 启动的 Autovisor 子进程生命周期管理。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal


TaskMode = Literal["single", "multi"]


class TaskAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskSnapshot:
    is_running: bool = False
    task_id: str | None = None
    mode: TaskMode | None = None
    pid: int | None = None
    start_time: float | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class TaskSupervisor:
    """启动、观察并停止 Dashboard 所属的单个任务进程树。"""

    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        python_executable: str | None = None,
        process_factory: Callable = subprocess.Popen,
        tree_terminator: Callable | None = None,
        clock: Callable[[], float] = time.time,
        log_path: str | Path | None = None,
    ):
        self.runtime_dir = Path(runtime_dir).resolve()
        self.python_executable = python_executable or sys.executable
        self.process_factory = process_factory
        self.tree_terminator = tree_terminator or self._terminate_process_tree
        self.clock = clock
        self.log_path = Path(log_path or self.runtime_dir / "logs" / "DashboardTask.log")
        self._lock = threading.RLock()
        self._process = None
        self._log_handle = None
        self._task_id: str | None = None
        self._mode: TaskMode | None = None
        self._start_time: float | None = None
        self._last_exit_code: int | None = None

    def _snapshot_locked(self) -> TaskSnapshot:
        process = self._process
        return TaskSnapshot(
            is_running=process is not None,
            task_id=self._task_id if process is not None else None,
            mode=self._mode if process is not None else None,
            pid=getattr(process, "pid", None) if process is not None else None,
            start_time=self._start_time if process is not None else None,
            exit_code=self._last_exit_code,
        )

    def _close_log_locked(self) -> None:
        if self._log_handle is None:
            return
        try:
            self._log_handle.close()
        finally:
            self._log_handle = None

    def _clear_process_locked(self, exit_code: int | None) -> None:
        self._last_exit_code = exit_code
        self._process = None
        self._task_id = None
        self._mode = None
        self._start_time = None
        self._close_log_locked()

    def refresh(self) -> TaskSnapshot:
        """同步进程退出状态并返回当前快照。"""
        with self._lock:
            if self._process is not None:
                exit_code = self._process.poll()
                if exit_code is not None:
                    self._clear_process_locked(exit_code)
            return self._snapshot_locked()

    def _build_command(
        self,
        mode: TaskMode,
        config_path: Path,
        course_url: str | None,
    ) -> list[str]:
        script = "Autovisor.py" if mode == "single" else "Autovisor_Multi.py"
        script_path = self.runtime_dir / script
        if not script_path.is_file():
            raise FileNotFoundError(script_path)
        command = [
            str(self.python_executable),
            str(script_path),
            "--config",
            str(config_path),
        ]
        if course_url:
            if mode != "single":
                raise ValueError("多账号模式不支持单课程临时覆盖")
            command.extend(["--course-url", course_url])
        return command

    def launch(
        self,
        *,
        mode: TaskMode,
        config_path: str | Path,
        course_url: str | None = None,
    ) -> TaskSnapshot:
        """启动任务；只有子进程创建成功后才更新运行状态。"""
        if mode not in {"single", "multi"}:
            raise ValueError(f"不支持的任务模式: {mode}")
        resolved_config = Path(config_path).resolve()
        if not resolved_config.is_file():
            raise FileNotFoundError(resolved_config)
        normalized_url = course_url.strip() if course_url else None

        with self._lock:
            self.refresh()
            if self._process is not None:
                raise TaskAlreadyRunning("已有任务正在运行")

            command = self._build_command(mode, resolved_config, normalized_url)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = self.log_path.open("a", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            kwargs = {
                "cwd": str(self.runtime_dir),
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            try:
                process = self.process_factory(command, **kwargs)
            except Exception:
                log_handle.close()
                raise

            self._process = process
            self._log_handle = log_handle
            self._task_id = normalized_url or (
                "all_accounts" if mode == "multi" else "all_courses"
            )
            self._mode = mode
            self._start_time = self.clock()
            self._last_exit_code = None
            return self._snapshot_locked()

    @staticmethod
    def _terminate_process_tree(process) -> None:
        pid = getattr(process, "pid", None)
        if not pid:
            process.terminate()
            return
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode != 0 and process.poll() is None:
                process.terminate()
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if process.poll() is None:
                process.terminate()

    @staticmethod
    def _kill_process_tree(process) -> None:
        pid = getattr(process, "pid", None)
        if os.name == "nt" and pid:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if process.poll() is None:
            process.kill()

    def stop(self, timeout: float = 10) -> tuple[bool, TaskSnapshot]:
        """停止任务进程树，返回 ``(是否确实停止了任务, 当前快照)``。"""
        with self._lock:
            self.refresh()
            process = self._process
            if process is None:
                return False, self._snapshot_locked()

        try:
            try:
                self.tree_terminator(process)
            except Exception:
                if process.poll() is None:
                    process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill_process_tree(process)
                process.wait(timeout=timeout)
        except Exception:
            self.refresh()
            raise

        with self._lock:
            if self._process is process:
                polled_code = process.poll()
                self._clear_process_locked(
                    polled_code if polled_code is not None else -1
                )

        return True, self.refresh()
