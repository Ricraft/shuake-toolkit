# -*- coding: utf-8 -*-
"""
统一启动器 - Yatori & Autovisor 启动管理工具
支持同时启动智慧树(Autovisor)和非智慧树(Yatori)脚本
支持自动下载和更新核心
"""

import subprocess
import threading
import os
import sys
import re
from datetime import datetime

from src.ai_service import AIConnectivityService
from src.application_bootstrap import (
    WebApplicationBootstrap,
    enable_windows_dpi_awareness,
    install_crash_hook,
    resolve_file_dialog_constants,
)
from src.autovisor_dependency_manager import AutovisorDependencyManager
from src.config_service import ConfigService
from src.core_launch_service import CoreLaunchService
from src.core_runtime_locator import CoreRuntimeLocator
from src.course_api_service import CourseAPIService
from src.course_catalog import CourseCatalogService
from src.course_overlay_service import CourseOverlayService
from src.course_plan_service import CoursePlanService
from src.course_run_service import CourseRunService
from src.tianyi_agent_service import TianyiAgentService
from src.desktop_platform_service import DesktopPlatformService
from src.dependencies import ensure_core_dependencies
from src.launcher_api import WebLauncherAPI
from src.launcher_startup_service import LauncherStartupService
from src.launcher_update_service import LauncherUpdateService
from src.process_supervisor import ProcessSupervisor
from src.preferences_service import PreferencesService
from src.practice_mode_service import PracticeModeService
from src.python_runtime import find_python_executable
from src.question_bank_action_service import QuestionBankActionService
from src.runtime_coordinator import RuntimeCoordinator
from src.runtime_process_service import RuntimeProcessService
from src.scheduled_task_service import ScheduledTaskService
from src.web_action_service import (
    AUTOVISOR_UPDATE_DISABLED_MESSAGE,
    WebActionService,
)
from src.web_settings_service import WebSettingsService
from src.web_state_service import WebStateService
from src.web_window_controller import WebWindowController

try:
    import webview
except ImportError:  # pragma: no cover
    webview = None

# FileDialog 兼容常量（main() 中会重新赋值）
FD_OPEN = 10   # OPEN_DIALOG
FD_SAVE = 30   # SAVE_DIALOG

try:
    import winsound
except ImportError:  # pragma: no cover
    winsound = None

# 导入核心管理器
try:
    from src.core_manager import CoreManager
except ImportError:
    CoreManager = None


class UnifiedLauncher:
    WEB_UI_FILE = os.path.join("web", "现代启动器_UI_预览.html")
    WEB_UI_SEARCH_PATHS = (
        WEB_UI_FILE,
    )
    WEB_PREFERENCES_FILE = os.path.join("data", "launcher_preferences.json")
    YATORI_ENTRY_FILES = ("yatori-go-console.exe", "start.bat")
    AUTOVISOR_EXECUTABLE_ENTRY_FILES = ("Autovisor.exe", "AUto.exe", "Auto.exe")
    AUTOVISOR_SCRIPT_ENTRY_FILES = ("Autovisor_Multi.py", "Autovisor.py", "main.py", "run.py")
    AUTOVISOR_ENTRY_FILES = AUTOVISOR_EXECUTABLE_ENTRY_FILES + AUTOVISOR_SCRIPT_ENTRY_FILES
    AUTOVISOR_SPEED_OPTIONS = ('1.0', '1.25', '1.5', '1.8')
    YATORI_DISPLAY_VERSION = "v2.6.2-beta.8"
    AUTOVISOR_DISPLAY_VERSION = "20260424 修复版"
    LAUNCHER_VERSION = "v1.4.1"
    AUTOVISOR_UPDATE_CONTACT_MESSAGE = "请联系开发者进行核心更新。"
    ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    PROGRESS_LINE_RE = re.compile(r"^(?P<desc>[^|%\r\n]+?)\s*\|.*?\|\s*(?P<percent>\d+%)\s*(?P<suffix>.*)$")

    """统一启动器主类"""
    
    @staticmethod
    def get_base_dir():
        """获取程序基础目录（处理 PyInstaller 打包后的路径问题）"""
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后的 exe 运行
            return os.path.dirname(os.path.abspath(sys.executable))
        else:
            # 普通 Python 脚本运行
            return os.path.dirname(os.path.abspath(__file__))

    @classmethod
    def resolve_web_ui_path(cls, base_dir):
        for relative_path in cls.WEB_UI_SEARCH_PATHS:
            candidate = os.path.join(base_dir, relative_path)
            if os.path.exists(candidate):
                return candidate
        return os.path.join(base_dir, cls.WEB_UI_FILE)

    @classmethod
    def get_preferences_file_path(cls, base_dir):
        return os.path.join(base_dir, cls.WEB_PREFERENCES_FILE)

    def _get_core_runtime_locator(self, base_dir=None):
        resolved_base_dir = os.path.abspath(base_dir or self.get_base_dir())
        locator = getattr(self, "_core_runtime_locator", None)
        expected_entries = (
            tuple(self.YATORI_ENTRY_FILES),
            tuple(self.AUTOVISOR_EXECUTABLE_ENTRY_FILES),
            tuple(self.AUTOVISOR_SCRIPT_ENTRY_FILES),
        )
        if (
            locator is None
            or os.path.normcase(locator.base_dir)
            != os.path.normcase(resolved_base_dir)
            or (
                locator.yatori_entry_files,
                locator.autovisor_executable_entry_files,
                locator.autovisor_script_entry_files,
            )
            != expected_entries
        ):
            locator = CoreRuntimeLocator(
                resolved_base_dir,
                yatori_entry_files=self.YATORI_ENTRY_FILES,
                autovisor_executable_entry_files=(
                    self.AUTOVISOR_EXECUTABLE_ENTRY_FILES
                ),
                autovisor_script_entry_files=self.AUTOVISOR_SCRIPT_ENTRY_FILES,
            )
            self._core_runtime_locator = locator
        return locator

    def _directory_has_any_file(self, directory, file_names):
        return self._get_core_runtime_locator().directory_has_any_file(
            directory,
            file_names,
        )

    def _find_runtime_path(self, base_dir, standard_dir, patterns, required_files):
        return self._get_core_runtime_locator(base_dir).find_runtime_path(
            standard_dir,
            patterns,
            required_files,
        )

    def find_yatori_path(self, base_dir):
        return self._get_core_runtime_locator(base_dir).find_yatori_path()

    def find_autovisor_path(self, base_dir):
        return self._get_core_runtime_locator(base_dir).find_autovisor_path()

    def _get_yatori_display_version(self):
        if self.core_manager:
            return (
                self.core_manager.local_versions.get('yatori')
                or getattr(self.core_manager, 'YATORI_FALLBACK_LOCAL_VERSION', self.YATORI_DISPLAY_VERSION)
            )
        return self.YATORI_DISPLAY_VERSION

    def _get_autovisor_display_version(self):
        if self.core_manager:
            version_info = self.core_manager.get_autovisor_local_version_info()
            return version_info.get('display', self.AUTOVISOR_DISPLAY_VERSION)
        return self.AUTOVISOR_DISPLAY_VERSION

    def _get_process_supervisor(self):
        supervisor = getattr(self, '_process_supervisor', None)
        processes = getattr(self, 'processes', {})
        running = getattr(self, 'running', {})
        starting = getattr(self, 'starting', {})
        stop_requested = getattr(self, 'stop_requested', {})
        if (
            supervisor is None
            or supervisor.processes is not processes
            or supervisor.running is not running
            or supervisor.starting is not starting
            or supervisor.stop_requested is not stop_requested
        ):
            supervisor = ProcessSupervisor(
                processes=processes,
                running=running,
                starting=starting,
                stop_requested=stop_requested,
                state_lock=getattr(self, '_runtime_lock', None),
                log_line=getattr(self, 'log', None),
                log_system=getattr(self, 'log_system', None),
            )
            self._process_supervisor = supervisor
        return supervisor

    def _get_runtime_process_service(self):
        service = getattr(self, '_runtime_process_service', None)
        if service is None:
            service = RuntimeProcessService(self)
            self._runtime_process_service = service
        return service

    def _get_runtime_coordinator(self):
        coordinator = getattr(self, '_runtime_coordinator', None)
        if coordinator is None:
            coordinator = RuntimeCoordinator(self)
            self._runtime_coordinator = coordinator
        return coordinator

    def _get_scheduled_task_service(self):
        service = getattr(self, '_scheduled_task_service', None)
        if service is None:
            service = ScheduledTaskService(
                log=getattr(self, 'log_system', lambda _message: None),
                lock=getattr(self, '_scheduled_timer_lock', None),
                pending=getattr(self, '_scheduled_timers', None),
            )
            self._scheduled_task_service = service
            self._scheduled_timer_lock = service.lock
            self._scheduled_timers = service.pending
        return service

    def _get_launcher_startup_service(self):
        service = getattr(self, '_launcher_startup_service', None)
        if service is None:
            service = LauncherStartupService(
                self,
                source_file=__file__,
                sound_module=winsound,
                core_manager_factory=CoreManager,
            )
            self._launcher_startup_service = service
        return service

    def _get_core_launch_service(self):
        service = getattr(self, '_core_launch_service', None)
        if service is None:
            service = CoreLaunchService(self)
            self._core_launch_service = service
        return service

    def _get_practice_mode_service(self):
        service = getattr(self, '_practice_mode_service', None)
        if service is None:
            service = PracticeModeService(self)
            self._practice_mode_service = service
        return service

    def _get_desktop_platform_service(self):
        service = getattr(self, '_desktop_platform_service', None)
        if service is None:
            service = DesktopPlatformService(
                self.get_base_dir(),
                __file__,
                log=self.log_system,
                get_window=lambda: getattr(self, 'web_window', None),
                sound_module=winsound,
            )
            self._desktop_platform_service = service
        return service

    def _get_web_window_controller(self):
        controller = getattr(self, '_web_window_controller', None)
        if controller is None:
            controller = WebWindowController(
                get_window=lambda: getattr(self, 'web_window', None),
                set_window=lambda window: setattr(self, 'web_window', window),
                schedule=lambda delay, callback: self._after(delay, callback),
                log=lambda message: self.log_system(message),
                should_minimize_to_tray=lambda: self._preference_enabled(
                    'minimizeToTray'
                ),
                minimize_window=lambda: self._minimize_main_window(),
                confirmed_exit=lambda: self.on_closing(confirmed=True),
                save_geometry=lambda: self._save_window_geometry_preference(),
            )
            self._web_window_controller = controller
        return controller

    @property
    def _allow_webview_close(self):
        return self._get_web_window_controller().allow_close

    @_allow_webview_close.setter
    def _allow_webview_close(self, value):
        self._get_web_window_controller().allow_close = bool(value)

    @property
    def _exit_confirmation_pending(self):
        return self._get_web_window_controller().confirmation_pending

    @_exit_confirmation_pending.setter
    def _exit_confirmation_pending(self, value):
        self._get_web_window_controller().confirmation_pending = bool(value)

    def _build_encoding_candidates(self, *preferred):
        return self._get_process_supervisor().build_encoding_candidates(*preferred)

    def _decode_output_line(self, raw_line, encodings):
        return self._get_process_supervisor().decode_output_line(raw_line, encodings)

    def _clean_log_text(self, text):
        return self._get_process_supervisor().clean_log_text(text)

    def _normalize_progress_log(self, text):
        return self._get_process_supervisor().normalize_progress_log(text)

    def _is_progress_log(self, text):
        return self._get_process_supervisor().is_progress_log(text)

    def _get_subprocess_window_kwargs(self):
        return self._get_process_supervisor().subprocess_window_kwargs()

    def _stream_process_output(self, process, source, encodings):
        self._get_process_supervisor().stream_process_output(
            process,
            source,
            encodings,
        )

    def _get_yatori_command(self):
        return self._get_core_runtime_locator().get_yatori_command(
            self.yatori_path
        )

    def _get_autovisor_entry_path(self, multi_mode):
        return self._get_core_runtime_locator().get_autovisor_entry_path(
            self.autovisor_path,
            multi_mode,
        )

    def _get_autovisor_dependency_manager(self):
        manager = getattr(self, 'autovisor_dependencies', None)
        if manager is not None:
            return manager
        logger = getattr(self, 'log_system', lambda _message: None)
        manager = AutovisorDependencyManager(
            get_autovisor_path=lambda: getattr(
                self,
                'autovisor_path',
                os.path.join(self.get_base_dir(), 'Autovisor'),
            ),
            log_system=logger,
            log_line=getattr(
                self,
                'log',
                lambda _source, line, **_kwargs: logger(line),
            ),
            is_progress_log=getattr(self, '_is_progress_log', lambda _line: False),
            run_logged_command=getattr(
                self,
                '_run_logged_command',
                lambda *_args, **_kwargs: 1,
            ),
            schedule=getattr(self, '_after', lambda _delay, callback: callback()),
            on_ready=getattr(self, 'start_autovisor', lambda: None),
            show_error=getattr(
                self,
                '_show_error',
                lambda title, message: logger(f"[{title}] {message}"),
            ),
        )
        self.autovisor_dependencies = manager
        return manager

    def _python_module_available(self, python_exe, module_name):
        return self._get_autovisor_dependency_manager().module_available(python_exe, module_name)

    def _check_autovisor_dependencies(self, python_exe):
        return self._get_autovisor_dependency_manager().check_dependencies(python_exe)

    def _get_autovisor_runtime_install_args(self, missing_package_names=None):
        return self._get_autovisor_dependency_manager().runtime_install_args(missing_package_names)

    def _normalize_browser_name(self, name):
        return self._get_autovisor_dependency_manager().normalize_browser_name(name)

    def _get_browser_registry_candidates(self, executable_name):
        return self._get_autovisor_dependency_manager()._browser_registry_candidates(executable_name)

    def _find_browser_executable(self, browser_name):
        return self._get_autovisor_dependency_manager().find_browser_executable(browser_name)

    def _has_playwright_chromium(self):
        return self._get_autovisor_dependency_manager().has_playwright_chromium()

    def _read_autovisor_config(self, config_path):
        from src.config_service import read_ini_config
        return read_ini_config(config_path)

    def _copy_config_section(self, parser, source, target):
        return self._get_autovisor_dependency_manager()._copy_config_section(parser, source, target)

    def _prepare_autovisor_config(self, config_path, multi_mode):
        return self._get_autovisor_dependency_manager().prepare_config(config_path, multi_mode)

    def _run_logged_command(self, cmd, cwd, source='system', env=None):
        return self._get_process_supervisor().run_logged_command(
            cmd,
            cwd,
            source,
            env,
        )

    def _terminate_process_tree(self, process, label):
        return self._get_process_supervisor().terminate_process_tree(
            process,
            label,
        )

    def _install_autovisor_with_mirrors(self, python_exe, install_args):
        return self._get_autovisor_dependency_manager().install_with_mirrors(python_exe, install_args)

    def _install_autovisor_dependencies_async(
        self,
        python_exe,
        missing_packages,
        ensure_playwright_browser=False,
    ):
        return self._get_autovisor_dependency_manager().install_async(
            python_exe,
            missing_packages,
            ensure_playwright_browser=ensure_playwright_browser,
        )

    def _default_yatori_user(self, index=None):
        return ConfigService.default_yatori_user(index)

    def _default_yatori_config(self):
        return ConfigService.default_yatori_config()

    def _default_autovisor_account(self, index=1):
        return self._get_config_service().default_autovisor_account(index)

    def _get_yatori_config_path(self):
        self.yatori_path = self.find_yatori_path(self.get_base_dir())
        return os.path.join(self.yatori_path, 'config.yaml')

    def _get_autovisor_config_path(self):
        self.autovisor_path = self.find_autovisor_path(self.get_base_dir())
        return os.path.join(self.autovisor_path, 'configs.ini')

    def _get_config_service(self):
        service = getattr(self, '_config_service', None)
        if service is None:
            service = ConfigService(
                self._get_yatori_config_path,
                self._get_autovisor_config_path,
                browser_finder=self._find_browser_executable,
                logger=self.log_system,
                speed_options=self.AUTOVISOR_SPEED_OPTIONS,
            )
            self._config_service = service
        return service

    def _as_int(self, value, default=0):
        return ConfigService.as_int(value, default)

    def _as_float(self, value, default=0.0):
        return ConfigService.as_float(value, default)

    def _normalize_autovisor_speed(self, value, default='1.0'):
        return self._get_config_service().normalize_autovisor_speed(value, default)

    def _validate_autovisor_accounts(self, accounts):
        return ConfigService.validate_autovisor_accounts(accounts)

    def _validate_yatori_runtime(self, users):
        return ConfigService.validate_yatori_runtime(users)

    def _validate_autovisor_runtime(self, accounts, multi_mode=False):
        return ConfigService.validate_autovisor_runtime(accounts, multi_mode)

    def _split_lines(self, value):
        return ConfigService.split_lines(value)

    def _split_csv(self, value):
        return ConfigService.split_csv(value)




    def _load_yatori_config_data(self):
        return self._get_config_service().load_yatori()

    def _save_yatori_config_data(self, config_data):
        self._get_config_service().save_yatori(config_data)

    def _extract_autovisor_index(self, section_name, prefix):
        return ConfigService.extract_autovisor_index(section_name, prefix)

    def _sorted_url_keys(self, option_names):
        return ConfigService.sorted_url_keys(option_names)

    def _load_autovisor_config_data(self):
        return self._get_config_service().load_autovisor()

    def _save_autovisor_config_data(self, config_data):
        self._get_config_service().save_autovisor(config_data)

    def _core_manager_log(self, message):
        """核心管理器日志回调"""
        self.log_system(f"[核心] {message}")

    def _handle_core_installed(self, core):
        """Refresh launcher paths after the update controller installs a core."""
        base_dir = self.get_base_dir()
        if core == 'yatori':
            self.yatori_path = self.find_yatori_path(base_dir)
        elif core == 'autovisor':
            self.autovisor_path = self.find_autovisor_path(base_dir)

    def auto_check_cores(self):
        """自动检查核心安装情况和更新"""
        if not self.core_manager:
            return
        
        # 检查 Yatori
        yatori_installed = self.core_manager.check_yatori_installed()
        
        if not yatori_installed:
            self.log_system("⚠️ 未检测到 Yatori 核心，请在 Web 界面中触发安装。")
        else:
            # Yatori 已安装，检查更新
            self.check_yatori_update_async()

    def __init__(self):
        """Initialize the WebView-backed launcher state."""
        self.web_window = None
        self._allow_webview_close = False
        self._exit_confirmation_pending = False

        self.processes = {
            'yatori': None,
            'autovisor': None,
            'practice': None,
        }
        self.log_history = {
            'yatori': [],
            'autovisor': [],
            'system': [],
        }
        self.running = {
            'yatori': False,
            'autovisor': False,
            'practice': False,
        }
        self.starting = {
            'yatori': False,
            'autovisor': False,
            'practice': False,
        }
        self._runtime_lock = threading.RLock()
        self._scheduled_task_service = ScheduledTaskService(log=self.log_system)
        self._scheduled_timer_lock = self._scheduled_task_service.lock
        self._scheduled_timers = self._scheduled_task_service.pending
        self.stop_requested = {
            'yatori': False,
            'autovisor': False,
            'practice': False,
        }
        self._last_start_error = {}
        self._last_start_failure_kind = {}
        self._runtime_event_sequence = 0
        self._last_runtime_event = None
        self.practice_account_id = None

        self.core_manager = None
        self._shutdown_pending = False
        self._launcher_close_pending = False

        startup_service = self._get_launcher_startup_service()
        startup_service.compose()
        self._recover_course_run_overlays()
        self.log_system("统一启动器已就绪")
        startup_service.schedule_deferred_tasks()

    def _after(self, delay_ms, callback):
        return self._get_scheduled_task_service().schedule(delay_ms, callback)

    def _cancel_scheduled_callbacks(self):
        service = getattr(self, '_scheduled_task_service', None)
        if service is None and not hasattr(self, '_scheduled_timers'):
            return 0
        return self._get_scheduled_task_service().cancel_all()

    @property
    def qb_server(self):
        """Compatibility view of the extracted question-bank controller."""
        return self.question_bank.server

    @property
    def qb_running(self):
        return self.question_bank.running

    @property
    def _qb_port(self):
        return self.question_bank.port

    @property
    def update_info(self):
        return self.update_controller.yatori_update_info

    @property
    def autovisor_update_info(self):
        return self.update_controller.autovisor_update_info

    @property
    def is_updating(self):
        return self.update_controller.installing

    @property
    def autovisor_version_checking(self):
        return self.update_controller.autovisor_checking

    @property
    def autovisor_installing(self):
        return self._get_autovisor_dependency_manager().installing

    def _get_autovisor_multi_mode(self):
        return bool(getattr(self, 'autovisor_multi_mode', True))

    def _set_autovisor_multi_mode(self, value):
        self.autovisor_multi_mode = bool(value)

    def attach_web_window(self, window):
        self._get_web_window_controller().attach(
            window,
            closing_handler=self._handle_web_window_closing,
            apply_preferences=self._apply_window_preferences,
        )

    def _show_web_exit_confirmation(self):
        return self._get_web_window_controller().show_exit_confirmation()

    def _request_web_exit_confirmation(self):
        return self._get_web_window_controller().request_exit_confirmation()

    def _handle_web_window_closing(self):
        return self._get_web_window_controller().handle_window_closing(
            request_confirmation=self._request_web_exit_confirmation,
        )

    def _close_main_window(self):
        return self._get_web_window_controller().close_window()

    def _show_error(self, title, message):
        self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")

    def _show_info(self, title, message):
        self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")

    def _show_warning(self, title, message):
        self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")

    def _append_log_history(self, source, log_line, replace_last=False):
        history = self.log_history.setdefault(source, [])
        normalized_line = log_line.rstrip('\n')
        if replace_last and history and self._is_progress_log(history[-1]):
            history[-1] = normalized_line
        else:
            history.append(normalized_line)
        if len(history) > 400:
            del history[:-400]

    def _get_preferences_service(self):
        service = getattr(self, '_preferences_service', None)
        if service is None:
            service = PreferencesService(
                self.preferences_path,
                log=self.log_system,
                apply_side_effects=self._handle_preference_side_effects,
                rollback_side_effects=self._rollback_preference_side_effects,
            )
            self._preferences_service = service
            self.web_preferences = service.values
        return service

    def _get_web_settings_service(self):
        service = getattr(self, '_web_settings_service', None)
        if service is None:
            service = WebSettingsService(self)
            self._web_settings_service = service
        return service

    def _get_web_state_service(self):
        service = getattr(self, '_web_state_service', None)
        if service is None:
            service = WebStateService(self)
            self._web_state_service = service
        return service

    def _load_web_preferences(self):
        return self._get_preferences_service().get()

    def _save_web_preferences(self):
        return self._get_preferences_service().save()

    def _preference_enabled(self, key, default=False):
        return self._get_preferences_service().enabled(key, default)

    def _startup_command(self):
        return self._get_desktop_platform_service().startup_command()

    def _set_windows_auto_start(self, enabled):
        return self._get_desktop_platform_service().set_windows_auto_start(enabled)

    def _clean_old_runtime_logs(self, base_dir=None, days=7):
        return self._get_desktop_platform_service().clean_old_runtime_logs(
            base_dir=base_dir,
            days=days,
        )

    def _apply_window_preferences(self, initial=False):
        always_on_top = self._preference_enabled('alwaysOnTop')
        start_minimized = initial and self._preference_enabled('startMinimized')

        if self.web_window:
            if always_on_top:
                self.log_system("当前 WebView 后端不保证支持窗口置顶，已保存该偏好。")
            if start_minimized:
                self._after(800, self._minimize_main_window)

    def _apply_auto_run_preference(self):
        if self._preference_enabled('autoRun'):
            self.log_system("已启用启动后自动运行，正在启动全部核心。")
            self.start_all()

    def _minimize_main_window(self):
        return self._get_desktop_platform_service().minimize_window()

    def _save_window_geometry_preference(self):
        # pywebview does not expose a portable read API for current geometry.
        return

    def _play_feedback_sound(self, error=False):
        return self._get_desktop_platform_service().play_feedback_sound(
            enabled=self._preference_enabled('soundEnabled', True),
            error=error,
        )

    def _notify_runtime_event(self, title, message, error=False):
        return self._get_runtime_coordinator().notify_runtime_event(
            title,
            message,
            error=error,
        )

    def _record_runtime_failure(self, script_type, message):
        return self._get_runtime_coordinator().record_runtime_failure(
            script_type,
            message,
        )

    def _handle_runtime_exit(self, script_type, return_code, stop_requested):
        return self._get_runtime_coordinator().handle_runtime_exit(
            script_type,
            return_code,
            stop_requested,
        )

    def _handle_runtime_failure(
        self,
        script_type,
        title,
        message,
        *,
        notification_message=None,
    ):
        return self._get_runtime_coordinator().handle_runtime_failure(
            script_type,
            title,
            message,
            notification_message=notification_message,
            allow_shutdown=script_type != 'practice',
        )

    def _maybe_shutdown_after_completion(self):
        if not self._preference_enabled('autoShutdown'):
            return
        result = self._get_desktop_platform_service().schedule_shutdown(
            delay_seconds=60,
        )
        self._shutdown_pending = bool(
            self._desktop_platform_service.shutdown_pending
        )
        if result.get('ok'):
            self.log_system("已启用刷完自动关机，将在 60 秒后关闭计算机。")
        else:
            self.log_system(result.get('message') or "自动关机指令执行失败")

    def _maybe_close_launcher_after_completion(self):
        """按偏好设置在刷课完成后关闭启动器本身。"""
        if not self._preference_enabled('closeLauncherOnComplete'):
            return False
        if getattr(self, '_shutdown_pending', False):
            self.log_system("已启用刷完自动关机，跳过自动关闭启动器。")
            return False
        if getattr(self, '_launcher_close_pending', False):
            return False
        self._launcher_close_pending = True
        self.log_system("已启用刷完自动关闭启动器，程序即将退出。")
        self._spawn_launcher_close()
        return True

    def _spawn_launcher_close(self):
        """在独立线程里执行已确认退出，避免阻塞完成回调。"""
        def do_close():
            try:
                self.on_closing(confirmed=True)
            except Exception as exc:
                self.log_system(f"自动关闭启动器失败: {repr(exc)[:160]}")
            finally:
                self._launcher_close_pending = False

        threading.Thread(target=do_close, daemon=True).start()
    def _cancel_shutdown(self):
        """取消正在进行的自动关机"""
        result = self._get_desktop_platform_service().cancel_shutdown()
        self._shutdown_pending = bool(
            self._desktop_platform_service.shutdown_pending
        )
        if result.get('ok'):
            self.log_system(result.get('message') or "已取消自动关机")
        else:
            self.log_system(result.get('message') or "取消自动关机失败")
        return result

    def _handle_preference_side_effects(self, payload):
        failures = []
        if 'autoStart' in payload:
            try:
                if not self._set_windows_auto_start(bool(payload['autoStart'])):
                    failures.append('开机自动启动未能应用')
            except Exception as exc:
                self.log_system(f"更新开机自动启动失败: {exc}")
                failures.append(f'开机自动启动应用失败: {exc}')
        if 'alwaysOnTop' in payload:
            self._apply_window_preferences()
        if payload.get('autoCleanLogs'):
            self._clean_old_runtime_logs()
        return failures

    def _rollback_preference_side_effects(self, previous, payload):
        failures = []
        if 'autoStart' in payload:
            try:
                if not self._set_windows_auto_start(
                    bool(previous.get('autoStart'))
                ):
                    failures.append('开机启动项恢复失败')
            except Exception as exc:
                failures.append(f'开机启动项恢复失败: {exc}')
        return failures

    def get_web_preferences(self):
        return self._get_preferences_service().get()

    def save_web_preference(self, payload):
        result = self._get_preferences_service().update(payload)
        self.web_preferences = self._preferences_service.values
        return result

    def get_web_runtime_state(self):
        return self._get_web_state_service().runtime_state()

    def get_web_initial_state(self):
        return self._get_web_state_service().initial_state()

    def save_settings_from_web(self, payload):
        return self._get_web_settings_service().save(payload)

    def tianyi_agent_chat_from_web(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        messages = payload.get('messages')
        if not isinstance(messages, list) or not messages:
            return {'ok': False, 'message': '聊天消息格式错误'}
        return self._get_tianyi_agent_service().chat(
            payload.get('config'),
            messages,
        )

    def confirm_tianyi_action_from_web(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        action_id = str(payload.get('id') or '').strip()
        if not action_id:
            return {'ok': False, 'message': '缺少待确认操作编号'}
        return self._get_tianyi_agent_service().confirm(action_id)

    def cancel_tianyi_action_from_web(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        return self._get_tianyi_agent_service().cancel(
            str(payload.get('id') or '')
        )

    def get_course_plans_from_web(self):
        return {'ok': True, 'courses': self._get_course_plan_service().list()}

    def save_course_plans_from_web(self, payload):
        return self._get_course_plan_service().save(payload)

    def resolve_course_from_web(self, query, core=None, account_index=0):
        return self._get_course_run_service().resolve(
            query,
            core=core or None,
            account_index=account_index,
        )

    def start_course_from_web(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        query = str(payload.get('query') or payload.get('course') or '').strip()
        if not query:
            return {
                'ok': False,
                'code': 'empty_query',
                'message': '没有识别到课程名',
            }
        return self._get_course_run_service().start(
            query,
            core=payload.get('core') or None,
            account_index=payload.get('accountIndex'),
            skip_questions=payload.get('skipQuestions'),
            max_minutes=payload.get('maxMinutes'),
        )

    def detect_browser_path_for_web(self, browser_name='Chrome'):
        normalized = self._normalize_browser_name(browser_name or 'Chrome')
        detected_path = self._find_browser_executable(normalized)
        if detected_path:
            self.log_system(f"已识别 {browser_name} 路径: {detected_path}")
            return {'ok': True, 'path': detected_path}
        self._show_warning("未找到浏览器", f"没有在系统中找到 {browser_name} 的可执行文件。")
        return {'ok': False, 'path': ''}

    def browse_browser_path_for_web(self):
        file_path = ''
        if self.web_window and webview:
            selection = self.web_window.create_file_dialog(
                FD_OPEN,
                file_types=('Executable Files (*.exe)', 'All Files (*.*)'),
            )
            if selection:
                file_path = selection[0]
        return {'ok': bool(file_path), 'path': file_path or ''}

    def _get_course_plan_service(self):
        service = getattr(self, '_course_plan_service', None)
        if service is None:
            service = CoursePlanService(
                os.path.join(self.get_base_dir(), 'data', 'course_plans.json'),
                logger=self.log_system,
            )
            self._course_plan_service = service
        return service

    def _get_course_overlay_service(self):
        service = getattr(self, '_course_overlay_service', None)
        if service is None:
            service = CourseOverlayService(
                os.path.join(
                    self.get_base_dir(),
                    'data',
                    'course_run_overlay.json',
                ),
                logger=self.log_system,
            )
            self._course_overlay_service = service
        return service

    def _get_course_run_service(self):
        service = getattr(self, '_course_run_service', None)
        if service is None:
            service = CourseRunService(
                self,
                plans=self._get_course_plan_service(),
                overlays=self._get_course_overlay_service(),
                # 只有用户明确要“拉取课程”时才会调到这两个提供器，因此强制刷新。
                autovisor_catalog=lambda index: self.get_autovisor_courses_from_web(
                    index,
                    force_refresh=True,
                ),
                yatori_catalog=lambda index: self.get_xuexitong_courses_from_web(
                    index,
                    force_refresh=True,
                ),
            )
            self._course_run_service = service
        return service

    def _recover_course_run_overlays(self):
        """启动时恢复上次崩溃残留的单课程配置。"""
        try:
            released = self._get_course_overlay_service().release_all()
        except Exception as exc:
            self.log_system(f"恢复单课程配置失败: {exc}")
            return []
        if released:
            self.log_system(
                "已恢复上次未清理的单课程配置: "
                + ", ".join(released)
            )
        return released

    def _release_course_run_overlay(self, core):
        service = getattr(self, '_course_run_service', None)
        if service is None:
            return False
        return service.release_overlay(core)

    def _apply_course_limit_overlay(self, minutes):
        return self._get_course_overlay_service().apply_ini(
            core='autovisor',
            config_path=self._get_autovisor_config_path(),
            section='course-option',
            option='limitMaxTime',
            value=str(minutes),
        )

    def start_autovisor_course(self, course_url, account_id, max_minutes=None):
        return self._get_core_launch_service().start_autovisor_course(
            course_url=course_url,
            account_id=account_id,
            max_minutes=max_minutes,
        )

    def _get_tianyi_agent_service(self):
        service = getattr(self, '_tianyi_agent_service', None)
        if service is None:
            service = TianyiAgentService(self, logger=self.log_system)
            self._tianyi_agent_service = service
        return service

    def _get_ai_service(self):
        service = getattr(self, '_ai_service', None)
        if service is None:
            service = AIConnectivityService(log=self.log_system)
            self._ai_service = service
        return service

    def test_ai_connectivity_from_web(self, config):
        return self._get_ai_service().test_connectivity(config)

    def fetch_model_list_from_web(self, config):
        return self._get_ai_service().fetch_model_list(config)

    def chat_with_ai_from_web(self, payload):
        config = payload.get('config') if isinstance(payload, dict) else None
        messages = payload.get('messages') if isinstance(payload, dict) else None
        return self._get_ai_service().chat(config, messages)

    def toggle_web_runtime(self, script_type):
        if not self.toggle_script(script_type):
            return {'ok': False, 'message': f'无法切换核心: {script_type}', 'state': self.get_web_initial_state()}
        return {'ok': True, 'state': self.get_web_initial_state()}

    def _get_web_action_service(self):
        service = getattr(self, '_web_action_service', None)
        if service is None:
            service = WebActionService(self)
            self._web_action_service = service
        return service

    def perform_web_action(self, action, script_type=None):
        return self._get_web_action_service().perform(action, script_type)

    # ========== WebView-only runtime methods ==========


















    def log(self, source, message, tag='', replace_last=False):
        """添加日志到指定源"""
        message = self._normalize_progress_log(str(message))
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {message}\n"
        self._append_log_history(source, log_line, replace_last=replace_last)

    def log_system(self, message, replace_last=False):
        """记录系统日志"""
        self.log('system', message, 'system', replace_last=replace_last)




    def start_script(self, script_type):
        """启动指定脚本"""
        if not hasattr(self, '_last_start_error'):
            self._last_start_error = {}
        if not hasattr(self, '_last_start_failure_kind'):
            self._last_start_failure_kind = {}
        self._last_start_error.pop(script_type, None)
        self._last_start_failure_kind.pop(script_type, None)
        if script_type == 'yatori':
            return bool(self.start_yatori())
        elif script_type == 'autovisor':
            return bool(self.start_autovisor())
        message = f"未知核心类型: {script_type}"
        self._last_start_error[script_type] = message
        self.log_system(message)
        return False

    def _reject_runtime_start(self, script_type, message, failure_kind=None):
        """Record an immediate launch rejection for Web action feedback."""
        if not hasattr(self, '_last_start_error'):
            self._last_start_error = {}
        if not hasattr(self, '_last_start_failure_kind'):
            self._last_start_failure_kind = {}
        self._last_start_error[script_type] = message
        if failure_kind:
            self._last_start_failure_kind[script_type] = failure_kind
        else:
            self._last_start_failure_kind.pop(script_type, None)
        return False

    def _claim_runtime_start(self, script_type):
        return self._get_runtime_coordinator().claim_runtime_start(script_type)

    def _mark_runtime_running(self, script_type, process):
        self._get_process_supervisor().mark_running(script_type, process)

    def _mark_runtime_stopped(self, script_type, process=None):
        stopped = self._get_process_supervisor().mark_stopped(
            script_type,
            process,
        )
        if stopped and script_type == 'practice':
            self.practice_account_id = None
        return stopped

    def start_yatori(self):
        return self._get_core_launch_service().start_yatori()

    def get_python_executable(self):
        return find_python_executable()

    def _prepare_autovisor_question_bank(self):
        return self._get_core_launch_service().prepare_autovisor_question_bank()

    def _build_autovisor_runtime_env(self):
        return self._get_core_launch_service().build_autovisor_runtime_env()

    def start_autovisor(self):
        return self._get_core_launch_service().start_autovisor()

    def stop_script(self, script_type):
        """停止指定脚本"""
        if script_type == 'yatori':
            self.stop_yatori()
            return True
        elif script_type == 'autovisor':
            self.stop_autovisor()
            return True
        self.log_system(f"未知核心类型: {script_type}")
        return False

    def toggle_script(self, script_type):
        """Use a single button to start or stop a runtime."""
        if script_type not in ('yatori', 'autovisor'):
            self.log_system(f"未知核心类型: {script_type}")
            return False
        if self.starting.get(script_type):
            self.log_system(f"{script_type} 正在启动，请稍候")
            return False
        if self.running.get(script_type):
            return self.stop_script(script_type)
        return self.start_script(script_type)

    def stop_yatori(self):
        """停止 Yatori"""
        if self.starting.get('yatori') and not self.processes.get('yatori'):
            self.stop_requested['yatori'] = True
            self.log_system("正在取消 Yatori 启动...")
            return
        if self.processes['yatori'] and self.running['yatori']:
            self.log_system("正在停止 Yatori...")
            self.stop_requested['yatori'] = True
            self._terminate_process_tree(self.processes['yatori'], "Yatori")
            self._mark_runtime_stopped('yatori')
            self.log_system("Yatori 已关闭刷课。")

    def stop_autovisor(self):
        """停止 Autovisor"""
        if self.starting.get('autovisor') and not self.processes.get('autovisor'):
            self.stop_requested['autovisor'] = True
            self.log_system("正在取消 Autovisor 启动...")
            return
        if self.processes['autovisor'] and self.running['autovisor']:
            self.log_system("正在停止 Autovisor...")
            self.stop_requested['autovisor'] = True
            self._terminate_process_tree(self.processes['autovisor'], "Autovisor")
            self._mark_runtime_stopped('autovisor')
            self.log_system("Autovisor 已关闭刷课。")

    def stop_practice_mode(self):
        return self._get_practice_mode_service().stop()

    def stop_practice_mode_from_web(self):
        return self._get_practice_mode_service().stop_from_web()


    # ============================================================
    # 题库服务器管理
    # ============================================================
    def _load_qb_settings(self):
        return self.question_bank.load_settings()

    def _apply_qb_ai_config(self):
        return self.question_bank.apply_ai_config()

    def _save_qb_settings(self):
        return self.question_bank.save_settings()

    def get_qb_settings_from_web(self):
        return self.question_bank.get_settings()

    def save_qb_settings_from_web(self, payload):
        return self.question_bank.update_settings(payload)

    def _get_question_bank_action_service(self):
        service = getattr(self, "_question_bank_action_service", None)
        if service is None:
            service = QuestionBankActionService(
                get_controller=lambda: getattr(self, "question_bank", None),
                get_window=lambda: getattr(self, "web_window", None),
                dialog_available=lambda: webview is not None,
                open_dialog_type=FD_OPEN,
                save_dialog_type=FD_SAVE,
                show_info=self._show_info,
                show_error=self._show_error,
            )
            self._question_bank_action_service = service
        return service

    def _get_course_catalog_service(self):
        service = getattr(self, '_course_catalog_service', None)
        base_dir = self.get_base_dir()
        if (
            service is None
            or os.path.abspath(str(service.base_dir)) != os.path.abspath(base_dir)
        ):
            service = CourseCatalogService(base_dir, logger=self.log_system)
            self._course_catalog_service = service
        return service

    def _get_course_api_service(self):
        service = getattr(self, '_course_api_service', None)
        if service is None:
            service = CourseAPIService(self)
            self._course_api_service = service
        return service

    def get_autovisor_courses_from_web(self, account_index=0, *, force_refresh=False):
        return self._get_course_api_service().get_autovisor_courses(
            account_index,
            force_refresh=force_refresh,
        )

    def get_xuexitong_courses_from_web(self, account_index=0, *, force_refresh=False):
        return self._get_course_api_service().get_xuexitong_courses(
            account_index,
            force_refresh=force_refresh,
        )

    def start_practice_mode_from_web(self, account_index=0):
        return self._get_practice_mode_service().start(account_index)

    def auto_start_question_bank(self):
        return self.question_bank.auto_start_if_enabled()

    def toggle_question_bank(self):
        return self.question_bank.toggle()

    def start_question_bank(self, silent=False):
        return self.question_bank.start(silent=silent)

    def stop_question_bank(self):
        return self.question_bank.stop()

    def _import_question_bank(self):
        return self._get_question_bank_action_service().import_questions()

    def _export_question_bank(self):
        return self._get_question_bank_action_service().export_questions()

    def _clear_question_bank(self):
        return self._get_question_bank_action_service().clear_questions()

    def _deduplicate_question_bank(self):
        return self._get_question_bank_action_service().deduplicate_questions()

    def _deduplicate_db_direct(self):
        return self.question_bank._deduplicate_database()

    def _detect_zerror_db_path(self):
        return self.question_bank.detect_zerror_db_path()

    def _is_zerror_db_available(self):
        return bool(self.question_bank.detect_zerror_db_path())

    def _get_zerror_db_info(self):
        return self.question_bank.get_zerror_db_info()

    def _configure_zerror_db_env(self):
        return self.question_bank.configure_zerror_environment()

    def _after_qb_status_update(self):
        """回调：题库服务器状态已变更 — 保留为Web UI同步挂钩点"""
        # 此方法被多处调用（启动/停止/失败），当前Web模式通过JS桥实时拉取状态
        # 若将来需要推送状态更新到Web前端，在此处实现
        pass

    def _sync_yatori_question_bank_url(self):
        """自动将 Yatori 的外挂题库 URL 指向本地题库服务器"""
        if not self.question_bank.running:
            return
        try:
            config = self._load_yatori_config_data()
            setting = config.get('setting', {})
            aqs = setting.get('apiQueSetting', {})
            target_url = f"http://127.0.0.1:{self.question_bank.port}/query"
            if aqs.get('url') != target_url:
                aqs['url'] = target_url
                setting['apiQueSetting'] = aqs
                config['setting'] = setting
                self._save_yatori_config_data(config)
                self.log_system(f"Yatori 外挂题库已自动指向: {target_url}")
        except Exception as e:
            self.log_system(f"同步Yatori题库URL失败: {e}")

    def get_question_bank_url(self):
        """获取题库服务器 URL（供 Autovisor 使用）"""
        return self.question_bank.get_query_url()

    def start_all(self):
        return self._get_runtime_coordinator().start_all()

    def stop_all(self):
        """停止所有脚本"""
        self.log_system("正在停止所有运行中的脚本...")
        course_service = getattr(self, '_course_api_service', None)
        if course_service is not None:
            course_service.stop_active_fetch()
        self.stop_yatori()
        self.stop_autovisor()
        self.stop_practice_mode()
        return {'ok': True}

    def clear_all_logs(self):
        """清空所有日志"""
        for key in self.log_history:
            self.log_history[key] = []
        self.log_system("日志面板已清空")

    def export_logs_to_file(self, tab, text):
        result = self._get_desktop_platform_service().export_logs(tab, text)
        if result.get('ok') and result.get('revealed'):
            self.log_system(result.get('message') or "日志已导出")
        elif not result.get('ok'):
            self.log_system(result.get('message') or "导出日志失败")
        return result

    def open_config_dir(self, script_type):
        """打开配置目录"""
        if script_type == 'yatori':
            path = self.yatori_path
        elif script_type == 'autovisor':
            path = self.autovisor_path
        else:
            return {'ok': False, 'message': f'未知核心类型: {script_type}'}

        result = self._get_desktop_platform_service().open_path(path)
        if not result.get('ok'):
            message = result.get('message') or f"目录不存在: {path}"
            self._show_error("错误", message)
            return {'ok': False, 'message': message}
        return result

    def open_config_generator(self):
        """打开配置生成器"""
        candidate_names = [
            "配置文件生成器.html",
            "统一配置生成器.html",
        ]
        candidates = [
            os.path.join(self.get_base_dir(), "web", file_name)
            for file_name in candidate_names
        ]
        result = self._get_desktop_platform_service().open_first_existing(
            candidates,
            missing_message="未找到配置生成器页面",
        )
        if not result.get('ok'):
            self._show_error(
                "错误",
                result.get('message') or "未找到配置生成器页面",
            )
        return result

    def open_external_url(self, url):
        """在系统默认浏览器中打开白名单内的外部链接。"""
        result = self._get_desktop_platform_service().open_external_url(url)
        if not result.get('ok'):
            self._show_error("错误", result.get('message') or "无法打开外部链接")
        return result

    # ==================== 核心管理功能 ====================

    def check_yatori_update_async(self):
        return self.update_controller.check_yatori_async(explicit=False)

    def show_update_available_notification(self, version):
        self.log_system(f"检测到 Yatori 新版本: {version}")

    def install_yatori_update_async(self, confirmation_token=None):
        return self.update_controller.install_yatori_confirmed_async(
            confirmation_token
        )

    def check_autovisor_update_async(self):
        return self.update_controller.check_autovisor_async()

    def show_autovisor_update_dialog(self):
        """关于页「检查 Autovisor 更新」：只读展示版本信息，不提供安装入口。"""
        return self.update_controller.show_autovisor_update_dialog()

    def handle_autovisor_version_result(self, result):
        return self.update_controller.handle_autovisor_result(result)

    def install_autovisor_update_async(self, release_info=None):
        self.log_system(AUTOVISOR_UPDATE_DISABLED_MESSAGE)
        self._show_warning(
            "Autovisor 本地适配保护",
            AUTOVISOR_UPDATE_DISABLED_MESSAGE,
        )
        return False

    def show_update_dialog(self):
        """Check Yatori updates and return Web confirmation dialog data."""
        return self.update_controller.prepare_yatori_update_confirmation()

    def handle_manual_check_result(self, result):
        return self.update_controller.handle_explicit_yatori_result(result)

    # ==================== 统一启动器自身更新 ====================

    def _handle_launcher_update_result(self, result):
        message = str((result or {}).get("message") or "").strip()
        if (result or {}).get("ok"):
            self._show_info(
                "统一启动器更新",
                message or "统一启动器更新已就绪",
            )
        else:
            self._show_error(
                "统一启动器更新",
                message or "统一启动器更新失败",
            )

    def _get_launcher_update_service(self):
        service = getattr(self, "_launcher_update_service", None)
        if service is None:
            service = LauncherUpdateService(
                self.get_base_dir(),
                current_version=self.LAUNCHER_VERSION,
                log=self.log_system,
                on_result=self._handle_launcher_update_result,
            )
            self._launcher_update_service = service
        return service

    def show_launcher_update_dialog(self):
        """检查统一启动器自身更新，返回 Web 确认弹窗数据。"""
        return self._get_launcher_update_service().prepare_update_confirmation()

    def install_launcher_update_async(self, confirmation_token=None):
        return self._get_launcher_update_service().install_confirmed_async(
            confirmation_token,
            schedule=self._after,
        )

    def on_closing(self, confirmed=False):
        """关闭窗口时清理"""
        if self._preference_enabled('minimizeToTray') and not confirmed:
            self.log_system("已按偏好设置最小化窗口，核心任务继续运行。")
            self._minimize_main_window()
            return

        if self.web_window and not confirmed:
            if self._request_web_exit_confirmation():
                return

        core_task_running = any(
            self.running.get(name) for name in ('yatori', 'autovisor', 'practice')
        )
        if core_task_running:
            if not confirmed:
                self.log_system("有核心任务正在运行，取消未确认的退出请求。")
                return
        self._cancel_scheduled_callbacks()
        if core_task_running:
            self.stop_all()
        else:
            course_service = getattr(self, '_course_api_service', None)
            if course_service is not None:
                course_service.stop_active_fetch()
        self.stop_question_bank()
        self._close_main_window()


def main():
    """主程序入口"""
    install_crash_hook(os.path.dirname(os.path.abspath(__file__)))

    # 启动时自动检查并安装缺失依赖
    dependency_failures = ensure_core_dependencies()
    if dependency_failures:
        raise RuntimeError(
            "项目依赖未就绪: "
            + ", ".join(dependency_failures)
            + "。请运行 `py -m pip install -r requirements.txt` 后重试。"
        )
    # 重新导入 webview（刚装的包需要重新加载）
    global webview
    if webview is None:
        try:
            import webview as _wv
            webview = _wv
        except ImportError:
            pass

    if webview is None:
        raise RuntimeError("Web 界面依赖 pywebview 不可用，请重新安装项目依赖。")

    # FileDialog 兼容常量（不同 pywebview 版本 API 不一致）
    global FD_OPEN, FD_SAVE
    FD_OPEN, FD_SAVE = resolve_file_dialog_constants(webview)
    enable_windows_dpi_awareness()

    bootstrap = WebApplicationBootstrap(
        launcher_factory=UnifiedLauncher,
        api_factory=WebLauncherAPI,
        webview_module=webview,
    )
    dev_mode = '--dev' in sys.argv
    return bootstrap.run(debug=dev_mode)


if __name__ == "__main__":
    main()
