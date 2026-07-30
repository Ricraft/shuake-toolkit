from types import SimpleNamespace

from src.launcher_startup_service import LauncherStartupService


class _Launcher:
    def __init__(self, *, accounts=None):
        self.web_window = object()
        self.running = {"yatori": True, "autovisor": False}
        self.core_manager = None
        self.logs = []
        self.cleaned = []
        self.dependency_prepared = False
        self.accounts = accounts or []

    def get_base_dir(self):
        return "project-root"

    def get_preferences_file_path(self, base_dir):
        return f"{base_dir}/preferences.json"

    def log_system(self, message):
        self.logs.append(message)

    def _handle_preference_side_effects(self, *_args):
        return None

    def _rollback_preference_side_effects(self, *_args):
        return None

    def _clean_old_runtime_logs(self, base_dir):
        self.cleaned.append(base_dir)

    def find_yatori_path(self, base_dir):
        return f"{base_dir}/Yatori"

    def find_autovisor_path(self, base_dir):
        return f"{base_dir}/Autovisor"

    def _get_autovisor_dependency_manager(self):
        self.dependency_prepared = True
        return object()

    def _after_qb_status_update(self):
        return None

    def _sync_yatori_question_bank_url(self):
        return None

    def _load_autovisor_config_data(self):
        return {"accounts": self.accounts}

    def _core_manager_log(self, _message):
        return None

    def _show_info(self, *_args):
        return None

    def _show_warning(self, *_args):
        return None

    def _show_error(self, *_args):
        return None

    def _handle_core_installed(self, _core):
        return None

    def _get_autovisor_display_version(self):
        return "local"

    def _after(self, delay, callback):
        return (delay, callback.__name__)

    def auto_start_question_bank(self):
        return None

    def auto_check_cores(self):
        return None

    def _apply_auto_run_preference(self):
        return None


def test_compose_builds_services_in_order_and_preserves_callbacks():
    launcher = _Launcher(accounts=[{"id": 1}, {"id": 2}])
    calls = {}

    def desktop_factory(base_dir, source_file, **kwargs):
        calls["desktop"] = (base_dir, source_file, kwargs)
        return "desktop"

    def preferences_factory(path, **kwargs):
        calls["preferences"] = (path, kwargs)
        return SimpleNamespace(values={"autoCleanLogs": True})

    def question_bank_factory(base_dir, **kwargs):
        calls["question_bank"] = (base_dir, kwargs)
        return SimpleNamespace(available=True)

    def core_manager_factory(base_dir, **kwargs):
        calls["core_manager"] = (base_dir, kwargs)
        return "core-manager"

    def update_factory(core_manager, **kwargs):
        calls["update"] = (core_manager, kwargs)
        return "update-controller"

    service = LauncherStartupService(
        launcher,
        source_file="launcher.py",
        sound_module="sound",
        core_manager_factory=core_manager_factory,
        desktop_factory=desktop_factory,
        preferences_factory=preferences_factory,
        question_bank_factory=question_bank_factory,
        update_factory=update_factory,
    )

    assert service.compose() == "project-root"

    assert launcher._desktop_platform_service == "desktop"
    assert launcher.preferences_path == "project-root/preferences.json"
    assert launcher.web_preferences == {"autoCleanLogs": True}
    assert launcher.cleaned == ["project-root"]
    assert launcher.yatori_path == "project-root/Yatori"
    assert launcher.autovisor_path == "project-root/Autovisor"
    assert launcher.dependency_prepared is True
    assert launcher.autovisor_multi_mode is True
    assert launcher.core_manager == "core-manager"
    assert launcher.update_controller == "update-controller"
    assert "[QB] 题库服务器模块已加载" in launcher.logs

    _, _, desktop_kwargs = calls["desktop"]
    assert desktop_kwargs["sound_module"] == "sound"
    assert desktop_kwargs["get_window"]() is launcher.web_window
    _, preference_kwargs = calls["preferences"]
    assert preference_kwargs["log"] == launcher.log_system
    _, question_bank_kwargs = calls["question_bank"]
    assert question_bank_kwargs["on_status_change"] == (
        launcher._after_qb_status_update
    )
    assert calls["core_manager"][1]["log_callback"] == (
        launcher._core_manager_log
    )
    update_core, update_kwargs = calls["update"]
    assert update_core == "core-manager"
    assert update_kwargs["is_core_running"]("yatori") is True
    assert update_kwargs["is_core_running"]("autovisor") is False
    assert update_kwargs["schedule"] == launcher._after


def test_compose_supports_missing_optional_core_manager_and_question_bank():
    launcher = _Launcher()

    service = LauncherStartupService(
        launcher,
        source_file="launcher.py",
        core_manager_factory=None,
        desktop_factory=lambda *_args, **_kwargs: object(),
        preferences_factory=lambda *_args, **_kwargs: SimpleNamespace(
            values={"autoCleanLogs": False}
        ),
        question_bank_factory=lambda *_args, **_kwargs: SimpleNamespace(
            available=False
        ),
        update_factory=lambda core_manager, **_kwargs: (
            "update",
            core_manager,
        ),
    )

    service.compose()

    assert launcher.cleaned == []
    assert launcher.autovisor_multi_mode is False
    assert launcher.core_manager is None
    assert launcher.update_controller == ("update", None)
    assert any("题库服务器模块未找到" in line for line in launcher.logs)


def test_deferred_tasks_keep_original_order_delays_and_callbacks():
    launcher = _Launcher()
    service = LauncherStartupService(launcher, source_file="launcher.py")

    scheduled = service.schedule_deferred_tasks()

    assert scheduled == {
        "auto_start_question_bank": (600, "auto_start_question_bank"),
        "auto_check_cores": (1000, "auto_check_cores"),
        "_apply_auto_run_preference": (
            1400,
            "_apply_auto_run_preference",
        ),
    }
