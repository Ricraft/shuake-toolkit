"""Launcher dependency discovery and the legacy auto-install bootstrap.

The dependency list lives here so importing the Web launcher no longer mixes
startup logic with package-management policy. ``requirements.txt`` mirrors
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
    Dependency("webview", "pywebview>=4,<5"),
)

OPTIONAL_DEPENDENCIES = ()

# Autovisor 的独立 FastAPI 面板依赖。统一启动器本身不需要这些包，
# 因此不把它们混入默认运行依赖或启动时的自动安装流程。
WEB_DASHBOARD_DEPENDENCIES = (
    Dependency("fastapi", "fastapi>=0.115,<1"),
    Dependency("uvicorn", "uvicorn>=0.30,<1"),
    Dependency("pydantic", "pydantic>=2,<3"),
)


def find_missing_dependencies(
    dependencies: Iterable[Dependency],
    import_module: Callable[[str], object] = importlib.import_module,
) -> list[str]:
    """Return requirements whose import modules are missing or broken."""
    missing = []
    for dependency in dependencies:
        try:
            import_module(dependency.module)
        except Exception:
            missing.append(dependency.requirement)
    return missing


def ensure_core_dependencies(
    *,
    include_optional: bool = True,
    python_executable: str | None = None,
    index_url: str = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple",
    import_module: Callable[[str], object] = importlib.import_module,
    install: Callable[..., object] = subprocess.check_call,
    output: Callable[[str], None] = print,
) -> list[str]:
    """Install missing launcher dependencies and return failed requirements.

    Auto-install is retained for compatibility with the existing launcher, but
    it is now isolated behind a function and reports failures to the caller.
    Heavy OpenCV/NumPy wheels are intentionally managed by Autovisor's local
    ``runtime_deps`` directory instead of being installed into global Python.
    """
    dependencies = CORE_DEPENDENCIES + (OPTIONAL_DEPENDENCIES if include_optional else ())
    missing = find_missing_dependencies(
        dependencies,
        import_module=import_module,
    )
    if not missing:
        return []

    if getattr(sys, "frozen", False):
        output("[启动器] 打包环境缺少内置依赖，已跳过无效的自安装尝试")
        output(f"[启动器] 缺失或损坏: {', '.join(missing)}")
        return missing

    executable = python_executable or sys.executable
    output(
        f"[启动器] 检测到 {len(missing)} 个依赖缺失或损坏，"
        "正在自动安装..."
    )
    output(f"[启动器] 待安装: {', '.join(missing)}")
    dependency_by_requirement = {
        dependency.requirement: dependency
        for dependency in dependencies
    }
    failures = []
    for requirement in missing:
        try:
            install(
                [executable, "-m", "pip", "install", requirement, "-i", index_url],
                timeout=120,
            )
        except Exception as exc:
            failures.append(requirement)
            output(f"[启动器] ✗ {requirement} 安装失败: {exc}")
            output(f"[启动器]   可手动运行: pip install {requirement}")
            continue

        importlib.invalidate_caches()
        dependency = dependency_by_requirement[requirement]
        try:
            import_module(dependency.module)
        except Exception as exc:
            failures.append(requirement)
            output(
                f"[启动器] ✗ {requirement} 安装后仍无法加载: "
                f"{type(exc).__name__}: {str(exc)[:120]}"
            )
            continue
        output(f"[启动器] ✓ {requirement} 安装并加载成功")
    output("[启动器] 依赖检查完成")
    return failures
