# -*- coding: utf-8 -*-
"""First-run bootstrap for the networked one-click release package.

The module deliberately depends on the Python standard library only until a
local virtual environment exists.  It prepares the dependencies that the
normal launcher cannot safely prepare for itself (pip packages, Playwright
Chromium, Autovisor's binary runtime and the Yatori core) and then launches
``统一启动器.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from src.core_manager import CoreManager
except Exception:  # pragma: no cover - bootstrap must remain standalone
    CoreManager = None  # type: ignore

try:
    from src.dependencies import CORE_DEPENDENCIES
except Exception:  # pragma: no cover - defensive fallback
    CORE_DEPENDENCIES = ()

FALLBACK_CORE_MODULES = (
    "yaml",
    "requests",
    "pygetwindow",
    "jieba",
    "Crypto",
    "playwright",
    "webview",
)

RUNTIME_DIR_NAME = ".runtime"
VENV_DIR_NAME = "venv"
LOG_DIR_NAME = "logs"
FULL_MANIFEST_NAME = "full-manifest.json"
REQUIREMENTS_NAME = "requirements.txt"
LAUNCHER_NAME = "统一启动器.py"

YATORI_DIR_NAME = "Yatori"
YATORI_CORE_NAME = "yatori-go-console.exe"
AUTOVISOR_DIR_NAME = "Autovisor"
AUTOVISOR_RUNTIME_SCRIPT = "download_runtime_deps.py"

PYPI_MIRRORS = (
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
    ("华为", "https://mirrors.huaweicloud.com/repository/pypi/simple"),
    ("官方", "https://pypi.org/simple"),
)
PLAYWRIGHT_HOSTS = (
    ("npmmirror", "https://npmmirror.com/mirrors/playwright"),
    ("官方", None),
)

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
MIN_PYTHON = (3, 11)
MAX_PYTHON = (3, 13)


@dataclass(frozen=True)
class PythonProbe:
    executable: str
    version: tuple[int, ...]


class BootstrapLogger:
    """Print to the console and mirror everything into one log file."""

    def __init__(
        self,
        log_dir: str | os.PathLike[str] | None,
        *,
        echo: bool = True,
    ):
        self.echo = echo
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.path: Path | None = None
        self._handle = None
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.path = self.log_dir / f"bootstrap_{stamp}.log"
            self._handle = self.path.open("a", encoding="utf-8", errors="replace")

    def _write(self, prefix: str, message: str) -> None:
        text = f"[{datetime.now().strftime('%H:%M:%S')}] {prefix}{message}"
        if self._handle is not None:
            try:
                self._handle.write(text + "\n")
                self._handle.flush()
            except Exception:
                pass
        if self.echo:
            print(text, flush=True)

    def info(self, message: str) -> None:
        self._write("", message)

    def warning(self, message: str) -> None:
        self._write("[WARN] ", message)

    def error(self, message: str) -> None:
        self._write("[ERROR] ", message)

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:
            pass

    def __enter__(self) -> "BootstrapLogger":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _emit(logger: Any, message: str, level: str = "info") -> None:
    if logger is None:
        return
    method = getattr(logger, level, None)
    if callable(method):
        method(message)
        return
    if callable(logger):
        logger(message)


def run_process(
    command: Iterable[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    capture: bool = False,
    run: Callable[..., Any] = subprocess.run,
) -> Any:
    """Run a child process with a small, testable wrapper."""
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": env,
        "timeout": timeout,
        "check": False,
    }
    if capture:
        kwargs.update(
            {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
        )
    return run([str(part) for part in command], **kwargs)


def parse_version_output(text: str | None) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def is_supported_python(version: tuple[int, ...] | None) -> bool:
    if not version or len(version) < 2:
        return False
    current = (version[0], version[1])
    return MIN_PYTHON <= current <= MAX_PYTHON


def _candidate_python_commands(which: Callable[[str], str | None]) -> list[list[str]]:
    candidates: list[list[str]] = []
    if sys.executable:
        candidates.append([sys.executable])
    py_launcher = which("py")
    if py_launcher:
        for version in ("3.13", "3.12", "3.11"):
            candidates.append([py_launcher, f"-{version}"])
    for name in ("python", "python3"):
        path = which(name)
        if path:
            candidates.append([path])
    return candidates


def _probe_python(
    command: list[str],
    *,
    run: Callable[..., Any] = subprocess.run,
) -> PythonProbe | None:
    probe_code = (
        "import json,sys;"
        "print(json.dumps([sys.version_info[0], sys.version_info[1], sys.executable]))"
    )
    try:
        result = run_process(
            [*command, "-c", probe_code],
            capture=True,
            run=run,
        )
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    output = str(getattr(result, "stdout", "") or "").strip().splitlines()
    if not output:
        return None

    # The probe emits JSON so sys.executable remains intact even when its path
    # contains spaces.  Accept the earlier whitespace format as well for
    # compatibility with existing callers and lightweight test doubles.
    try:
        payload = json.loads(output[-1])
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, list) and len(payload) == 3:
        raw_major, raw_minor, executable = payload
    else:
        parts = output[-1].split(maxsplit=2)
        if len(parts) < 3:
            return None
        raw_major, raw_minor, executable = parts
    try:
        version = (int(raw_major), int(raw_minor))
    except (TypeError, ValueError):
        return None
    if not isinstance(executable, str) or not executable:
        return None
    return PythonProbe(executable, version)


def find_python(
    *,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    logger: Any = None,
) -> str | None:
    """Find a usable Python 3.11-3.13 interpreter."""
    fallback: str | None = None
    for command in _candidate_python_commands(which):
        probe = _probe_python(command, run=run)
        if probe is None:
            continue
        fallback = fallback or probe.executable
        if is_supported_python(probe.version):
            _emit(
                logger,
                "Python: "
                f"{probe.executable} "
                f"({probe.version[0]}.{probe.version[1]})",
            )
            return probe.executable
    if fallback:
        _emit(
            logger,
            f"[WARN] 未找到 3.11-3.14 的 Python，将尝试使用: {fallback}",
            "warning",
        )
        return fallback
    return None


def venv_python_path(
    venv_dir: str | os.PathLike[str],
    *,
    os_name: str | None = None,
) -> Path:
    name = os.name if os_name is None else os_name
    if name == "nt":
        return Path(venv_dir) / "Scripts" / "python.exe"
    return Path(venv_dir) / "bin" / "python"


def venv_pythonw_path(
    venv_dir: str | os.PathLike[str],
    *,
    os_name: str | None = None,
) -> Path:
    name = os.name if os_name is None else os_name
    if name == "nt":
        return Path(venv_dir) / "Scripts" / "pythonw.exe"
    return Path(venv_dir) / "bin" / "python"


def ensure_venv(
    python_exe: str,
    *,
    venv_dir: str | os.PathLike[str] | None = None,
    run: Callable[..., Any] = subprocess.run,
    logger: Any = None,
) -> Path:
    if venv_dir is None:
        venv_dir = PROJECT_ROOT / RUNTIME_DIR_NAME / VENV_DIR_NAME
    venv_dir = Path(venv_dir)
    target = venv_python_path(venv_dir)
    if target.is_file():
        _emit(logger, f"本地运行环境已存在: {venv_dir}")
        return target

    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    _emit(logger, f"正在创建本地运行环境: {venv_dir}")
    result = run_process([python_exe, "-m", "venv", str(venv_dir)], run=run)
    if getattr(result, "returncode", 1) != 0 or not target.is_file():
        raise RuntimeError(f"创建虚拟环境失败: {venv_dir}")
    return target


def default_core_modules() -> tuple[str, ...]:
    modules = tuple(
        dependency.module
        for dependency in CORE_DEPENDENCIES
        if getattr(dependency, "module", None)
    )
    return modules or FALLBACK_CORE_MODULES


def module_imports_ok(
    python_exe: str | os.PathLike[str],
    modules: Iterable[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> bool:
    module_list = list(modules)
    if not module_list:
        return True
    code = "import importlib;" + ";".join(
        f"importlib.import_module({module!r})" for module in module_list
    )
    try:
        result = run_process(
            [python_exe, "-c", code],
            cwd=cwd,
            capture=True,
            run=run,
        )
    except Exception:
        return False
    return getattr(result, "returncode", 1) == 0


def build_pip_install_command(
    venv_python: str | os.PathLike[str],
    mirror_url: str,
    requirements_file: str | os.PathLike[str],
) -> list[str]:
    return [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "-r",
        str(requirements_file),
        "--disable-pip-version-check",
        "--prefer-binary",
        "--no-color",
        "-i",
        mirror_url,
    ]


def ensure_requirements(
    venv_python: str | os.PathLike[str],
    *,
    requirements_file: str | os.PathLike[str] | None = None,
    mirrors: tuple[tuple[str, str], ...] = PYPI_MIRRORS,
    run: Callable[..., Any] = subprocess.run,
    logger: Any = None,
    import_check: Callable[..., bool] = module_imports_ok,
) -> None:
    requirements_file = Path(requirements_file or (PROJECT_ROOT / REQUIREMENTS_NAME))
    modules = default_core_modules()
    if import_check(str(venv_python), modules, run=run):
        _emit(logger, "Python 依赖已可用，跳过安装")
        return

    pip_probe = run_process(
        [venv_python, "-m", "pip", "--version"],
        capture=True,
        run=run,
    )
    if getattr(pip_probe, "returncode", 1) != 0:
        _emit(logger, "venv 中缺少 pip，正在执行 ensurepip ...")
        ensurepip = run_process(
            [venv_python, "-m", "ensurepip", "--upgrade"],
            capture=True,
            run=run,
        )
        if getattr(ensurepip, "returncode", 1) != 0:
            raise RuntimeError("venv 中 pip 不可用，且 ensurepip 失败")

    last_error = ""
    for mirror_name, mirror_url in mirrors:
        _emit(logger, f"正在通过 {mirror_name} 安装 Python 依赖...")
        result = run_process(
            build_pip_install_command(venv_python, mirror_url, requirements_file),
            cwd=PROJECT_ROOT,
            run=run,
        )
        if getattr(result, "returncode", 1) == 0 and import_check(
            str(venv_python), modules, run=run
        ):
            _emit(logger, f"Python 依赖安装完成（{mirror_name}）")
            return
        last_error = f"{mirror_name} 安装失败，返回码 {getattr(result, 'returncode', '?')}"
        _emit(logger, last_error, "warning")

    raise RuntimeError(f"Python 依赖安装失败：{last_error}")


def has_system_browser(
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> bool:
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        if which(name):
            return True
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relative_paths = (
        ("Google", "Chrome", "Application", "chrome.exe"),
        ("Microsoft", "Edge", "Application", "msedge.exe"),
    )
    for root in roots:
        if not root:
            continue
        for parts in relative_paths:
            if Path(root).joinpath(*parts).is_file():
                return True
    return False


def has_playwright_chromium() -> bool:
    custom_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    roots = [custom_root] if custom_root else [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            if any(
                name.startswith("chromium-")
                for name in os.listdir(root)
            ):
                return True
        except OSError:
            continue
    return False


def ensure_playwright_browser(
    venv_python: str | os.PathLike[str],
    *,
    hosts: tuple[tuple[str, str | None], ...] = PLAYWRIGHT_HOSTS,
    run: Callable[..., Any] = subprocess.run,
    logger: Any = None,
) -> None:
    if has_system_browser():
        _emit(logger, "检测到系统 Chrome/Edge，跳过 Playwright Chromium 安装")
        return
    if has_playwright_chromium():
        _emit(logger, "Playwright Chromium 已存在")
        return

    last_error = ""
    for host_name, host_url in hosts:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if host_url:
            env["PLAYWRIGHT_DOWNLOAD_HOST"] = host_url
        _emit(logger, f"正在通过 {host_name} 准备 Playwright Chromium ...")
        result = run_process(
            [venv_python, "-m", "playwright", "install", "chromium"],
            cwd=PROJECT_ROOT,
            env=env,
            timeout=1800,
            run=run,
        )
        if getattr(result, "returncode", 1) == 0 and has_playwright_chromium():
            _emit(logger, f"Playwright Chromium 安装完成（{host_name}）")
            return
        last_error = f"{host_name} 安装失败，返回码 {getattr(result, 'returncode', '?')}"
        _emit(logger, last_error, "warning")

    raise RuntimeError(f"Playwright Chromium 安装失败：{last_error}")


def autovisor_runtime_ready(
    venv_python: str | os.PathLike[str],
    *,
    run: Callable[..., Any] = subprocess.run,
) -> bool:
    autovisor_dir = PROJECT_ROOT / AUTOVISOR_DIR_NAME
    runtime_dir = autovisor_dir / "runtime_deps"
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(autovisor_dir)!r});"
        f"sys.path.insert(0, {str(runtime_dir)!r});"
        "import cv2, numpy;"
        "print(cv2.__version__)"
    )
    try:
        result = run_process(
            [venv_python, "-c", code],
            cwd=PROJECT_ROOT,
            capture=True,
            run=run,
        )
    except Exception:
        return False
    return getattr(result, "returncode", 1) == 0


def ensure_autovisor_runtime(
    venv_python: str | os.PathLike[str],
    *,
    run: Callable[..., Any] = subprocess.run,
    logger: Any = None,
) -> None:
    autovisor_dir = PROJECT_ROOT / AUTOVISOR_DIR_NAME
    script = autovisor_dir / AUTOVISOR_RUNTIME_SCRIPT
    if not script.is_file():
        raise RuntimeError(
            f"未找到 {script}，完整包不完整，无法准备 Autovisor 二进制依赖"
        )
    if autovisor_runtime_ready(venv_python, run=run):
        _emit(logger, "Autovisor runtime_deps 已就绪")
        return

    _emit(logger, "正在下载 Autovisor 运行时依赖（numpy/opencv）...")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = run_process(
        [venv_python, str(script)],
        cwd=autovisor_dir,
        env=env,
        timeout=1800,
        run=run,
    )
    if getattr(result, "returncode", 1) != 0 or not autovisor_runtime_ready(
        venv_python, run=run
    ):
        raise RuntimeError("Autovisor 运行时依赖准备失败")


def read_full_manifest(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = Path(root or PROJECT_ROOT) / FULL_MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def yatori_core_installed(root: str | os.PathLike[str] | None = None) -> bool:
    return (Path(root or PROJECT_ROOT) / YATORI_DIR_NAME / YATORI_CORE_NAME).is_file()


def _build_yatori_release_info(
    manifest: dict[str, Any] | None,
    *,
    url_override: str | None = None,
    sha256_override: str | None = None,
    size_override: int | None = None,
    version_override: str | None = None,
) -> dict[str, Any] | None:
    if url_override:
        info: dict[str, Any] = {
            "version": version_override or "release-asset",
            "download_url": url_override,
            "asset_size": size_override,
            "digest": None,
        }
        if sha256_override:
            digest = sha256_override.strip()
            info["digest"] = digest if digest.lower().startswith("sha256:") else f"sha256:{digest}"
        return info

    core = (manifest or {}).get("yatoriCore")
    if not isinstance(core, dict):
        return None
    url = str(core.get("url") or "").strip()
    sha256 = str(core.get("sha256") or "").strip()
    if not url:
        return None
    info = {
        "version": str(core.get("version") or version_override or "release-asset"),
        "download_url": url,
        "asset_size": core.get("size", size_override),
        "digest": None,
    }
    if sha256:
        info["digest"] = sha256 if sha256.lower().startswith("sha256:") else f"sha256:{sha256}"
    return info


def ensure_yatori_core(
    *,
    root: str | os.PathLike[str] | None = None,
    manifest: dict[str, Any] | None = None,
    run: Callable[..., Any] = subprocess.run,
    logger: Any = None,
    url_override: str | None = None,
    sha256_override: str | None = None,
    size_override: int | None = None,
    version_override: str | None = None,
) -> bool:
    root_path = Path(root or PROJECT_ROOT)
    if yatori_core_installed(root_path):
        _emit(logger, "Yatori 核心已存在")
        return True
    if CoreManager is None:
        _emit(logger, "无法导入 CoreManager，跳过 Yatori 核心准备", "warning")
        return False

    manager = CoreManager(str(root_path), log_callback=lambda message: _emit(logger, f"[Yatori] {message}"))
    release_info = _build_yatori_release_info(
        manifest,
        url_override=url_override,
        sha256_override=sha256_override,
        size_override=size_override,
        version_override=version_override,
    )
    if release_info is None:
        try:
            release_info = manager.get_yatori_latest_release()
        except Exception as exc:
            _emit(logger, f"检查 Yatori 最新版本失败: {exc}", "warning")
            release_info = None

    if not release_info:
        _emit(logger, "未获取到可用的 Yatori 核心下载信息", "warning")
        return False

    try:
        success = bool(manager.install_yatori(release_info, None))
    except Exception as exc:
        _emit(logger, f"Yatori 核心安装异常: {exc}", "warning")
        return False
    if success and yatori_core_installed(root_path):
        _emit(logger, "Yatori 核心准备完成")
        return True
    _emit(logger, "Yatori 核心未安装成功，启动器仍可启动，稍后可在界面重试", "warning")
    return False


def webview2_installed(*, os_name: str | None = None) -> bool:
    name = os.name if os_name is None else os_name
    if name != "nt":
        return True
    roots = (
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    )
    for root in roots:
        if not root:
            continue
        application_root = Path(root) / "Microsoft" / "EdgeWebView" / "Application"
        try:
            if application_root.is_dir() and any(
                (item / "msedgewebview2.exe").is_file()
                for item in application_root.iterdir()
                if item.is_dir()
            ):
                return True
        except OSError:
            continue
    return False


def ensure_webview2(*, logger: Any = None, os_name: str | None = None) -> bool:
    if webview2_installed(os_name=os_name):
        _emit(logger, "WebView2 Runtime 检测通过")
        return True
    _emit(
        logger,
        "未检测到 Microsoft Edge WebView2 Runtime。",
        "error",
    )
    _emit(logger, f"请先安装 WebView2 Runtime: {WEBVIEW2_URL}", "error")
    _emit(logger, "安装完成后重新运行 启动依赖.cmd。", "error")
    return False


def launch_launcher(
    venv_python: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
    debug: bool = False,
    run: Callable[..., Any] = subprocess.Popen,
) -> int:
    root_path = Path(root or PROJECT_ROOT)
    launcher = root_path / LAUNCHER_NAME
    if not launcher.is_file():
        raise RuntimeError(f"未找到启动器入口: {launcher}")
    python_exe = Path(venv_python)
    pythonw = venv_pythonw_path(python_exe.parent.parent)
    executable = pythonw if pythonw.is_file() else python_exe
    command = [str(executable), str(launcher)]
    if debug:
        command.append("--dev")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = run(command, cwd=str(root_path), env=env)

    # Do not wait for the GUI for its full lifetime.  Give Popen a short
    # bounded window to expose an immediate startup failure; a still-running
    # process (or a simple test double without wait/poll) is considered started.
    wait = getattr(process, "wait", None)
    returncode: Any = None
    if callable(wait):
        try:
            returncode = wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return 0
        except TypeError:
            # Some lightweight Popen fakes expose wait() without the timeout
            # argument.  Never call that potentially-unbounded method.
            poll = getattr(process, "poll", None)
            if callable(poll):
                returncode = poll()
    else:
        poll = getattr(process, "poll", None)
        if callable(poll):
            returncode = poll()

    if returncode is None:
        returncode = getattr(process, "returncode", None)
    if isinstance(returncode, int):
        raise RuntimeError(f"启动器在启动确认期间提前退出，返回码: {returncode}")
    return 0


def run_checks(*, logger: Any = None) -> int:
    """Perform a read-only diagnostic for --check."""
    _emit(logger, f"当前 Python: {sys.executable} ({sys.version.split()[0]})")
    results = (
        ("Python 解释器", bool(sys.executable)),
        ("requirements.txt", (PROJECT_ROOT / REQUIREMENTS_NAME).is_file()),
        ("bootstrap.py", (PROJECT_ROOT / "bootstrap.py").is_file()),
        ("Autovisor/download_runtime_deps.py", (PROJECT_ROOT / AUTOVISOR_DIR_NAME / AUTOVISOR_RUNTIME_SCRIPT).is_file()),
        ("Yatori 核心", yatori_core_installed()),
        ("系统 Chrome/Edge", has_system_browser()),
        ("WebView2 Runtime", webview2_installed()),
    )
    failed = False
    for label, ok in results:
        _emit(logger, f"[{'OK' if ok else 'MISSING'}] {label}")
        failed = failed or not ok
    _emit(logger, "检查完成。" + ("存在缺失项。" if failed else "依赖均可用。"))
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="刷课工具包一键依赖引导")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="安装依赖并启动（默认入口使用）")
    mode.add_argument("--check", action="store_true", help="只检查，不安装、不启动")
    parser.add_argument("--no-launch", action="store_true", help="准备完成后不启动启动器")
    parser.add_argument("--skip-yatori", action="store_true", help="跳过 Yatori 核心准备")
    parser.add_argument("--require-yatori", action="store_true", help="Yatori 准备失败时中止")
    parser.add_argument("--skip-autovisor-runtime", action="store_true", help="跳过 Autovisor runtime_deps")
    parser.add_argument("--skip-webview2-check", action="store_true", help="跳过 WebView2 检测")
    parser.add_argument("--debug", action="store_true", help="以 --dev 模式启动启动器")
    parser.add_argument("--venv-dir", default=str(PROJECT_ROOT / RUNTIME_DIR_NAME / VENV_DIR_NAME), help="虚拟环境目录")
    parser.add_argument("--log-dir", default=str(PROJECT_ROOT / LOG_DIR_NAME), help="日志目录")
    parser.add_argument("--quiet", action="store_true", help="不向控制台打印日志")
    parser.add_argument("--yatori-url", help="覆盖 Yatori 核心下载 URL")
    parser.add_argument("--yatori-sha256", help="覆盖 Yatori 核心 SHA-256")
    parser.add_argument("--yatori-size", type=int, help="覆盖 Yatori 核心大小")
    parser.add_argument("--yatori-version", help="覆盖 Yatori 核心版本名")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # --check is documented as read-only: use a console-only logger and do
        # not create the project's logs directory or a bootstrap log file.
        logger = BootstrapLogger(
            None if args.check else args.log_dir,
            echo=not args.quiet,
        )
    except Exception as exc:
        # Logger setup must not be the one failure that escapes the CLI with a
        # traceback or attempts to log recursively to the failed destination.
        print(
            f"一键引导失败：无法初始化日志（{exc}）。"
            "请检查日志目录的写入权限和磁盘空间。",
            file=sys.stderr,
            flush=True,
        )
        return 1

    if logger.path is not None:
        logger.info(f"一键引导日志: {logger.path}")
    try:
        if args.check:
            return run_checks(logger=logger)
        if not args.install:
            build_parser().print_help()
            return 0

        python_exe = find_python(logger=logger)
        if not python_exe:
            logger.error("未找到可用的 Python 3.11-3.14，请先安装 Python 并勾选 Add to PATH。")
            return 1

        venv_python = ensure_venv(
            python_exe,
            venv_dir=args.venv_dir,
            logger=logger,
        )
        ensure_requirements(venv_python, logger=logger)
        ensure_playwright_browser(venv_python, logger=logger)

        if not args.skip_autovisor_runtime:
            ensure_autovisor_runtime(venv_python, logger=logger)

        if not args.skip_yatori:
            manifest = read_full_manifest(PROJECT_ROOT)
            yatori_ok = ensure_yatori_core(
                manifest=manifest,
                logger=logger,
                url_override=args.yatori_url,
                sha256_override=args.yatori_sha256,
                size_override=args.yatori_size,
                version_override=args.yatori_version,
            )
            if not yatori_ok and args.require_yatori:
                logger.error("Yatori 核心未就绪，已按 --require-yatori 中止。")
                return 1

        if not args.skip_webview2_check and not ensure_webview2(logger=logger):
            return 1

        if args.no_launch:
            logger.info("依赖准备完成；已按 --no-launch 跳过启动。")
            return 0

        launch_launcher(venv_python, root=PROJECT_ROOT, debug=args.debug)
        logger.info("启动器已启动。")
        return 0
    except Exception as exc:
        logger.error(f"引导失败: {exc}")
        if logger.path is not None:
            logger.error(traceback.format_exc())
            print()
            print("=" * 60)
            print("一键引导失败，请把 logs 下的 bootstrap 日志发给维护者。")
            print(f"错误摘要: {exc}")
            print("=" * 60)
        else:
            # --check has no log file by design; still expose errors when
            # --quiet was requested rather than silently swallowing a failure.
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            print(f"只读检查失败：{exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())

