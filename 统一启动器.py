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
import locale
import glob
import re
from datetime import datetime

from src.atomic_io import (
    atomic_write_text,
    capture_file_state,
    restore_file_state,
)
from src.ai_service import AIConnectivityService
from src.autovisor_dependency_manager import AutovisorDependencyManager
from src.config_service import ConfigService
from src.course_api_service import CourseAPIService
from src.course_catalog import CourseCatalogService
from src.dependencies import ensure_core_dependencies
from src.launcher_api import WebLauncherAPI
from src.process_supervisor import ProcessSupervisor
from src.preferences_service import PreferencesService
from src.practice_mode_service import PracticeModeService
from src.python_runtime import find_python_executable
from src.question_bank_controller import QuestionBankController
from src.runtime_activity import summarize_autovisor_activity
from src.runtime_process_service import RuntimeProcessService
from src.update_controller import UpdateController
from src.web_action_service import WebActionService

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
    LAUNCHER_VERSION = "v1.1.0"
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

    def _directory_has_any_file(self, directory, file_names):
        return os.path.isdir(directory) and any(
            os.path.exists(os.path.join(directory, name)) for name in file_names
        )

    def _find_runtime_path(self, base_dir, standard_dir, patterns, required_files):
        candidates = [os.path.join(base_dir, standard_dir)]
        for pattern in patterns:
            candidates.extend(sorted(glob.glob(pattern)))

        seen = set()
        for candidate in candidates:
            normalized = os.path.normpath(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            if self._directory_has_any_file(normalized, required_files):
                return normalized

        return os.path.join(base_dir, standard_dir)

    def find_yatori_path(self, base_dir):
        return self._find_runtime_path(
            base_dir,
            "Yatori",
            [
                os.path.join(base_dir, "yatori-go-console*", "yatori-go-console*", "command"),
                os.path.join(base_dir, "yatori-go-console*", "command"),
                os.path.join(base_dir, "yatori-go-console*"),
            ],
            self.YATORI_ENTRY_FILES,
        )

    def find_autovisor_path(self, base_dir):
        candidates = [
            os.path.join(base_dir, "Autovisor"),
            os.path.join(base_dir, "AUto"),
            os.path.join(base_dir, "Auto"),
        ]
        for prefix in ("Autovisor", "AUto", "Auto"):
            candidates.extend(
                [
                    os.path.join(base_dir, f"{prefix}*", f"{prefix}*"),
                    os.path.join(base_dir, f"{prefix}*"),
                ]
            )

        seen = set()
        for pattern in candidates:
            if "*" in pattern:
                matched = sorted(glob.glob(pattern))
            else:
                matched = [pattern]
            for candidate in matched:
                normalized = os.path.normpath(candidate)
                if normalized in seen:
                    continue
                seen.add(normalized)
                if self._directory_has_any_file(normalized, self.AUTOVISOR_ENTRY_FILES):
                    return normalized

        return os.path.join(base_dir, "Autovisor")

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

    def _get_practice_mode_service(self):
        service = getattr(self, '_practice_mode_service', None)
        if service is None:
            service = PracticeModeService(self)
            self._practice_mode_service = service
        return service

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
        exe_path = os.path.join(self.yatori_path, 'yatori-go-console.exe')
        if os.path.exists(exe_path):
            return [exe_path], exe_path

        bat_path = os.path.join(self.yatori_path, 'start.bat')
        if os.path.exists(bat_path):
            return ['cmd', '/c', bat_path], bat_path

        return None, None

    def _get_autovisor_entry_path(self, multi_mode):
        # EXE 优先（含全部依赖，开箱即用）
        for entry_name in self.AUTOVISOR_EXECUTABLE_ENTRY_FILES:
            entry_path = os.path.join(self.autovisor_path, entry_name)
            if os.path.exists(entry_path):
                return entry_path, entry_name, entry_name.lower().endswith('.exe'), True
        # 回退到脚本模式
        ordered_entries = list(self.AUTOVISOR_SCRIPT_ENTRY_FILES)
        preferred = 'Autovisor_Multi.py' if multi_mode else 'Autovisor.py'
        if preferred in ordered_entries:
            ordered_entries.remove(preferred)
        ordered_entries.insert(0, preferred)

        for entry_name in ordered_entries:
            entry_path = os.path.join(self.autovisor_path, entry_name)
            if os.path.exists(entry_path):
                return entry_path, entry_name, preferred == entry_name, entry_path.lower().endswith('.exe')

        return None, preferred, False, False

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
        self.stop_requested = {
            'yatori': False,
            'autovisor': False,
            'practice': False,
        }
        self._last_start_error = {}
        self.practice_account_id = None

        base_dir = self.get_base_dir()
        self.preferences_path = self.get_preferences_file_path(base_dir)
        self._preferences_service = PreferencesService(
            self.preferences_path,
            log=self.log_system,
            apply_side_effects=self._handle_preference_side_effects,
            rollback_side_effects=self._rollback_preference_side_effects,
        )
        self.web_preferences = self._preferences_service.values
        if self.web_preferences.get('autoCleanLogs'):
            self._clean_old_runtime_logs(base_dir)
        self.yatori_path = self.find_yatori_path(base_dir)
        self.autovisor_path = self.find_autovisor_path(base_dir)

        self.core_manager = None
        self._shutdown_pending = False

        self._get_autovisor_dependency_manager()

        self.question_bank = QuestionBankController(
            base_dir,
            log=self.log_system,
            on_status_change=self._after_qb_status_update,
            sync_external_url=self._sync_yatori_question_bank_url,
        )
        if self.question_bank.available:
            self.log_system("[QB] 题库服务器模块已加载")
        else:
            self.log_system("[QB] 题库服务器模块未找到，请确保 题库服务器.py 在同目录下")

        initial_autovisor_config = self._load_autovisor_config_data()
        self.autovisor_multi_mode = len(initial_autovisor_config.get('accounts', [])) > 1
        if CoreManager:
            self.core_manager = CoreManager(base_dir, log_callback=self._core_manager_log)
        self.update_controller = UpdateController(
            self.core_manager,
            log=self.log_system,
            show_info=self._show_info,
            show_warning=self._show_warning,
            show_error=self._show_error,
            is_core_running=lambda core: bool(self.running.get(core)),
            on_installed=self._handle_core_installed,
            get_autovisor_version=self._get_autovisor_display_version,
            schedule=self._after,
        )

        self.log_system("统一启动器已就绪")
        self._after(600, self.auto_start_question_bank)
        self._after(1000, self.auto_check_cores)
        self._after(1400, self._apply_auto_run_preference)

    def _after(self, delay_ms, callback):
        delay_seconds = max(delay_ms, 0) / 1000.0
        if delay_seconds <= 0:
            callback()
            return

        timer = threading.Timer(delay_seconds, callback)
        timer.daemon = True
        timer.start()

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
        self.web_window = window

        if self.web_window:
            self.web_window.events.closing += self._handle_web_window_closing
            self._apply_window_preferences(initial=True)

    def _request_web_exit_confirmation(self):
        """请求Web端显示退出确认对话框，使用非阻塞方式"""
        if not self.web_window:
            return False

        try:
            # 使用定时器延迟执行JS，避免在closing事件中阻塞
            import threading
            def show_modal():
                try:
                    if self.web_window:
                        self.web_window.evaluate_js(
                            """
                            (function () {
                                if (typeof showExitModal === 'function') {
                                    showExitModal();
                                    return true;
                                }
                                return false;
                            })()
                            """
                        )
                except Exception:
                    pass
            threading.Timer(0.1, show_modal).start()
            return True
        except Exception:
            return False

    def _handle_web_window_closing(self):
        if self._allow_webview_close:
            return

        if self._preference_enabled('minimizeToTray'):
            self.log_system("已按偏好设置最小化窗口，核心任务继续运行。")
            self._minimize_main_window()
            return False

        if self._request_web_exit_confirmation():
            return False

    def _close_main_window(self):
        """安全关闭主窗口"""
        self._save_window_geometry_preference()
        if self.web_window:
            self._allow_webview_close = True
            self.web_window.destroy()

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

    def _load_web_preferences(self):
        return self._get_preferences_service().get()

    def _save_web_preferences(self):
        return self._get_preferences_service().save()

    def _preference_enabled(self, key, default=False):
        return self._get_preferences_service().enabled(key, default)

    def _startup_command(self):
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}"'
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    def _set_windows_auto_start(self, enabled):
        if os.name != 'nt':
            self.log_system("当前系统不支持自动创建开机启动项。")
            return False

        startup_dir = os.path.join(
            os.environ.get('APPDATA', ''),
            r'Microsoft\Windows\Start Menu\Programs\Startup',
        )
        if not startup_dir.strip("\\") or not os.path.isdir(startup_dir):
            self.log_system("未找到 Windows 启动目录，开机自动启动未生效。")
            return False

        shortcut_path = os.path.join(startup_dir, "统一刷课启动器.bat")
        if enabled:
            work_dir = os.path.dirname(os.path.abspath(__file__))
            shortcut = "".join(
                (
                    "@echo off\n",
                    f"cd /d \"{work_dir}\"\n",
                    f"start \"\" {self._startup_command()}\n",
                )
            )
            atomic_write_text(shortcut_path, shortcut)
            self.log_system("已启用开机自动启动")
        else:
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)
            self.log_system("已关闭开机自动启动")
        return True

    def _clean_old_runtime_logs(self, base_dir=None, days=7):
        base_dir = base_dir or self.get_base_dir()
        cutoff = datetime.now().timestamp() - days * 24 * 60 * 60
        log_dirs = (
            os.path.join(base_dir, "Autovisor", "logs"),
            os.path.join(base_dir, "Yatori", "assets", "log"),
        )
        removed = 0
        for log_dir in log_dirs:
            if not os.path.isdir(log_dir):
                continue
            for entry in os.listdir(log_dir):
                path = os.path.join(log_dir, entry)
                if not os.path.isfile(path):
                    continue
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except Exception as exc:
                    self.log_system(f"清理日志失败: {path} ({exc})")
        if removed:
            self.log_system(f"已自动清理 {removed} 个 7 天前的日志文件")

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
        try:
            if self.web_window and hasattr(self.web_window, 'minimize'):
                self.web_window.minimize()
            elif self.web_window and hasattr(self.web_window, 'hide'):
                self.web_window.hide()
            else:
                self.log_system("当前窗口后端不支持自动最小化。")
        except Exception as exc:
            self.log_system(f"最小化窗口失败: {exc}")

    def _save_window_geometry_preference(self):
        # pywebview does not expose a portable read API for current geometry.
        return

    def _play_feedback_sound(self, error=False):
        if not self._preference_enabled('soundEnabled', True):
            return
        try:
            if winsound:
                winsound.MessageBeep(winsound.MB_ICONHAND if error else winsound.MB_OK)
            else:
                print('\a', end='')
        except Exception:
            pass

    def _notify_runtime_event(self, title, message, error=False):
        pref_key = 'notifyOnError' if error else 'notifyOnComplete'
        if not self._preference_enabled(pref_key, True):
            return
        self._play_feedback_sound(error=error)
        self._after(0, lambda: self._show_error(title, message) if error else self._show_info(title, message))

    def _record_runtime_failure(self, script_type, message):
        """Persist a runtime failure in the matching Web-visible log stream."""
        target = 'autovisor' if script_type == 'practice' else script_type
        if target in getattr(self, 'log_history', {}):
            self.log(target, f"[ERROR] {message}")

    def _handle_runtime_exit(self, script_type, return_code, stop_requested):
        if stop_requested:
            return

        label = "Yatori" if script_type == 'yatori' else "Autovisor"
        if return_code:
            message = f"{label} 已退出，返回码: {return_code}"
            self._record_runtime_failure(script_type, message)
            self._notify_runtime_event(
                f"{label} 运行异常",
                message,
                error=True,
            )
            return

        self._notify_runtime_event(
            f"{label} 任务结束",
            f"{label} 已正常停止或完成任务。",
            error=False,
        )

        if not self.running.get('yatori') and not self.running.get('autovisor'):
            self._maybe_shutdown_after_completion()

    def _maybe_shutdown_after_completion(self):
        if not self._preference_enabled('autoShutdown'):
            return
        self.log_system("已启用刷完自动关机，将在 60 秒后关闭计算机。")
        self._shutdown_pending = True
        try:
            subprocess.Popen(
                ["shutdown", "/s", "/t", "60", "/c", "刷课任务已结束，统一启动器按偏好设置自动关机。"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self.log_system(f"自动关机指令执行失败: {exc}")

    def _cancel_shutdown(self):
        """取消正在进行的自动关机"""
        try:
            subprocess.run(
                ["shutdown", "/a"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            self.log_system("已取消自动关机")
            self._shutdown_pending = False
            return {"ok": True, "message": "已取消关机"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "取消关机超时"}
        except Exception as exc:
            self.log_system(f"取消自动关机失败: {exc}")
            return {"ok": False, "message": f"取消失败: {exc}"}

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
        yatori_version = self._get_yatori_display_version()
        autovisor_version = self._get_autovisor_display_version()
        logs = {name: list(lines) for name, lines in self.log_history.items()}
        autovisor_activity = summarize_autovisor_activity(
            logs.get('autovisor'),
            running=bool(self.running.get('autovisor')),
            starting=bool(self.starting.get('autovisor')),
        )
        core_paths = "\n".join(
            [
                f"Yatori: {self.yatori_path}",
                f"Autovisor: {self.autovisor_path}",
            ]
        )
        return {
            'backend_ready': True,
            'yatori_running': bool(self.running.get('yatori')),
            'autovisor_running': bool(self.running.get('autovisor')),
            'yatori_version': yatori_version,
            'autovisor_version': autovisor_version,
            'about_version': f"统一启动器 {self.LAUNCHER_VERSION} | Yatori {yatori_version} | Autovisor {autovisor_version}",
            'core_paths': core_paths,
            'versions': {
                'yatori': yatori_version,
                'autovisor': autovisor_version,
            },
            'paths': {
                'yatori': self.yatori_path,
                'autovisor': self.autovisor_path,
            },
            'installed': {
                'yatori': self._directory_has_any_file(self.yatori_path, self.YATORI_ENTRY_FILES),
                'autovisor': self._directory_has_any_file(self.autovisor_path, self.AUTOVISOR_ENTRY_FILES),
            },
            'running': dict(self.running),
            'starting': dict(self.starting),
            'practice_account_id': getattr(self, 'practice_account_id', None),
            'qb_running': self.question_bank.running,
            'qb_port': self.question_bank.port,
            'qb_stats': self.question_bank.get_stats(),
            'logs': logs,
            'autovisor_activity': autovisor_activity,
            'shutdown_pending': self._shutdown_pending,
            'autovisor_multi_mode': self._get_autovisor_multi_mode(),
        }

    def get_web_initial_state(self):
        return {
            'runtime': self.get_web_runtime_state(),
            'settings': {
                'yatori': self._load_yatori_config_data(),
                'autovisor': self._load_autovisor_config_data(),
                'questionbank': self.get_qb_settings_from_web(),
            },
        }

    def save_settings_from_web(self, payload):
        yatori_data = payload.get('yatori') if isinstance(payload, dict) else None
        autovisor_data = payload.get('autovisor') if isinstance(payload, dict) else None
        qb_data = payload.get('questionbank') if isinstance(payload, dict) else None
        if not isinstance(yatori_data, dict):
            yatori_data = self._load_yatori_config_data()
        if not isinstance(autovisor_data, dict):
            autovisor_data = self._load_autovisor_config_data()
        if isinstance(qb_data, dict):
            try:
                requested_qb_port = int(qb_data.get('port', 8083))
            except (TypeError, ValueError):
                return {'ok': False, 'message': '题库端口必须是整数', 'state': self.get_web_initial_state()}
            if not 1024 <= requested_qb_port <= 65535:
                return {
                    'ok': False,
                    'message': '题库端口必须在 1024 到 65535 之间',
                    'state': self.get_web_initial_state(),
                }
        accounts = autovisor_data.get('accounts') or [self._default_autovisor_account(1)]
        multi_mode = bool(autovisor_data.get('multi_mode'))
        if len(accounts) > 1:
            multi_mode = True
        if not multi_mode:
            accounts = accounts[:1]
        for account in accounts:
            account['limit_speed'] = self._normalize_autovisor_speed(account.get('limit_speed', '1.0'), '1.0')
        validation_error = self._validate_autovisor_accounts(accounts)
        if validation_error:
            return {'ok': False, 'message': validation_error, 'state': self.get_web_initial_state()}
        autovisor_data = {
            'multi_mode': multi_mode,
            'browser_driver': autovisor_data.get('browser_driver', 'Chrome') or 'Chrome',
            'browser_path': autovisor_data.get('browser_path', '').strip(),
            'accounts': accounts,
        }

        merged = self._load_yatori_config_data()
        merged.setdefault('setting', {})
        incoming_setting = yatori_data.get('setting', {})
        merged['setting'].setdefault('basicSetting', {}).update(incoming_setting.get('basicSetting', {}))
        merged['setting'].setdefault('emailInform', {}).update(incoming_setting.get('emailInform', {}))
        merged['setting'].setdefault('aiSetting', {}).update(incoming_setting.get('aiSetting', {}))
        merged['setting'].setdefault('apiQueSetting', {}).update(incoming_setting.get('apiQueSetting', {}))
        # Merge users: preserve fields not exposed in the Web UI
        existing_users = merged.get('users') if isinstance(merged.get('users'), list) else []
        incoming_users = yatori_data.get('users') or [self._default_yatori_user(1)]
        merged_users = []
        for i, incoming_user in enumerate(incoming_users):
            existing = existing_users[i] if i < len(existing_users) and isinstance(existing_users[i], dict) else {}
            merged_user = dict(existing)
            merged_user.update({k: v for k, v in incoming_user.items() if k != 'coursesCustom'})
            existing_cc = existing.get('coursesCustom') if isinstance(existing.get('coursesCustom'), dict) else {}
            merged_cc = dict(existing_cc)
            merged_cc.update(incoming_user.get('coursesCustom', {}))
            merged_user['coursesCustom'] = merged_cc
            merged_users.append(merged_user)
        merged['users'] = merged_users or [self._default_yatori_user(1)]
        config_paths = [
            self._get_yatori_config_path(),
            self._get_autovisor_config_path(),
        ]
        if isinstance(qb_data, dict):
            config_paths.append(self.question_bank.config_path)
        try:
            file_snapshot = capture_file_state(config_paths)
        except OSError as exc:
            return {
                'ok': False,
                'message': f'无法读取现有配置，已取消保存: {exc}',
                'state': self.get_web_initial_state(),
            }

        previous_multi_mode = self._get_autovisor_multi_mode()
        try:
            self._save_yatori_config_data(merged)
            self._save_autovisor_config_data(autovisor_data)
            if isinstance(qb_data, dict):
                qb_result = self.save_qb_settings_from_web(qb_data)
                if not qb_result.get('ok'):
                    raise RuntimeError(
                        qb_result.get('message', '题库设置保存失败')
                    )
            self._set_autovisor_multi_mode(autovisor_data.get('multi_mode'))
        except Exception as exc:
            self._set_autovisor_multi_mode(previous_multi_mode)
            restore_error = None
            try:
                restore_file_state(file_snapshot)
            except OSError as rollback_exc:
                restore_error = str(rollback_exc)
            message = str(exc) or '配置保存失败'
            if restore_error:
                message = f'{message}；旧配置恢复失败: {restore_error}'
            else:
                message = f'{message}；未保留任何部分改动'
            self.log_system(f"启动器配置保存失败: {message}")
            return {
                'ok': False,
                'message': message,
                'state': self.get_web_initial_state(),
            }
        self.log_system("启动器配置已保存")
        return {'ok': True, 'state': self.get_web_initial_state()}

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
        self._last_start_error.pop(script_type, None)
        if script_type == 'yatori':
            return bool(self.start_yatori())
        elif script_type == 'autovisor':
            return bool(self.start_autovisor())
        message = f"未知核心类型: {script_type}"
        self._last_start_error[script_type] = message
        self.log_system(message)
        return False

    def _reject_runtime_start(self, script_type, message):
        """Record an immediate launch rejection for Web action feedback."""
        if not hasattr(self, '_last_start_error'):
            self._last_start_error = {}
        self._last_start_error[script_type] = message
        return False

    def _claim_runtime_start(self, script_type):
        """Atomically reserve a runtime start so rapid clicks cannot fork twice."""
        return self._get_process_supervisor().claim_start(script_type)

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
        """启动 Yatori"""
        if self.running['yatori'] or self.starting['yatori']:
            self.log_system("Yatori 已经在运行或启动中")
            return True

        self.yatori_path = self.find_yatori_path(self.get_base_dir())

        # 检查配置文件
        config_path = os.path.join(self.yatori_path, 'config.yaml')
        if not os.path.exists(config_path):
            self.log_system(f"错误: 未找到配置文件 {config_path}")
            self._show_error("启动失败", "未找到 config.yaml 配置文件\n请使用配置生成器创建配置")
            return self._reject_runtime_start('yatori', '未找到 Yatori 的 config.yaml，请先保存配置')

        cmd, entry_path = self._get_yatori_command()
        if not cmd:
            self.log_system("错误: 未找到 Yatori 可执行文件")
            self._show_error("启动失败", "未找到 Yatori 可执行文件")
            return self._reject_runtime_start('yatori', '未找到 Yatori 可执行文件，请检查核心是否安装完整')

        # 同步题库 URL 到 Yatori 配置
        self._sync_yatori_question_bank_url()

        if not self._claim_runtime_start('yatori'):
            self.log_system("Yatori 已经在运行或启动中")
            return True

        self.log_system("正在启动 Yatori...")
        self.log_system(f"Yatori 入口: {entry_path}")
        return self._get_runtime_process_service().start(
            core='yatori',
            label='Yatori',
            command=cmd,
            cwd=self.yatori_path,
            encodings=self._build_encoding_candidates(
                'utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'cp936'
            ),
        )

    def get_python_executable(self):
        return find_python_executable()

    def _prepare_autovisor_question_bank(self):
        """Start and briefly probe the local question-bank service."""
        import socket
        import time

        if not self.question_bank.running:
            self.log_system("[Autovisor] 正在启动题库服务器...")
            self.start_question_bank(silent=True)
            for _ in range(30):
                if self.stop_requested.get('autovisor'):
                    return
                time.sleep(0.1)
        else:
            self.log_system("[Autovisor] 题库服务器已在运行")

        port = self.question_bank.port
        result = None
        for attempt in range(3):
            if self.stop_requested.get('autovisor'):
                return
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                result = sock.connect_ex(('127.0.0.1', port))
            if result == 0:
                self.log_system(
                    f"[Autovisor] 题库服务器验证成功 (尝试 {attempt + 1})"
                )
                return
            self.log_system(
                f"[Autovisor] 端口验证失败 (尝试 {attempt + 1}/3)，等待2秒..."
            )
            for _ in range(20):
                if self.stop_requested.get('autovisor'):
                    return
                time.sleep(0.1)
        self.log_system(
            f"[Autovisor] 警告: 题库服务器验证失败，错误码 {result}，继续启动..."
        )

    def _build_autovisor_runtime_env(self):
        env = os.environ.copy()
        qb_url = self.get_question_bank_url()
        if qb_url:
            env["QB_URL"] = qb_url
            self.log_system(f"[Autovisor] 设置题库URL: {qb_url}")
        else:
            self.log_system("[Autovisor] 警告: 题库服务器未启动，使用默认URL")
        return env

    def start_autovisor(self):
        """启动 Autovisor"""
        if self.autovisor_installing:
            self.log_system("Autovisor 依赖安装中，请等待安装完成后自动启动。")
            return True

        if self.running['autovisor'] or self.starting['autovisor']:
            self.log_system("Autovisor 已经在运行或启动中")
            return True

        self.autovisor_path = self.find_autovisor_path(self.get_base_dir())
        multi_mode = self._get_autovisor_multi_mode()
        script_path, script_name, exact_match, is_executable = self._get_autovisor_entry_path(multi_mode)

        if not script_path:
            self.log_system(f"错误: 未找到 Autovisor 入口文件，目录: {self.autovisor_path}")
            self._show_error("启动失败", "Autovisor 目录中未找到可用入口文件\n请检查 Autovisor 包是否完整")
            return self._reject_runtime_start('autovisor', '未找到 Autovisor 入口文件，请检查核心是否安装完整')

        # 检查配置文件
        config_path = os.path.join(self.autovisor_path, 'configs.ini')
        if not os.path.exists(config_path):
            self.log_system(f"错误: 未找到配置文件 {config_path}")
            self._show_error("启动失败", "未找到 configs.ini 配置文件\n请使用配置生成器创建配置")
            return self._reject_runtime_start('autovisor', '未找到 Autovisor 的 configs.ini，请先保存配置')

        saved_config = self._load_autovisor_config_data()
        runtime_validation_error = self._validate_autovisor_runtime(
            saved_config.get('accounts') or [],
            multi_mode,
        )
        if runtime_validation_error:
            self.log_system(f"Autovisor 启动已取消: {runtime_validation_error}")
            return self._reject_runtime_start(
                'autovisor',
                runtime_validation_error,
            )

        runtime_state = self._prepare_autovisor_config(config_path, multi_mode)
        for summary in runtime_state['browser_summaries']:
            self.log_system(summary)

        python_exe = None
        if is_executable:
            self.log_system("检测到 Autovisor 可执行版，将直接启动 EXE。")
        else:
            # 检查 Python 环境
            python_exe = self.get_python_executable()
            if not python_exe:
                self.log_system("错误: 未找到 Python 解释器")
                self._show_error("启动失败", "未找到 Python 解释器\n请确保已安装 Python 并添加到环境变量")
                return self._reject_runtime_start('autovisor', '未找到可用的 Python 解释器')

            missing_dependencies = self._check_autovisor_dependencies(python_exe)
            if missing_dependencies or runtime_state['needs_playwright_browser']:
                package_names = ", ".join(sorted({item[1] for item in missing_dependencies}))
                self.log_system(f"错误: 当前 Python 环境缺少 Autovisor 依赖: {package_names}")
                for module_name, package_name, error in missing_dependencies:
                    if error:
                        self.log_system(f"依赖检查失败 [{module_name}/{package_name}]: {error}")
                if runtime_state['needs_playwright_browser']:
                    self.log_system("未检测到可用 Chrome/Edge，将自动安装 Playwright Chromium 作为浏览器回退。")
                accepted = self._install_autovisor_dependencies_async(
                    python_exe,
                    missing_dependencies,
                    ensure_playwright_browser=runtime_state['needs_playwright_browser']
                )
                if accepted:
                    return True
                # A concurrent click may have claimed installation between the
                # initial check and this call; the request is still in progress.
                if self.autovisor_installing:
                    return True
                return self._reject_runtime_start('autovisor', 'Autovisor 依赖安装任务未能启动')

        if not self._claim_runtime_start('autovisor'):
            self.log_system("Autovisor 已经在运行或启动中")
            return True

        self.log_system("正在启动 Autovisor...")
        self.log('autovisor', "正在启动任务...")
        self.log_system(f"Autovisor 目录: {self.autovisor_path}")
        self.log_system(f"Autovisor 入口: {script_name}")
        if is_executable:
            self.log_system("启动方式: 直接运行 EXE")
        else:
            self.log_system(f"Python 解释器: {python_exe}")
        if is_executable:
            self.log_system("提示: 当前 Autovisor 为可执行版，已跳过 Python 依赖检查。")
        elif not exact_match:
            expected_name = 'Autovisor_Multi.py' if multi_mode else 'Autovisor.py'
            self.log_system(f"警告: 未找到请求入口 {expected_name}，已回落到 {script_name}")
        command = [script_path] if is_executable else [python_exe, script_path]
        return self._get_runtime_process_service().start(
            core='autovisor',
            label='Autovisor',
            command=command,
            cwd=self.autovisor_path,
            encodings=self._build_encoding_candidates(
                locale.getpreferredencoding(False), 'utf-8', 'gb18030', 'gbk'
            ),
            before_launch=self._prepare_autovisor_question_bank,
            env_factory=self._build_autovisor_runtime_env,
        )

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

    def get_autovisor_courses_from_web(self, account_index=0):
        return self._get_course_api_service().get_autovisor_courses(account_index)

    def get_xuexitong_courses_from_web(self, account_index=0):
        return self._get_course_api_service().get_xuexitong_courses(account_index)

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
        """Choose a JSON file in WebView and delegate the import."""
        if not self.question_bank.available:
            message = "题库服务器不可用"
            self._show_error("导入失败", message)
            return {'ok': False, 'message': message}
        file_path = ""
        if self.web_window and webview:
            selection = self.web_window.create_file_dialog(
                FD_OPEN,
                file_types=("JSON 文件 (*.json)", "所有文件 (*.*)"),
            )
            if selection:
                file_path = selection[0]
        if not file_path:
            return {'ok': True, 'cancelled': True}
        result = self.question_bank.import_questions(file_path)
        if result.get("ok"):
            self._show_info("导入成功", result["message"])
            result = dict(result)
            result.update({'toast': result['message'], 'toastType': 'success'})
        else:
            self._show_error("导入失败", result.get("message", "未知错误"))
        return result

    def _export_question_bank(self):
        """Choose a JSON destination in WebView and delegate the export."""
        if not self.question_bank.available:
            message = "题库服务器不可用"
            self._show_error("导出失败", message)
            return {'ok': False, 'message': message}
        file_path = ""
        if self.web_window and webview:
            selection = self.web_window.create_file_dialog(
                FD_SAVE,
                file_types=("JSON 文件 (*.json)", "所有文件 (*.*)"),
            )
            if selection:
                file_path = selection[0]
                if not file_path.lower().endswith(".json"):
                    file_path += ".json"
        if not file_path:
            return {'ok': True, 'cancelled': True}
        result = self.question_bank.export_questions(file_path)
        if result.get("ok"):
            self._show_info("导出成功", result["message"])
            result = dict(result)
            result.update({'toast': result['message'], 'toastType': 'success'})
        else:
            self._show_error("导出失败", result.get("message", "未知错误"))
        return result

    def _clear_question_bank(self):
        result = self.question_bank.clear_questions()
        if result.get("ok"):
            self._show_info("清空成功", result["message"])
            result = dict(result)
            result.update({'toast': result['message'], 'toastType': 'success'})
        else:
            self._show_error("清空失败", result.get("message", "未知错误"))
        return result

    def _deduplicate_question_bank(self):
        result = self.question_bank.deduplicate_questions()
        if result.get("ok"):
            self._show_info("去重完成", result["message"])
        else:
            self._show_error("去重失败", result.get("message", "未知错误"))
        return bool(result.get("ok")), result.get("message", "去重失败")

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
        """启动所有脚本"""
        self.log_system("正在一键启动所有脚本...")
        failures = []
        if not self.question_bank.available:
            failures.append("题库服务器模块不可用")
        elif not self.question_bank.running:
            if not self.start_question_bank(silent=True):
                failures.append("题库服务器启动失败")
        if not self.running['yatori']:
            if not self.start_yatori():
                failures.append(
                    getattr(self, '_last_start_error', {}).get(
                        'yatori', 'Yatori 启动失败'
                    )
                )
        if not self.running['autovisor']:
            if not self.start_autovisor():
                failures.append(
                    getattr(self, '_last_start_error', {}).get(
                        'autovisor', 'Autovisor 启动失败'
                    )
                )
        if failures:
            message = '；'.join(dict.fromkeys(failures))
            self.log_system(f"一键启动未全部成功: {message}")
            return {'ok': False, 'message': message}
        return {
            'ok': True,
            'message': '全部启动请求已提交',
            'toast': '全部启动请求已提交',
            'toastType': 'success',
        }

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
        """导出日志到文件并打开所在文件夹"""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"logs_{tab}_{ts}.txt"
            export_dir = os.path.join(self.get_base_dir(), "logs")
            os.makedirs(export_dir, exist_ok=True)
            filepath = os.path.join(export_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(text)
            subprocess.Popen(['explorer', '/select,', os.path.normpath(filepath)])
            self.log_system(f"日志已导出: {filename}")
            return {"ok": True, "path": filepath, "message": f"日志已导出: {filename}"}
        except Exception as exc:
            self.log_system(f"导出日志失败: {exc}")
            return {"ok": False, "message": f"导出失败: {exc}"}


    def open_config_dir(self, script_type):
        """打开配置目录"""
        if script_type == 'yatori':
            path = self.yatori_path
        elif script_type == 'autovisor':
            path = self.autovisor_path
        else:
            return {'ok': False, 'message': f'未知核心类型: {script_type}'}

        if os.path.exists(path):
            os.startfile(path)
            return {'ok': True}
        else:
            message = f"目录不存在: {path}"
            self._show_error("错误", message)
            return {'ok': False, 'message': message}

    def open_config_generator(self):
        """打开配置生成器"""
        candidate_names = [
            "配置文件生成器.html",
            "统一配置生成器.html",
        ]
        for file_name in candidate_names:
            generator_path = os.path.join(self.get_base_dir(), "web", file_name)
            if os.path.exists(generator_path):
                os.startfile(generator_path)
                return {'ok': True}
        message = "未找到配置生成器页面"
        self._show_error("错误", message)
        return {'ok': False, 'message': message}

    # ==================== 核心管理功能 ====================

    def check_yatori_update_async(self):
        return self.update_controller.check_yatori_async(explicit=False)

    def show_update_available_notification(self, version):
        self.log_system(f"检测到 Yatori 新版本: {version}")

    def install_yatori_update_async(self, release_info=None):
        return self.update_controller.install_yatori_async(release_info)

    def check_autovisor_update_async(self):
        return self.update_controller.check_autovisor_async()

    def handle_autovisor_version_result(self, result):
        return self.update_controller.handle_autovisor_result(result)

    def install_autovisor_update_async(self, release_info=None):
        return self.update_controller.install_autovisor_async(release_info)

    def show_update_dialog(self):
        """Check and install Yatori from the explicit Web UI action."""
        return self.update_controller.check_yatori_async(explicit=True)

    def handle_manual_check_result(self, result):
        return self.update_controller.handle_explicit_yatori_result(result)

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
            self.stop_all()
        else:
            course_service = getattr(self, '_course_api_service', None)
            if course_service is not None:
                course_service.stop_active_fetch()
        self.stop_question_bank()
        self._close_main_window()


def main():
    """主程序入口"""
    # 全局未捕获异常处理 — 写入崩溃日志
    import traceback as _traceback
    _CRASH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_CRASH_DIR, exist_ok=True)

    def _crash_handler(exc_type, exc_value, exc_tb):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(_CRASH_DIR, f"crash_{ts}.log")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"Time: {datetime.now()}\n")
                f.write(f"Type: {exc_type.__name__}\n")
                f.write(f"Value: {exc_value}\n")
                f.write("Traceback:\n")
                _traceback.print_tb(exc_tb, file=f)
                f.write("\n")
                _traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
            print(f"[CRASH] 崩溃日志已写入: {path}")
        except Exception:
            pass
        # 调用原始 excepthook（显示默认错误对话框）
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_handler

    # 启动时自动检查并安装缺失依赖
    ensure_core_dependencies()
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
    FD_OPEN = getattr(getattr(webview, 'FileDialog', None), 'OPEN',
              getattr(webview, 'OPEN_DIALOG', 10))
    FD_SAVE = getattr(getattr(webview, 'FileDialog', None), 'SAVE',
              getattr(webview, 'SAVE_DIALOG', 30))
    # 设置高 DPI 感知 (Windows 10+)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass

    app = UnifiedLauncher()
    api = WebLauncherAPI(app)
    html_path = app.resolve_web_ui_path(app.get_base_dir())
    window = webview.create_window(
        "统一启动器",
        html_path,
        js_api=api,
        width=1480,
        height=940,
        min_size=(1180, 760),
        confirm_close=False,
    )
    app.attach_web_window(window)
    dev_mode = '--dev' in sys.argv
    # 非开发者模式启动后隐藏控制台
    if not dev_mode and os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0
            )
        except Exception:
            pass
    webview.start(debug=dev_mode, http_server=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback as _tb
        _crash_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(_crash_dir, exist_ok=True)
        _ts = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(_crash_dir, f"crash_{_ts}.log"), 'w', encoding='utf-8') as _f:
            _tb.print_exc(file=_f)
        print(f"[FATAL] 启动崩溃，日志已写入 logs/crash_{_ts}.log")
        raise
