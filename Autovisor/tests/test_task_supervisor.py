# encoding=utf-8

import sys
import time
from pathlib import Path

import pytest


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from web.task_supervisor import TaskAlreadyRunning, TaskSupervisor

sys.path.remove(_AUTOVISOR_ROOT)


class _Process:
    def __init__(self, pid=4321):
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise AssertionError(f"process was not terminated (timeout={timeout})")
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


class _ProcessFactory:
    def __init__(self):
        self.calls = []
        self.processes = []

    def __call__(self, command, **kwargs):
        process = _Process(pid=4321 + len(self.processes))
        self.calls.append((command, kwargs))
        self.processes.append(process)
        return process


def _create_supervisor(temp_path, *, tree_terminator=None):
    runtime_dir = temp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "Autovisor.py").write_text("", encoding="utf-8")
    (runtime_dir / "Autovisor_Multi.py").write_text("", encoding="utf-8")
    config_path = temp_path / "configs.ini"
    config_path.write_text("[user-account]\n", encoding="utf-8")
    factory = _ProcessFactory()
    supervisor = TaskSupervisor(
        runtime_dir,
        python_executable="python-test",
        process_factory=factory,
        tree_terminator=tree_terminator,
        clock=lambda: 123.5,
    )
    return supervisor, factory, config_path


def test_launch_single_course_builds_real_cli_command(tmp_path):
    supervisor, factory, config_path = _create_supervisor(tmp_path)

    snapshot = supervisor.launch(
        mode="single",
        config_path=config_path,
        course_url="https://example.test/course/1",
    )

    command, kwargs = factory.calls[0]
    assert command == [
        "python-test",
        str((tmp_path / "runtime" / "Autovisor.py").resolve()),
        "--config",
        str(config_path.resolve()),
        "--course-url",
        "https://example.test/course/1",
    ]
    assert kwargs["cwd"] == str((tmp_path / "runtime").resolve())
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert snapshot.is_running is True
    assert snapshot.task_id == "https://example.test/course/1"
    assert snapshot.session_id
    assert snapshot.mode == "single"
    assert snapshot.pid == 4321
    assert snapshot.start_time == 123.5
    log_text = (tmp_path / "runtime" / "logs" / "DashboardTask.log").read_text(
        encoding="utf-8"
    )
    assert "[DASHBOARD_SESSION_START]" in log_text
    assert f"session_id={snapshot.session_id}" in log_text
    assert "mode=single" in log_text
    assert "task_id=https://example.test/course/1" in log_text

    with pytest.raises(TaskAlreadyRunning):
        supervisor.launch(mode="single", config_path=config_path)

    factory.processes[0].returncode = 0
    finished = supervisor.refresh()
    assert finished.is_running is False
    assert finished.exit_code == 0
    assert kwargs["stdout"].closed is True


def test_launch_failure_does_not_leave_session_boundary(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "Autovisor.py").write_text("", encoding="utf-8")
    config_path = tmp_path / "configs.ini"
    config_path.write_text("[user-account]\n", encoding="utf-8")
    log_path = tmp_path / "launch.log"

    def fail_launch(_command, **_kwargs):
        raise OSError("launch failed")

    supervisor = TaskSupervisor(
        runtime_dir,
        python_executable="python-test",
        process_factory=fail_launch,
        log_path=log_path,
    )

    with pytest.raises(OSError):
        supervisor.launch(mode="single", config_path=config_path)

    assert log_path.read_text(encoding="utf-8") == ""


def test_stop_terminates_owned_process_tree(tmp_path):
    terminated = []

    def terminate_tree(process):
        terminated.append(process.pid)
        process.terminate()

    supervisor, factory, config_path = _create_supervisor(
        tmp_path,
        tree_terminator=terminate_tree,
    )
    supervisor.launch(mode="multi", config_path=config_path)

    stopped, snapshot = supervisor.stop(timeout=0)

    assert stopped is True
    assert terminated == [4321]
    assert factory.processes[0].terminate_calls == 1
    assert snapshot.is_running is False
    assert snapshot.exit_code == -15

    stopped_again, _snapshot = supervisor.stop(timeout=0)
    assert stopped_again is False


def test_multi_mode_rejects_single_course_override(tmp_path):
    supervisor, factory, config_path = _create_supervisor(tmp_path)

    with pytest.raises(ValueError, match="多账号模式"):
        supervisor.launch(
            mode="multi",
            config_path=config_path,
            course_url="https://example.test/course/1",
        )

    assert factory.calls == []
    assert supervisor.refresh().is_running is False


def test_launch_requires_existing_config(tmp_path):
    supervisor, factory, _config_path = _create_supervisor(tmp_path)

    with pytest.raises(FileNotFoundError):
        supervisor.launch(
            mode="single",
            config_path=tmp_path / "missing.ini",
        )

    assert factory.calls == []


def test_real_short_lived_subprocess_exit_is_observed(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "Autovisor.py").write_text(
        "import time\nprint('dashboard-child-started', flush=True)\ntime.sleep(0.05)\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "configs.ini"
    config_path.write_text("[user-account]\n", encoding="utf-8")
    log_path = tmp_path / "child.log"
    supervisor = TaskSupervisor(
        runtime_dir,
        python_executable=sys.executable,
        log_path=log_path,
    )

    started = supervisor.launch(mode="single", config_path=config_path)
    deadline = time.time() + 5
    finished = started
    try:
        while finished.is_running and time.time() < deadline:
            time.sleep(0.01)
            finished = supervisor.refresh()
    finally:
        if supervisor.refresh().is_running:
            supervisor.stop(timeout=1)

    assert started.pid is not None
    assert finished.is_running is False
    assert finished.exit_code == 0
    assert "dashboard-child-started" in log_path.read_text(encoding="utf-8")
