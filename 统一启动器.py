# -*- coding: utf-8 -*-
"""
统一启动器 - Yatori & Autovisor 启动管理工具
支持同时启动智慧树(Autovisor)和非智慧树(Yatori)脚本
支持自动下载和更新核心
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import threading
import os
import sys
import locale
import glob
import re
import shutil
import configparser
import io
import json
import urllib.request
from datetime import datetime
import queue

from src.atomic_io import atomic_dump_json, atomic_write_text
from src.dependencies import ensure_core_dependencies
from src.launcher_api import WebLauncherAPI
try:
    import yaml
except ImportError:
    yaml = None

try:
    from src.题库服务器 import (
        QuestionBankServer,
        configure_ai as qb_configure_ai,
        configure_ai_models as qb_configure_ai_models,
        configure_match as qb_configure_match,
        configure_auto_save as qb_configure_auto_save,
        configure_remote_zerror as qb_configure_remote,
        get_match_config as qb_get_match_config,
        get_ai_config as qb_get_ai_config,
        get_auto_save as qb_get_auto_save,
        get_remote_zerror_config as qb_get_remote_config,
    )
    QB_AVAILABLE = True
except ImportError:
    QB_AVAILABLE = False
    QuestionBankServer = None
    qb_configure_ai = lambda **kw: None
    qb_configure_ai_models = lambda **kw: None
    qb_configure_match = lambda **kw: None
    qb_configure_auto_save = lambda **kw: None
    qb_configure_remote = lambda **kw: None
    qb_get_match_config = lambda: {}
    qb_get_ai_config = lambda: {}
    qb_get_auto_save = lambda: True
    qb_get_remote_config = lambda: {}

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
    YATORI_ACCOUNT_TYPES = (
        'XUEXITONG',
        'YINGHUA',
        'CANGHUI',
        'ENAEA',
        'CQIE',
        'KETANGX',
        'ICVE',
        'QSXT',
        'WELEARN',
        'HQKJ',
    )
    # Per-platform supported modes (aligned with Yatori core v2.6.2-beta.8)
    YATORI_PLATFORM_MODE_RULES = {
        'XUEXITONG': {'videoModes': ('0', '1', '2', '3'), 'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': False},
        'YINGHUA':   {'videoModes': ('0', '1', '2'),    'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': True},
        'CANGHUI':   {'videoModes': ('0', '1'),         'examModes': ('0',),         'submitModes': ('0',),     'requireUrl': False},
        'ENAEA':     {'videoModes': ('0', '1', '2', '3'), 'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': False},
        'CQIE':      {'videoModes': ('0', '1', '2'),    'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': False},
        'KETANGX':   {'videoModes': ('0', '1'),         'examModes': ('0',),         'submitModes': ('0',),     'requireUrl': False},
        'ICVE':      {'videoModes': ('0', '1', '2'),    'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': False},
        'QSXT':      {'videoModes': ('0', '1', '2'),    'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': False},
        'WELEARN':   {'videoModes': ('0', '1', '2'),    'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': False},
        'HQKJ':      {'videoModes': ('0', '1', '2'),    'examModes': ('0', '1', '2'), 'submitModes': ('0', '1'), 'requireUrl': True},
    }
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

    def _build_encoding_candidates(self, *preferred):
        candidates = list(preferred)
        candidates.extend(
            [
                locale.getpreferredencoding(False),
                getattr(sys.stdout, "encoding", None),
                "utf-8-sig",
                "utf-8",
                "gb18030",
                "gbk",
                "cp936",
            ]
        )

        unique = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            normalized = candidate.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(candidate)
        return unique

    def _decode_output_line(self, raw_line, encodings):
        for encoding in encodings:
            try:
                return raw_line.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_line.decode(encodings[0], errors='replace')

    def _clean_log_text(self, text):
        text = self.ANSI_ESCAPE_RE.sub('', text)
        text = text.replace('\t', ' ')
        text = text.replace('\r', '')
        return text.rstrip('\r\n ')

    def _normalize_progress_log(self, text):
        if '%' not in text or '|' not in text:
            return text

        match = self.PROGRESS_LINE_RE.match(text.strip())
        if not match:
            return text

        desc = ' '.join(match.group('desc').split())
        percent = match.group('percent')
        suffix = ' '.join(match.group('suffix').split())
        if suffix:
            return f"{desc} {percent} | {suffix}"
        return f"{desc} {percent}"

    def _is_progress_log(self, text):
        normalized = self._normalize_progress_log(text)
        return normalized != text or ('%' in text and '进度' in text)

    def _get_subprocess_window_kwargs(self):
        creationflags = 0
        startupinfo = None
        if sys.platform == 'win32':
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            creationflags |= getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            creationflags |= getattr(subprocess, 'DETACHED_PROCESS', 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        return creationflags, startupinfo

    def _stream_process_output(self, process, source, encodings):
        if not process or not process.stdout:
            return

        buffer = b''
        while True:
            raw_chunk = process.stdout.read(1)
            if not raw_chunk:
                break

            if isinstance(raw_chunk, str):
                raw_chunk = raw_chunk.encode(encodings[0], errors='replace')

            if raw_chunk in (b'\r', b'\n'):
                if buffer:
                    line = self._decode_output_line(buffer, encodings)
                    line = self._clean_log_text(line)
                    if line:
                        self.log(source, line, replace_last=self._is_progress_log(line))
                    buffer = b''
                continue

            buffer += raw_chunk

        if buffer:
            line = self._decode_output_line(buffer, encodings)
            line = self._clean_log_text(line)
            if line:
                self.log(source, line, replace_last=self._is_progress_log(line))

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
        for encoding in ('utf-8', 'utf-8-sig', 'gbk', 'gb18030'):
            parser = configparser.ConfigParser()
            parser.optionxform = str
            try:
                with open(config_path, 'r', encoding=encoding) as handle:
                    parser.read_file(handle)
                return parser
            except (UnicodeDecodeError, configparser.Error):
                continue
        parser = configparser.ConfigParser()
        parser.optionxform = str
        with open(config_path, 'r', encoding='utf-8', errors='replace') as handle:
            parser.read_file(handle)
        return parser

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
        creationflags, startupinfo = self._get_subprocess_window_kwargs()

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=False,
            creationflags=creationflags,
            startupinfo=startupinfo,
            env=env,
        )
        self._stream_process_output(
            process,
            source,
            self._build_encoding_candidates(locale.getpreferredencoding(False), 'utf-8', 'gb18030', 'gbk')
        )
        return process.wait()

    def _terminate_process_tree(self, process, label):
        if not process:
            return

        pid = getattr(process, 'pid', None)
        if pid is None:
            return

        if sys.platform == 'win32':
            creationflags, startupinfo = self._get_subprocess_window_kwargs()
            try:
                subprocess.run(
                    ['taskkill', '/PID', str(pid), '/T', '/F'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
                return
            except Exception as exc:
                self.log_system(f"{label} 进程树终止失败，回退到普通终止: {exc}")

        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

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
        return {
            'accountType': 'XUEXITONG',
            'url': '',
            'remarkName': f'账号{index}' if index else '',
            'account': '',
            'password': '',
            'isProxy': 0,
            'informEmails': [],
            'coursesCustom': {
                'studyTime': '',
                'cxNode': 3,
                'cxChapterTestSw': 1,
                'cxWorkSw': 1,
                'cxExamSw': 1,
                'shuffleSw': 0,
                'videoModel': 1,
                'autoExam': 0,
                'examAutoSubmit': 0,
                'includeCourses': [],
                'excludeCourses': [],
                'coursesSettings': [],
            },
        }

    def _default_yatori_config(self):
        return {
            'setting': {
                'basicSetting': {
                    'completionTone': 1,
                    'colorLog': 1,
                    'logOutFileSw': 1,
                    'logLevel': 'INFO',
                    'logModel': 0,
                    'WebModel': 0,
                },
                'emailInform': {
                    'sw': 0,
                    'SMTPHost': '',
                    'SMTPPort': 0,
                    'userName': '',
                    'password': '',
                },
                'aiSetting': {
                    'aiType': 'TONGYI',
                    'aiUrl': '',
                    'model': '',
                    'API_KEY': '',
                },
                'apiQueSetting': {
                    'url': 'http://127.0.0.1:8083/query',
                },
            },
            'users': [self._default_yatori_user(1)],
        }

    def _default_autovisor_account(self, index=1):
        return {
            'name': f'账号 {index}',
            'username': '',
            'password': '',
            'driver': 'Chrome',
            'exe_path': self._find_browser_executable('chrome') or self._find_browser_executable('edge') or '',
            'enable_auto_captcha': True,
            'enable_hide_window': False,
            'limit_max_time': '30',
            'limit_speed': '1.0',
            'sound_off': True,
            'course_urls': [],
        }

    def _get_yatori_config_path(self):
        self.yatori_path = self.find_yatori_path(self.get_base_dir())
        return os.path.join(self.yatori_path, 'config.yaml')

    def _get_autovisor_config_path(self):
        self.autovisor_path = self.find_autovisor_path(self.get_base_dir())
        return os.path.join(self.autovisor_path, 'configs.ini')

    def _as_int(self, value, default=0):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    def _as_float(self, value, default=0.0):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return default

    def _normalize_autovisor_speed(self, value, default='1.0'):
        normalized = str(value or '').strip()
        return normalized if normalized in self.AUTOVISOR_SPEED_OPTIONS else default

    def _validate_autovisor_accounts(self, accounts):
        for index, account in enumerate(accounts or [], start=1):
            if account.get('enable_hide_window') and (not account.get('username', '').strip() or not account.get('password', '')):
                return f"Autovisor 账号 {index} 开启隐藏窗口时必须填写账号和密码"
        return None

    def _split_lines(self, value):
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [line.strip() for line in str(value or '').replace('\r', '').split('\n') if line.strip()]

    def _split_csv(self, value):
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value or '').split(',') if item.strip()]

    def _read_text_widget(self, widget):
        return widget.get('1.0', tk.END).strip() if widget else ''

    def _collect_yatori_accounts_form_data(self):
        collected = []
        for index, form in enumerate(getattr(self, 'yatori_account_forms', []), start=1):
            collected.append(
                {
                    'accountType': form['account_type'].get().strip() or 'XUEXITONG',
                    'url': form['site_url'].get().strip(),
                    'remarkName': form['remark_name'].get().strip() or f'账号{index}',
                    'account': form['account'].get().strip(),
                    'password': form['password'].get(),
                    'isProxy': 1 if form['use_proxy'].get() else 0,
                    'informEmails': self._split_csv(form['inform_emails'].get()),
                    'coursesCustom': {
                        'studyTime': form['study_time'].get().strip(),
                        'cxNode': self._as_int(form['cx_node'].get(), 3),
                        'cxChapterTestSw': 1 if form['cx_chapter_test'].get() else 0,
                        'cxWorkSw': 1 if form['cx_work'].get() else 0,
                        'cxExamSw': 1 if form['cx_exam'].get() else 0,
                        'shuffleSw': 1 if form['shuffle'].get() else 0,
                        'videoModel': self._as_int(form['video_model'].get(), 1),
                        'autoExam': self._as_int(form['auto_exam'].get(), 0),
                        'examAutoSubmit': self._as_int(form['auto_submit'].get(), 0),
                        'includeCourses': self._split_lines(self._read_text_widget(form['include_courses'])),
                        'excludeCourses': self._split_lines(self._read_text_widget(form['exclude_courses'])),
                    },
                }
            )
        return collected or [self._default_yatori_user(1)]

    def _collect_autovisor_accounts_form_data(self):
        collected = []
        default_driver = getattr(self, 'autovisor_browser_driver_var', None)
        default_path = getattr(self, 'autovisor_browser_path_var', None)
        global_driver = (default_driver.get().strip() if default_driver else 'Chrome') or 'Chrome'
        global_path = (default_path.get().strip() if default_path else '')
        for index, form in enumerate(getattr(self, 'autovisor_account_forms', []), start=1):
            per_driver = form['driver'].get().strip()
            per_path = form['exe_path'].get().strip()
            # Use per-account values if explicitly set; otherwise fall back to global
            if per_driver and per_driver != global_driver:
                driver = per_driver
            else:
                driver = global_driver
            if per_path and per_path != global_path:
                exe_path = per_path
            else:
                exe_path = global_path if not per_path else per_path
            collected.append(
                {
                    'name': form['name'].get().strip() or f'账号 {index}',
                    'username': form['username'].get().strip(),
                    'password': form['password'].get(),
                    'driver': driver,
                    'exe_path': exe_path,
                    'enable_auto_captcha': bool(form['enable_auto_captcha'].get()),
                    'enable_hide_window': bool(form['enable_hide_window'].get()),
                    'limit_max_time': form['limit_max_time'].get().strip() or '30',
                    'limit_speed': self._normalize_autovisor_speed(form['limit_speed'].get(), '1.0'),
                    'sound_off': bool(form['sound_off'].get()),
                    'course_urls': self._split_lines(self._read_text_widget(form['course_urls'])),
                }
            )
        return collected or [self._default_autovisor_account(1)]

    def _load_yatori_config_data(self):
        config = self._default_yatori_config()
        config_path = self._get_yatori_config_path()
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as handle:
                    loaded = yaml.safe_load(handle) or {}
                if isinstance(loaded, dict):
                    # Merge all keys from loaded config, preserving unknown keys
                    for key, value in loaded.items():
                        if key in config and isinstance(config[key], dict) and isinstance(value, dict):
                            config[key].update(value)
                        else:
                            config[key] = value
            except Exception as exc:
                self.log_system(f"读取 Yatori 配置失败，已回退默认值: {exc}")

        setting = config.setdefault('setting', {})
        basic = setting.setdefault('basicSetting', {})
        basic_defaults = self._default_yatori_config()['setting']['basicSetting']
        for key, value in basic_defaults.items():
            basic.setdefault(key, value)

        email = setting.setdefault('emailInform', {})
        for key, value in self._default_yatori_config()['setting']['emailInform'].items():
            email.setdefault(key, value)

        ai = setting.setdefault('aiSetting', {})
        for key, value in self._default_yatori_config()['setting']['aiSetting'].items():
            ai.setdefault(key, value)

        api = setting.setdefault('apiQueSetting', {})
        api.setdefault('url', 'http://127.0.0.1:8083/query')

        users = config.get('users') if isinstance(config.get('users'), list) else []
        normalized_users = []
        default_user = self._default_yatori_user(0)
        for index, user in enumerate(users, start=1):
            if not isinstance(user, dict):
                continue
            user_data = dict(default_user)
            user_data['remarkName'] = user.get('remarkName', f'账号{index}')
            # Merge all top-level user fields from loaded config
            for key, value in user.items():
                if key == 'coursesCustom':
                    continue
                user_data[key] = value
            # Deep-merge coursesCustom
            courses_custom = dict(default_user['coursesCustom'])
            loaded_cc = user.get('coursesCustom') if isinstance(user.get('coursesCustom'), dict) else {}
            courses_custom.update(loaded_cc)
            user_data['coursesCustom'] = courses_custom
            normalized_users.append(user_data)
        config['users'] = normalized_users or [self._default_yatori_user(1)]
        return config

    def _save_yatori_config_data(self, config_data):
        config_path = self._get_yatori_config_path()
        buffer = io.StringIO()
        yaml.safe_dump(config_data, buffer, allow_unicode=True, sort_keys=False)
        atomic_write_text(config_path, buffer.getvalue())

    def _extract_autovisor_index(self, section_name, prefix):
        if section_name == prefix:
            return 1
        if section_name.startswith(prefix + '-'):
            suffix = section_name[len(prefix) + 1:]
            if suffix.isdigit():
                return int(suffix)
        return None

    def _sorted_url_keys(self, option_names):
        def sort_key(name):
            match = re.search(r'(\d+)', name or '')
            return (0, int(match.group(1))) if match else (1, name)

        return sorted(option_names, key=sort_key)

    def _load_autovisor_config_data(self):
        parser = configparser.ConfigParser()
        parser.optionxform = str
        config_path = self._get_autovisor_config_path()
        indices = set()
        if os.path.exists(config_path):
            try:
                parser = self._read_autovisor_config(config_path)
            except Exception as exc:
                self.log_system(f"读取 Autovisor 配置失败，已回退默认值: {exc}")
                parser = configparser.ConfigParser()
                parser.optionxform = str

        section_prefixes = ('user-account', 'browser-option', 'script-option', 'course-option', 'course-url')
        for section in parser.sections():
            for prefix in section_prefixes:
                index = self._extract_autovisor_index(section, prefix)
                if index:
                    indices.add(index)
                    break

        if not indices:
            indices = {1}

        accounts = []
        raw_sections = set(parser.sections())
        for index in sorted(indices):
            suffixed = any(section.endswith(f'-{index}') for section in raw_sections)
            suffix = f'-{index}' if index > 1 or suffixed else ''
            user_section = f'user-account{suffix}'
            browser_section = f'browser-option{suffix}'
            script_section = f'script-option{suffix}'
            course_section = f'course-option{suffix}'
            url_section = f'course-url{suffix}'
            account = self._default_autovisor_account(index)
            if parser.has_section(user_section):
                account['name'] = parser.get(user_section, 'name', fallback=account['name']).strip() or account['name']
                account['username'] = parser.get(user_section, 'username', fallback=account['username']).strip()
                account['password'] = parser.get(user_section, 'password', fallback=account['password'])
            if parser.has_section(browser_section):
                account['driver'] = parser.get(browser_section, 'driver', fallback=account['driver']).strip() or account['driver']
                account['exe_path'] = parser.get(browser_section, 'EXE_PATH', fallback=account['exe_path']).strip()
            if parser.has_section(script_section):
                account['enable_auto_captcha'] = parser.getboolean(script_section, 'enableAutoCaptcha', fallback=account['enable_auto_captcha'])
                account['enable_hide_window'] = parser.getboolean(script_section, 'enableHideWindow', fallback=account['enable_hide_window'])
            if parser.has_section(course_section):
                account['limit_max_time'] = parser.get(course_section, 'limitMaxTime', fallback=account['limit_max_time']).strip()
                account['limit_speed'] = parser.get(course_section, 'limitSpeed', fallback=account['limit_speed']).strip()
                account['sound_off'] = parser.getboolean(course_section, 'soundOff', fallback=account['sound_off'])
            if parser.has_section(url_section):
                course_urls = []
                for option_name in self._sorted_url_keys(parser.options(url_section)):
                    value = parser.get(url_section, option_name, fallback='').strip()
                    if value:
                        course_urls.append(value)
                account['course_urls'] = course_urls
            accounts.append(account)

        return {
            'multi_mode': len(accounts) > 1,
            'browser_driver': accounts[0]['driver'] if accounts else 'Chrome',
            'browser_path': accounts[0]['exe_path'] if accounts else '',
            'accounts': accounts or [self._default_autovisor_account(1)],
        }

    def _save_autovisor_config_data(self, config_data):
        config_path = self._get_autovisor_config_path()
        if os.path.exists(config_path):
            try:
                parser = self._read_autovisor_config(config_path)
            except Exception:
                parser = configparser.ConfigParser()
                parser.optionxform = str
        else:
            parser = configparser.ConfigParser()
            parser.optionxform = str

        for section in list(parser.sections()):
            if any(
                section == prefix or section.startswith(prefix + '-')
                for prefix in ('user-account', 'browser-option', 'script-option', 'course-option', 'course-url')
            ):
                parser.remove_section(section)

        accounts = config_data.get('accounts') or [self._default_autovisor_account(1)]
        multi_mode = bool(config_data.get('multi_mode'))
        shared_driver = config_data.get('browser_driver', 'Chrome') or 'Chrome'
        shared_path = config_data.get('browser_path', '').strip()

        for index, account in enumerate(accounts, start=1):
            suffix = '' if index == 1 else f'-{index}'
            user_section = f'user-account{suffix}'
            browser_section = f'browser-option{suffix}'
            script_section = f'script-option{suffix}'
            course_section = f'course-option{suffix}'
            url_section = f'course-url{suffix}'

            parser.add_section(user_section)
            parser.set(user_section, 'name', account.get('name', f'账号 {index}').strip() or f'账号 {index}')
            parser.set(user_section, 'username', account.get('username', '').strip())
            parser.set(user_section, 'password', account.get('password', ''))

            parser.add_section(browser_section)
            parser.set(browser_section, 'driver', shared_driver)
            parser.set(browser_section, 'EXE_PATH', shared_path)

            parser.add_section(script_section)
            parser.set(script_section, 'enableAutoCaptcha', 'True' if account.get('enable_auto_captcha', True) else 'False')
            parser.set(script_section, 'enableHideWindow', 'True' if account.get('enable_hide_window', False) else 'False')

            parser.add_section(course_section)
            parser.set(course_section, 'limitMaxTime', str(self._as_int(account.get('limit_max_time', '30'), 30)))
            parser.set(course_section, 'limitSpeed', str(self._as_float(account.get('limit_speed', '1.0'), 1.0)))
            parser.set(course_section, 'soundOff', 'True' if account.get('sound_off', True) else 'False')

            parser.add_section(url_section)
            course_urls = self._split_lines(account.get('course_urls', []))
            if not course_urls:
                parser.set(url_section, 'URL1', '')
            else:
                for url_index, course_url in enumerate(course_urls, start=1):
                    parser.set(url_section, f'URL{url_index}', course_url)

        buffer = io.StringIO()
        parser.write(buffer)
        atomic_write_text(config_path, buffer.getvalue())

    def _core_manager_log(self, message):
        """核心管理器日志回调"""
        self.log_system(f"[核心] {message}")

    def auto_check_cores(self):
        """自动检查核心安装情况和更新"""
        if not self.core_manager:
            return
        
        # 检查 Yatori
        yatori_installed = self.core_manager.check_yatori_installed()
        
        if not yatori_installed:
            # Yatori 未安装，提示下载
            self.log_system("⚠️ 未检测到 Yatori 核心，准备自动下载...")
            self.show_yatori_install_dialog()
        else:
            # Yatori 已安装，检查更新
            self.log_system("正在检查 Yatori 更新...")
            self.check_yatori_update_async()

    def __init__(self, root, ui_mode='tk'):
        self.root = root
        self.ui_mode = ui_mode
        self.web_window = None
        self._allow_webview_close = False
        self.root.title("Yatori & Autovisor 统一启动器")
        if self.ui_mode == 'tk':
            self.root.geometry("1450x940")
            self.root.minsize(1180, 760)

        self.colors = {
            'bg': '#f5f5f7',
            'sidebar': '#1d1d1f',
            'surface': '#ffffff',
            'card': '#ffffff',
            'surface_alt': '#f5f5f7',
            'surface_dark': '#272729',
            'surface_dark_2': '#2a2a2c',
            'surface_black': '#000000',
            'primary': '#0066cc',
            'primary_hover': '#0052b3',
            'primary_focus': '#0071e3',
            'primary_on_dark': '#2997ff',
            'primary_soft': '#e8f0fe',
            'text': '#1d1d1f',
            'text_muted': '#86868b',
            'text_on_dark': '#ffffff',
            'text_on_dark_muted': '#cccccc',
            'border': '#d2d2d7',
            'hairline': '#e0e0e0',
            'divider_soft': '#f0f0f0',
            'surface_pearl': '#fafafc',
            'success': '#34c759',
            'danger': '#ff3b30',
            'warning': '#ff9500',
            'console': '#1d1d1f',
            'console_text': '#f5f5f7',
        }

        self.processes = {
            'yatori': None,
            'autovisor': None,
            'practice': None,
        }
        self.log_queues = {
            'yatori': queue.Queue(),
            'autovisor': queue.Queue(),
            'system': queue.Queue(),
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
        self.update_info = None
        self.autovisor_update_info = None
        self.is_updating = False
        self.autovisor_installing = False
        self.autovisor_version_checking = False
        self._shutdown_pending = False

        # 题库服务器状态（内部用纯 Python 值，tk 模式下额外创建 tk 变量绑定 UI）
        self.qb_server = None
        self._qb_port = 8083
        self.qb_running = False
        self._qb_auto_start = True
        self.qb_port = None
        self.qb_auto_start = None
        self._qb_ai_enabled = True
        self._qb_ai_url = ""
        self._qb_ai_model = ""
        self._qb_ai_api_key = ""
        self._qb_ai_type = "OPENAI"
        self.qb_ai_enabled_var = None
        self.qb_ai_url_var = None
        self.qb_ai_model_var = None
        self.qb_ai_api_key_var = None
        self.qb_ai_type_var = None
        self._qb_ai_models = []  # 多模型配置: [{"type":"OPENAI","url":"","model":"","api_key":""}]
        self._qb_ai_concurrent = True
        self.qb_ai_concurrent_var = None
        self.qb_ai_model2_type_var = None
        self.qb_ai_model2_url_var = None
        self.qb_ai_model2_name_var = None
        self.qb_ai_model2_key_var = None
        self._qb_remote_url = ""
        self._qb_remote_token = ""
        self.qb_remote_url_var = None
        self.qb_remote_token_var = None
        self._qb_match_threshold = 0.72
        self._qb_match_coverage = 0.75
        self._qb_match_fastpass = 0.85
        self._qb_match_title_weight = 0.7
        self._qb_match_options_weight = 0.3
        self.qb_match_threshold_var = None
        self.qb_match_coverage_var = None
        self.qb_match_fastpass_var = None
        self.qb_match_title_weight_var = None
        self.qb_match_options_weight_var = None
        self._qb_auto_save = True
        self.qb_auto_save_var = None
        if QB_AVAILABLE:
            self.log_system("[QB] 题库服务器模块已加载")
        else:
            self.log_system("[QB] 题库服务器模块未找到，请确保 题库服务器.py 在同目录下")

        self._load_qb_settings()

        self.current_view = 'dashboard'
        self.current_settings_tab = 'yatori'
        self.view_frames = {}
        self.nav_buttons = {}
        self.settings_tab_buttons = {}
        self.yatori_account_forms = []
        self.autovisor_account_forms = []
        initial_autovisor_config = self._load_autovisor_config_data()
        self.autovisor_multi_mode = len(initial_autovisor_config.get('accounts', [])) > 1
        self.autovisor_multi_var = (
            tk.BooleanVar(value=self.autovisor_multi_mode) if self.ui_mode == 'tk' else None
        )
        if CoreManager:
            self.core_manager = CoreManager(base_dir, log_callback=self._core_manager_log)

        self.log_system("统一启动器已就绪")
        self._refresh_runtime_summary()
        if self.ui_mode == 'tk':
            self._init_apple_ui()
        self._after(600, self.auto_start_question_bank)
        self._after(1000, self.auto_check_cores)
        self._after(1400, self._apply_auto_run_preference)

    def _after(self, delay_ms, callback):
        if self.ui_mode == 'tk' and self.root:
            self.root.after(delay_ms, callback)
            return

        delay_seconds = max(delay_ms, 0) / 1000.0
        if delay_seconds <= 0:
            callback()
            return

        timer = threading.Timer(delay_seconds, callback)
        timer.daemon = True
        timer.start()

    def _get_autovisor_multi_mode(self):
        if self.ui_mode == 'tk' and self.autovisor_multi_var is not None:
            try:
                value = bool(self.autovisor_multi_var.get())
                self.autovisor_multi_mode = value
                return value
            except RuntimeError:
                pass
        return bool(getattr(self, 'autovisor_multi_mode', True))

    def _set_autovisor_multi_mode(self, value):
        normalized = bool(value)
        self.autovisor_multi_mode = normalized
        if self.ui_mode == 'tk' and self.autovisor_multi_var is not None:
            self.autovisor_multi_var.set(normalized)

    def attach_web_window(self, window):
        self.web_window = window

        if self.ui_mode == 'web' and self.web_window:
            self.web_window.events.closing += self._handle_web_window_closing
            self._apply_window_preferences(initial=True)

    def _request_web_exit_confirmation(self):
        """请求Web端显示退出确认对话框，使用非阻塞方式"""
        if not (self.ui_mode == 'web' and self.web_window):
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
        if self.ui_mode == 'tk':
            try:
                if self.root:
                    self.root.quit()
                    self.root.destroy()
            except Exception:
                pass
            return

        if self.web_window:
            self._allow_webview_close = True
            self.web_window.destroy()

    def _show_error(self, title, message):
        if self.ui_mode == 'tk':
            messagebox.showerror(title, message)
        else:
            self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")

    def _show_info(self, title, message):
        if self.ui_mode == 'tk':
            messagebox.showinfo(title, message)
        else:
            self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")

    def _show_warning(self, title, message):
        if self.ui_mode == 'tk':
            messagebox.showwarning(title, message)
        else:
            self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")

    def _ask_yes_no(self, title, message, default=True):
        if self.ui_mode == 'tk':
            return messagebox.askyesno(title, message)
        self.log_system(f"[{title}] {str(message).replace(chr(10), ' | ')}")
        return default

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

        if self.ui_mode == 'tk' and self.root:
            try:
                self.root.attributes('-topmost', always_on_top)
            except Exception as exc:
                self.log_system(f"应用置顶设置失败: {exc}")
            if start_minimized:
                self._after(300, self._minimize_main_window)
            elif initial:
                self.root.deiconify()
            return

        if self.ui_mode == 'web' and self.web_window:
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
            if self.ui_mode == 'tk' and self.root:
                self.root.iconify()
            elif self.ui_mode == 'web' and self.web_window and hasattr(self.web_window, 'minimize'):
                self.web_window.minimize()
            elif self.ui_mode == 'web' and self.web_window and hasattr(self.web_window, 'hide'):
                self.web_window.hide()
            else:
                self.log_system("当前窗口后端不支持自动最小化。")
        except Exception as exc:
            self.log_system(f"最小化窗口失败: {exc}")

    def _save_window_geometry_preference(self):
        if not self._preference_enabled('rememberGeometry'):
            return
        if self.ui_mode == 'tk' and self.root:
            try:
                self.web_preferences['windowGeometry'] = self.root.geometry()
                self._save_web_preferences()
            except Exception as exc:
                self.log_system(f"保存窗口位置失败: {exc}")

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
            'qb_running': self.qb_running,
            'qb_port': self._qb_port,
            'qb_stats': self.qb_server.get_stats() if self.qb_running and self.qb_server else None,
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
        if self.ui_mode == 'web' and self.web_window and webview:
            selection = self.web_window.create_file_dialog(
                FD_OPEN,
                file_types=('Executable Files (*.exe)', 'All Files (*.*)'),
            )
            if selection:
                file_path = selection[0]
        else:
            file_path = filedialog.askopenfilename(
                title="Select Browser Executable",
                filetypes=[("Executable", "*.exe"), ("All Files", "*.*")],
            )
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
                self.show_update_dialog()
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

    # ========== TK UI methods removed - using webview only ==========
    def _init_apple_ui(self):
        """初始化 Apple 风格 UI 布局"""
        self.root.configure(bg=self.colors['bg'])

        style = ttk.Style()
        style.theme_use('clam')

        style.configure('Apple.TFrame', background=self.colors['bg'])
        style.configure('AppleNav.TFrame', background=self.colors['sidebar'])
        style.configure('AppleContent.TFrame', background=self.colors['bg'])

        style.configure(
            'ApplePill.TButton',
            background=self.colors['primary'],
            foreground='#ffffff',
            borderwidth=0,
            focuscolor='none',
            font=('Microsoft YaHei', 11),
            padding=(22, 8),
        )
        style.map('ApplePill.TButton',
                  background=[('active', self.colors['primary_hover']), ('disabled', self.colors['border'])],
                  foreground=[('disabled', self.colors['text_muted'])])

        style.configure(
            'ApplePillOutline.TButton',
            background='transparent',
            foreground=self.colors['primary'],
            borderwidth=1,
            bordercolor=self.colors['primary'],
            focuscolor='none',
            font=('Microsoft YaHei', 11),
            padding=(22, 8),
        )
        style.map('ApplePillOutline.TButton',
                  background=[('active', self.colors['primary_soft'])],
                  foreground=[('disabled', self.colors['text_muted'])])

        style.configure(
            'AppleGhost.TButton',
            background='transparent',
            foreground=self.colors['primary'],
            borderwidth=0,
            focuscolor='none',
            font=('Microsoft YaHei', 10),
            padding=(12, 6),
        )
        style.map('AppleGhost.TButton',
                  foreground=[('active', self.colors['primary_hover']), ('disabled', self.colors['text_muted'])])

        style.configure(
            'AppleDanger.TButton',
            background=self.colors['danger'],
            foreground='#ffffff',
            borderwidth=0,
            focuscolor='none',
            font=('Microsoft YaHei', 11),
            padding=(22, 8),
        )
        style.map('AppleDanger.TButton',
                  background=[('active', '#e0352b'), ('disabled', self.colors['border'])])

        style.configure(
            'AppleNav.TButton',
            background=self.colors['sidebar'],
            foreground=self.colors['text_on_dark_muted'],
            borderwidth=0,
            focuscolor='none',
            font=('Microsoft YaHei', 11),
            padding=(16, 10),
            anchor='w',
        )
        style.map('AppleNav.TButton',
                  background=[('active', '#3a3a3c')],
                  foreground=[('active', self.colors['text_on_dark'])])

        style.configure(
            'AppleNavActive.TButton',
            background='#3a3a3c',
            foreground=self.colors['text_on_dark'],
            borderwidth=0,
            focuscolor='none',
            font=('Microsoft YaHei', 11, 'bold'),
            padding=(16, 10),
            anchor='w',
        )

        style.configure('Apple.TNotebook', background=self.colors['surface_alt'], borderwidth=0)
        style.configure('Apple.TNotebook.Tab', background=self.colors['surface_alt'],
                        foreground=self.colors['text_muted'], padding=(16, 8),
                        font=('Microsoft YaHei', 10), borderwidth=0)
        style.map('Apple.TNotebook.Tab',
                  background=[('selected', self.colors['surface'])],
                  foreground=[('selected', self.colors['text'])])

        style.configure('Apple.TLabelframe', background=self.colors['surface_alt'],
                        foreground=self.colors['text_muted'], borderwidth=1,
                        relief='solid', bordercolor=self.colors['hairline'])
        style.configure('Apple.TLabelframe.Label', background=self.colors['surface_alt'],
                        foreground=self.colors['text_muted'], font=('Microsoft YaHei', 9, 'bold'))

        style.configure('Apple.Horizontal.TProgressbar', background=self.colors['primary'],
                        troughcolor=self.colors['divider_soft'], borderwidth=0, thickness=6)

        self._build_main_layout()
        self._build_dashboard_view()
        self._build_settings_view()
        self._build_about_view()
        self._switch_view('dashboard')
        self.start_log_update()

    def _build_main_layout(self):
        """构建主布局：左侧导航栏 + 右侧内容区"""
        nav = tk.Frame(self.root, bg=self.colors['sidebar'], width=220)
        nav.pack(side=tk.LEFT, fill=tk.Y)
        nav.pack_propagate(False)

        nav_inner = tk.Frame(nav, bg=self.colors['sidebar'], padx=16, pady=20)
        nav_inner.pack(fill=tk.BOTH, expand=True)

        logo_frame = tk.Frame(nav_inner, bg=self.colors['sidebar'])
        logo_frame.pack(fill=tk.X, pady=(0, 32))
        tk.Label(logo_frame, text="Autovisor", font=('Microsoft YaHei', 18, 'bold'),
                 bg=self.colors['sidebar'], fg=self.colors['text_on_dark']).pack(anchor='w')
        tk.Label(logo_frame, text="统一刷课管理", font=('Microsoft YaHei', 10),
                 bg=self.colors['sidebar'], fg=self.colors['text_on_dark_muted']).pack(anchor='w', pady=(2, 0))

        nav_items = [
            ('dashboard', '概览', '▦'),
            ('settings', '配置', '⚙'),
            ('about', '关于', 'ℹ'),
        ]
        for key, label, icon in nav_items:
            btn = ttk.Button(nav_inner, text=f"  {icon}  {label}",
                             style='AppleNav.TButton',
                             command=lambda k=key: self._switch_view(k))
            btn.pack(fill=tk.X, pady=2)
            self.nav_buttons[key] = btn

        nav_inner.pack(fill=tk.BOTH, expand=True)

        spacer = tk.Frame(nav_inner, bg=self.colors['sidebar'])
        spacer.pack(fill=tk.BOTH, expand=True)

        bottom_frame = tk.Frame(nav_inner, bg=self.colors['sidebar'])
        bottom_frame.pack(fill=tk.X, pady=(16, 0))
        tk.Label(bottom_frame, text=f"Yatori {self._get_yatori_display_version()}",
                 font=('Microsoft YaHei', 8), bg=self.colors['sidebar'],
                 fg=self.colors['text_on_dark_muted']).pack(anchor='w')
        tk.Label(bottom_frame, text=f"Autovisor {self._get_autovisor_display_version()}",
                 font=('Microsoft YaHei', 8), bg=self.colors['sidebar'],
                 fg=self.colors['text_on_dark_muted']).pack(anchor='w')

        self.content_host = tk.Frame(self.root, bg=self.colors['bg'])
        self.content_host.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _create_card(self, parent, padding=24, tone='white'):
        if tone == 'dark':
            bg = self.colors['surface_dark']
            fg = self.colors['text_on_dark']
        elif tone == 'dark2':
            bg = self.colors['surface_dark_2']
            fg = self.colors['text_on_dark']
        elif tone == 'soft':
            bg = self.colors['surface_alt']
            fg = self.colors['text']
        elif tone == 'pearl':
            bg = self.colors['surface_pearl']
            fg = self.colors['text']
        else:
            bg = self.colors['surface']
            fg = self.colors['text']

        card = tk.Frame(parent, bg=bg, padx=padding, pady=padding,
                        highlightbackground=self.colors['hairline'],
                        highlightthickness=1 if tone in ('white', 'pearl') else 0)
        return card

    def show_settings(self, tab_name='yatori'):
        self.current_settings_tab = tab_name
        self._switch_view('settings')
        self._switch_settings_tab(tab_name)

    def _switch_view(self, view_name):
        self.current_view = view_name
        for key, btn in self.nav_buttons.items():
            btn.configure(style='AppleNavActive.TButton' if key == view_name else 'AppleNav.TButton')
        for name, frame in self.view_frames.items():
            if name == view_name:
                frame.pack(fill=tk.BOTH, expand=True)
            else:
                frame.pack_forget()

    def _switch_settings_tab(self, tab_name):
        self.current_settings_tab = tab_name
        if hasattr(self, 'settings_notebook') and self.settings_notebook:
            tab_ids = {'yatori': 0, 'autovisor': 1}
            self.settings_notebook.select(tab_ids.get(tab_name, 0))

    def _create_labeled_input(self, parent, label_text, variable, *, combo_values=None, show=None, buttons=None, width=20):
        row = tk.Frame(parent, bg=parent.cget('bg') if parent.cget('bg') != 'SystemButtonFace' else self.colors['surface'])
        row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(row, text=label_text, font=('Microsoft YaHei', 10),
                 bg=row.cget('bg'), fg=self.colors['text_muted'], anchor='w').pack(anchor='w')
        input_row = tk.Frame(row, bg=row.cget('bg'))
        input_row.pack(fill=tk.X, pady=(4, 0))
        if combo_values:
            widget = ttk.Combobox(input_row, textvariable=variable, values=combo_values,
                                  state='readonly', font=('Microsoft YaHei', 10), width=width)
        else:
            widget = tk.Entry(input_row, textvariable=variable, font=('Microsoft YaHei', 10),
                              width=width, show=show or '',
                              bg=self.colors['surface'], fg=self.colors['text'],
                              insertbackground=self.colors['text'],
                              relief='solid', bd=1,
                              highlightbackground=self.colors['hairline'],
                              highlightthickness=1)
        widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if buttons:
            for btn_text, btn_cmd, btn_style in buttons:
                ttk.Button(input_row, text=btn_text, style=btn_style or 'AppleGhost.TButton',
                           command=btn_cmd).pack(side=tk.LEFT, padx=(6, 0))
        return widget

    def _create_labeled_text(self, parent, label_text, value='', height=4):
        row = tk.Frame(parent, bg=parent.cget('bg') if parent.cget('bg') != 'SystemButtonFace' else self.colors['surface'])
        row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(row, text=label_text, font=('Microsoft YaHei', 10),
                 bg=row.cget('bg'), fg=self.colors['text_muted'], anchor='w').pack(anchor='w')
        text_widget = tk.Text(row, height=height, font=('Consolas', 10),
                              bg=self.colors['surface'], fg=self.colors['text'],
                              insertbackground=self.colors['text'],
                              relief='solid', bd=1,
                              highlightbackground=self.colors['hairline'],
                              highlightthickness=1, wrap=tk.WORD)
        text_widget.pack(fill=tk.X, pady=(4, 0))
        if value:
            text_widget.insert('1.0', value)
        return text_widget

    def _create_surface_check(self, parent, text, variable):
        row = tk.Frame(parent, bg=parent.cget('bg') if parent.cget('bg') != 'SystemButtonFace' else self.colors['surface'])
        row.pack(fill=tk.X, pady=(0, 6))
        tk.Checkbutton(row, text=text, variable=variable,
                       bg=row.cget('bg'), activebackground=row.cget('bg'),
                       fg=self.colors['text'], selectcolor=self.colors['surface'],
                       font=('Microsoft YaHei', 10),
                       anchor='w').pack(anchor='w')

    def _create_scrollable_settings_body(self, parent):
        canvas = tk.Canvas(parent, bg=self.colors['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=self.colors['bg'])
        scroll_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        return scroll_frame

    def _build_dashboard_view(self):
        dashboard = tk.Frame(self.content_host, bg=self.colors['bg'])
        self.view_frames['dashboard'] = dashboard

        canvas = tk.Canvas(dashboard, bg=self.colors['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(dashboard, orient='vertical', command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=self.colors['bg'])
        scroll_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw', tags='scroll_frame')
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_canvas_configure(event):
            canvas.itemconfig('scroll_frame', width=event.width)
        canvas.bind('<Configure>', _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        canvas.bind_all('<MouseWheel>', _on_mousewheel, add='+')

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        hero = tk.Frame(scroll_frame, bg=self.colors['bg'])
        hero.pack(fill=tk.X, padx=40, pady=(40, 0))
        hero.grid_columnconfigure(0, weight=1)
        hero.grid_columnconfigure(1, weight=1)

        yatori_card = self._create_card(hero, padding=32, tone='white')
        yatori_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        y_top = tk.Frame(yatori_card, bg=self.colors['surface'])
        y_top.pack(fill=tk.X)
        y_title = tk.Frame(y_top, bg=self.colors['surface'])
        y_title.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(y_title, text="Yatori", font=('Microsoft YaHei', 20, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(anchor='w')
        tk.Label(y_title, text="非智慧树系统核心", font=('Microsoft YaHei', 10),
                 bg=self.colors['surface'], fg=self.colors['text_muted']).pack(anchor='w', pady=(4, 0))
        self.yatori_ready_badge = tk.Label(y_top, text="待检测", font=('Microsoft YaHei', 9, 'bold'),
                                           bg=self.colors['surface_alt'], fg=self.colors['text_muted'], padx=12, pady=4)
        self.yatori_ready_badge.pack(side=tk.RIGHT)
        self.yatori_version_label = tk.Label(
            yatori_card,
            text=f"当前核心版本: {self._get_yatori_display_version()}",
            font=('Microsoft YaHei', 9),
            bg=self.colors['surface'],
            fg=self.colors['text_muted'],
        )
        self.yatori_version_label.pack(anchor='w', pady=(24, 8))
        self.yatori_status = tk.Label(yatori_card, text="● 未启动", font=('Microsoft YaHei', 11),
                                      bg=self.colors['surface'], fg=self.colors['text_muted'])
        self.yatori_status.pack(anchor='w')
        y_actions = tk.Frame(yatori_card, bg=self.colors['surface'])
        y_actions.pack(fill=tk.X, pady=(24, 0))
        self.yatori_btn = ttk.Button(y_actions, text="启动核心", style="ApplePill.TButton",
                                     command=lambda: self.toggle_script('yatori'))
        self.yatori_btn.pack(side=tk.LEFT)
        self.yatori_stop_btn = ttk.Button(y_actions, text="停止运行", style="AppleDanger.TButton",
                                          command=lambda: self.stop_script('yatori'), state='disabled')
        self.yatori_stop_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.yatori_stop_btn.pack_forget()
        ttk.Button(y_actions, text="配置设置", style="ApplePillOutline.TButton",
                   command=lambda: self.show_settings('yatori')).pack(side=tk.LEFT, padx=(10, 0))
        self.yatori_update_btn = ttk.Button(y_actions, text="安装/更新核心", style="AppleGhost.TButton",
                                            command=self.show_update_dialog)
        self.yatori_update_btn.pack(side=tk.LEFT, padx=(10, 0))

        autovisor_card = self._create_card(hero, padding=32, tone='dark')
        autovisor_card.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        a_top = tk.Frame(autovisor_card, bg=self.colors['surface_dark'])
        a_top.pack(fill=tk.X)
        a_title = tk.Frame(a_top, bg=self.colors['surface_dark'])
        a_title.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(a_title, text="Autovisor", font=('Microsoft YaHei', 20, 'bold'),
                 bg=self.colors['surface_dark'], fg=self.colors['text_on_dark']).pack(anchor='w')
        tk.Label(a_title, text="智慧树专用核心", font=('Microsoft YaHei', 10),
                 bg=self.colors['surface_dark'], fg=self.colors['text_on_dark_muted']).pack(anchor='w', pady=(4, 0))
        self.autovisor_ready_badge = tk.Label(a_top, text="待检测", font=('Microsoft YaHei', 9, 'bold'),
                                              bg=self.colors['surface_dark_2'], fg=self.colors['primary_on_dark'], padx=12, pady=4)
        self.autovisor_ready_badge.pack(side=tk.RIGHT)
        self.autovisor_version_label = tk.Label(
            autovisor_card,
            text=f"当前核心版本: {self._get_autovisor_display_version()}",
            font=('Microsoft YaHei', 9),
            bg=self.colors['surface_dark'],
            fg=self.colors['text_on_dark_muted'],
        )
        self.autovisor_version_label.pack(anchor='w', pady=(24, 8))
        a_status_row = tk.Frame(autovisor_card, bg=self.colors['surface_dark'])
        a_status_row.pack(fill=tk.X)
        self.autovisor_status = tk.Label(a_status_row, text="● 未启动", font=('Microsoft YaHei', 11),
                                         bg=self.colors['surface_dark'], fg=self.colors['text_on_dark_muted'])
        self.autovisor_status.pack(side=tk.LEFT)
        tk.Checkbutton(
            a_status_row,
            text="多账号模式",
            variable=self.autovisor_multi_var,
            bg=self.colors['surface_dark'],
            activebackground=self.colors['surface_dark'],
            fg=self.colors['text_on_dark'],
            selectcolor=self.colors['surface_dark_2'],
            font=('Microsoft YaHei', 10),
        ).pack(side=tk.RIGHT)

        a_actions = tk.Frame(autovisor_card, bg=self.colors['surface_dark'])
        a_actions.pack(fill=tk.X, pady=(24, 0))
        self.autovisor_btn = ttk.Button(a_actions, text="启动核心", style="ApplePill.TButton",
                                        command=lambda: self.toggle_script('autovisor'))
        self.autovisor_btn.pack(side=tk.LEFT)
        self.autovisor_stop_btn = ttk.Button(a_actions, text="停止运行", style="AppleDanger.TButton",
                                             command=lambda: self.stop_script('autovisor'), state='disabled')
        self.autovisor_stop_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.autovisor_stop_btn.pack_forget()
        ttk.Button(a_actions, text="配置设置", style="ApplePillOutline.TButton",
                   command=lambda: self.show_settings('autovisor')).pack(side=tk.LEFT, padx=(10, 0))
        self.autovisor_update_btn = ttk.Button(
            a_actions,
            text="检查更新",
            style="AppleGhost.TButton",
            command=self.check_autovisor_update_async,
        )
        self.autovisor_update_btn.pack(side=tk.LEFT, padx=(10, 0))

        console_section = tk.Frame(scroll_frame, bg=self.colors['bg'])
        console_section.pack(fill=tk.BOTH, expand=True, padx=40, pady=(32, 40))

        log_card = self._create_card(console_section, padding=24, tone='white')
        log_card.pack(fill=tk.BOTH, expand=True)
        log_header = tk.Frame(log_card, bg=self.colors['surface'])
        log_header.pack(fill=tk.X, pady=(0, 16))
        tk.Label(log_header, text="» 系统控制台", font=('Microsoft YaHei', 16, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT)
        log_tools = tk.Frame(log_header, bg=self.colors['surface'])
        log_tools.pack(side=tk.RIGHT)
        ttk.Button(log_tools, text="清空日志", style="AppleGhost.TButton", command=self.clear_all_logs).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(log_tools, text="复制内容", style="AppleGhost.TButton", command=self.copy_current_log).pack(side=tk.LEFT)

        self.log_notebook = ttk.Notebook(log_card, style="Apple.TNotebook")
        self.log_notebook.pack(fill=tk.BOTH, expand=True)

        def create_log_tab(name):
            frame = tk.Frame(self.log_notebook, bg=self.colors['surface'], padx=4, pady=4)
            self.log_notebook.add(frame, text=name)
            text_area = scrolledtext.ScrolledText(
                frame,
                wrap=tk.WORD,
                font=('Consolas', 10),
                bg=self.colors['console'],
                fg=self.colors['console_text'],
                insertbackground=self.colors['console_text'],
                relief='flat',
                borderwidth=0,
            )
            text_area.pack(fill=tk.BOTH, expand=True)
            text_area.configure(state='disabled')
            return text_area

        self.log_texts = {
            'system': create_log_tab('系统'),
            'yatori': create_log_tab('Yatori'),
            'autovisor': create_log_tab('Autovisor'),
            'question_bank': create_log_tab('题库'),
        }
        self.log_notebook.bind('<<NotebookTabChanged>>', self._on_log_tab_changed)

    def _build_settings_view(self):
        page = tk.Frame(self.content_host, bg=self.colors['bg'])
        self.view_frames['settings'] = page

        header = tk.Frame(page, bg=self.colors['bg'])
        header.pack(fill=tk.X, padx=40, pady=(40, 0))
        title_wrap = tk.Frame(header, bg=self.colors['bg'])
        title_wrap.pack(side=tk.LEFT)
        tk.Label(title_wrap, text="核心设置", font=('Microsoft YaHei', 26, 'bold'),
                 bg=self.colors['bg'], fg=self.colors['text']).pack(anchor='w')
        tk.Label(title_wrap, text="这里保存的配置会直接写入运行所需的 config.yaml 与 configs.ini。",
                 font=('Microsoft YaHei', 10), bg=self.colors['bg'], fg=self.colors['text_muted']).pack(anchor='w', pady=(4, 0))
        action_wrap = tk.Frame(header, bg=self.colors['bg'])
        action_wrap.pack(side=tk.RIGHT)
        ttk.Button(action_wrap, text="重新加载", style="AppleGhost.TButton", command=self._load_settings_into_forms).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(action_wrap, text="打开目录", style="AppleGhost.TButton", command=self.open_current_settings_dir).pack(side=tk.LEFT, padx=(0, 10))
        self.save_settings_btn = ttk.Button(action_wrap, text="保存全部配置", style="ApplePill.TButton", command=self.save_all_settings)
        self.save_settings_btn.pack(side=tk.LEFT)

        tab_bar = tk.Frame(page, bg=self.colors['bg'])
        tab_bar.pack(fill=tk.X, padx=40, pady=(24, 0))
        pill_group = tk.Frame(tab_bar, bg=self.colors['surface_alt'], padx=4, pady=4)
        pill_group.pack(side=tk.LEFT)
        for tab_name, label in (('yatori', 'Yatori 配置'), ('autovisor', 'Autovisor 配置')):
            button = tk.Button(
                pill_group,
                text=label,
                relief='flat',
                bd=0,
                padx=18,
                pady=10,
                font=('Microsoft YaHei', 10),
                bg=self.colors['surface_alt'],
                fg=self.colors['text_muted'],
                activebackground=self.colors['surface_alt'],
                activeforeground=self.colors['text_muted'],
                command=lambda current=tab_name: self._switch_settings_tab(current),
            )
            button.pack(side=tk.LEFT, padx=4)
            self.settings_tab_buttons[tab_name] = button

        scroll_outer, body = self._create_scrollable_settings_body(page)

        def adjust_z_order():
            try:
                scroll_outer.lower()
                header.lift()
                tab_bar.lift()
            except tk.TclError:
                pass
        page.after(100, adjust_z_order)

        self.yatori_settings_panel = tk.Frame(body, bg=self.colors['bg'])
        self.autovisor_settings_panel = tk.Frame(body, bg=self.colors['bg'])

        self.yatori_log_level_var = tk.StringVar()
        self.yatori_log_model_var = tk.StringVar()
        self.yatori_web_model_var = tk.StringVar()
        self.yatori_completion_tone_var = tk.BooleanVar(value=True)
        self.yatori_color_log_var = tk.BooleanVar(value=True)
        self.yatori_log_out_file_var = tk.BooleanVar(value=True)
        self.yatori_email_sw_var = tk.BooleanVar(value=False)
        self.yatori_smtp_host_var = tk.StringVar()
        self.yatori_smtp_port_var = tk.StringVar()
        self.yatori_email_user_var = tk.StringVar()
        self.yatori_email_password_var = tk.StringVar()
        self.yatori_ai_type_var = tk.StringVar()
        self.yatori_ai_url_var = tk.StringVar()
        self.yatori_ai_model_var = tk.StringVar()
        self.yatori_ai_api_key_var = tk.StringVar()
        self.yatori_api_url_var = tk.StringVar()

        general_grid = tk.Frame(self.yatori_settings_panel, bg=self.colors['bg'])
        general_grid.pack(fill=tk.X)
        general_grid.grid_columnconfigure(0, weight=1)
        general_grid.grid_columnconfigure(1, weight=1)

        general_card = self._create_card(general_grid, padding=24, tone='white')
        general_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10), pady=(0, 16))
        tk.Label(general_card, text="全局行为", font=('Microsoft YaHei', 14, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(anchor='w', pady=(0, 12))
        self._create_labeled_input(general_card, "日志级别", self.yatori_log_level_var,
                                   combo_values=('INFO', 'DEBUG', 'WARN', 'ERROR'))
        self._create_labeled_input(general_card, "日志显示模式", self.yatori_log_model_var,
                                   combo_values=('0', '1'))
        self._create_labeled_input(general_card, "Web 模式", self.yatori_web_model_var,
                                   combo_values=('0', '1'))
        self._create_surface_check(general_card, "完成时播放提示音", self.yatori_completion_tone_var)
        self._create_surface_check(general_card, "启用彩色日志", self.yatori_color_log_var)
        self._create_surface_check(general_card, "写入日志文件", self.yatori_log_out_file_var)

        email_card = self._create_card(general_grid, padding=24, tone='white')
        email_card.grid(row=0, column=1, sticky='nsew', padx=(10, 0), pady=(0, 16))
        tk.Label(email_card, text="邮件通知", font=('Microsoft YaHei', 14, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(anchor='w', pady=(0, 12))
        self._create_surface_check(email_card, "启用 SMTP 邮件通知", self.yatori_email_sw_var)
        self._create_labeled_input(email_card, "SMTP 服务器", self.yatori_smtp_host_var)
        self._create_labeled_input(email_card, "SMTP 端口", self.yatori_smtp_port_var)
        self._create_labeled_input(email_card, "用户名 / 邮箱", self.yatori_email_user_var)
        self._create_labeled_input(email_card, "授权码 / 密码", self.yatori_email_password_var, show='*')

        ai_card = self._create_card(self.yatori_settings_panel, padding=24, tone='white')
        ai_card.pack(fill=tk.X, pady=(0, 16))
        tk.Label(ai_card, text="AI 模型配置", font=('Microsoft YaHei', 14, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(anchor='w', pady=(0, 12))
        self._create_labeled_input(ai_card, "AI 提供商", self.yatori_ai_type_var,
                                   combo_values=('TONGYI', 'DEEPSEEK'))
        self._create_labeled_input(ai_card, "AI 接口地址", self.yatori_ai_url_var)
        self._create_labeled_input(ai_card, "模型名称", self.yatori_ai_model_var)
        self._create_labeled_input(ai_card, "API Key", self.yatori_ai_api_key_var, show='*')

        api_card = self._create_card(self.yatori_settings_panel, padding=24, tone='white')
        api_card.pack(fill=tk.X, pady=(0, 16))
        tk.Label(api_card, text="题库接口", font=('Microsoft YaHei', 14, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(anchor='w', pady=(0, 12))
        self._create_labeled_input(api_card, "题库 API 地址", self.yatori_api_url_var)

        account_header = tk.Frame(self.yatori_settings_panel, bg=self.colors['bg'])
        account_header.pack(fill=tk.X, pady=(4, 8))
        tk.Label(account_header, text="学习账号", font=('Microsoft YaHei', 16, 'bold'),
                 bg=self.colors['bg'], fg=self.colors['text']).pack(side=tk.LEFT)
        ttk.Button(account_header, text="添加账号", style="ApplePill.TButton", command=self._add_yatori_account).pack(side=tk.RIGHT)
        self.yatori_accounts_container = tk.Frame(self.yatori_settings_panel, bg=self.colors['bg'])
        self.yatori_accounts_container.pack(fill=tk.BOTH, expand=True)

        self.autovisor_browser_driver_var = tk.StringVar(value='Chrome')
        self.autovisor_browser_path_var = tk.StringVar()

        env_card = self._create_card(self.autovisor_settings_panel, padding=24, tone='white')
        env_card.pack(fill=tk.X, pady=(0, 16))
        tk.Label(env_card, text="运行环境", font=('Microsoft YaHei', 14, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack(anchor='w', pady=(0, 12))
        env_grid = tk.Frame(env_card, bg=self.colors['surface'])
        env_grid.pack(fill=tk.X)
        env_grid.grid_columnconfigure(0, weight=1)
        env_grid.grid_columnconfigure(1, weight=1)
        left_env = tk.Frame(env_grid, bg=self.colors['surface'])
        left_env.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        right_env = tk.Frame(env_grid, bg=self.colors['surface'])
        right_env.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self._create_labeled_input(left_env, "首选浏览器", self.autovisor_browser_driver_var,
                                   combo_values=('Chrome', 'Edge'))
        self._create_labeled_input(
            right_env,
            "浏览器路径",
            self.autovisor_browser_path_var,
            buttons=[
                ("自动识别", self._detect_selected_browser_path, "AppleGhost.TButton"),
                ("浏览", self._browse_browser_path, "AppleGhost.TButton"),
            ],
        )
        tk.Checkbutton(
            env_card,
            text="启用多账号模式",
            variable=self.autovisor_multi_var,
            bg=self.colors['surface'],
            activebackground=self.colors['surface'],
            fg=self.colors['text'],
            selectcolor='white',
            font=('Microsoft YaHei', 10),
            anchor='w',
        ).pack(anchor='w', pady=(10, 0))

        a_account_header = tk.Frame(self.autovisor_settings_panel, bg=self.colors['bg'])
        a_account_header.pack(fill=tk.X, pady=(4, 8))
        tk.Label(a_account_header, text="智慧树账号", font=('Microsoft YaHei', 16, 'bold'),
                 bg=self.colors['bg'], fg=self.colors['text']).pack(side=tk.LEFT)
        ttk.Button(a_account_header, text="添加账号", style="ApplePill.TButton", command=self._add_autovisor_account).pack(side=tk.RIGHT)
        self.autovisor_accounts_container = tk.Frame(self.autovisor_settings_panel, bg=self.colors['bg'])
        self.autovisor_accounts_container.pack(fill=tk.BOTH, expand=True)

    def _build_about_view(self):
        page = tk.Frame(self.content_host, bg=self.colors['bg'])
        self.view_frames['about'] = page

        hero = self._create_card(page, padding=40, tone='white')
        hero.pack(fill=tk.BOTH, expand=True, padx=40, pady=40)
        top = tk.Frame(hero, bg=self.colors['surface'])
        top.pack(expand=True)

        icon = tk.Canvas(top, width=96, height=96, bg=self.colors['surface'], highlightthickness=0)
        icon.create_oval(4, 4, 92, 92, fill=self.colors['primary'], outline='')
        icon.create_text(48, 48, text="R", fill="white", font=('Segoe UI', 30, 'bold'))
        icon.pack(pady=(0, 20))

        tk.Label(top, text=f"统一启动器 {self.LAUNCHER_VERSION}", font=('Microsoft YaHei', 28, 'bold'),
                 bg=self.colors['surface'], fg=self.colors['text']).pack()
        self.about_version_label = tk.Label(
            top,
            text=f"Yatori {self._get_yatori_display_version()} / Autovisor {self._get_autovisor_display_version()}",
            font=('Microsoft YaHei', 11),
            bg=self.colors['surface'],
            fg=self.colors['text_muted'],
        )
        self.about_version_label.pack(pady=(10, 28))

        info_card = tk.Frame(top, bg=self.colors['surface_alt'], padx=24, pady=20)
        info_card.pack(fill=tk.X, padx=60)
        tk.Label(info_card, text="当前核心目录", font=('Microsoft YaHei', 12, 'bold'),
                 bg=self.colors['surface_alt'], fg=self.colors['text']).pack(anchor='w')
        self.about_paths_label = tk.Label(
            info_card,
            text="",
            justify=tk.LEFT,
            font=('Consolas', 10),
            bg=self.colors['surface_alt'],
            fg=self.colors['text_muted'],
        )
        self.about_paths_label.pack(anchor='w', pady=(8, 0))

        # 致谢信息
        thanks_card = tk.Frame(top, bg=self.colors['surface_alt'], padx=24, pady=16)
        thanks_card.pack(fill=tk.X, padx=60, pady=(16, 0))
        tk.Label(thanks_card, text="特别致谢", font=('Microsoft YaHei', 12, 'bold'),
                 bg=self.colors['surface_alt'], fg=self.colors['text']).pack(anchor='w')
        tk.Label(thanks_card, text="题库架构由 ZError 提供支持",
                 font=('Microsoft YaHei', 10),
                 bg=self.colors['surface_alt'], fg=self.colors['text_muted']).pack(anchor='w', pady=(8, 0))

        # 更新日志
        changelog_card = tk.Frame(top, bg=self.colors['surface_alt'], padx=24, pady=16)
        changelog_card.pack(fill=tk.X, padx=60, pady=(16, 0))
        tk.Label(changelog_card, text="更新日志", font=('Microsoft YaHei', 12, 'bold'),
                 bg=self.colors['surface_alt'], fg=self.colors['text']).pack(anchor='w')
        changelog_text = tk.Text(changelog_card, height=6, wrap=tk.WORD,
                                  font=('Microsoft YaHei', 9),
                                  bg=self.colors['surface_alt'], fg=self.colors['text_muted'],
                                  relief='flat', highlightthickness=0)
        changelog_text.pack(fill=tk.X, pady=(8, 0))
        changelog_content = """2026-05-18 v1.0.0
• 新增: 软件设置背景透明度调节功能
• 新增: 关于页更新日志显示
• 优化: 界面交互体验

2026-05-15 v0.9.5
• 新增: 题库服务器集成
• 修复: 多账号模式配置问题

2026-05-10 v0.9.0
• 初始版本发布
• 支持 Yatori & Autovisor 双核心"""
        changelog_text.insert(tk.END, changelog_content)
        changelog_text.config(state='disabled')

        actions = tk.Frame(top, bg=self.colors['surface'])
        actions.pack(pady=32)
        ttk.Button(actions, text="打开 Yatori 目录", style="AppleGhost.TButton",
                   command=lambda: self.open_config_dir('yatori')).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="打开 Autovisor 目录", style="AppleGhost.TButton",
                   command=lambda: self.open_config_dir('autovisor')).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="检查 Autovisor 更新", style="ApplePill.TButton",
                   command=self.check_autovisor_update_async).pack(side=tk.LEFT, padx=6)

    def _refresh_runtime_summary(self):
        y_installed = self._directory_has_any_file(self.yatori_path, self.YATORI_ENTRY_FILES)
        a_installed = self._directory_has_any_file(self.autovisor_path, self.AUTOVISOR_ENTRY_FILES)
        if hasattr(self, 'yatori_ready_badge'):
            self.yatori_ready_badge.config(
                text="已就绪" if y_installed else "未安装",
                bg=self.colors['surface_alt'],
                fg=self.colors['success'] if y_installed else self.colors['danger'],
            )
        if hasattr(self, 'autovisor_ready_badge'):
            self.autovisor_ready_badge.config(
                text="已就绪" if a_installed else "未安装",
                bg=self.colors['primary_soft'] if a_installed else self.colors['surface_alt'],
                fg=self.colors['primary'] if a_installed else self.colors['danger'],
            )
        if hasattr(self, 'about_paths_label'):
            self.about_paths_label.config(text=f"Yatori: {self.yatori_path}\nAutovisor: {self.autovisor_path}")
        if hasattr(self, 'about_version_label'):
            self.about_version_label.config(
                text=f"Yatori {self._get_yatori_display_version()} / Autovisor {self._get_autovisor_display_version()}"
            )

    def _populate_yatori_settings(self, config):
        basic = config.get('setting', {}).get('basicSetting', {})
        email = config.get('setting', {}).get('emailInform', {})
        ai = config.get('setting', {}).get('aiSetting', {})
        api = config.get('setting', {}).get('apiQueSetting', {})
        self.yatori_log_level_var.set(str(basic.get('logLevel', 'INFO')))
        self.yatori_log_model_var.set(str(basic.get('logModel', 0)))
        self.yatori_web_model_var.set(str(basic.get('WebModel', 0)))
        self.yatori_completion_tone_var.set(bool(self._as_int(basic.get('completionTone', 1), 1)))
        self.yatori_color_log_var.set(bool(self._as_int(basic.get('colorLog', 1), 1)))
        self.yatori_log_out_file_var.set(bool(self._as_int(basic.get('logOutFileSw', 1), 1)))
        self.yatori_email_sw_var.set(bool(self._as_int(email.get('sw', 0), 0)))
        self.yatori_smtp_host_var.set(str(email.get('SMTPHost', '')))
        self.yatori_smtp_port_var.set(str(email.get('SMTPPort', 0)))
        self.yatori_email_user_var.set(str(email.get('userName', '')))
        self.yatori_email_password_var.set(str(email.get('password', '')))
        self.yatori_ai_type_var.set(str(ai.get('aiType', 'TONGYI')))
        self.yatori_ai_url_var.set(str(ai.get('aiUrl', '')))
        self.yatori_ai_model_var.set(str(ai.get('model', '')))
        self.yatori_ai_api_key_var.set(str(ai.get('API_KEY', '')))
        self.yatori_api_url_var.set(str(api.get('url', 'http://127.0.0.1:8083/query')))
        self._render_yatori_accounts(config.get('users') or [self._default_yatori_user(1)])

    def _populate_autovisor_settings(self, config):
        self._set_autovisor_multi_mode(config.get('multi_mode'))
        self.autovisor_browser_driver_var.set(config.get('browser_driver', 'Chrome') or 'Chrome')
        self.autovisor_browser_path_var.set(config.get('browser_path', ''))
        self._render_autovisor_accounts(config.get('accounts') or [self._default_autovisor_account(1)])

    def _load_settings_into_forms(self):
        self._populate_yatori_settings(self._load_yatori_config_data())
        self._populate_autovisor_settings(self._load_autovisor_config_data())
        self._refresh_runtime_summary()
        self.log_system("已重新加载启动器配置")

    def _get_yatori_platform_rule(self, platform_code):
        return self.YATORI_PLATFORM_MODE_RULES.get(platform_code, self.YATORI_PLATFORM_MODE_RULES['XUEXITONG'])

    def _sync_yatori_platform_card(self, form):
        """Update video/exam/submit combo boxes when platform type changes."""
        platform_code = form['account_type'].get().strip() or 'XUEXITONG'
        rule = self._get_yatori_platform_rule(platform_code)
        # Update video mode combo
        video_widget = form.get('_video_widget')
        if video_widget:
            current_video = form['video_model'].get()
            video_widget['values'] = rule['videoModes']
            if current_video not in rule['videoModes']:
                form['video_model'].set(rule['videoModes'][0])
        # Update auto exam combo
        exam_widget = form.get('_exam_widget')
        if exam_widget:
            current_exam = form['auto_exam'].get()
            exam_widget['values'] = rule['examModes']
            if current_exam not in rule['examModes']:
                form['auto_exam'].set(rule['examModes'][0])
        # Update submit mode combo
        submit_widget = form.get('_submit_widget')
        if submit_widget:
            current_submit = form['auto_submit'].get()
            submit_widget['values'] = rule['submitModes']
            if current_submit not in rule['submitModes']:
                form['auto_submit'].set(rule['submitModes'][0])

    def _render_yatori_accounts(self, accounts):
        for child in self.yatori_accounts_container.winfo_children():
            child.destroy()
        self.yatori_account_forms = []
        active_accounts = accounts or [self._default_yatori_user(1)]
        for index, account in enumerate(active_accounts, start=1):
            card = self._create_card(self.yatori_accounts_container, padding=18, tone='white')
            card.pack(fill=tk.X, pady=(0, 14))
            header = tk.Frame(card, bg=self.colors['surface'])
            header.pack(fill=tk.X, pady=(0, 10))
            title = account.get('remarkName') or f'账号{index}'
            tk.Label(header, text=title, font=('Microsoft YaHei', 13, 'bold'),
                     bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT)
            if len(active_accounts) > 1:
                ttk.Button(header, text="删除", style="Ghost.TButton",
                           command=lambda current=index - 1: self._remove_yatori_account(current)).pack(side=tk.RIGHT)

            platform_code = str(account.get('accountType', 'XUEXITONG'))
            rule = self._get_yatori_platform_rule(platform_code)
            cc = account.get('coursesCustom', {})
            # Ensure initial values are valid for this platform
            initial_video = str(cc.get('videoModel', rule['videoModes'][0]))
            if initial_video not in rule['videoModes']:
                initial_video = rule['videoModes'][0]
            initial_exam = str(cc.get('autoExam', rule['examModes'][0]))
            if initial_exam not in rule['examModes']:
                initial_exam = rule['examModes'][0]
            initial_submit = str(cc.get('examAutoSubmit', rule['submitModes'][0]))
            if initial_submit not in rule['submitModes']:
                initial_submit = rule['submitModes'][0]
            form = {
                'account_type': tk.StringVar(value=platform_code),
                'site_url': tk.StringVar(value=str(account.get('url', ''))),
                'remark_name': tk.StringVar(value=str(account.get('remarkName', title))),
                'account': tk.StringVar(value=str(account.get('account', ''))),
                'password': tk.StringVar(value=str(account.get('password', ''))),
                'use_proxy': tk.BooleanVar(value=bool(self._as_int(account.get('isProxy', 0), 0))),
                'inform_emails': tk.StringVar(value=', '.join(account.get('informEmails', []) or [])),
                'study_time': tk.StringVar(value=str(cc.get('studyTime', ''))),
                'cx_node': tk.StringVar(value=str(cc.get('cxNode', 3))),
                'cx_chapter_test': tk.BooleanVar(value=bool(self._as_int(cc.get('cxChapterTestSw', 1), 1))),
                'cx_work': tk.BooleanVar(value=bool(self._as_int(cc.get('cxWorkSw', 1), 1))),
                'cx_exam': tk.BooleanVar(value=bool(self._as_int(cc.get('cxExamSw', 1), 1))),
                'shuffle': tk.BooleanVar(value=bool(self._as_int(cc.get('shuffleSw', 0), 0))),
                'video_model': tk.StringVar(value=initial_video),
                'auto_exam': tk.StringVar(value=initial_exam),
                'auto_submit': tk.StringVar(value=initial_submit),
            }

            top_grid = tk.Frame(card, bg=self.colors['surface'])
            top_grid.pack(fill=tk.X)
            top_grid.grid_columnconfigure(0, weight=1)
            top_grid.grid_columnconfigure(1, weight=1)
            left = tk.Frame(top_grid, bg=self.colors['surface'])
            left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
            right = tk.Frame(top_grid, bg=self.colors['surface'])
            right.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
            account_type_widget = self._create_labeled_input(left, "账号类型", form['account_type'],
                                       combo_values=self.YATORI_ACCOUNT_TYPES)
            tk.Label(
                left,
                text="提示：英华学堂、海旗科技建议同时填写站点地址",
                font=('Microsoft YaHei', 9),
                bg=self.colors['surface'],
                fg=self.colors['text_muted'],
                anchor='w',
            ).pack(fill=tk.X, pady=(0, 8))
            self._create_labeled_input(left, "备注名称", form['remark_name'])
            self._create_labeled_input(left, "登录账号", form['account'])
            self._create_labeled_input(right, "站点地址 / URL", form['site_url'])
            self._create_labeled_input(right, "密码", form['password'], show='*')
            self._create_labeled_input(right, "通知邮箱（逗号分隔）", form['inform_emails'])
            self._create_surface_check(card, "启用代理", form['use_proxy'])
            self._create_surface_check(card, "随机打乱课程", form['shuffle'])
            form['_video_widget'] = self._create_labeled_input(card, "视频模式", form['video_model'], combo_values=rule['videoModes'])
            form['_exam_widget'] = self._create_labeled_input(card, "自动考试模式", form['auto_exam'], combo_values=rule['examModes'])
            form['_submit_widget'] = self._create_labeled_input(card, "交卷模式", form['auto_submit'], combo_values=rule['submitModes'])
            # Wire platform change callback
            def _make_on_platform_change(f=form):
                def _inner(*_):
                    self._sync_yatori_platform_card(f)
                return _inner
            form['account_type'].trace_add('write', _make_on_platform_change())
            tk.Label(
                card,
                text="视频: 0=不刷 1=普通(单课依次) 2=暴力(多课并行) 3=去红/多节点 | 考试: 1=AI 2=题库API 3=学习通内置AI | 交卷: 0=仅保存 1=自动提交 2=智能提交(无空题才交)",
                font=('Microsoft YaHei', 9),
                bg=self.colors['surface'],
                fg=self.colors['text_muted'],
                anchor='w',
                justify=tk.LEFT,
            ).pack(fill=tk.X, pady=(0, 10))

            # 学习通专属设置
            cx_card = self._create_card(card, padding=14, tone='soft')
            cx_card.pack(fill=tk.X, pady=(0, 10))
            tk.Label(cx_card, text="学习通高级设置", font=('Microsoft YaHei', 12, 'bold'),
                     bg=self.colors['surface_alt'], fg=self.colors['text']).pack(anchor='w', pady=(0, 8))
            cx_grid = tk.Frame(cx_card, bg=self.colors['surface_alt'])
            cx_grid.pack(fill=tk.X)
            cx_grid.grid_columnconfigure(0, weight=1)
            cx_grid.grid_columnconfigure(1, weight=1)
            cx_left = tk.Frame(cx_grid, bg=self.colors['surface_alt'])
            cx_left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
            cx_right = tk.Frame(cx_grid, bg=self.colors['surface_alt'])
            cx_right.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
            self._create_labeled_input(cx_left, "多节点并发数 (cxNode)", form['cx_node'],
                                       combo_values=('-1', '1', '2', '3', '4', '5', '6', '7', '8'))
            tk.Label(cx_left, text="视频模式=3时生效，-1=不限，数值越大并发越高",
                     font=('Microsoft YaHei', 8), bg=self.colors['surface_alt'],
                     fg=self.colors['text_muted'], anchor='w').pack(fill=tk.X, pady=(0, 6))
            self._create_labeled_input(cx_left, "学习时间段 (studyTime)", form['study_time'])
            tk.Label(cx_left, text="WeLearn等平台学习时间范围，如 10-30，留空使用默认",
                     font=('Microsoft YaHei', 8), bg=self.colors['surface_alt'],
                     fg=self.colors['text_muted'], anchor='w').pack(fill=tk.X, pady=(0, 6))
            self._create_surface_check(cx_right, "章节测试 (cxChapterTestSw)", form['cx_chapter_test'])
            self._create_surface_check(cx_right, "课后作业 (cxWorkSw)", form['cx_work'])
            self._create_surface_check(cx_right, "自动考试 (cxExamSw)", form['cx_exam'])

            text_grid = tk.Frame(card, bg=self.colors['surface'])
            text_grid.pack(fill=tk.X)
            text_grid.grid_columnconfigure(0, weight=1)
            text_grid.grid_columnconfigure(1, weight=1)
            include_wrap = tk.Frame(text_grid, bg=self.colors['surface'])
            include_wrap.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
            exclude_wrap = tk.Frame(text_grid, bg=self.colors['surface'])
            exclude_wrap.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
            include_text = self._create_labeled_text(
                include_wrap,
                "仅包含这些课程（每行一个）",
                '\n'.join(cc.get('includeCourses', []) or []),
                height=4,
            )
            exclude_text = self._create_labeled_text(
                exclude_wrap,
                "排除这些课程（每行一个）",
                '\n'.join(account.get('coursesCustom', {}).get('excludeCourses', []) or []),
                height=4,
            )
            form['include_courses'] = include_text
            form['exclude_courses'] = exclude_text
            self.yatori_account_forms.append(form)

    def _render_autovisor_accounts(self, accounts):
        for child in self.autovisor_accounts_container.winfo_children():
            child.destroy()
        self.autovisor_account_forms = []
        active_accounts = accounts or [self._default_autovisor_account(1)]
        multi = len(active_accounts) > 1
        for index, account in enumerate(active_accounts, start=1):
            card = self._create_card(self.autovisor_accounts_container, padding=18, tone='white')
            card.pack(fill=tk.X, pady=(0, 14))
            header = tk.Frame(card, bg=self.colors['surface'])
            header.pack(fill=tk.X, pady=(0, 10))
            name_var = tk.StringVar(value=str(account.get('name') or f'账号 {index}'))
            tk.Label(header, textvariable=name_var, font=('Microsoft YaHei', 13, 'bold'),
                     bg=self.colors['surface'], fg=self.colors['text']).pack(side=tk.LEFT)
            if multi:
                ttk.Button(header, text="删除", style="Ghost.TButton",
                           command=lambda current=index - 1: self._remove_autovisor_account(current)).pack(side=tk.RIGHT)

            form = {
                'name': name_var,
                'username': tk.StringVar(value=str(account.get('username', ''))),
                'password': tk.StringVar(value=str(account.get('password', ''))),
                'driver': tk.StringVar(value=str(account.get('driver', 'Chrome'))),
                'exe_path': tk.StringVar(value=str(account.get('exe_path', ''))),
                'enable_auto_captcha': tk.BooleanVar(value=bool(account.get('enable_auto_captcha', True))),
                'enable_hide_window': tk.BooleanVar(value=bool(account.get('enable_hide_window', False))),
                'limit_max_time': tk.StringVar(value=str(account.get('limit_max_time', '30'))),
                'limit_speed': tk.StringVar(value=str(account.get('limit_speed', '1.0'))),
                'sound_off': tk.BooleanVar(value=bool(account.get('sound_off', True))),
            }
            top_grid = tk.Frame(card, bg=self.colors['surface'])
            top_grid.pack(fill=tk.X)
            top_grid.grid_columnconfigure(0, weight=1)
            top_grid.grid_columnconfigure(1, weight=1)
            left = tk.Frame(top_grid, bg=self.colors['surface'])
            left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
            right = tk.Frame(top_grid, bg=self.colors['surface'])
            right.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
            self._create_labeled_input(left, "卡片名称", form['name'])
            self._create_labeled_input(left, "手机号 / 学号", form['username'])
            self._create_labeled_input(left, "密码", form['password'], show='*')
            self._create_labeled_input(right, "播放倍速", form['limit_speed'],
                                       combo_values=self.AUTOVISOR_SPEED_OPTIONS)
            self._create_labeled_input(right, "单门课程最大时长（分钟）", form['limit_max_time'])

            # Per-account browser settings (useful in multi-mode for different accounts)
            if multi:
                browser_card = self._create_card(card, padding=12, tone='soft')
                browser_card.pack(fill=tk.X, pady=(6, 10))
                tk.Label(browser_card, text="此账号浏览器（覆盖全局设置）", font=('Microsoft YaHei', 11, 'bold'),
                         bg=self.colors['surface_alt'], fg=self.colors['text']).pack(anchor='w', pady=(0, 6))
                browser_grid = tk.Frame(browser_card, bg=self.colors['surface_alt'])
                browser_grid.pack(fill=tk.X)
                browser_grid.grid_columnconfigure(0, weight=1)
                browser_grid.grid_columnconfigure(1, weight=1)
                b_left = tk.Frame(browser_grid, bg=self.colors['surface_alt'])
                b_left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
                b_right = tk.Frame(browser_grid, bg=self.colors['surface_alt'])
                b_right.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
                self._create_labeled_input(b_left, "浏览器类型", form['driver'],
                                           combo_values=('Chrome', 'Edge'))
                self._create_labeled_input(b_right, "浏览器路径（留空=全局）", form['exe_path'])
                tk.Label(browser_card, text="留空则使用上方全局浏览器设置；填写则此账号使用独立浏览器。",
                         font=('Microsoft YaHei', 8), bg=self.colors['surface_alt'],
                         fg=self.colors['text_muted'], anchor='w').pack(fill=tk.X, pady=(4, 0))

            self._create_surface_check(card, "自动验证码", form['enable_auto_captcha'])
            self._create_surface_check(card, "隐藏浏览器窗口", form['enable_hide_window'])
            tk.Label(
                card,
                text="提示：开启隐藏浏览器窗口后，必须填写账号和密码。",
                font=('Microsoft YaHei', 9),
                bg=self.colors['surface'],
                fg=self.colors['text_muted'],
                anchor='w',
            ).pack(fill=tk.X, pady=(0, 8))
            self._create_surface_check(card, "静音播放", form['sound_off'])
            course_urls_text = self._create_labeled_text(
                card,
                "课程链接（每行一个）",
                '\n'.join(account.get('course_urls', []) or []),
                height=5,
            )
            form['course_urls'] = course_urls_text
            self.autovisor_account_forms.append(form)

    def _add_yatori_account(self):
        accounts = self._collect_yatori_accounts_form_data()
        accounts.append(self._default_yatori_user(len(accounts) + 1))
        self._render_yatori_accounts(accounts)

    def _remove_yatori_account(self, index):
        accounts = self._collect_yatori_accounts_form_data()
        if len(accounts) <= 1:
            return
        accounts.pop(index)
        self._render_yatori_accounts(accounts)

    def _add_autovisor_account(self):
        accounts = self._collect_autovisor_accounts_form_data()
        accounts.append(self._default_autovisor_account(len(accounts) + 1))
        self._set_autovisor_multi_mode(True)
        self._render_autovisor_accounts(accounts)

    def _remove_autovisor_account(self, index):
        accounts = self._collect_autovisor_accounts_form_data()
        if len(accounts) <= 1:
            return
        accounts.pop(index)
        if len(accounts) <= 1:
            self._set_autovisor_multi_mode(False)
        self._render_autovisor_accounts(accounts)

    def _detect_selected_browser_path(self):
        browser_name = self.autovisor_browser_driver_var.get().strip() or 'Chrome'
        normalized = self._normalize_browser_name(browser_name)
        detected_path = self._find_browser_executable(normalized)
        if detected_path:
            self.autovisor_browser_path_var.set(detected_path)
            self.log_system(f"已识别 {browser_name} 路径: {detected_path}")
        else:
            self._show_warning("未找到浏览器", f"没有在系统中找到 {browser_name} 的可执行文件。")

    def _browse_browser_path(self):
        file_path = filedialog.askopenfilename(
            title="选择浏览器可执行文件",
            filetypes=[("Executable", "*.exe"), ("All Files", "*.*")],
        )
        if file_path:
            self.autovisor_browser_path_var.set(file_path)

    def _build_yatori_config_from_form(self):
        smtp_port = self._as_int(self.yatori_smtp_port_var.get(), 0)
        return {
            'setting': {
                'basicSetting': {
                    'completionTone': 1 if self.yatori_completion_tone_var.get() else 0,
                    'colorLog': 1 if self.yatori_color_log_var.get() else 0,
                    'logOutFileSw': 1 if self.yatori_log_out_file_var.get() else 0,
                    'logLevel': self.yatori_log_level_var.get().strip() or 'INFO',
                    'logModel': self._as_int(self.yatori_log_model_var.get(), 0),
                    'WebModel': self._as_int(self.yatori_web_model_var.get(), 0),
                },
                'emailInform': {
                    'sw': 1 if self.yatori_email_sw_var.get() else 0,
                    'SMTPHost': self.yatori_smtp_host_var.get().strip(),
                    'SMTPPort': smtp_port,
                    'userName': self.yatori_email_user_var.get().strip(),
                    'password': self.yatori_email_password_var.get(),
                },
                'aiSetting': {
                    'aiType': self.yatori_ai_type_var.get().strip() or 'TONGYI',
                    'aiUrl': self.yatori_ai_url_var.get().strip(),
                    'model': self.yatori_ai_model_var.get().strip(),
                    'API_KEY': self.yatori_ai_api_key_var.get(),
                },
                'apiQueSetting': {
                    'url': self.yatori_api_url_var.get().strip() or 'http://127.0.0.1:8083/query',
                },
            },
            'users': self._collect_yatori_accounts_form_data(),
        }

    def _build_autovisor_config_from_form(self):
        accounts = self._collect_autovisor_accounts_form_data()
        validation_error = self._validate_autovisor_accounts(accounts)
        if validation_error:
            raise ValueError(validation_error)
        browser_path = self.autovisor_browser_path_var.get().strip()
        browser_driver = self.autovisor_browser_driver_var.get().strip() or 'Chrome'
        multi_mode = self._get_autovisor_multi_mode() and len(accounts) > 1
        if not multi_mode:
            accounts = accounts[:1]
        for account in accounts:
            account['driver'] = browser_driver
            account['exe_path'] = browser_path
        return {
            'multi_mode': multi_mode,
            'browser_driver': browser_driver,
            'browser_path': browser_path,
            'accounts': accounts,
        }

    def _save_yatori_form(self, silent=False):
        if self.ui_mode != 'tk':
            return True
        try:
            merged = self._load_yatori_config_data()
            built = self._build_yatori_config_from_form()
            merged.setdefault('setting', {})
            merged['setting'].setdefault('basicSetting', {}).update(built['setting']['basicSetting'])
            merged['setting'].setdefault('emailInform', {}).update(built['setting']['emailInform'])
            merged['setting'].setdefault('aiSetting', {}).update(built['setting']['aiSetting'])
            merged['setting'].setdefault('apiQueSetting', {}).update(built['setting']['apiQueSetting'])
            # Merge users: preserve fields not exposed in the form (e.g. coursesSettings)
            existing_users = merged.get('users') if isinstance(merged.get('users'), list) else []
            built_users = built.get('users', [])
            merged_users = []
            for i, built_user in enumerate(built_users):
                existing = existing_users[i] if i < len(existing_users) and isinstance(existing_users[i], dict) else {}
                merged_user = dict(existing)
                merged_user.update({k: v for k, v in built_user.items() if k != 'coursesCustom'})
                existing_cc = existing.get('coursesCustom') if isinstance(existing.get('coursesCustom'), dict) else {}
                merged_cc = dict(existing_cc)
                merged_cc.update(built_user.get('coursesCustom', {}))
                merged_user['coursesCustom'] = merged_cc
                merged_users.append(merged_user)
            merged['users'] = merged_users or [self._default_yatori_user(1)]
            self._save_yatori_config_data(merged)
            if not silent:
                self.log_system("Yatori 配置已保存")
            return True
        except Exception as exc:
            self._show_error("保存失败", f"保存 Yatori 配置失败：\n{exc}")
            return False

    def _save_autovisor_form(self, silent=False):
        if self.ui_mode != 'tk':
            return True
        try:
            self._save_autovisor_config_data(self._build_autovisor_config_from_form())
            if not silent:
                self.log_system("Autovisor 配置已保存")
            return True
        except Exception as exc:
            self._show_error("保存失败", f"保存 Autovisor 配置失败：\n{exc}")
            return False

    def save_all_settings(self):
        y_saved = self._save_yatori_form(silent=False)
        a_saved = self._save_autovisor_form(silent=False)
        if y_saved and a_saved:
            self._show_info("保存成功", "启动器配置已写入对应配置文件。")

    def open_current_settings_dir(self):
        self.open_config_dir('yatori' if self.current_settings_tab == 'yatori' else 'autovisor')

    def log(self, source, message, tag='', replace_last=False):
        """添加日志到指定源"""
        message = self._normalize_progress_log(str(message))
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {message}\n"
        self._append_log_history(source, log_line, replace_last=replace_last)

        if source == 'system':
            self.log_queues['system'].put((log_line, tag, replace_last))
        elif source == 'yatori':
            self.log_queues['yatori'].put((log_line, tag, replace_last))
        elif source == 'autovisor':
            self.log_queues['autovisor'].put((log_line, tag, replace_last))

    def log_system(self, message, replace_last=False):
        """记录系统日志"""
        self.log('system', message, 'system', replace_last=replace_last)

    def start_log_update(self):
        """启动日志更新循环"""
        self.update_logs()

    def _render_log_widget(self, widget, source):
        widget.config(state='normal')
        widget.delete('1.0', tk.END)
        history = self.log_history.get(source, [])
        if history:
            widget.insert(tk.END, '\n'.join(history) + '\n')
        widget.see(tk.END)
        widget.config(state='disabled')

    def update_logs(self):
        """更新日志显示"""
        # 更新系统日志
        while not self.log_queues['system'].empty():
            try:
                log_line, tag, replace_last = self.log_queues['system'].get_nowait()
                if replace_last:
                    self._render_log_widget(self.system_log, 'system')
                else:
                    self.system_log.config(state='normal')
                    self.system_log.insert(tk.END, log_line)
                    self.system_log.see(tk.END)
                    self.system_log.config(state='disabled')
            except queue.Empty:
                break

        # 更新 Yatori 日志
        while not self.log_queues['yatori'].empty():
            try:
                log_line, tag, replace_last = self.log_queues['yatori'].get_nowait()
                if replace_last:
                    self._render_log_widget(self.yatori_log, 'yatori')
                else:
                    self.yatori_log.config(state='normal')
                    self.yatori_log.insert(tk.END, log_line)
                    self.yatori_log.see(tk.END)
                    self.yatori_log.config(state='disabled')
            except queue.Empty:
                break

        # 更新 Autovisor 日志
        while not self.log_queues['autovisor'].empty():
            try:
                log_line, tag, replace_last = self.log_queues['autovisor'].get_nowait()
                if replace_last:
                    self._render_log_widget(self.autovisor_log, 'autovisor')
                else:
                    self.autovisor_log.config(state='normal')
                    self.autovisor_log.insert(tk.END, log_line)
                    self.autovisor_log.see(tk.END)
                    self.autovisor_log.config(state='disabled')
            except queue.Empty:
                break

        # 继续循环
        self._after(100, self.update_logs)

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
        with self._runtime_lock:
            if self.running.get(script_type) or self.starting.get(script_type):
                return False
            self.stop_requested[script_type] = False
            self.starting[script_type] = True
            return True

    def _mark_runtime_running(self, script_type, process):
        with self._runtime_lock:
            self.processes[script_type] = process
            self.starting[script_type] = False
            self.running[script_type] = True

    def _mark_runtime_stopped(self, script_type, process=None):
        with self._runtime_lock:
            current = self.processes.get(script_type)
            if process is None or current is None or current is process:
                self.processes[script_type] = None
                self.running[script_type] = False
                self.starting[script_type] = False

    def start_yatori(self):
        """启动 Yatori"""
        if self.running['yatori'] or self.starting['yatori']:
            self.log_system("Yatori 已经在运行或启动中")
            return

        if hasattr(self, '_save_yatori_form') and not self._save_yatori_form(silent=True):
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
                self.update_ui_state('yatori', True)
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
                self.update_ui_state('yatori', False)
                if return_code:
                    self.log_system(f"Yatori 已退出，返回码: {return_code}")
                else:
                    self.log_system("Yatori 已停止")
                self._handle_runtime_exit('yatori', return_code, was_requested)

            except Exception as e:
                self.log_system(f"Yatori 启动失败: {str(e)}")
                self._mark_runtime_stopped('yatori')
                self.update_ui_state('yatori', False)
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

        if hasattr(self, '_save_autovisor_form') and not self._save_autovisor_form(silent=True):
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
                if not self.qb_running:
                    self.log_system("[Autovisor] 正在启动题库服务器...")
                    self.start_question_bank(silent=True)
                    time.sleep(3)  # 等待服务器完全启动
                else:
                    self.log_system("[Autovisor] 题库服务器已在运行")
                
                # 确保服务器可用
                import socket
                port = self._qb_port
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
                self.update_ui_state('autovisor', True)
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
                self.update_ui_state('autovisor', False)
                if return_code:
                    self.log_system(f"Autovisor 已退出，返回码: {return_code}")
                else:
                    self.log_system("Autovisor 已停止")
                self._handle_runtime_exit('autovisor', return_code, was_requested)

            except Exception as e:
                self.log_system(f"Autovisor 启动失败: {str(e)}")
                self._mark_runtime_stopped('autovisor')
                self.update_ui_state('autovisor', False)
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
            self.update_ui_state('yatori', False)
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
            self.update_ui_state('autovisor', False)
            self.log_system("Autovisor 已关闭刷课。")

    def stop_practice_mode(self):
        """Stop the optional practice process and its browser children."""
        process = self.processes.get('practice')
        if process and process.poll() is None:
            self.log_system("正在停止刷题模式...")
            self._terminate_process_tree(process, "刷题模式")
        self.processes['practice'] = None
        self.running['practice'] = False

    def update_ui_state(self, script_type, is_running):
        """更新 UI 状态"""
        if self.ui_mode != 'tk':
            return

        def update():
            # 获取对应的标签索引 (系统动态:0, Yatori:1, Autovisor:2)
            if script_type == 'yatori':
                if is_running:
                    self.yatori_btn.config(state='normal', text='停止运行', style='Danger.TButton', command=lambda: self.toggle_script('yatori'))
                    self.yatori_status.config(text="● 运行中", fg=self.colors['success'])
                    self.log_notebook.select(1)
                else:
                    self.yatori_btn.config(state='normal', text='启动核心', style='Primary.TButton', command=lambda: self.toggle_script('yatori'))
                    self.yatori_status.config(text="● 未启动", fg=self.colors['text_muted'])

            elif script_type == 'autovisor':
                if is_running:
                    self.autovisor_btn.config(state='normal', text='停止运行', style='Danger.TButton', command=lambda: self.toggle_script('autovisor'))
                    self.autovisor_status.config(text="● 运行中", fg=self.colors['success'])
                    self.log_notebook.select(2)
                else:
                    self.autovisor_btn.config(state='normal', text='启动核心', style='Primary.TButton', command=lambda: self.toggle_script('autovisor'))
                    self.autovisor_status.config(text="● 未启动", fg=self.colors['text_muted'])

        # 在主线程更新 UI
        self._after(0, update)

    # ============================================================
    # 题库服务器管理
    # ============================================================
    def _load_qb_settings(self):
        """从本地文件加载题库设置"""
        try:
            qb_config_path = os.path.join(self.get_base_dir(), "data", "qb_config.json")
            if os.path.exists(qb_config_path):
                with open(qb_config_path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    self._qb_auto_start = saved.get('auto_start', True)
                    self._qb_ai_enabled = bool(saved.get('ai_enabled', True))
                    self._qb_ai_type = saved.get('ai_type', 'OPENAI')
                    self._qb_ai_url = saved.get('ai_url', '')
                    self._qb_ai_model = saved.get('ai_model', '')
                    self._qb_ai_api_key = saved.get('ai_api_key', '') or os.environ.get('QB_AI_API_KEY', '')
                    self._qb_auto_save = saved.get('auto_save', True)
                    try:
                        saved_port = int(saved.get('port', 8083))
                    except (TypeError, ValueError):
                        saved_port = 8083
                    self._qb_port = saved_port if 1024 <= saved_port <= 65535 else 8083
                    self.log_system('[QB] 已从本地加载题库设置')
                    
                    # 立即推送到题库服务器模块
                    self._apply_qb_ai_config()
                    qb_configure_auto_save(enabled=self._qb_auto_save)
        except Exception as e:
            self.log_system(f'[QB] 加载题库设置失败: {e}')

    def _apply_qb_ai_config(self):
        """将AI配置推送到题库服务器"""
        if not QB_AVAILABLE:
            return
        try:
            models = []
            if self._qb_ai_enabled and self._qb_ai_api_key:
                models.append({
                    "type": self._qb_ai_type or "OPENAI",
                    "url": self._qb_ai_url or "",
                    "model": self._qb_ai_model or "",
                    "api_key": self._qb_ai_api_key or "",
                })
            qb_configure_ai_models(models=models, enabled=self._qb_ai_enabled, concurrent=self._qb_ai_concurrent)
            self.log_system(f"[QB] AI配置已应用 (enabled={self._qb_ai_enabled}, models={len(models)})")
        except Exception as e:
            self.log_system(f"[QB] AI配置应用失败: {e}")

    def _save_qb_settings(self):
        """保存题库设置到本地文件"""
        try:
            qb_config_path = os.path.join(self.get_base_dir(), "data", "qb_config.json")
            saved = {
                'auto_start': self._qb_auto_start,
                'ai_enabled': self._qb_ai_enabled,
                'ai_type': self._qb_ai_type,
                'ai_url': self._qb_ai_url,
                'ai_model': self._qb_ai_model,
                # api_key 不写入磁盘，通过环境变量 QB_AI_API_KEY 设置
                'auto_save': self._qb_auto_save,
                'port': self._qb_port,
            }
            atomic_dump_json(qb_config_path, saved)
            self.log_system('[QB] 题库设置已保存到本地')
            return True
        except Exception as e:
            self.log_system(f'[QB] 保存题库设置失败: {e}')
            return False

    def get_qb_settings_from_web(self):
        """从Web获取题库设置"""
        return {
            'auto_start': self._qb_auto_start,
            'ai_enabled': self._qb_ai_enabled,
            'ai_type': self._qb_ai_type,
            'ai_url': self._qb_ai_url,
            'ai_model': self._qb_ai_model,
            'ai_api_key': self._qb_ai_api_key,
            'auto_save': self._qb_auto_save,
            'port': self._qb_port,
        }

    def save_qb_settings_from_web(self, payload):
        """从Web保存题库设置"""
        if not isinstance(payload, dict):
            return {'ok': False, 'message': '题库设置格式错误'}
        try:
            port = int(payload.get('port', 8083))
            if not 1024 <= port <= 65535:
                return {'ok': False, 'message': '题库端口必须在 1024 到 65535 之间'}
            old_port = self._qb_port
            was_running = self.qb_running
            self._qb_auto_start = bool(payload.get('auto_start', True))
            self._qb_ai_enabled = bool(payload.get('ai_enabled', False))
            self._qb_ai_type = str(payload.get('ai_type', 'OPENAI'))
            self._qb_ai_url = str(payload.get('ai_url', ''))
            self._qb_ai_model = str(payload.get('ai_model', ''))
            self._qb_ai_api_key = str(payload.get('ai_api_key', ''))
            self._qb_auto_save = bool(payload.get('auto_save', True))
            self._qb_port = port
            if not self._save_qb_settings():
                return {'ok': False, 'message': '题库设置写入失败'}
            self._apply_qb_ai_config()
            if QB_AVAILABLE:
                qb_configure_auto_save(enabled=self._qb_auto_save)
            if was_running and port != old_port:
                self.log_system(f'[QB] 端口由 {old_port} 改为 {port}，正在重启题库服务器')
                self.stop_question_bank()
                self.start_question_bank(silent=True)
                if not self.qb_running:
                    return {'ok': False, 'message': f'题库服务器未能在新端口 {port} 启动'}
            return {'ok': True, 'message': '题库设置已保存'}
        except Exception as e:
            return {'ok': False, 'message': f'保存题库设置失败: {e}'}

    def get_autovisor_courses_from_web(self, account_index=0):
        """从Web获取Autovisor课程列表 - 运行 fetch_zhs_courses.py"""
        import subprocess
        import os
        import json

        script_path = os.path.join(self.get_base_dir(), "scripts", "fetch_zhs_courses.py")
        if not os.path.exists(script_path):
            return {'ok': False, 'message': f'未找到课程获取脚本: {script_path}'}

        python_exe = self.get_python_executable()
        if not python_exe:
            return {'ok': False, 'message': '未找到 Python 解释器'}

        account_number = account_index + 1
        self.log_system(f"[课程获取] 正在获取第 {account_number} 个账号的课程...")

        # 检查本地缓存 (30分钟有效期)
        cache_file = os.path.join(self.get_base_dir(), "data", "course_cache.json")
        cache_key = f"account_{account_index}"
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                cached_entry = cache_data.get(cache_key)
                if cached_entry:
                    cached_at = datetime.fromisoformat(cached_entry['cached_at'])
                    age = (datetime.now() - cached_at).total_seconds()
                    if age < 1800:
                        self.log_system(f"[课程获取] 缓存命中 ({(age/60):.0f}分钟前), 直接返回")
                        return cached_entry['data']
                    self.log_system(f"[课程获取] 缓存已过期 ({(age/60):.0f}分钟前), 重新获取")
        except Exception as e:
            self.log_system(f"[课程获取] 缓存读取失败: {e}, 将重新获取")

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

        course_file = os.path.join(self.get_base_dir(), "data", "zhs_course.json")
        if not os.path.exists(course_file):
            return {'ok': False, 'message': '未找到课程数据文件'}

        try:
            with open(course_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            return {'ok': False, 'message': f'读取课程数据失败: {e}'}

        if not data:
            return {'ok': True, 'courses': []}

        # 从 configs.ini 读取当前账号的用户名，用于查找对应课程数据
        try:
            ini_path = os.path.join(self.get_base_dir(), "Autovisor", "configs.ini")
            section_map = {0: "user-account", 1: "user-account-2", 2: "user-account-3", 3: "user-account-4", 4: "user-account-5"}
            section = section_map.get(account_index, "user-account")
            parser = configparser.ConfigParser()
            parser.read(ini_path, encoding='utf-8')
            target_username = parser.get(section, 'username', fallback=None)
        except Exception:
            target_username = None

        if target_username and target_username in data:
            account_data = data[target_username]
            self.log_system(f"[课程获取] 找到账号 {target_username} 的课程数据")
        elif target_username:
            self.log_system(f"[课程获取] 未找到账号 {target_username} 的数据，该账号可能未登录过")
            return {'ok': True, 'courses': []}
        else:
            # 回退：取第一个键
            first_key = list(data.keys())[0]
            account_data = data[first_key]
            self.log_system(f"[课程获取] 无法确定当前账号，使用默认数据: {first_key}")

        raw_courses = account_data.get('courses', [])
        raw_notices = account_data.get('notices', [])
        
        courses = []
        for c in raw_courses:
            secret = c.get('secret', '')
            course_type = c.get('courseType', 1)
            course_name = c.get('courseName', '未知课程')
            
            if course_type == 1:
                course_url = f'https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={secret}' if secret else ''
                type_label = '普通课'
            elif course_type == 7:
                course_url = f'https://wisdom-mooc.zhihuishu.com/study/index?recruitAndCourseId={secret}' if secret else ''
                type_label = '共享课'
            else:
                course_url = f'https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId={secret}' if secret else ''
                type_label = '未知类型'
            
            courses.append({
                'name': course_name,
                'title': c.get('lessonName', ''),
                'id': secret,
                'url': course_url,
                'progress': c.get('progress', '0%'),
                'type': type_label,
                'courseType': course_type
            })
        
        for n in raw_notices:
            live_id = n.get('liveCourseId', '')
            course_id = n.get('courseId', '')
            recruit_id = n.get('recruitId', '')
            task_name = n.get('taskName', '见面课')
            course_name = n.get('courseName', '未知课程')
            
            if live_id and course_id and recruit_id:
                live_url = f'https://lc.zhihuishu.com/live/vod_room.html?liveId={live_id}&courseId={course_id}&recruitId={recruit_id}'
                courses.append({
                    'name': f'{course_name} - {task_name}',
                    'title': '见面课',
                    'id': f'live_{live_id}',
                    'url': live_url,
                    'progress': '-',
                    'type': '见面课',
                    'courseType': 'live'
                })

        # 存入缓存
        result = {'ok': True, 'courses': courses}
        try:
            cache_full = {}
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_full = json.load(f)
            cache_full[cache_key] = {
                'cached_at': datetime.now().isoformat(),
                'data': result
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_full, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log_system(f"[课程获取] 缓存写入失败: {e}")

        return result

    def get_xuexitong_courses_from_web(self, account_index=0):
        """获取学习通课程列表 - 直接调用 学习通 API (基于 Yatori XueXiTPullCourseAction 原理)"""
        import subprocess
        import json
        from Crypto.Cipher import AES as AES_CTR
        import base64

        # 1. 从 Yatori 配置中读取指定账号
        yatori_config = self._load_yatori_config_data()
        users = yatori_config.get('users', [])
        if account_index >= len(users):
            return {'ok': False, 'message': f'账号索引 {account_index} 超出范围 (共 {len(users)} 个账号)'}

        user = users[account_index]
        account_type = user.get('accountType', '')
        if account_type != 'XUEXITONG':
            return {'ok': False, 'message': f'账号类型 {account_type} 不是学习通，无法获取课程'}

        username = user.get('account', '').strip()
        password = user.get('password', '').strip()
        if not username or not password:
            return {'ok': False, 'message': '账号或密码为空，请先在配置中填写'}

        self.log_system(f"[学习通课程] 正在获取账号 {account_index} 的课程...")

        # 2. 检查本地缓存 (30分钟有效期)
        cache_file = os.path.join(self.get_base_dir(), "data", "course_cache.json")
        cache_key = f"xxt_account_{account_index}"
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                cached_entry = cache_data.get(cache_key)
                if cached_entry:
                    cached_at = datetime.fromisoformat(cached_entry['cached_at'])
                    age = (datetime.now() - cached_at).total_seconds()
                    if age < 1800:
                        self.log_system(f"[学习通课程] 缓存命中 ({(age/60):.0f}分钟前), 直接返回")
                        return cached_entry['data']
                    self.log_system(f"[学习通课程] 缓存已过期 ({(age/60):.0f}分钟前), 重新获取")
        except Exception as e:
            self.log_system(f"[学习通课程] 缓存读取失败: {e}, 将重新获取")

        # 3. AES 加密 + 登录
        AES_KEY = "u2oh6Vu^HWe4_AES"

        def aes_encrypt(message):
            key_bytes = AES_KEY.encode("utf-8")
            iv_bytes = AES_KEY.encode("utf-8")
            block_size = AES_CTR.block_size
            pad_len = block_size - len(message.encode("utf-8")) % block_size
            padded = message.encode("utf-8") + bytes([pad_len] * pad_len)
            cipher = AES_CTR.new(key_bytes, AES_CTR.MODE_CBC, iv_bytes)
            return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")

        session = self._xxt_http_session if hasattr(self, '_xxt_http_session') else None
        if session is None:
            import requests as req_lib
            session = req_lib.Session()
            session.trust_env = False
            self._xxt_http_session = session

        # 登录
        login_url = "https://passport2.chaoxing.com/fanyalogin"
        login_data = {
            "fid": "-1",
            "uname": aes_encrypt(username),
            "password": aes_encrypt(password),
            "refer": "http%253A%252F%252Fi.chaoxing.com",
            "t": "true",
            "forbidotherlogin": "0",
            "validate": "",
            "doubleFactorLogin": "0",
            "independentId": "0",
        }
        login_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        try:
            resp = session.post(login_url, data=login_data, headers=login_headers, timeout=30)
        except Exception as e:
            return {'ok': False, 'message': f'登录请求失败: {e}'}

        # 处理登录响应
        login_ok = False
        if resp.status_code in (302, 301):
            login_ok = True
            redirect_url = resp.headers.get("Location", "http://i.chaoxing.com")
            try:
                session.get(redirect_url, headers=login_headers, timeout=30)
            except Exception:
                pass
        else:
            try:
                login_result = resp.json()
                if login_result.get("mes") == "成功" or login_result.get("status") is True:
                    login_ok = True
                else:
                    return {'ok': False, 'message': f'学习通登录失败: {login_result.get("mes", "未知错误")}'}
            except Exception:
                login_ok = len(session.cookies) > 0

        if not login_ok:
            return {'ok': False, 'message': '学习通登录失败，请检查账号密码'}

        self.log_system(f"[学习通课程] 登录成功 (cookies: {len(session.cookies)} 个)")

        # 4. 获取课程列表
        course_url = "http://mooc1-api.chaoxing.com/mycourse/backclazzdata?view=json&rss=1"
        course_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "http://mooc1-1.chaoxing.com/visit/courses",
        }

        try:
            resp2 = session.get(course_url, headers=course_headers, timeout=30)
            if resp2.status_code != 200:
                return {'ok': False, 'message': f'课程列表请求失败 (HTTP {resp2.status_code})'}
            data = resp2.json()
        except Exception as e:
            return {'ok': False, 'message': f'课程列表请求失败: {e}'}

        # 5. 解析课程数据
        channels = data.get("channelList", [])
        courses = []
        seen_names = set()
        for ch in channels:
            content = ch.get("content") or {}
            course_obj = content.get("course") or {}
            course_data_list = course_obj.get("data") or []
            main_name = content.get("name", "")

            if course_data_list:
                for cd in course_data_list:
                    cname = cd.get("name") or main_name
                    if cname and cname not in seen_names:
                        seen_names.add(cname)
                        courses.append({
                            'name': cname,
                            'courseId': str(cd.get("id", "")),
                            'teacher': cd.get("teacherfactor", ""),
                            'school': cd.get("schools", ""),
                            'imageurl': cd.get("imageurl", ""),
                            'isstart': content.get("isstart", False),
                            'isretire': content.get("isretire", 0),
                        })
            elif main_name and main_name not in seen_names:
                seen_names.add(main_name)
                courses.append({
                    'name': main_name,
                    'courseId': str(ch.get("key", "")),
                    'teacher': '',
                    'school': '',
                    'imageurl': '',
                    'isstart': content.get("isstart", False),
                    'isretire': content.get("isretire", 0),
                })

        self.log_system(f"[学习通课程] 获取到 {len(courses)} 门课程")

        # 6. 存入缓存
        result = {'ok': True, 'courses': courses}
        try:
            cache_full = {}
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_full = json.load(f)
            cache_full[cache_key] = {
                'cached_at': datetime.now().isoformat(),
                'data': result
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_full, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log_system(f"[学习通课程] 缓存写入失败: {e}")

        return result

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

        if not self.qb_running:
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
        """启动器就绪后自动开启题库（根据偏好设置）"""
        if not QB_AVAILABLE:
            return
        # 自动检测并设置 ZError 数据库路径
        self._configure_zerror_db_env()
        # 先刷新 UI 显示 ZError 内置状态
        self._after_qb_status_update()
        auto_start = self._qb_auto_start
        if auto_start:
            self.start_question_bank(silent=True)

    def toggle_question_bank(self):
        """切换题库服务器状态"""
        if self.qb_running:
            self.stop_question_bank()
        else:
            self.start_question_bank()

    def start_question_bank(self, silent=False):
        """启动题库服务器"""
        if not QB_AVAILABLE:
            if not silent:
                self.log_system("题库服务器模块未找到，请确保 题库服务器.py 在同目录下")
            return
        if self.qb_running:
            return
        port = self._qb_port
        
        try:
            self._configure_zerror_db_env()
            if self.qb_server:
                self.qb_server.stop()
            self.qb_server = QuestionBankServer(port=port)
            self.log_system(f"[QB] 创建题库服务器实例，端口={port}")
            self.qb_server.start()
            
            # 验证端口是否监听
            import socket
            import time
            time.sleep(0.5)
            for i in range(5):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                sock.close()
                if result == 0:
                    self.log_system(f"[QB] 端口 {port} 验证成功")
                    break
                else:
                    self.log_system(f"[QB] 端口验证失败 (尝试 {i+1}/5)，错误码: {result}")
                    time.sleep(0.3)
            else:
                self.log_system(f"[QB] 警告: 端口 {port} 验证失败，但服务器标记为运行中")
            
            self.qb_running = True
            self._qb_port = port
            # 应用AI配置
            self._apply_qb_ai_config()
            self._after_qb_status_update()
            if not silent:
                zinfo = self._get_zerror_db_info()
                self.log_system(f"题库服务器已启动 → {self.qb_server.url}")
                if zinfo:
                    self.log_system(f"📦 ZError题库已内置: {zinfo}")
            else:
                self.log_system(f"题库服务器已自动启动 → {self.qb_server.url}")
            self._sync_yatori_question_bank_url()
        except OSError as e:
            self.log_system(f"题库服务器启动失败(端口{port}被占用?): {e}")
            import traceback
            self.log_system(f"[QB] 详细错误: {traceback.format_exc()[:500]}")
            self.qb_running = False
            self._after_qb_status_update()
        except Exception as e:
            self.log_system(f"题库服务器启动异常: {e}")
            import traceback
            self.log_system(f"[QB] 详细错误: {traceback.format_exc()[:500]}")
            self.qb_running = False
            self._after_qb_status_update()

    def stop_question_bank(self):
        """停止题库服务器"""
        if self.qb_server and self.qb_running:
            try:
                self.qb_server.stop()
            except Exception:
                pass
        self.qb_running = False
        self.qb_server = None
        self._after_qb_status_update()
        self.log_system("题库服务器已停止")

    def _import_question_bank(self):
        """导入题库数据"""
        if not QB_AVAILABLE:
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
        if not QB_AVAILABLE:
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
        if self.qb_running and self.qb_server:
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{getattr(self.qb_server, 'port', 8083)}/api/deduplicate",
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
        if not self.qb_running:
            return
        try:
            config = self._load_yatori_config_data()
            setting = config.get('setting', {})
            aqs = setting.get('apiQueSetting', {})
            target_url = f"http://127.0.0.1:{self._qb_port}/query"
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
        if self.qb_running and self.qb_server:
            return f"{self.qb_server.url}/query"
        return None

    def start_all(self):
        """启动所有脚本"""
        self.log_system("正在一键启动所有脚本...")
        if not self.qb_running and QB_AVAILABLE:
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

        if self.ui_mode == 'tk':
            for log_widget in [self.system_log, self.yatori_log, self.autovisor_log]:
                log_widget.config(state='normal')
                log_widget.delete(1.0, tk.END)
                log_widget.config(state='disabled')
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

    def copy_current_log(self):
        """复制当前显示的日志"""
        if self.ui_mode != 'tk':
            return
        tab_id = self.log_notebook.index(self.log_notebook.select())
        if tab_id == 0:
            text = self.system_log.get(1.0, tk.END)
        elif tab_id == 1:
            text = self.yatori_log.get(1.0, tk.END)
        elif tab_id == 2:
            text = self.autovisor_log.get(1.0, tk.END)
        else:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(text.strip())
        self.log_system("日志内容已复制到剪贴板")

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
        """异步检查 Yatori 更新"""
        if not self.core_manager or self.is_updating:
            return
        
        def check():
            try:
                result = self.core_manager.check_yatori_update()
                if result:
                    self.update_info = result
                    has_update = result.get('has_update', False)
                    installed = result.get('installed', False)
                    
                    if has_update and installed:
                        # 有更新可用
                        version = result['info']['version']
                        self._after(0, lambda: self.show_update_available_notification(version))
                    elif not installed:
                        # 未安装
                        self._after(0, self.show_yatori_install_dialog)
            except Exception as e:
                self.log_system(f"检查更新失败: {e}")
        
        thread = threading.Thread(target=check, daemon=True)
        thread.start()

    def show_update_available_notification(self, version):
        """显示更新可用通知"""
        self.log_system(f"检测到 Yatori 新版本: {version}")
        # 更新按钮显示有新版本
        if self.ui_mode == 'tk':
            self.yatori_update_btn.config(text=f"⬆️ 更新可用 ({version})")

    def check_autovisor_update_async(self):
        """异步检查 Autovisor 最新版本"""
        if not self.core_manager:
            self._show_error("提示", "核心管理器初始化失败，无法检查版本。")
            return

        if self.autovisor_version_checking:
            self.log_system("Autovisor 版本检查正在进行中，请稍候。")
            return

        self.autovisor_version_checking = True
        if self.ui_mode == 'tk':
            self.autovisor_update_btn.config(state='disabled', text="⏳ 正在检查...")
        self.log_system("正在检查 Autovisor 最新版本...")

        def check():
            result = None
            error = None
            try:
                result = self.core_manager.check_autovisor_update()
            except Exception as exc:
                error = exc
            finally:
                def finish():
                    self.autovisor_version_checking = False
                    if self.ui_mode == 'tk':
                        self.autovisor_update_btn.config(state='normal', text="⬇️ 检查更新")
                    if error:
                        self.log_system(f"Autovisor 版本检查失败: {error}")
                        self._show_error("Autovisor 版本", f"检查版本失败：\n{error}")
                    elif not result:
                        self.log_system("Autovisor 版本检查失败: 未获取到版本信息")
                        self._show_error("Autovisor 版本", "未能获取最新版本信息，请稍后重试。")
                    else:
                        self.handle_autovisor_version_result(result)

                self._after(0, finish)

        threading.Thread(target=check, daemon=True).start()

    def handle_autovisor_version_result(self, result):
        """处理 Autovisor 版本检查结果"""
        release_info = result.get('info') or result
        latest_version = release_info.get('version', '未知')
        current_version = result.get('version') or self._get_autovisor_display_version()
        has_update = result.get('has_update', False)
        installed = result.get('installed', True)

        self.log_system(f"Autovisor 最新版本: {latest_version}")
        if self.ui_mode == 'tk':
            self.autovisor_update_btn.config(text=f"⬇️ 检查更新 ({latest_version})")

        if not has_update:
            self._show_info(
                "Autovisor 版本",
                f"当前已是最新版本。\n\n当前版本: {current_version}",
            )
            return

        self.autovisor_update_info = result
        message = (
            f"检测到 Autovisor 新版本: {latest_version}\n\n"
            f"当前版本: {current_version if installed else '未安装'}\n\n"
            "是否立即下载并自动覆盖更新？"
        )
        if self.ui_mode == 'web':
            self.log_system(message.replace("\n", " "))
            self.log_system("Web UI 已记录更新信息，可通过 install_autovisor_update 动作触发安装。")
            return

        if self._ask_yes_no("Autovisor 版本", message, default=False):
            self.install_autovisor_update_async(release_info)

    def install_autovisor_update_async(self, release_info=None):
        """后台安装/更新 Autovisor。"""
        if not self.core_manager:
            self._show_error("系统错误", "核心管理器初始化失败")
            return False

        if self.is_updating:
            self._show_info("管理中心", "正在处理任务，请勿重复操作")
            return False

        if self.running.get('autovisor'):
            self._show_warning("Autovisor 更新", "请先停止 Autovisor，再执行核心更新。")
            return False

        if release_info is None:
            release_info = (self.autovisor_update_info or {}).get('info')

        if not release_info:
            self._show_warning("Autovisor 更新", "暂无可安装的版本信息，请先检查更新。")
            return False

        self.is_updating = True
        self.log_system(f"开始安装 Autovisor {release_info.get('version', 'unknown')}...")
        if self.ui_mode == 'tk':
            self.autovisor_update_btn.config(state='disabled', text="⏳ 正在安装...")

        def on_progress(info):
            progress = info.get('progress', 0) or 0
            total = info.get('total')
            downloaded = info.get('downloaded')
            if total:
                self.log_system(f"Autovisor 下载中: {progress:.1f}% ({downloaded}/{total} bytes)", replace_last=True)
            else:
                self.log_system(f"Autovisor 下载中: {downloaded or 0} bytes", replace_last=True)

        def install_worker():
            success = False
            error = None
            try:
                success = self.core_manager.install_autovisor(release_info, on_progress)
            except Exception as exc:
                error = exc
            finally:
                def finish():
                    self.is_updating = False
                    if self.ui_mode == 'tk':
                        self.autovisor_update_btn.config(state='normal', text="⬇️ 检查更新")
                    if success:
                        self.autovisor_update_info = None
                        self.autovisor_path = self.find_autovisor_path(self.get_base_dir())
                        self.log_system("✅ Autovisor 核心处理完成")
                        self._show_info("Autovisor 更新", "Autovisor 核心已成功安装/更新。")
                        if self.ui_mode == 'tk':
                            self.autovisor_version_label.config(text=f"当前核心版本: {self._get_autovisor_display_version()}")
                    else:
                        message = str(error) if error else "下载或安装失败"
                        self.log_system(f"❌ Autovisor 安装失败: {message}")
                        self._show_error("Autovisor 更新", f"{message}\n\n请检查网络设置或稍后重试。")

                self._after(0, finish)

        threading.Thread(target=install_worker, daemon=True).start()
        return True

    def show_yatori_install_dialog(self):
        """显示 Yatori 安装/更新对话框"""
        if self.ui_mode != 'tk':
            self.log_system("检测到 Yatori 需要安装或更新，可稍后通过更新流程处理。")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("核心管理")
        dialog.geometry("500x350")
        dialog.configure(bg=self.colors['card'])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        # 内容容器
        content_frame = tk.Frame(dialog, bg=self.colors['card'], padx=30, pady=30)
        content_frame.pack(fill=tk.BOTH, expand=True)

        if self.update_info and self.update_info.get('installed'):
            title_text = "发现新版本"
            icon_color = self.colors['primary']
            # 修正：从 local_versions 读取当前版本
            current_version = self._get_yatori_display_version()
            latest_version = self.update_info['info']['version']
            message = f"Yatori 有新版本可用\n\n当前版本: {current_version}\n最新版本: {latest_version}"
        else:
            title_text = "安装核心组件"
            icon_color = self.colors['warning']
            message = "检测到环境缺失 Yatori 核心组件。\n\nYatori 用于处理非智慧树平台的课程脚本，包含学习通、英华、各大学院等平台。"

        # 标题
        header_lbl = tk.Label(content_frame, text=title_text, font=('Microsoft YaHei', 16, 'bold'),
                              bg=self.colors['card'], fg=self.colors['text'])
        header_lbl.pack(anchor=tk.W, pady=(0, 5))

        # 描述文字
        msg_lbl = tk.Label(content_frame, text=message, font=('Microsoft YaHei', 10),
                           bg=self.colors['card'], fg=self.colors['text_muted'],
                           wraplength=440, justify=tk.LEFT)
        msg_lbl.pack(anchor=tk.W, pady=(0, 25))

        # 进度区域 (初始隐藏)
        progress_container = tk.Frame(content_frame, bg=self.colors['card'])
        
        progress_bar = ttk.Progressbar(progress_container, style="Modern.Horizontal.TProgressbar",
                                       maximum=100, length=440, mode='determinate')
        progress_bar.pack(fill=tk.X, pady=(0, 10))

        status_label = tk.Label(progress_container, text="准备就绪", font=('Microsoft YaHei', 9),
                                bg=self.colors['card'], fg=self.colors['primary'])
        status_label.pack(anchor=tk.W)

        # 按钮区域
        btn_frame = tk.Frame(content_frame, bg=self.colors['card'])
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)

        def _fmt_size(size_bytes):
            if size_bytes is None:
                return "未知"
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size_bytes < 1024:
                    return f"{size_bytes:.1f} {unit}"
                size_bytes /= 1024
            return f"{size_bytes:.1f} TB"

        def _fmt_time(seconds):
            if seconds is None:
                return "未知"
            seconds = int(seconds)
            if seconds < 60:
                return f"{seconds}秒"
            minutes, sec = divmod(seconds, 60)
            if minutes < 60:
                return f"{minutes}分{sec}秒"
            hours, minutes = divmod(minutes, 60)
            return f"{hours}时{minutes}分{sec}秒"

        def on_progress(info):
            def update():
                progress = info.get('progress', 0) or 0
                downloaded = info.get('downloaded')
                total = info.get('total')
                speed = info.get('speed')
                elapsed = info.get('elapsed')
                eta = info.get('eta')

                progress_bar['value'] = progress

                if total:
                    status_label.config(
                        text=(
                            f"下载中 {progress:.1f}% | "
                            f"{_fmt_size(downloaded)}/{_fmt_size(total)} | "
                            f"{_fmt_size(speed)}/s | "
                            f"已用 {_fmt_time(elapsed)} | "
                            f"剩余 {_fmt_time(eta)}"
                        ),
                        fg=self.colors['primary']
                    )
                else:
                    status_label.config(
                        text=(
                            f"下载中 | {_fmt_size(downloaded)} | "
                            f"{_fmt_size(speed)}/s | "
                            f"已用 {_fmt_time(elapsed)}"
                        ),
                        fg=self.colors['primary']
                    )

            self._after(0, update)

        def do_install():
            """执行安装"""
            if self.is_updating:
                return
            
            self.is_updating = True
            install_btn.pack_forget()
            cancel_btn.config(text="隐藏窗口", command=dialog.destroy)
            
            progress_container.pack(fill=tk.X, pady=10)
            status_label.config(text="正在从 GitHub 下载资源包...")

            def install_thread():
                try:
                    # 获取最新版本信息（如果没有）
                    if not self.update_info or not self.update_info.get('info'):
                        release_info = self.core_manager.get_yatori_latest_release()
                    else:
                        release_info = self.update_info['info']

                    if not release_info:
                        self._after(0, lambda: status_label.config(text="❌ 获取版本信息失败", fg=self.colors['danger']))
                        return

                    # 执行安装
                    success = self.core_manager.install_yatori(release_info, on_progress)
                    
                    if success:
                        self._after(0, lambda: self.on_install_success(dialog))
                    else:
                        self._after(0, lambda: self.on_install_failed(dialog, status_label))
                except Exception as e:
                    self._after(0, lambda: self.on_install_failed(dialog, status_label, str(e)))
                finally:
                    self.is_updating = False

            thread = threading.Thread(target=install_thread, daemon=True)
            thread.start()

        def do_cancel():
            """取消安装"""
            dialog.destroy()
            self.log_system("用户关闭了安装向导")

        install_btn = ttk.Button(btn_frame, text=" 立即下载并安装 ", style="Primary.TButton", command=do_install)
        install_btn.pack(side=tk.RIGHT)

        cancel_btn = ttk.Button(btn_frame, text=" 稍后处理 ", style="Outline.TButton", command=do_cancel)
        cancel_btn.pack(side=tk.RIGHT, padx=10)

    def on_install_success(self, dialog):
        """安装成功回调"""
        self._show_info("管理中心", "Yatori 核心已成功安装/更新！\n\n现在您可以正常启动该核心了。")
        dialog.destroy()
        self.log_system("✅ Yatori 核心处理完成")
        
        # 刷新路径
        self.yatori_path = os.path.join(self.get_base_dir(), "Yatori")
        
        # 清空更新信息，刷新安装状态
        self.update_info = None
        self.is_updating = False
        
        # 更新按钮状态
        if self.ui_mode == 'tk':
            self.yatori_update_btn.config(text="⬇️ 安装/更新核心")
            self.yatori_version_label.config(text=f"当前核心版本: {self._get_yatori_display_version()}")
        
        # 延迟重新检查核心状态
        self._after(500, self.auto_check_cores)

    def on_install_failed(self, dialog, status_label, error=None):
        """安装失败回调"""
        msg = f"操作失败: {error}" if error else "下载失败，请查看系统日志了解详情"
        status_label.config(text=f"❌ {msg}", fg=self.colors['danger'])
        self._show_error("管理中心", f"{msg}\n\n常见原因:\n• 网络连接不稳定\n• GitHub 资源不存在\n• 代理镜像不可用\n\n请查看上方系统日志获取详细错误信息。")
        self.log_system(f"❌ Yatori 安装失败: {error or '请查看日志了解详情'}")

    def show_update_dialog(self):
        """显示手动更新对话框"""
        if not self.core_manager:
            self._show_error("系统错误", "核心管理器初始化失败")
            return
        
        if self.is_updating:
            self._show_info("管理中心", "正在处理任务，请勿重复操作")
            return

        # 重新检查更新
        self.log_system("手动触发版本检查...")
        if self.ui_mode == 'tk':
            self.yatori_update_btn.config(state='disabled', text="⏳ 正在检查...")
        
        def check():
            try:
                result = self.core_manager.check_yatori_update()
                self._after(0, lambda: self.handle_manual_check_result(result))
            finally:
                if self.ui_mode == 'tk':
                    self._after(0, lambda: self.yatori_update_btn.config(state='normal'))
        
        thread = threading.Thread(target=check, daemon=True)
        thread.start()

    def handle_manual_check_result(self, result):
        """处理手动检查结果"""
        if self.ui_mode == 'tk':
            self.yatori_update_btn.config(text="⬇️ 安装/更新核心")
        
        if not result:
            self._show_error("管理中心", "无法连接至 GitHub 节点，请检查网络环境。")
            return
        
        has_update = result.get('has_update', False)
        installed = result.get('installed', False)
        
        if not installed:
            # 未安装
            self.update_info = result
            self.show_yatori_install_dialog()
        elif has_update:
            # 有更新
            self.update_info = result
            version = result['info']['version']
            if self._ask_yes_no("管理中心", 
                                   f"检测到 Yatori 新版本: {version}\n\n"
                                   f"当前版本: {result.get('version', '未知')}\n\n"
                                   f"是否立即开始下载并自动覆盖更新？"):
                self.show_yatori_install_dialog()
        else:
            # 已是最新
            version = result.get('version', '未知')
            self._show_info("管理中心", f"当前已是最新版本 ({version})")

    def on_closing(self, confirmed=False):
        """关闭窗口时清理"""
        if self._preference_enabled('minimizeToTray') and not confirmed:
            self.log_system("已按偏好设置最小化窗口，核心任务继续运行。")
            self._minimize_main_window()
            return

        if self.ui_mode == 'tk' and hasattr(self, '_save_yatori_form'):
            self._save_yatori_form(silent=True)
        elif self.ui_mode != 'tk':
            try:
                self.save_settings_from_web(
                    {
                        'yatori': self._load_yatori_config_data(),
                        'autovisor': self._load_autovisor_config_data(),
                    }
                )
            except Exception:
                pass

        if self.ui_mode == 'tk' and hasattr(self, '_save_autovisor_form'):
            self._save_autovisor_form(silent=True)

        if self.ui_mode == 'web' and self.web_window and not confirmed:
            if self._request_web_exit_confirmation():
                return

        if any(self.running.get(name) for name in ('yatori', 'autovisor', 'practice')):
            if confirmed or self._ask_yes_no("确认退出", "有脚本正在运行，确定要强行退出吗？\n(这可能会导致数据未保存)"):
                self.stop_all()
                self.stop_question_bank()
        else:
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

    root = tk.Tk()
    root.withdraw()

    if not webview:
        app = UnifiedLauncher(root, ui_mode='tk')
        root.mainloop()
        return

    app = UnifiedLauncher(root, ui_mode='web')
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
    try:
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
    finally:
        try:
            root.destroy()
        except Exception:
            pass


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
