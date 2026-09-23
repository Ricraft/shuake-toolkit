# encoding=utf-8
"""Yatori timeout uses the launcher process lifecycle, never a real core."""

import threading
from types import SimpleNamespace


from src.preferences_service import DEFAULT_PREFERENCES, PreferencesService
from src.yatori_runtime_limit import YatoriRuntimeLimit
from 统一启动器 import UnifiedLauncher


class FakeTimer:
    created = []

    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.started = False
        self.cancelled = False
        self.daemon = False
        self.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # A callback may already be queued when cancel() runs.
        self.callback()


class FakeProcess:
    def __init__(self):
        self.exit_code = None

    def poll(self):
        return self.exit_code


def make_launcher():
    process = FakeProcess()
    stops, logs = [], []
    launcher = SimpleNamespace(
        _runtime_lock=threading.RLock(),
        processes={"yatori": process},
        running={"yatori": True},
        stop_requested={"yatori": False},
        log_system=logs.append,
    )
    def stop():
        launcher.stop_requested["yatori"] = True
        stops.append(launcher.processes["yatori"])
    launcher.stop_yatori = stop
    return launcher, process, stops, logs


def test_starts_only_for_running_yatori_and_expires_after_configured_minutes():
    FakeTimer.created = []
    launcher, process, stops, logs = make_launcher()
    limit = YatoriRuntimeLimit(launcher, timer_factory=FakeTimer)
    limit.started(process, 12)
    timer = FakeTimer.created[-1]
    assert timer.seconds == 720 and timer.daemon and timer.started
    assert stops == []
    timer.fire()
    assert stops == [process]
    assert launcher.stop_requested["yatori"] is True
    assert any("不代表课程已完成" in line for line in logs)
    timer.fire()
    assert stops == [process]


def test_early_exit_or_manual_stop_cancels_and_queued_callback_is_inert():
    FakeTimer.created = []
    launcher, process, stops, _logs = make_launcher()
    limit = YatoriRuntimeLimit(launcher, timer_factory=FakeTimer)
    limit.started(process, 12)
    timer = FakeTimer.created[-1]
    launcher.running["yatori"] = False
    limit.cancel(process)
    assert timer.cancelled
    timer.fire()
    assert stops == []
    launcher.running["yatori"] = True
    limit.started(process, 12)
    second = FakeTimer.created[-1]
    launcher.stop_requested["yatori"] = True
    second.fire()
    assert stops == []


def test_old_process_cannot_cancel_or_stop_restarted_process():
    FakeTimer.created = []
    launcher, old_process, stops, _logs = make_launcher()
    limit = YatoriRuntimeLimit(launcher, timer_factory=FakeTimer)
    limit.started(old_process, 12)
    old_timer = FakeTimer.created[-1]
    fresh_process = FakeProcess()
    launcher.processes["yatori"] = fresh_process
    limit.started(fresh_process, 12)
    new_timer = FakeTimer.created[-1]
    assert old_timer.cancelled
    limit.cancel(old_process)
    assert not new_timer.cancelled
    old_timer.fire()
    assert stops == []
    new_timer.fire()
    assert stops == [fresh_process]


def test_disabled_or_exited_process_does_not_trigger_stop():
    FakeTimer.created = []
    launcher, process, stops, _logs = make_launcher()
    limit = YatoriRuntimeLimit(launcher, timer_factory=FakeTimer)
    limit.started(process, 0)
    assert FakeTimer.created == []
    limit.started(process, 12)
    process.exit_code = 0
    FakeTimer.created[-1].fire()
    assert stops == []


def test_preferences_validate_bounds_and_persist_disabled_default(tmp_path):
    assert DEFAULT_PREFERENCES["yatoriMaxRuntimeMinutes"] == 0
    service = PreferencesService(tmp_path / "prefs.json")
    assert service.update({"yatoriMaxRuntimeMinutes": 12})["ok"]
    assert service.get()["yatoriMaxRuntimeMinutes"] == 12
    for invalid in (-1, 10081, float("nan"), True, "nonsense"):
        assert not service.update({"yatoriMaxRuntimeMinutes": invalid})["ok"]
        assert service.get()["yatoriMaxRuntimeMinutes"] == 12
    reloaded = PreferencesService(tmp_path / "prefs.json")
    assert reloaded.get()["yatoriMaxRuntimeMinutes"] == 12
    assert reloaded.update({"yatoriMaxRuntimeMinutes": 0})["ok"]
    assert reloaded.get()["yatoriMaxRuntimeMinutes"] == 0


def test_launcher_marks_running_before_scheduling_and_stopped_process_cancels():
    FakeTimer.created = []
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher._runtime_lock = threading.RLock()
    launcher.processes = {"yatori": None}
    launcher.running = {"yatori": False}
    launcher.starting = {"yatori": True}
    launcher.stop_requested = {"yatori": False}
    launcher.get_web_preferences = lambda: {"yatoriMaxRuntimeMinutes": 12}
    launcher.log_system = lambda _text: None
    class Supervisor:
        def mark_running(self, core, process):
            assert core == "yatori"
            launcher.processes[core] = process
            launcher.running[core] = True
        def mark_stopped(self, core, process=None):
            if process is not None and launcher.processes[core] is not process:
                return False
            launcher.processes[core] = None
            launcher.running[core] = False
            return True
    supervisor = Supervisor()
    launcher._get_process_supervisor = lambda: supervisor
    launcher._yatori_runtime_limit = YatoriRuntimeLimit(launcher, timer_factory=FakeTimer)
    process = FakeProcess()
    launcher._mark_runtime_running("yatori", process)
    timer = FakeTimer.created[-1]
    assert timer.seconds == 720
    assert launcher._mark_runtime_stopped("yatori", process) is True
    assert timer.cancelled


def test_real_stop_yatori_marks_requested_before_terminating_and_cancels_timer():
    FakeTimer.created = []
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher._runtime_lock = threading.RLock()
    process = FakeProcess()
    launcher.processes = {"yatori": process}
    launcher.running = {"yatori": True}
    launcher.starting = {"yatori": False}
    launcher.stop_requested = {"yatori": False}
    launcher.log_system = lambda _text: None
    def terminate(current, label):
        assert current is process and label == "Yatori"
        assert launcher.stop_requested["yatori"] is True
    launcher._terminate_process_tree = terminate
    launcher._yatori_runtime_limit = YatoriRuntimeLimit(launcher, timer_factory=FakeTimer)
    launcher._yatori_runtime_limit.started(process, 12)
    timer = FakeTimer.created[-1]
    def mark_stopped(core):
        assert core == "yatori"
        launcher.running[core] = False
        launcher.processes[core] = None
    launcher._mark_runtime_stopped = mark_stopped
    launcher.stop_yatori()
    assert timer.cancelled
    assert launcher.stop_requested["yatori"] is True
    timer.fire()
    assert launcher.running["yatori"] is False
