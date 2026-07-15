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
import shutil
import json
import urllib.request
from datetime import datetime

from src.atomic_io import atomic_dump_json
from src.config_service import ConfigService, read_ini_config
from src.course_catalog import (
    CourseCatalogError,
    CourseCatalogService,
    normalize_account_index,
    parse_zhs_course_data,
)
from src.dependencies import ensure_core_dependencies
from src.launcher_api import WebLauncherAPI
from src.process_supervisor import ProcessSupervisor
from src.question_bank_controller import QuestionBankController
from src.update_controller import UpdateController

try:
    import webview
except ImportError:  # pragma: no cover
    webview = None

# FileDialog 兼容常量（main() 中会重新赋值）
FD_OPEN = 10   # OPEN_DIALOG
FD_SAVE = 30   # SAVE_DIALOG

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

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
    AUTOVISOR_PYPI_MIRRORS = (
        ("清华", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"),
        ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
        ("华为", "https://mirrors.huaweicloud.com/repository/pypi/simple"),
        ("官方", "https://pypi.org/simple"),
    )
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

    def _python_module_available(self, python_exe, module_name):
        try:
            result = subprocess.run(
                [python_exe, '-c', f'import {module_name}'],
                cwd=self.autovisor_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            return result.returncode == 0, (result.stderr or '').strip()
        except Exception as exc:
            return False, str(exc)

    def _check_autovisor_dependencies(self, python_exe):
        self.log_system(f"依赖检查Python: {python_exe}")
        # 先用当前解释器快速自检，辅助诊断 Python 路径不匹配问题
        try:
            import pygetwindow
            self.log_system("当前进程已装有 pygetwindow")
        except ImportError as ie:
            self.log_system(f"当前进程缺少: {ie.name}（这可能说明启动器用的Python与你安装依赖的Python不是同一个）")

        dependency_map = {
            'playwright': 'playwright',
            'pygetwindow': 'PyGetWindow',
            'requests': 'requests',
        }

        missing = []
        for module_name, package_name in dependency_map.items():
            is_available, error = self._python_module_available(python_exe, module_name)
            if not is_available:
                if error:
                    self.log_system(f"  缺失 {package_name} ({module_name}): {error}")
                else:
                    self.log_system(f"  缺失 {package_name} ({module_name})")
                missing.append((module_name, package_name, error))
        return missing

    def _get_autovisor_runtime_install_args(self, missing_package_names=None):
        package_specs = {
            'playwright': 'playwright>=1.52,<2',
            'PyGetWindow': 'PyGetWindow>=0.0.9,<1',
            'requests': 'requests>=2.32,<3',
        }
        if missing_package_names:
            return [package_specs[name] for name in missing_package_names if name in package_specs]
        return [package_specs[name] for name in package_specs.keys()]

    def _normalize_browser_name(self, name):
        normalized = (name or '').strip().lower()
        if normalized in {'edge', 'msedge'}:
            return 'edge'
        if normalized in {'chrome', 'google-chrome'}:
            return 'chrome'
        if normalized in {'chromium', 'playwright-chromium'}:
            return 'chromium'
        return normalized or 'chrome'

    def _get_browser_registry_candidates(self, executable_name):
        if not winreg:
            return []

        candidates = []
        key_path = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    if value:
                        candidates.append(value)
            except OSError:
                continue
        return candidates

    def _find_browser_executable(self, browser_name):
        browser = self._normalize_browser_name(browser_name)
        executable_map = {
            'chrome': 'chrome.exe',
            'edge': 'msedge.exe',
        }
        executable_name = executable_map.get(browser)
        if not executable_name:
            return None

        base_dirs = [
            os.environ.get('PROGRAMFILES'),
            os.environ.get('PROGRAMFILES(X86)'),
            os.environ.get('LOCALAPPDATA'),
        ]
        browser_dirs = {
            'chrome': [
                ('Google', 'Chrome', 'Application'),
                ('Chrome', 'Application'),
            ],
            'edge': [
                ('Microsoft', 'Edge', 'Application'),
                ('Edge', 'Application'),
            ],
        }

        candidates = []
        for base_dir in base_dirs:
            if not base_dir:
                continue
            for parts in browser_dirs.get(browser, []):
                candidates.append(os.path.join(base_dir, *parts, executable_name))
        candidates.extend(self._get_browser_registry_candidates(executable_name))

        seen = set()
        for candidate in candidates:
            normalized_path = os.path.normpath(candidate)
            if normalized_path in seen:
                continue
            seen.add(normalized_path)
            if os.path.exists(normalized_path):
                return normalized_path
        return None

    def _has_playwright_chromium(self):
        custom_root = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '').strip()
        if custom_root:
            cache_roots = [custom_root]
        else:
            cache_roots = [
                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ms-playwright'),
                os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'ms-playwright'),
            ]
        for root in cache_roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                if any(name.startswith('chromium-') for name in os.listdir(root)):
                    return True
            except OSError:
                continue
        return False

    def _read_autovisor_config(self, config_path):
        return read_ini_config(config_path)

    def _copy_config_section(self, parser, source, target):
        if not parser.has_section(source) or parser.has_section(target):
            return False

        parser.add_section(target)
        for option, value in parser.items(source, raw=True):
            parser.set(target, option, value)
        return True

    def _prepare_autovisor_config(self, config_path, multi_mode):
        if not os.path.exists(config_path):
            return {
                'changed': False,
                'needs_playwright_browser': False,
                'browser_summaries': [],
            }

        parser = self._read_autovisor_config(config_path)
        changed = False
        browser_summaries = []

        if True:
            aliases = (
                ('user-account-1', 'user-account'),
                ('browser-option-1', 'browser-option'),
                ('script-option-1', 'script-option'),
                ('course-option-1', 'course-option'),
                ('course-url-1', 'course-url'),
            )
            for source, target in aliases:
                if self._copy_config_section(parser, source, target):
                    changed = True
                    browser_summaries.append(f"已兼容单账号配置段: [{source}] -> [{target}]")

        browser_sections = [
            section for section in parser.sections()
            if section == 'browser-option' or section.startswith('browser-option-')
        ]

        if not browser_sections:
            parser.add_section('browser-option')
            parser.set('browser-option', 'driver', 'Chrome')
            parser.set('browser-option', 'EXE_PATH', '')
            browser_sections = ['browser-option']
            changed = True
            browser_summaries.append("已补充默认浏览器配置段 [browser-option]")

        needs_playwright_browser = False
        default_driver = 'chrome' if self._find_browser_executable('chrome') else 'edge'

        for section in browser_sections:
            driver = self._normalize_browser_name(parser.get(section, 'driver', fallback=default_driver))
            if driver not in {'chrome', 'edge', 'chromium'}:
                driver = default_driver
                parser.set(section, 'driver', 'Chrome' if driver == 'chrome' else 'Edge')
                changed = True

            exe_path = parser.get(section, 'EXE_PATH', fallback='').strip()
            if exe_path and not os.path.exists(exe_path):
                parser.set(section, 'EXE_PATH', '')
                exe_path = ''
                changed = True

            if not exe_path:
                detected_path = self._find_browser_executable(driver)
                if detected_path:
                    parser.set(section, 'EXE_PATH', detected_path)
                    exe_path = detected_path
                    changed = True
                    browser_summaries.append(f"{section}: 已自动定位 {driver} 路径 -> {detected_path}")

            if not exe_path:
                needs_playwright_browser = not self._has_playwright_chromium()
                browser_summaries.append(f"{section}: 未找到 {driver}，将回退到 Playwright Chromium")

        if changed:
            with open(config_path, 'w', encoding='utf-8') as handle:
                parser.write(handle)

        return {
            'changed': changed,
            'needs_playwright_browser': needs_playwright_browser,
            'browser_summaries': browser_summaries,
        }

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
        last_error = None
        total_packages = len(install_args)
        for i, pkg_spec in enumerate(install_args, 1):
            self.log_system(f"[{i}/{total_packages}] 正在安装 {pkg_spec} ...")

        for mirror_name, mirror_url in self.AUTOVISOR_PYPI_MIRRORS:
            self.log_system(f"正在尝试通过 {mirror_name} 镜像安装 {total_packages} 个依赖...")
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            try:
                process = subprocess.Popen(
                    [
                        python_exe, '-m', 'pip', 'install',
                        '--disable-pip-version-check',
                        '--prefer-binary',
                        '--progress-bar', 'on',
                        '-i', mirror_url,
                        *install_args,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.autovisor_path,
                    text=True,
                    encoding='utf-8', errors='replace',
                    env=env,
                )
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        self.log('system', line, replace_last=self._is_progress_log(line))
                install_code = process.wait()
            except Exception as e:
                install_code = -1
                self.log_system(f"安装进程异常: {e}")
            if install_code == 0:
                self.log_system(f"已通过 {mirror_name} 镜像完成 {total_packages} 个包的安装。")
                return

            last_error = RuntimeError(f"{mirror_name} 镜像安装失败，返回码: {install_code}")
            self.log_system(str(last_error))

        raise last_error or RuntimeError("所有 PyPI 镜像均安装失败")

    def _install_autovisor_dependencies_async(self, python_exe, missing_packages, ensure_playwright_browser=False):
        if getattr(self, 'autovisor_installing', False):
            self.log_system("Autovisor 依赖安装正在进行中，请稍候。")
            return

        self.autovisor_installing = True
        install_targets = list(missing_packages)
        if ensure_playwright_browser:
            install_targets.append('playwright-browser')
        self.log_system(f"Autovisor runtime setup: {', '.join(install_targets)}")
        self.log_system(f"开始安装 Autovisor 依赖: {', '.join(missing_packages)}")
        self.log_system("依赖下载与安装进度会显示在系统日志中。")

        def install_worker():
            success = False
            try:
                if missing_packages:
                    # 只安装真正缺失的包
                    missing_names = sorted({pkg_name for _, pkg_name, _ in missing_packages})
                    install_args = self._get_autovisor_runtime_install_args(missing_names)
                    self.log_system(f"缺 {len(missing_names)} 个包，正在通过镜像安装: {', '.join(missing_names)}")
                    self._install_autovisor_with_mirrors(python_exe, install_args)

                self.log_system("正在执行 playwright install chromium，浏览器下载进度将输出到系统日志...")
                playwright_code = 0
                if ensure_playwright_browser:
                    playwright_code = self._run_logged_command(
                    [python_exe, '-m', 'playwright', 'install', 'chromium'],
                    self.autovisor_path
                )
                if playwright_code != 0:
                    raise RuntimeError(f"playwright install 返回码: {playwright_code}")

                success = True
                self.log_system("Autovisor 依赖安装完成，准备重新启动。")
            except Exception as exc:
                self.log_system(f"Autovisor 依赖安装失败: {exc}")
                self._after(
                    0,
                    lambda: self._show_error(
                        "依赖安装失败",
                        f"Autovisor 依赖自动安装失败：\n{exc}\n\n请查看系统日志后重试。"
                    )
                )
            finally:
                self.autovisor_installing = False
                if success:
                    self._after(0, self.start_autovisor)

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
            self.log_system("正在检查 Yatori 更新...")
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
        }
        self._runtime_lock = threading.RLock()
        self.stop_requested = {
            'yatori': False,
            'autovisor': False,
        }

        base_dir = self.get_base_dir()
        self.preferences_path = self.get_preferences_file_path(base_dir)
        self.web_preferences = self._load_web_preferences()
        if self.web_preferences.get('autoCleanLogs'):
            self._clean_old_runtime_logs(base_dir)
        self.yatori_path = self.find_yatori_path(base_dir)
        self.autovisor_path = self.find_autovisor_path(base_dir)

        self.core_manager = None
        self.autovisor_installing = False
        self._shutdown_pending = False

        self.question_bank = QuestionBankController(
            base_dir,
            log=self.log_system,
            prepare_environment=self._configure_zerror_db_env,
            get_external_db_info=self._get_zerror_db_info,
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

    def _load_web_preferences(self):
        defaults = {
            'theme': 'dark',
            'autoStart': False,
            'autoShutdown': False,
            'autoRun': False,
            'minimizeToTray': False,
            'startMinimized': False,
            'alwaysOnTop': False,
            'notifyOnComplete': True,
            'notifyOnError': True,
            'soundEnabled': True,
            'rememberGeometry': False,
            'autoCleanLogs': False,
            'qb_external_url': '',
        }
        try:
            if os.path.exists(self.preferences_path):
                with open(self.preferences_path, 'r', encoding='utf-8') as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    defaults.update(stored)
        except Exception as exc:
            self.log_system(f"加载启动器偏好失败: {exc}")
        return defaults

    def _save_web_preferences(self):
        try:
            atomic_dump_json(self.preferences_path, self.web_preferences)
        except Exception as exc:
            self.log_system(f"保存启动器偏好失败: {exc}")

    def _preference_enabled(self, key, default=False):
        return bool(self.web_preferences.get(key, default))

    def _startup_command(self):
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}"'
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    def _set_windows_auto_start(self, enabled):
        if os.name != 'nt':
            self.log_system("当前系统不支持自动创建开机启动项。")
            return

        startup_dir = os.path.join(
            os.environ.get('APPDATA', ''),
            r'Microsoft\Windows\Start Menu\Programs\Startup',
        )
        if not startup_dir.strip("\\") or not os.path.isdir(startup_dir):
            self.log_system("未找到 Windows 启动目录，开机自动启动未生效。")
            return

        shortcut_path = os.path.join(startup_dir, "统一刷课启动器.bat")
        if enabled:
            work_dir = os.path.dirname(os.path.abspath(__file__))
            with open(shortcut_path, 'w', encoding='utf-8') as handle:
                handle.write("@echo off\n")
                handle.write(f"cd /d \"{work_dir}\"\n")
                handle.write(f"start \"\" {self._startup_command()}\n")
            self.log_system("已启用开机自动启动")
        else:
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)
            self.log_system("已关闭开机自动启动")

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

    def _handle_runtime_exit(self, script_type, return_code, stop_requested):
        if stop_requested:
            return

        label = "Yatori" if script_type == 'yatori' else "Autovisor"
        if return_code:
            self._notify_runtime_event(
                f"{label} 运行异常",
                f"{label} 已退出，返回码: {return_code}",
                error=True,
            )
        else:
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
        if 'autoStart' in payload:
            try:
                self._set_windows_auto_start(bool(payload['autoStart']))
            except Exception as exc:
                self.log_system(f"更新开机自动启动失败: {exc}")
        if 'alwaysOnTop' in payload:
            self._apply_window_preferences()
        if payload.get('autoCleanLogs'):
            self._clean_old_runtime_logs()

    def get_web_preferences(self):
        return dict(self.web_preferences)

    def save_web_preference(self, payload):
        if not isinstance(payload, dict):
            return {'ok': False, 'message': '偏好设置格式错误', 'preferences': self.get_web_preferences()}

        self.web_preferences.update(payload)
        self._save_web_preferences()
        self._handle_preference_side_effects(payload)
        return {'ok': True, 'preferences': self.get_web_preferences()}

    def get_web_runtime_state(self):
        yatori_version = self._get_yatori_display_version()
        autovisor_version = self._get_autovisor_display_version()
        logs = {name: list(lines) for name, lines in self.log_history.items()}
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
            'qb_running': self.question_bank.running,
            'qb_port': self.question_bank.port,
            'qb_stats': self.question_bank.get_stats(),
            'logs': logs,
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
        self._save_yatori_config_data(merged)

        self._set_autovisor_multi_mode(autovisor_data.get('multi_mode'))
        self._save_autovisor_config_data(autovisor_data)
        if isinstance(qb_data, dict):
            qb_result = self.save_qb_settings_from_web(qb_data)
            if not qb_result.get('ok'):
                return {
                    'ok': False,
                    'message': qb_result.get('message', '题库设置保存失败'),
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

    def test_ai_connectivity_from_web(self, config):
        """从Web界面测试AI连通性"""
        try:
            # 导入AI连通性测试模块
            try:
                from scripts.ai_connectivity_test import AIConnectivityTester
            except ImportError:
                self.log_system("[AI测试] 导入ai_connectivity_test模块失败")
                return {
                    'ok': False,
                    'success': False,
                    'message': 'AI连通性测试模块加载失败'
                }

            provider = config.get('provider', 'SILICON')
            api_url = config.get('api_url', '')
            api_key = config.get('api_key', '')
            model = config.get('model', '')

            if not api_key or not api_key.strip():
                return {
                    'ok': False,
                    'success': False,
                    'message': 'API Key 不能为空'
                }

            self.log_system(f"[AI测试] 正在测试 {AIConnectivityTester.PLATFORM_NAMES.get(provider, provider)} 连通性...")

            success, message, details = AIConnectivityTester.test_connectivity(
                provider=provider,
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout=30
            )

            if success:
                self.log_system(f"[AI测试] 连通性测试成功: {message}")
            else:
                self.log_system(f"[AI测试] 连通性测试失败: {message}")

            return {
                'ok': True,
                'success': success,
                'message': message,
                'details': details
            }

        except Exception as e:
            self.log_system(f"[AI测试] 测试过程发生错误: {e}")
            return {
                'ok': False,
                'success': False,
                'message': f'测试过程发生错误: {str(e)}'
            }

    def fetch_model_list_from_web(self, config):
        """从Web界面获取AI模型列表"""
        try:
            # 导入AI连通性测试模块
            try:
                from scripts.ai_connectivity_test import AIConnectivityTester
            except ImportError:
                self.log_system("[AI模型] 导入ai_connectivity_test模块失败")
                return {
                    'ok': False,
                    'success': False,
                    'message': 'AI模块加载失败',
                    'models': []
                }

            provider = config.get('provider', 'SILICON')
            api_url = config.get('api_url', '')
            api_key = config.get('api_key', '')

            if not api_key or not api_key.strip():
                return {
                    'ok': False,
                    'success': False,
                    'message': 'API Key 不能为空',
                    'models': []
                }

            if not api_url:
                return {
                    'ok': False,
                    'success': False,
                    'message': 'API 地址不能为空',
                    'models': []
                }

            self.log_system(f"[AI模型] 正在获取 {AIConnectivityTester.PLATFORM_NAMES.get(provider, provider)} 的模型列表...")

            success, message, models = AIConnectivityTester.fetch_model_list(
                provider=provider,
                api_url=api_url,
                api_key=api_key,
                timeout=30
            )

            if success:
                self.log_system(f"[AI模型] 成功获取 {len(models)} 个模型")
            else:
                self.log_system(f"[AI模型] 获取模型列表失败: {message}")

            return {
                'ok': True,
                'success': success,
                'message': message,
                'models': models
            }

        except Exception as e:
            self.log_system(f"[AI模型] 获取模型列表时发生错误: {e}")
            return {
                'ok': False,
                'success': False,
                'message': f'获取模型列表时发生错误: {str(e)}',
                'models': []
            }

    def toggle_web_runtime(self, script_type):
        if not self.toggle_script(script_type):
            return {'ok': False, 'message': f'无法切换核心: {script_type}', 'state': self.get_web_initial_state()}
        return {'ok': True, 'state': self.get_web_initial_state()}

    def perform_web_action(self, action, script_type=None):
        try:
            if action == 'start':
                if not self.start_script(script_type):
                    return {'ok': False, 'message': f'未知核心类型: {script_type}'}
            elif action == 'stop':
                if not self.stop_script(script_type):
                    return {'ok': False, 'message': f'未知核心类型: {script_type}'}
            elif action == 'start_all':
                self.start_all()
            elif action == 'stop_all':
                self.stop_all()
            elif action == 'open_config_dir':
                self.open_config_dir(script_type)
            elif action == 'open_config_generator':
                self.open_config_generator()
            elif action == 'show_settings_dir':
                self.open_config_dir(script_type or 'yatori')
            elif action == 'show_update_dialog':
                if not self.show_update_dialog():
                    return {'ok': False, 'message': '无法检查或安装 Yatori 更新'}
            elif action == 'check_autovisor_update':
                self.check_autovisor_update_async()
            elif action == 'install_autovisor_update':
                if not self.install_autovisor_update_async():
                    return {'ok': False, 'message': '暂无可安装的 Autovisor 更新'}
            elif action == 'clear_logs':
                self.clear_all_logs()
            elif action == 'exit':
                self.on_closing()
            elif action == 'exit_app':
                # 使用线程异步执行退出，避免阻塞JS调用
                import threading
                def do_exit():
                    try:
                        self.on_closing(confirmed=True)
                    except Exception:
                        pass
                threading.Thread(target=do_exit, daemon=True).start()
            elif action == 'toggle_question_bank':
                self.toggle_question_bank()
            elif action == 'start_question_bank':
                self.start_question_bank()
            elif action == 'stop_question_bank':
                self.stop_question_bank()
            elif action == 'clear_question_bank':
                self._clear_question_bank()
            elif action == 'export_question_bank':
                self._export_question_bank()
            elif action == 'import_question_bank':
                self._import_question_bank()
            elif action == 'deduplicate_question_bank':
                ok, msg = self._deduplicate_question_bank()
                r = {'ok': True, 'state': self.get_web_initial_state()}
                r['toast'] = msg
                r['toastType'] = 'success' if ok else 'error'
                return r
            else:
                return {'ok': False, 'message': f'未知操作: {action}'}
        except Exception as exc:
            self._show_error("操作失败", str(exc))
            return {'ok': False, 'message': str(exc)}

        return {'ok': True, 'state': self.get_web_initial_state()}

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
        if script_type == 'yatori':
            self.start_yatori()
            return True
        elif script_type == 'autovisor':
            self.start_autovisor()
            return True
        self.log_system(f"未知核心类型: {script_type}")
        return False

    def _claim_runtime_start(self, script_type):
        """Atomically reserve a runtime start so rapid clicks cannot fork twice."""
        return self._get_process_supervisor().claim_start(script_type)

    def _mark_runtime_running(self, script_type, process):
        self._get_process_supervisor().mark_running(script_type, process)

    def _mark_runtime_stopped(self, script_type, process=None):
        return self._get_process_supervisor().mark_stopped(
            script_type,
            process,
        )

    def start_yatori(self):
        """启动 Yatori"""
        if self.running['yatori'] or self.starting['yatori']:
            self.log_system("Yatori 已经在运行或启动中")
            return

        self.yatori_path = self.find_yatori_path(self.get_base_dir())

        # 检查配置文件
        config_path = os.path.join(self.yatori_path, 'config.yaml')
        if not os.path.exists(config_path):
            self.log_system(f"错误: 未找到配置文件 {config_path}")
            self._show_error("启动失败", "未找到 config.yaml 配置文件\n请使用配置生成器创建配置")
            return

        cmd, entry_path = self._get_yatori_command()
        if not cmd:
            self.log_system("错误: 未找到 Yatori 可执行文件")
            self._show_error("启动失败", "未找到 Yatori 可执行文件")
            return

        # 同步题库 URL 到 Yatori 配置
        self._sync_yatori_question_bank_url()

        if not self._claim_runtime_start('yatori'):
            self.log_system("Yatori 已经在运行或启动中")
            return

        self.log_system("正在启动 Yatori...")
        self.log_system(f"Yatori 入口: {entry_path}")

        def run_yatori():
            try:
                if self.stop_requested.get('yatori'):
                    self.log_system("Yatori 启动已取消")
                    self._mark_runtime_stopped('yatori')
                    return
                # Windows 下创建新进程组，方便后续终止子进程
                creationflags, startupinfo = self._get_subprocess_window_kwargs()

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.yatori_path,
                    text=False,
                    creationflags=creationflags,
                    startupinfo=startupinfo
                )

                self._mark_runtime_running('yatori', process)
                if self.stop_requested.get('yatori'):
                    self._terminate_process_tree(process, "Yatori")

                yatori_encodings = self._build_encoding_candidates(
                    'utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'cp936'
                )
                self._stream_process_output(process, 'yatori', yatori_encodings)

                # 进程结束
                return_code = process.wait()
                was_requested = self.stop_requested.get('yatori', False)
                self._mark_runtime_stopped('yatori', process)
                if return_code:
                    self.log_system(f"Yatori 已退出，返回码: {return_code}")
                else:
                    self.log_system("Yatori 已停止")
                self._handle_runtime_exit('yatori', return_code, was_requested)

            except Exception as e:
                self.log_system(f"Yatori 启动失败: {str(e)}")
                self._mark_runtime_stopped('yatori')
                self._notify_runtime_event("Yatori 启动失败", str(e), error=True)

        # 在后台线程运行
        thread = threading.Thread(target=run_yatori, daemon=True)
        try:
            thread.start()
        except Exception:
            self._mark_runtime_stopped('yatori')
            raise

    def _resolve_base_python(self, venv_exe):
        """从 venv 的 python.exe 路径推断 base Python 路径"""
        try:
            # venv 结构: .venv/Scripts/python.exe → base 在 .venv 的父目录或 pyvenv.cfg 的 home
            scripts_dir = os.path.dirname(venv_exe)
            venv_dir = os.path.dirname(scripts_dir)  # .venv 目录
            parent_dir = os.path.dirname(venv_dir)     # .venv 的父目录

            # 方法1: 读取 pyvenv.cfg 的 home 字段
            cfg_path = os.path.join(venv_dir, 'pyvenv.cfg')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith('home'):
                            home = line.split('=', 1)[1].strip()
                            base = os.path.join(home, 'python.exe')
                            if os.path.exists(base):
                                return base
                            base_scripts = os.path.join(home, 'Scripts', 'python.exe')
                            if os.path.exists(base_scripts):
                                return base_scripts

            # 方法2: 直接去父目录找 python.exe
            for candidate_dir in (parent_dir, venv_dir):
                candidate = os.path.join(candidate_dir, 'python.exe')
                if os.path.exists(candidate) and candidate != venv_exe:
                    return candidate
        except Exception:
            pass
        return None

    def get_python_executable(self):
        """获取 Python 解释器路径"""
        if not getattr(sys, 'frozen', False):
            return sys.executable
        
        # 打包环境下，尝试找到系统 Python
        python_cmd = shutil.which('python') or shutil.which('python3')
        if python_cmd:
            return python_cmd

        # Windows Python Launcher can find registered interpreters that are not
        # present on PATH.
        py_launcher = shutil.which('py')
        if py_launcher:
            for version in ('3.13', '3.12', '3.11', '3.10', '3.9'):
                try:
                    result = subprocess.run(
                        [py_launcher, f'-{version}', '-c', 'import sys; print(sys.executable)'],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    candidate = result.stdout.strip()
                    if result.returncode == 0 and os.path.isfile(candidate):
                        return candidate
                except (OSError, subprocess.SubprocessError):
                    continue
        
        # 如果找不到，尝试常见路径
        user_name = os.getenv('USERNAME', '')
        common_paths = [
            r'C:\Python39\python.exe',
            r'C:\Python310\python.exe',
            r'C:\Python311\python.exe',
            r'C:\Python312\python.exe',
            r'C:\Python313\python.exe',
            os.path.join(r'C:\Users', user_name, r'AppData\Local\Programs\Python\Python39\python.exe'),
            os.path.join(r'C:\Users', user_name, r'AppData\Local\Programs\Python\Python310\python.exe'),
            os.path.join(r'C:\Users', user_name, r'AppData\Local\Programs\Python\Python311\python.exe'),
            os.path.join(r'C:\Users', user_name, r'AppData\Local\Programs\Python\Python312\python.exe'),
            os.path.join(r'C:\Users', user_name, r'AppData\Local\Programs\Python\Python313\python.exe'),
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        return None

    def start_autovisor(self):
        """启动 Autovisor"""
        if self.autovisor_installing:
            self.log_system("Autovisor 依赖安装中，请等待安装完成后自动启动。")
            return

        if self.running['autovisor'] or self.starting['autovisor']:
            self.log_system("Autovisor 已经在运行或启动中")
            return

        self.autovisor_path = self.find_autovisor_path(self.get_base_dir())
        multi_mode = self._get_autovisor_multi_mode()
        script_path, script_name, exact_match, is_executable = self._get_autovisor_entry_path(multi_mode)

        if not script_path:
            self.log_system(f"错误: 未找到 Autovisor 入口文件，目录: {self.autovisor_path}")
            self._show_error("启动失败", "Autovisor 目录中未找到可用入口文件\n请检查 Autovisor 包是否完整")
            return

        # 检查配置文件
        config_path = os.path.join(self.autovisor_path, 'configs.ini')
        if not os.path.exists(config_path):
            self.log_system(f"错误: 未找到配置文件 {config_path}")
            self._show_error("启动失败", "未找到 configs.ini 配置文件\n请使用配置生成器创建配置")
            return

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
                return

            missing_dependencies = self._check_autovisor_dependencies(python_exe)
            if missing_dependencies or runtime_state['needs_playwright_browser']:
                package_names = ", ".join(sorted({item[1] for item in missing_dependencies}))
                self.log_system(f"错误: 当前 Python 环境缺少 Autovisor 依赖: {package_names}")
                for module_name, package_name, error in missing_dependencies:
                    if error:
                        self.log_system(f"依赖检查失败 [{module_name}/{package_name}]: {error}")
                if runtime_state['needs_playwright_browser']:
                    self.log_system("未检测到可用 Chrome/Edge，将自动安装 Playwright Chromium 作为浏览器回退。")
                self._install_autovisor_dependencies_async(
                    python_exe,
                    sorted({item[1] for item in missing_dependencies}),
                    ensure_playwright_browser=runtime_state['needs_playwright_browser']
                )
                return

        if not self._claim_runtime_start('autovisor'):
            self.log_system("Autovisor 已经在运行或启动中")
            return

        self.log_system("正在启动 Autovisor...")
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

        def run_autovisor():
            try:
                # 确保题库服务器已启动
                import time
                if not self.question_bank.running:
                    self.log_system("[Autovisor] 正在启动题库服务器...")
                    self.start_question_bank(silent=True)
                    time.sleep(3)  # 等待服务器完全启动
                else:
                    self.log_system("[Autovisor] 题库服务器已在运行")
                
                # 确保服务器可用
                import socket
                port = self.question_bank.port
                for attempt in range(3):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2)
                    result = sock.connect_ex(('127.0.0.1', port))
                    sock.close()
                    if result == 0:
                        self.log_system(f"[Autovisor] 题库服务器验证成功 (尝试 {attempt+1})")
                        break
                    self.log_system(f"[Autovisor] 端口验证失败 (尝试 {attempt+1}/3)，等待2秒...")
                    time.sleep(2)
                else:
                    self.log_system(f"[Autovisor] 警告: 题库服务器验证失败，错误码 {result}，继续启动...")

                if self.stop_requested.get('autovisor'):
                    self.log_system("Autovisor 启动已取消")
                    self._mark_runtime_stopped('autovisor')
                    return
                
                if is_executable:
                    cmd = [script_path]
                else:
                    # 使用 Python 运行（使用检测到的 Python 路径）
                    cmd = [python_exe, script_path]

                # Windows 下创建新进程组，方便后续终止子进程
                creationflags, startupinfo = self._get_subprocess_window_kwargs()

                env = os.environ.copy()
                qb_url = self.get_question_bank_url()
                if qb_url:
                    env["QB_URL"] = qb_url
                    self.log_system(f"[Autovisor] 设置题库URL: {qb_url}")
                else:
                    self.log_system("[Autovisor] 警告: 题库服务器未启动，使用默认URL")
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.autovisor_path,
                    env=env,
                    text=False,
                    creationflags=creationflags,
                    startupinfo=startupinfo
                )

                self._mark_runtime_running('autovisor', process)
                if self.stop_requested.get('autovisor'):
                    self._terminate_process_tree(process, "Autovisor")

                autovisor_encodings = self._build_encoding_candidates(
                    locale.getpreferredencoding(False), 'utf-8', 'gb18030', 'gbk'
                )
                self._stream_process_output(process, 'autovisor', autovisor_encodings)

                # 进程结束
                return_code = process.wait()
                was_requested = self.stop_requested.get('autovisor', False)
                self._mark_runtime_stopped('autovisor', process)
                if return_code:
                    self.log_system(f"Autovisor 已退出，返回码: {return_code}")
                else:
                    self.log_system("Autovisor 已停止")
                self._handle_runtime_exit('autovisor', return_code, was_requested)

            except Exception as e:
                self.log_system(f"Autovisor 启动失败: {str(e)}")
                self._mark_runtime_stopped('autovisor')
                self._notify_runtime_event("Autovisor 启动失败", str(e), error=True)

        # 在后台线程运行
        thread = threading.Thread(target=run_autovisor, daemon=True)
        try:
            thread.start()
        except Exception:
            self._mark_runtime_stopped('autovisor')
            raise

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
        """Stop the optional practice process and its browser children."""
        process = self.processes.get('practice')
        if process and process.poll() is None:
            self.log_system("正在停止刷题模式...")
            self._terminate_process_tree(process, "刷题模式")
        self.processes['practice'] = None
        self.running['practice'] = False


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

    def get_autovisor_courses_from_web(self, account_index=0):
        """从Web获取Autovisor课程列表 - 运行 fetch_zhs_courses.py"""
        import subprocess
        import os
        import json

        try:
            account_index = normalize_account_index(account_index)
        except CourseCatalogError as e:
            return {'ok': False, 'message': str(e)}
        accounts = self._load_autovisor_config_data().get('accounts') or []
        if account_index >= len(accounts):
            return {'ok': False, 'message': f'智慧树账号索引 {account_index} 不存在'}
        account = accounts[account_index]
        account_number = self._as_int(account.get('account_id'), account_index + 1)
        catalog_account_index = max(account_number - 1, 0)
        self.log_system(f"[课程获取] 正在获取账号配置 {account_number} 的课程...")
        target_username = str(account.get('username', '')).strip()
        if not target_username:
            return {
                'ok': False,
                'message': f'智慧树账号配置 {account_number} 未配置用户名',
            }
        catalog = self._get_course_catalog_service()
        cached = catalog.get_cached('zhs', catalog_account_index, target_username)
        if cached is not None:
            self.log_system("[课程获取] 账号身份匹配，使用30分钟内缓存")
            return cached

        script_path = os.path.join(
            self.get_base_dir(),
            "scripts",
            "fetch_zhs_courses.py",
        )
        if not os.path.exists(script_path):
            return {'ok': False, 'message': f'未找到课程获取脚本: {script_path}'}
        python_exe = self.get_python_executable()
        if not python_exe:
            return {'ok': False, 'message': '未找到 Python 解释器'}

        # 检查并安装playwright依赖
        is_playwright_available, error = self._python_module_available(python_exe, 'playwright')
        if not is_playwright_available:
            self.log_system(f"[课程获取] 检测到缺少playwright依赖，正在安装...")
            try:
                env = os.environ.copy()
                env['PLAYWRIGHT_DOWNLOAD_HOST'] = 'https://npmmirror.com/mirrors/playwright'
                env['PYTHONUNBUFFERED'] = '1'
                
                # 安装playwright包
                install_code = self._run_logged_command(
                    [python_exe, '-m', 'pip', 'install', '--disable-pip-version-check', 
                     '--no-color', '--prefer-binary', '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple',
                     'playwright>=1.52,<2'],
                    self.get_base_dir(),
                    env=env
                )
                if install_code != 0:
                    return {'ok': False, 'message': f'安装playwright失败，返回码: {install_code}'}
                
                # 安装浏览器
                self.log_system("[课程获取] 正在安装Playwright Chromium浏览器...")
                install_code = self._run_logged_command(
                    [python_exe, '-m', 'playwright', 'install', 'chromium'],
                    self.get_base_dir(),
                    env=env
                )
                if install_code != 0:
                    return {'ok': False, 'message': f'安装Chromium浏览器失败，返回码: {install_code}'}
                
                self.log_system("[课程获取] Playwright环境准备完成")
            except Exception as e:
                return {'ok': False, 'message': f'准备Playwright环境失败: {e}'}

        course_file = os.path.join(
            self.get_base_dir(),
            "data",
            "zhs_course.json",
        )
        previous_course_mtime = (
            os.stat(course_file).st_mtime_ns
            if os.path.exists(course_file)
            else None
        )
        try:
            creationflags, startupinfo = self._get_subprocess_window_kwargs()
            env = os.environ.copy()
            env['PLAYWRIGHT_DOWNLOAD_HOST'] = 'https://npmmirror.com/mirrors/playwright'
            process = subprocess.Popen(
                [python_exe, script_path, str(account_number)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.get_base_dir(),
                text=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
                env=env,
            )
            encodings = self._build_encoding_candidates('utf-8', 'gbk')
            buffer = b''
            all_output = []
            while True:
                chunk = process.stdout.read(1)
                if not chunk:
                    break
                buffer += chunk
                if chunk == b'\n':
                    line = self._decode_output_line(buffer, encodings)
                    cleaned = self._clean_log_text(line)
                    if cleaned:
                        self.log_system(f"[课程获取] {cleaned}")
                        all_output.append(cleaned)
                    buffer = b''
            process.wait()
            return_code = process.returncode
            if return_code != 0:
                error_lines = all_output[-10:] if all_output else []
                error_detail = '\n'.join(error_lines) if error_lines else '无输出'
                return {
                    'ok': False, 
                    'message': f'脚本异常退出(返回码:{return_code})\nPython: {python_exe}\n最近输出:\n{error_detail}'
                }
        except Exception as e:
            return {'ok': False, 'message': f'运行脚本失败: {e}'}

        if not os.path.exists(course_file):
            return {'ok': False, 'message': '未找到课程数据文件'}
        if (
            previous_course_mtime is not None
            and os.stat(course_file).st_mtime_ns == previous_course_mtime
        ):
            return {
                'ok': False,
                'message': '课程获取脚本未刷新数据文件，请检查登录或验证状态',
            }

        try:
            with open(course_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            return {'ok': False, 'message': f'读取课程数据失败: {e}'}

        try:
            courses, selected_identity = parse_zhs_course_data(
                data,
                target_username,
            )
        except CourseCatalogError as e:
            return {'ok': False, 'message': str(e)}
        if target_username and not courses and target_username not in data:
            self.log_system(
                f"[课程获取] 未找到账号 {target_username} 的数据，该账号可能未登录过"
            )
        elif selected_identity:
            self.log_system(f"[课程获取] 使用账号 {selected_identity} 的课程数据")
        result = {'ok': True, 'courses': courses}
        try:
            catalog.put_cached(
                'zhs',
                catalog_account_index,
                selected_identity,
                result,
            )
        except Exception as e:
            self.log_system(f"[课程获取] 缓存写入失败: {e}")
        return result

    def get_xuexitong_courses_from_web(self, account_index=0):
        """获取学习通课程列表，使用身份隔离缓存与 HTTPS API。"""
        try:
            account_index = normalize_account_index(account_index)
        except CourseCatalogError as e:
            return {'ok': False, 'message': str(e)}
        yatori_config = self._load_yatori_config_data()
        users = yatori_config.get('users', [])
        if account_index >= len(users):
            return {
                'ok': False,
                'message': f'账号索引 {account_index} 超出范围 (共 {len(users)} 个账号)',
            }
        user = users[account_index]
        account_type = str(user.get('accountType', '')).upper()
        if account_type != 'XUEXITONG':
            return {
                'ok': False,
                'message': f'账号类型 {account_type} 不是学习通，无法获取课程',
            }
        username = str(user.get('account', '')).strip()
        password = str(user.get('password', '')).strip()
        self.log_system(f"[学习通课程] 正在获取账号 {account_index} 的课程...")
        try:
            return self._get_course_catalog_service().get_xuexitong_courses(
                account_index,
                username,
                password,
            )
        except Exception as e:
            return {'ok': False, 'message': f'获取学习通课程失败: {e}'}

    def start_practice_mode_from_web(self):
        """启动刷题模式 - 运行 Practice_Mode.py，日志接入 Autovisor 面板"""
        existing = self.processes.get('practice')
        if existing and existing.poll() is None:
            return {'ok': False, 'message': '刷题模式已经在运行中'}
        self.processes['practice'] = None
        self.running['practice'] = False

        script_path = os.path.join(self.autovisor_path, "Practice_Mode.py")
        if not os.path.exists(script_path):
            self.log_system(f"[刷题模式] 未找到 {script_path}")
            return {'ok': False, 'message': f'未找到刷题模式入口: Autovisor/Practice_Mode.py'}

        python_exe = self.get_python_executable()
        if not python_exe:
            return {'ok': False, 'message': '未找到 Python 解释器'}

        if not self.question_bank.running:
            self.log_system("[刷题模式] 正在启动题库服务器...")
            self.start_question_bank(silent=True)

        self.log_system("[刷题模式] 正在启动刷题模式...")

        try:
            creationflags, startupinfo = self._get_subprocess_window_kwargs()
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'

            process = subprocess.Popen(
                [python_exe, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.autovisor_path,
                text=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
                env=env,
            )

            self.processes['practice'] = process
            self.running['practice'] = True

            practice_encodings = self._build_encoding_candidates(
                locale.getpreferredencoding(False), 'utf-8', 'gb18030', 'gbk'
            )

            def stream_and_wait():
                try:
                    self._stream_process_output(process, 'autovisor', practice_encodings)
                    process.wait()
                finally:
                    if self.processes.get('practice') is process:
                        self.processes['practice'] = None
                    self.running['practice'] = False
                    self.log_system("[刷题模式] 已退出")

            thread = threading.Thread(target=stream_and_wait, daemon=True)
            thread.start()

            self.log_system("[刷题模式] 刷题模式已启动，请在打开的浏览器中操作")
            return {'ok': True, 'message': '刷题模式已启动，系统将自动登录并导航到课程页，请手动点击测验'}
        except Exception as e:
            self.log_system(f"[刷题模式] 启动失败: {e}")
            return {'ok': False, 'message': f'启动刷题模式失败: {e}'}

    def auto_start_question_bank(self):
        return self.question_bank.auto_start_if_enabled()

    def toggle_question_bank(self):
        return self.question_bank.toggle()

    def start_question_bank(self, silent=False):
        return self.question_bank.start(silent=silent)

    def stop_question_bank(self):
        return self.question_bank.stop()

    def _import_question_bank(self):
        """导入题库数据"""
        if not self.question_bank.available:
            self._show_error("导入失败", "题库服务器不可用")
            return
        file_path = ''
        if self.web_window and webview:
            selection = self.web_window.create_file_dialog(
                FD_OPEN,
                file_types=('JSON 文件 (*.json)', '所有文件 (*.*)'),
            )
            if selection:
                file_path = selection[0]
        if not file_path:
            return
        try:
            import sqlite3
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list):
                self._show_error("导入失败", "文件格式错误：需要JSON数组")
                return
            conn = sqlite3.connect(os.path.join(self.get_base_dir(), "data", "题库缓存.db"))
            imported = 0
            for item in data:
                if isinstance(item, dict) and item.get('question') and item.get('answer'):
                    conn.execute(
                        "INSERT OR IGNORE INTO AIResponses (Question, Answer, Options, QuestionType, IsAi) VALUES (?, ?, ?, ?, ?)",
                        (item.get('question'), item.get('answer'), item.get('options'), item.get('type'), item.get('is_ai', 0))
                    )
                    imported += 1
            conn.commit()
            conn.close()
            self.log_system(f"[QB] 成功导入 {imported} 条题目")
            self._show_info("导入成功", f"已导入 {imported} 条题目")
        except Exception as e:
            self.log_system(f"[QB] 导入失败: {e}")
            self._show_error("导入失败", str(e))

    def _export_question_bank(self):
        """导出题库数据"""
        if not self.question_bank.available:
            self._show_error("导出失败", "题库服务器不可用")
            return
        file_path = ''
        if self.web_window and webview:
            selection = self.web_window.create_file_dialog(
                FD_SAVE,
                file_types=('JSON 文件 (*.json)', '所有文件 (*.*)'),
            )
            if selection:
                file_path = selection[0]
                if not file_path.endswith('.json'):
                    file_path += '.json'
        if not file_path:
            return
        try:
            import sqlite3
            conn = sqlite3.connect(os.path.join(self.get_base_dir(), "data", "题库缓存.db"))
            cursor = conn.execute("SELECT Question, Answer, Options, QuestionType, IsAi FROM AIResponses")
            data = []
            for row in cursor.fetchall():
                data.append({
                    'question': row[0],
                    'answer': row[1],
                    'options': row[2],
                    'type': row[3],
                    'is_ai': bool(row[4])
                })
            conn.close()
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log_system(f"[QB] 成功导出 {len(data)} 条题目到 {file_path}")
            self._show_info("导出成功", f"已导出 {len(data)} 条题目")
        except Exception as e:
            self.log_system(f"[QB] 导出失败: {e}")
            self._show_error("导出失败", str(e))

    def _clear_question_bank(self):
        """清空本地题库缓存"""
        try:
            import sqlite3
            db_path = os.path.join(self.get_base_dir(), "data", "题库缓存.db")
            if not os.path.exists(db_path):
                self._show_info("提示", "题库缓存为空，无需清空")
                return
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM AIResponses")
            conn.commit()
            count = conn.total_changes
            conn.close()
            self.log_system(f"[QB] 已清空题库缓存，共删除 {count} 条记录")
            self._show_info("清空成功", f"已清空题库缓存，共删除 {count} 条记录")
        except Exception as e:
            self.log_system(f"[QB] 清空题库缓存失败: {e}")
            self._show_error("清空失败", str(e))

    def _deduplicate_question_bank(self):
        """清理题库中的重复题目，返回 (ok, message)"""
        # 先尝试 HTTP 方式（题库服务器运行中时）
        if self.question_bank.running and self.question_bank.server:
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{getattr(self.question_bank.server, 'port', 8083)}/api/deduplicate",
                    method='POST',
                    data=b'{}',
                    headers={"Content-Type": "application/json"}
                )
                resp = urllib.request.urlopen(req, timeout=30)
                result = json.loads(resp.read().decode('utf-8'))
                if result.get('success'):
                    deleted = result.get('deleted', 0)
                    self.log_system(f"[QB] 去重完成，已清理 {deleted} 条重复记录")
                    self._show_info("去重完成", f"已清理 {deleted} 条重复记录")
                    return True, f"去重完成，已清理 {deleted} 条重复记录"
                else:
                    self._show_error("去重失败", result.get('message', ''))
                    return False, result.get('message', '去重失败')
            except Exception:
                self.log_system("[QB] HTTP 去重失败，回退到直接操作数据库...")

        # 回退：直接操作数据库
        try:
            deleted = self._deduplicate_db_direct()
            if deleted > 0:
                self.log_system(f"[QB] 去重完成，已清理 {deleted} 条重复记录")
                self._show_info("去重完成", f"已清理 {deleted} 条重复记录")
                return True, f"去重完成，已清理 {deleted} 条重复记录"
            else:
                self._show_info("去重完成", "未发现重复记录")
                return True, "未发现重复记录"
        except Exception as e:
            self.log_system(f"[QB] 去重失败: {e}")
            self._show_error("去重失败", str(e))
            return False, str(e)

    def _deduplicate_db_direct(self):
        """无需服务器，直接操作数据库去重"""
        import sqlite3
        import re
        db_path = os.path.join(self.get_base_dir(), "data", "题库缓存.db")
        if not os.path.exists(db_path):
            return 0
        
        def norm(t):
            if not t:
                return ""
            t = t.strip().lower()
            t = re.sub(r'\s+', '', t)
            t = t.replace('\n', '').replace('\r', '')
            t = t.replace('&nbsp;', ' ')
            t = re.sub(r"[，、；：。！？【】《》\"\"''（）…—·]+", '', t)
            return t
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT Id, Question, COALESCE(Options, '') FROM AIResponses ORDER BY CreateTime ASC")
        all_rows = cursor.fetchall()
        
        seen = {}
        duplicate_ids = []
        for row in all_rows:
            row_id = row[0]
            key = (norm(row[1]), norm(row[2]))
            if key in seen:
                duplicate_ids.append(row_id)
            else:
                seen[key] = row_id
        
        deleted = 0
        for dup_id in duplicate_ids:
            cursor.execute("DELETE FROM AIResponses WHERE Id = ?", (dup_id,))
            deleted += 1
        conn.commit()
        conn.close()
        return deleted

    # ==== ZError 内置数据库检测 ====

    def _detect_zerror_db_path(self):
        """自动检测 ZError 数据库路径"""
        candidates = []
        try:
            username = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
            if not username:
                userprofile = os.environ.get("USERPROFILE", "")
                if userprofile:
                    username = os.path.basename(userprofile)
            if username:
                candidates.append(
                    os.path.join("C:\\Users", username, "AppData", "Local", "ZError", "airesponses.db")
                )
        except Exception:
            pass
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            candidates.append(os.path.join(local_appdata, "ZError", "airesponses.db"))
        candidates.append(os.path.join(self.get_base_dir(), "airesponses.db"))
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _is_zerror_db_available(self):
        return bool(self._detect_zerror_db_path())

    def _get_zerror_db_info(self):
        path = self._detect_zerror_db_path()
        if not path:
            return ""
        try:
            import sqlite3
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM AIResponses").fetchone()[0]
            conn.close()
            return f"{count} 条记录 ({path})"
        except Exception:
            return f"已检测 ({path})"

    def _configure_zerror_db_env(self):
        """设置环境变量让 题库服务器.py 读取 ZError 数据库"""
        path = self._detect_zerror_db_path()
        if path:
            os.environ["ZERROR_DB_PATH"] = path
            if not hasattr(self, '_zerror_db_logged') or not self._zerror_db_logged:
                try:
                    import sqlite3
                    conn = sqlite3.connect(path)
                    count = conn.execute("SELECT COUNT(*) FROM AIResponses").fetchone()[0]
                    conn.close()
                    self.log_system(f"📦 检测到 ZError 题库数据库: {count} 条记录")
                except Exception:
                    self.log_system(f"📦 检测到 ZError 题库数据库: {path}")
                self._zerror_db_logged = True
        else:
            if not hasattr(self, '_zerror_db_logged') or not self._zerror_db_logged:
                self.log_system("⚠️ 未检测到 ZError 题库数据库，将使用本地缓存")
                self._zerror_db_logged = True

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
        if not self.question_bank.running and self.question_bank.available:
            self.start_question_bank(silent=True)
        if not self.running['yatori']:
            self.start_yatori()
        if not self.running['autovisor']:
            self.start_autovisor()

    def stop_all(self):
        """停止所有脚本"""
        self.log_system("正在停止所有运行中的脚本...")
        self.stop_yatori()
        self.stop_autovisor()
        self.stop_practice_mode()

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
        else:
            path = self.autovisor_path

        if os.path.exists(path):
            os.startfile(path)
        else:
            self._show_error("错误", f"目录不存在: {path}")

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
                return
        self._show_error("错误", "未找到配置生成器页面")

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

        if any(self.running.get(name) for name in ('yatori', 'autovisor', 'practice')):
            if not confirmed:
                self.log_system("有核心任务正在运行，取消未确认的退出请求。")
                return
            self.stop_all()
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
