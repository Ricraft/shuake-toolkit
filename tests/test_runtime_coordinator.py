import threading
from types import SimpleNamespace

from src.runtime_coordinator import RuntimeCoordinator


class Supervisor:
    def __init__(self, launcher):
        self.launcher = launcher
        self.calls = []

    def claim_start(self, core):
        self.calls.append(core)
        if self.launcher.running.get(core) or self.launcher.starting.get(core):
            return False
        self.launcher.starting[core] = True
        return True


def make_runtime_launcher():
    system_logs = []
    core_logs = []
    notices = []
    sounds = []
    shutdowns = []
    launcher = SimpleNamespace(
        running={"yatori": False, "autovisor": False, "practice": False},
        starting={"yatori": False, "autovisor": False, "practice": False},
        _runtime_lock=threading.RLock(),
        _runtime_failure_since_batch=False,
        _runtime_batch_starting=False,
        _runtime_event_sequence=0,
        _last_runtime_event=None,
        _shutdown_pending=False,
        _last_start_error={},
        _last_start_failure_kind={},
        log_history={"yatori": [], "autovisor": [], "system": []},
        log_system=system_logs.append,
        log=lambda core, message: core_logs.append((core, message)),
        _preference_enabled=lambda _key, _default=True: True,
        _play_feedback_sound=lambda error=False: sounds.append(error),
        _after=lambda _delay, callback: callback(),
        _show_error=lambda title, message: notices.append(
            ("error", title, message)
        ),
        _show_info=lambda title, message: notices.append(
            ("info", title, message)
        ),
        _maybe_shutdown_after_completion=lambda: shutdowns.append(True),
    )
    supervisor = Supervisor(launcher)
    launcher._get_process_supervisor = lambda: supervisor

    def reject(core, message, failure_kind=None):
        launcher._last_start_error[core] = message
        if failure_kind:
            launcher._last_start_failure_kind[core] = failure_kind
        return False

    launcher._reject_runtime_start = reject
    coordinator = RuntimeCoordinator(launcher)
    launcher._record_runtime_failure = coordinator.record_runtime_failure
    launcher._notify_runtime_event = coordinator.notify_runtime_event
    launcher._handle_runtime_failure = coordinator.handle_runtime_failure
    launcher.coordinator = coordinator
    launcher.supervisor = supervisor
    launcher.system_logs = system_logs
    launcher.core_logs = core_logs
    launcher.notices = notices
    launcher.sounds = sounds
    launcher.shutdowns = shutdowns
    return launcher


def test_failed_core_exiting_last_always_skips_shutdown_and_finishes_round():
    launcher = make_runtime_launcher()

    launcher.coordinator.handle_runtime_exit("autovisor", 3, False)

    assert launcher.shutdowns == []
    assert launcher._runtime_failure_since_batch is False
    assert launcher._last_runtime_event["kind"] == "crash"
    assert launcher._last_runtime_event["return_code"] == 3
    assert ("autovisor", "[ERROR] Autovisor 已退出，返回码: 3") in (
        launcher.core_logs
    )
    assert "本轮任务存在异常退出，已跳过自动关机" in launcher.system_logs


def test_normal_exit_does_not_shutdown_while_another_core_is_starting():
    launcher = make_runtime_launcher()
    launcher.starting["autovisor"] = True

    launcher.coordinator.handle_runtime_exit("yatori", 0, False)

    assert launcher.shutdowns == []
    launcher.starting["autovisor"] = False
    launcher.coordinator.handle_runtime_exit("autovisor", 0, False)
    assert launcher.shutdowns == [True]


def test_normal_exit_does_not_shutdown_while_practice_mode_is_running():
    launcher = make_runtime_launcher()
    launcher.running["practice"] = True

    launcher.coordinator.handle_runtime_exit("yatori", 0, False)

    assert launcher.shutdowns == []


def test_new_start_cancels_pending_shutdown_before_claiming_runtime():
    launcher = make_runtime_launcher()
    launcher._runtime_failure_since_batch = True
    launcher._shutdown_pending = True
    cancellations = []

    def cancel_shutdown():
        cancellations.append(True)
        launcher._shutdown_pending = False
        return {"ok": True, "message": "已取消关机"}

    launcher._cancel_shutdown = cancel_shutdown

    assert launcher.coordinator.claim_runtime_start("yatori") is True

    assert cancellations == [True]
    assert launcher.supervisor.calls == ["yatori"]
    assert launcher.starting["yatori"] is True
    assert launcher._runtime_failure_since_batch is False


def test_start_is_rejected_when_pending_shutdown_cannot_be_cancelled():
    launcher = make_runtime_launcher()
    launcher._shutdown_pending = True
    launcher._cancel_shutdown = lambda: {
        "ok": False,
        "message": "系统拒绝取消",
    }

    assert launcher.coordinator.claim_runtime_start("autovisor") is False

    assert launcher.supervisor.calls == []
    assert launcher._last_start_failure_kind["autovisor"] == "system"
    assert "系统拒绝取消" in launcher._last_start_error["autovisor"]


def test_duplicate_batch_start_is_rejected_while_first_request_is_running():
    launcher = make_runtime_launcher()
    launcher.question_bank = SimpleNamespace(available=True, running=True)
    launcher.start_question_bank = lambda **_kwargs: True
    entered = threading.Event()
    release = threading.Event()
    first_results = []

    def start_yatori():
        entered.set()
        release.wait(timeout=2)
        return True

    launcher.start_yatori = start_yatori
    launcher.start_autovisor = lambda: True
    worker = threading.Thread(
        target=lambda: first_results.append(
            launcher.coordinator.start_all()
        )
    )
    worker.start()
    assert entered.wait(timeout=1)

    duplicate = launcher.coordinator.start_all()
    release.set()
    worker.join(timeout=2)

    assert duplicate == {
        "ok": False,
        "message": "一键启动正在进行中，请勿重复操作",
    }
    assert first_results[0]["ok"] is True
    assert launcher._runtime_batch_starting is False


def test_disabled_notification_preference_suppresses_sound_and_dialog():
    launcher = make_runtime_launcher()
    launcher._preference_enabled = lambda _key, _default=True: False

    result = launcher.coordinator.notify_runtime_event(
        "任务结束",
        "全部完成",
    )

    assert result is False
    assert launcher.sounds == []
    assert launcher.notices == []


def test_async_start_failure_exiting_last_is_finalized_independently_of_order():
    launcher = make_runtime_launcher()

    launcher.coordinator.handle_runtime_failure(
        "yatori",
        "Yatori 启动失败",
        "Yatori 启动失败: cannot spawn",
        notification_message="cannot spawn",
    )

    assert launcher.shutdowns == []
    assert launcher._runtime_failure_since_batch is False
    assert launcher._last_runtime_event["kind"] == "runtime_failure"
    assert "本轮任务存在异常退出，已跳过自动关机" in launcher.system_logs


def test_failure_is_not_cleared_while_batch_is_still_starting_other_cores():
    launcher = make_runtime_launcher()
    launcher._runtime_batch_starting = True

    launcher.coordinator.handle_runtime_failure(
        "yatori",
        "Yatori 启动失败",
        "Yatori 启动失败: cannot spawn",
    )

    assert launcher._runtime_failure_since_batch is True
    assert "本轮任务存在异常退出，已跳过自动关机" not in (
        launcher.system_logs
    )

    launcher._runtime_batch_starting = False
    launcher.coordinator._finalize_if_idle(allow_shutdown=False)
    assert launcher._runtime_failure_since_batch is False
    assert "本轮任务存在异常退出，已跳过自动关机" in launcher.system_logs
