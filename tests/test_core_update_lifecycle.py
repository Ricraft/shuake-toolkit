"""Core updates and launch reservations must not overlap."""
import threading
from types import SimpleNamespace

import pytest

from src.runtime_coordinator import RuntimeCoordinator
from src.update_controller import UpdateController


def make_controller(launcher):
    pending, notices = [], []
    updates = UpdateController(
        SimpleNamespace(), log=lambda *a: None,
        show_info=lambda *a: None, show_error=lambda *a: None,
        show_warning=lambda *a: notices.append(a),
        is_core_running=lambda core: bool(launcher.starting.get(core)),
        on_installed=lambda core: None, get_autovisor_version=lambda: "local",
        schedule=lambda ms, cb: cb(), run_async=pending.append,
    )
    return updates, pending, notices


@pytest.mark.parametrize("core,updated", [("yatori", "yatori"), ("zhs", "zhs"), ("practice", "autovisor")])
def test_update_blocks_same_core_start_and_releases_after_completion(core, updated):
    errors = []
    launcher = SimpleNamespace(starting={}, log_system=lambda *a: None)
    updates, pending, _ = make_controller(launcher)
    updates.core_manager = SimpleNamespace(**{f"install_{updated}": lambda *a: True})
    launcher.update_controller = updates
    launcher._get_process_supervisor = lambda: SimpleNamespace(claim_start=lambda name: launcher.starting.setdefault(name, True))
    launcher._reject_runtime_start = lambda *a, **kw: errors.append((a, kw)) or False
    coordinator = RuntimeCoordinator(launcher)
    coordinator._cancel_pending_shutdown_locked = lambda core: True
    coordinator._runtime_is_idle = lambda: False

    assert updates._install_async(updated, {"version": "new"})
    assert coordinator.claim_runtime_start(core) is False
    assert errors[-1][1]["failure_kind"] == "system"
    assert launcher.starting == {}
    other = "yatori" if updated != "yatori" else "zhs"
    assert coordinator.claim_runtime_start(other)
    pending.pop()()
    assert coordinator.claim_runtime_start(core)


def test_start_reservation_and_update_are_serialized_across_threads():
    launcher = SimpleNamespace(starting={})
    updates, pending, notices = make_controller(launcher)
    reserved, release, checked = threading.Event(), threading.Event(), threading.Event()
    result = []

    def reserve():
        with updates.core_start_guard("zhs") as allowed:
            assert allowed
            reserved.set()
            assert release.wait(2)
            launcher.starting["zhs"] = True

    def update():
        checked.set()
        result.append(updates.install_zhs_async({"version": "new"}))

    starter = threading.Thread(target=reserve)
    updater = threading.Thread(target=update)
    starter.start()
    try:
        assert reserved.wait(2)
        updater.start()
        assert checked.wait(2)
    finally:
        release.set()
        starter.join(2)
        if updater.ident is not None:
            updater.join(2)
    assert not starter.is_alive() and not updater.is_alive()
    assert result == [False] and notices and pending == []


def test_zhs_environment_preparation_cannot_begin_during_core_install():
    from 统一启动器 import UnifiedLauncher
    launcher = SimpleNamespace(starting={})
    updates, _, _ = make_controller(launcher)
    updates.installing, updates.installing_core = True, "zhs"
    launcher.update_controller = updates
    launcher._zhs_runtime_prepare_allowed = lambda **kw: pytest.fail("must not prepare during install")
    assert UnifiedLauncher._request_zhs_runtime_prepare(launcher, force=True) is False


@pytest.mark.parametrize("kind", ["core", "launcher", "autovisor", "zhs"])
def test_exit_waits_for_installers_before_cancelling_tasks_or_applying_update(kind):
    from 统一启动器 import UnifiedLauncher
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    warnings = []
    launcher._preference_enabled = lambda *a: False
    launcher.log_system = lambda *a: None
    launcher._show_warning = lambda *a: warnings.append(a)
    launcher._cancel_scheduled_callbacks = lambda: pytest.fail("must not tear down while installing")
    attributes = {"core": "update_controller", "launcher": "_launcher_update_service", "autovisor": "autovisor_dependencies"}
    if kind == "zhs":
        launcher._zhs_runtime_prepare_running = True
    else:
        setattr(launcher, attributes[kind], SimpleNamespace(installing=True))
    assert launcher._launcher_update_cores_running()
    launcher.on_closing(confirmed=True)
    assert warnings
