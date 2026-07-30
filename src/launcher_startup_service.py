"""Composition and deferred startup tasks for the Web launcher."""

from __future__ import annotations

from src.desktop_platform_service import DesktopPlatformService
from src.preferences_service import PreferencesService
from src.question_bank_controller import QuestionBankController
from src.update_controller import UpdateController


class LauncherStartupService:
    """Build launcher services in order and register delayed startup work."""

    DEFERRED_TASKS = (
        (600, "auto_start_question_bank"),
        (1000, "auto_check_cores"),
        (1400, "_apply_auto_run_preference"),
    )

    def __init__(
        self,
        launcher,
        *,
        source_file,
        sound_module=None,
        core_manager_factory=None,
        desktop_factory=DesktopPlatformService,
        preferences_factory=PreferencesService,
        question_bank_factory=QuestionBankController,
        update_factory=UpdateController,
    ):
        self.launcher = launcher
        self.source_file = source_file
        self.sound_module = sound_module
        self.core_manager_factory = core_manager_factory
        self.desktop_factory = desktop_factory
        self.preferences_factory = preferences_factory
        self.question_bank_factory = question_bank_factory
        self.update_factory = update_factory

    def compose(self) -> str:
        launcher = self.launcher
        base_dir = launcher.get_base_dir()

        launcher._desktop_platform_service = self.desktop_factory(
            base_dir,
            self.source_file,
            log=launcher.log_system,
            get_window=lambda: launcher.web_window,
            sound_module=self.sound_module,
        )
        launcher.preferences_path = launcher.get_preferences_file_path(base_dir)
        launcher._preferences_service = self.preferences_factory(
            launcher.preferences_path,
            log=launcher.log_system,
            apply_side_effects=launcher._handle_preference_side_effects,
            rollback_side_effects=launcher._rollback_preference_side_effects,
        )
        launcher.web_preferences = launcher._preferences_service.values
        if launcher.web_preferences.get("autoCleanLogs"):
            launcher._clean_old_runtime_logs(base_dir)

        launcher.yatori_path = launcher.find_yatori_path(base_dir)
        launcher.autovisor_path = launcher.find_autovisor_path(base_dir)
        launcher._get_autovisor_dependency_manager()

        launcher.question_bank = self.question_bank_factory(
            base_dir,
            log=launcher.log_system,
            on_status_change=launcher._after_qb_status_update,
            sync_external_url=launcher._sync_yatori_question_bank_url,
        )
        if launcher.question_bank.available:
            launcher.log_system("[QB] 题库服务器模块已加载")
        else:
            launcher.log_system(
                "[QB] 题库服务器模块未找到，请确保 题库服务器.py 在同目录下"
            )

        initial_autovisor_config = launcher._load_autovisor_config_data()
        launcher.autovisor_multi_mode = (
            len(initial_autovisor_config.get("accounts", [])) > 1
        )
        if self.core_manager_factory:
            launcher.core_manager = self.core_manager_factory(
                base_dir,
                log_callback=launcher._core_manager_log,
            )

        launcher.update_controller = self.update_factory(
            launcher.core_manager,
            log=launcher.log_system,
            show_info=launcher._show_info,
            show_warning=launcher._show_warning,
            show_error=launcher._show_error,
            is_core_running=lambda core: bool(launcher.running.get(core)),
            on_installed=launcher._handle_core_installed,
            get_autovisor_version=launcher._get_autovisor_display_version,
            schedule=launcher._after,
        )
        return base_dir

    def schedule_deferred_tasks(self) -> dict[str, object]:
        launcher = self.launcher
        scheduled = {}
        for delay_ms, callback_name in self.DEFERRED_TASKS:
            callback = getattr(launcher, callback_name)
            scheduled[callback_name] = launcher._after(delay_ms, callback)
        return scheduled
