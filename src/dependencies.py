"""Launcher dependency discovery and the legacy auto-install bootstrap.

The dependency list lives here so importing the launcher no longer mixes UI
construction with package-management policy.  ``requirements.txt`` mirrors
these constraints.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class Dependency:
    module: str
    requirement: str


CORE_DEPENDENCIES = (
    Dependency("yaml", "PyYAML>=6.0.1,<7"),
    Dependency("requests", "requests>=2.32,<3"),
    Dependency("pygetwindow", "PyGetWindow>=0.0.9,<1"),
    Dependency("jieba", "jieba>=0.42.1,<1"),
    Dependency("Crypto", "pycryptodome>=3.20,<4"),
    Dependency("playwright", "playwright>=1.52,<2"),
)

OPTIONAL_DEPENDENCIES = (
    Dependency("webview", "pywebview>=4,<5"),
)


def find_missing_dependencies(
    dependencies: Iterable[Dependency],
    import_module: Callable[[str], object] = importlib.import_module,
) -> list[str]:
    """Return requirement strings whose import modules are unavailable."""
    missing = []
    for dependency in dependencies:
        try:
            import_module(dependency.module)
        except ImportError:
            missing.append(dependency.requirement)
    return missing


def ensure_core_dependencies(
    *,
    include_optional: bool = True,
    python_executable: str | None = None,
    index_url: str = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple",
) -> list[str]:
    """Install missing launcher dependencies and return failed requirements.

    Auto-install is retained for compatibility with the existing launcher, but
    it is now isolated behind a function and reports failures to the caller.
    Heavy OpenCV/NumPy wheels are intentionally managed by Autovisor's local
    ``runtime_deps`` directory instead of being installed into global Python.
    """
    dependencies = CORE_DEPENDENCIES + (OPTIONAL_DEPENDENCIES if include_optional else ())
    missing = find_missing_dependencies(dependencies)
    if not missing:
        return []

    if getattr(sys, "frozen", False):
        print("[启动器] 打包环境缺少内置依赖，已跳过无效的自安装尝试")
        print(f"[启动器] 缺失: {', '.join(missing)}")
        return missing

    executable = python_executable or sys.executable
    print(f"[启动器] 检测到 {len(missing)} 个依赖缺失，正在自动安装...")
    print(f"[启动器] 待安装: {', '.join(missing)}")
    failures = []
    for requirement in missing:
        try:
            subprocess.check_call(
                [executable, "-m", "pip", "install", requirement, "-i", index_url],
                timeout=120,
            )
            print(f"[启动器] ✓ {requirement} 安装成功")
        except Exception as exc:
            failures.append(requirement)
            print(f"[启动器] ✗ {requirement} 安装失败: {exc}")
            print(f"[启动器]   可手动运行: pip install {requirement}")
    print("[启动器] 依赖检查完成")
    return failures
