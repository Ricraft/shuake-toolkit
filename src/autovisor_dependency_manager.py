"""Autovisor Python dependency and browser runtime preparation."""

from __future__ import annotations

import io
import os
import subprocess
import threading
from collections.abc import Callable

from src.atomic_io import atomic_write_text
from src.config_service import read_ini_config

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows hosts
    winreg = None


PYPI_MIRRORS = (
    ("清华", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"),
    ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
    ("华为", "https://mirrors.huaweicloud.com/repository/pypi/simple"),
    ("官方", "https://pypi.org/simple"),
)

RUNTIME_PACKAGE_SPECS = {
    "playwright": "playwright>=1.52,<2",
    "PyGetWindow": "PyGetWindow>=0.0.9,<1",
    "requests": "requests>=2.32,<3",
}

MODULE_PACKAGES = {
    "playwright": "playwright",
    "pygetwindow": "PyGetWindow",
    "requests": "requests",
}


def _run_in_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


class AutovisorDependencyManager:
    """Prepare script-mode Autovisor dependencies exactly once at a time."""

    def __init__(
        self,
        *,
        get_autovisor_path: Callable[[], str],
        log_system: Callable[[str], None],
        log_line: Callable[..., None],
        is_progress_log: Callable[[str], bool],
        run_logged_command: Callable[..., int],
        schedule: Callable[[int, Callable[[], None]], None],
        on_ready: Callable[[], None],
        show_error: Callable[[str, str], None],
        run_async: Callable[[Callable[[], None]], None] = _run_in_background,
        mirrors=PYPI_MIRRORS,
    ):
        self.get_autovisor_path = get_autovisor_path
        self.log_system = log_system
        self.log_line = log_line
        self.is_progress_log = is_progress_log
        self.run_logged_command = run_logged_command
        self.schedule = schedule
        self.on_ready = on_ready
        self.show_error = show_error
        self.run_async = run_async
        self.mirrors = tuple(mirrors)
        self.installing = False
        self._lock = threading.RLock()

    @property
    def autovisor_path(self) -> str:
        return self.get_autovisor_path()

    def module_available(self, python_executable: str, module_name: str):
        try:
            result = subprocess.run(
                [python_executable, "-c", f"import {module_name}"],
                cwd=self.autovisor_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            return result.returncode == 0, (result.stderr or "").strip()
        except Exception as exc:
            return False, str(exc)

    def check_dependencies(self, python_executable: str):
        self.log_system(f"依赖检查Python: {python_executable}")
        missing = []
        for module_name, package_name in MODULE_PACKAGES.items():
            available, error = self.module_available(python_executable, module_name)
            if available:
                continue
            suffix = f": {error}" if error else ""
            self.log_system(f"  缺失 {package_name} ({module_name}){suffix}")
            missing.append((module_name, package_name, error))
        return missing

    @staticmethod
    def runtime_install_args(missing_package_names=None):
        names = missing_package_names or RUNTIME_PACKAGE_SPECS.keys()
        return [RUNTIME_PACKAGE_SPECS[name] for name in names if name in RUNTIME_PACKAGE_SPECS]

    @staticmethod
    def normalize_browser_name(name):
        normalized = (name or "").strip().lower()
        if normalized in {"edge", "msedge"}:
            return "edge"
        if normalized in {"chrome", "google-chrome"}:
            return "chrome"
        if normalized in {"chromium", "playwright-chromium"}:
            return "chromium"
        return normalized or "chrome"

    @staticmethod
    def _browser_registry_candidates(executable_name):
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

    def find_browser_executable(self, browser_name):
        browser = self.normalize_browser_name(browser_name)
        executable_name = {"chrome": "chrome.exe", "edge": "msedge.exe"}.get(browser)
        if not executable_name:
            return None
        base_dirs = (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        browser_dirs = {
            "chrome": (("Google", "Chrome", "Application"), ("Chrome", "Application")),
            "edge": (("Microsoft", "Edge", "Application"), ("Edge", "Application")),
        }
        candidates = []
        for base_dir in base_dirs:
            if base_dir:
                candidates.extend(
                    os.path.join(base_dir, *parts, executable_name)
                    for parts in browser_dirs[browser]
                )
        candidates.extend(self._browser_registry_candidates(executable_name))
        seen = set()
        for candidate in candidates:
            normalized = os.path.normcase(os.path.normpath(candidate))
            if normalized in seen:
                continue
            seen.add(normalized)
            if os.path.isfile(candidate):
                return os.path.normpath(candidate)
        return None

    @staticmethod
    def has_playwright_chromium():
        custom_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
        cache_roots = [custom_root] if custom_root else [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
            os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright"),
        ]
        for root in cache_roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                if any(name.startswith("chromium-") for name in os.listdir(root)):
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _copy_config_section(parser, source, target):
        if not parser.has_section(source) or parser.has_section(target):
            return False
        parser.add_section(target)
        for option, value in parser.items(source, raw=True):
            parser.set(target, option, value)
        return True

    def prepare_config(self, config_path, _multi_mode=False):
        if not os.path.exists(config_path):
            return {
                "changed": False,
                "needs_playwright_browser": False,
                "browser_summaries": [],
            }
        parser = read_ini_config(config_path)
        changed = False
        summaries = []
        aliases = (
            ("user-account-1", "user-account"),
            ("browser-option-1", "browser-option"),
            ("script-option-1", "script-option"),
            ("course-option-1", "course-option"),
            ("course-url-1", "course-url"),
        )
        for source, target in aliases:
            if self._copy_config_section(parser, source, target):
                changed = True
                summaries.append(f"已兼容单账号配置段: [{source}] -> [{target}]")

        browser_sections = [
            section
            for section in parser.sections()
            if section == "browser-option" or section.startswith("browser-option-")
        ]
        if not browser_sections:
            parser.add_section("browser-option")
            parser.set("browser-option", "driver", "Chrome")
            parser.set("browser-option", "EXE_PATH", "")
            browser_sections = ["browser-option"]
            changed = True
            summaries.append("已补充默认浏览器配置段 [browser-option]")

        needs_playwright_browser = False
        default_driver = "chrome" if self.find_browser_executable("chrome") else "edge"
        for section in browser_sections:
            driver = self.normalize_browser_name(
                parser.get(section, "driver", fallback=default_driver)
            )
            if driver not in {"chrome", "edge", "chromium"}:
                driver = default_driver
                parser.set(section, "driver", "Chrome" if driver == "chrome" else "Edge")
                changed = True

            executable_path = parser.get(section, "EXE_PATH", fallback="").strip()
            if executable_path and not os.path.isfile(executable_path):
                parser.set(section, "EXE_PATH", "")
                executable_path = ""
                changed = True
            if not executable_path:
                detected = self.find_browser_executable(driver)
                if detected:
                    parser.set(section, "EXE_PATH", detected)
                    executable_path = detected
                    changed = True
                    summaries.append(f"{section}: 已自动定位 {driver} 路径 -> {detected}")
            if not executable_path:
                needs_playwright_browser = not self.has_playwright_chromium()
                summaries.append(f"{section}: 未找到 {driver}，将回退到 Playwright Chromium")

        if changed:
            output = io.StringIO()
            parser.write(output)
            atomic_write_text(config_path, output.getvalue())
        return {
            "changed": changed,
            "needs_playwright_browser": needs_playwright_browser,
            "browser_summaries": summaries,
        }

    def install_with_mirrors(self, python_executable, install_args):
        if not install_args:
            return
        total = len(install_args)
        for index, package_spec in enumerate(install_args, 1):
            self.log_system(f"[{index}/{total}] 正在安装 {package_spec} ...")
        last_error = None
        for mirror_name, mirror_url in self.mirrors:
            self.log_system(f"正在尝试通过 {mirror_name} 镜像安装 {total} 个依赖...")
            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            try:
                process = subprocess.Popen(
                    [
                        python_executable,
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--prefer-binary",
                        "--progress-bar",
                        "on",
                        "-i",
                        mirror_url,
                        *install_args,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.autovisor_path,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=environment,
                )
                for line in process.stdout or ():
                    line = line.strip()
                    if line:
                        self.log_line(
                            "system",
                            line,
                            replace_last=self.is_progress_log(line),
                        )
                return_code = process.wait()
            except Exception as exc:
                return_code = -1
                self.log_system(f"安装进程异常: {exc}")
            if return_code == 0:
                self.log_system(f"已通过 {mirror_name} 镜像完成 {total} 个包的安装。")
                return
            last_error = RuntimeError(f"{mirror_name} 镜像安装失败，返回码: {return_code}")
            self.log_system(str(last_error))
        raise last_error or RuntimeError("所有 PyPI 镜像均安装失败")

    def install_async(
        self,
        python_executable,
        missing_packages,
        *,
        ensure_playwright_browser=False,
    ) -> bool:
        normalized_missing = []
        for item in missing_packages:
            if isinstance(item, str):
                normalized_missing.append((item.lower(), item, ""))
            else:
                try:
                    module_name, package_name, error = item
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"无效的依赖描述: {item!r}") from exc
                normalized_missing.append((module_name, package_name, error))
        missing_packages = normalized_missing
        with self._lock:
            if self.installing:
                self.log_system("Autovisor 依赖安装正在进行中，请稍候。")
                return False
            self.installing = True

        targets = [package_name for _, package_name, _ in missing_packages]
        if ensure_playwright_browser:
            targets.append("playwright-browser")
        self.log_system(f"Autovisor runtime setup: {', '.join(targets) or '无需额外安装'}")
        self.log_system("依赖下载与安装进度会显示在系统日志中。")

        def worker():
            success = False
            error_message = ""
            try:
                if missing_packages:
                    missing_names = sorted({name for _, name, _ in missing_packages})
                    install_args = self.runtime_install_args(missing_names)
                    if len(install_args) != len(missing_names):
                        unknown = sorted(set(missing_names) - set(RUNTIME_PACKAGE_SPECS))
                        raise RuntimeError(f"没有安装规则的依赖: {', '.join(unknown)}")
                    self.log_system(
                        f"缺 {len(missing_names)} 个包，正在通过镜像安装: {', '.join(missing_names)}"
                    )
                    self.install_with_mirrors(python_executable, install_args)
                if ensure_playwright_browser:
                    self.log_system("正在准备 Playwright Chromium，下载进度将输出到系统日志...")
                    code = self.run_logged_command(
                        [python_executable, "-m", "playwright", "install", "chromium"],
                        self.autovisor_path,
                    )
                    if code != 0:
                        raise RuntimeError(f"playwright install 返回码: {code}")
                success = True
                self.log_system("Autovisor 依赖安装完成，准备重新启动。")
            except Exception as exc:
                error_message = str(exc)
                self.log_system(f"Autovisor 依赖安装失败: {error_message}")
            finally:
                with self._lock:
                    self.installing = False
                if success:
                    self.schedule(0, self.on_ready)
                else:
                    message = (
                        f"Autovisor 依赖自动安装失败：\n{error_message}\n\n"
                        "请查看系统日志后重试。"
                    )
                    self.schedule(
                        0,
                        lambda message=message: self.show_error("依赖安装失败", message),
                    )

        self.run_async(worker)
        return True
