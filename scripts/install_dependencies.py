# -*- coding: utf-8 -*-
"""检查或安装项目依赖。

这是维护脚本，不创建任何桌面窗口。默认只检查依赖；只有显式传入
``--install`` 才会调用 pip，避免误操作当前 Python 环境。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.dependencies import (  # noqa: E402
    CORE_DEPENDENCIES,
    OPTIONAL_DEPENDENCIES,
    WEB_DASHBOARD_DEPENDENCIES,
    Dependency,
    find_missing_dependencies,
)

DEFAULT_INDEX_URL = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
DEFAULT_PLAYWRIGHT_HOST = "https://npmmirror.com/mirrors/playwright"


def selected_dependencies(*, with_dashboard: bool = False) -> tuple[Dependency, ...]:
    """返回本次检查或安装所需的依赖集合。"""
    dependencies = CORE_DEPENDENCIES + OPTIONAL_DEPENDENCIES
    if with_dashboard:
        dependencies += WEB_DASHBOARD_DEPENDENCIES
    return dependencies


def check_dependencies(
    dependencies: Sequence[Dependency],
    *,
    output: Callable[[str], None] = print,
) -> list[str]:
    """检查依赖并返回缺失的 requirement 字符串。"""
    missing = set(find_missing_dependencies(dependencies))
    output(f"Python: {sys.executable}")
    for dependency in dependencies:
        status = "缺失" if dependency.requirement in missing else "正常"
        output(f"[{status}] {dependency.requirement}")
    return [item.requirement for item in dependencies if item.requirement in missing]


def install_dependencies(
    requirements: Sequence[str],
    *,
    python_executable: str,
    index_url: str,
    environment: dict[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    output: Callable[[str], None] = print,
) -> list[str]:
    """逐项安装依赖并返回安装失败的项目。"""
    failures: list[str] = []
    for requirement in requirements:
        output(f"正在安装: {requirement}")
        command = [
            python_executable,
            "-m",
            "pip",
            "install",
            requirement,
            "--index-url",
            index_url,
            "--no-cache-dir",
        ]
        try:
            result = run(command, env=environment, check=False)
        except OSError as exc:
            output(f"[失败] {requirement}: {exc}")
            failures.append(requirement)
            continue
        if result.returncode:
            output(f"[失败] {requirement}: pip 退出码 {result.returncode}")
            failures.append(requirement)
        else:
            output(f"[完成] {requirement}")
    return failures


def install_playwright_browser(
    *,
    python_executable: str,
    download_host: str,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    output: Callable[[str], None] = print,
) -> bool:
    """安装 Playwright Chromium，成功时返回 True。"""
    environment = os.environ.copy()
    environment["PLAYWRIGHT_DOWNLOAD_HOST"] = download_host
    output("正在安装 Playwright Chromium...")
    try:
        result = run(
            [python_executable, "-m", "playwright", "install", "chromium"],
            env=environment,
            check=False,
        )
    except OSError as exc:
        output(f"[失败] Playwright Chromium: {exc}")
        return False
    if result.returncode:
        output(f"[失败] Playwright Chromium: 退出码 {result.returncode}")
        return False
    output("[完成] Playwright Chromium")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查或安装刷课工具包依赖")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="只检查依赖（默认行为）",
    )
    mode.add_argument(
        "--install",
        action="store_true",
        help="安装当前缺失的依赖",
    )
    parser.add_argument(
        "--with-dashboard",
        action="store_true",
        help="同时检查或安装 Autovisor FastAPI 面板依赖",
    )
    parser.add_argument(
        "--install-browser",
        action="store_true",
        help="安装 Playwright Chromium（仅与 --install 一起使用）",
    )
    parser.add_argument("--python", default=sys.executable, help="目标 Python 解释器")
    parser.add_argument("--index-url", default=DEFAULT_INDEX_URL, help="PyPI 镜像地址")
    parser.add_argument(
        "--playwright-download-host",
        default=DEFAULT_PLAYWRIGHT_HOST,
        help="Playwright 浏览器下载镜像",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.install_browser and not args.install:
        print("错误: --install-browser 必须与 --install 一起使用", file=sys.stderr)
        return 2

    dependencies = selected_dependencies(with_dashboard=args.with_dashboard)
    missing = check_dependencies(dependencies)
    if not args.install:
        if missing:
            print(f"检查完成：缺少 {len(missing)} 个依赖。")
            print("需要安装时请重新运行并添加 --install。")
            return 1
        print("检查完成：依赖均可用。")
        return 0

    failures = install_dependencies(
        missing,
        python_executable=args.python,
        index_url=args.index_url,
        environment=os.environ.copy(),
    )
    browser_ok = True
    if args.install_browser and "playwright>=1.52,<2" not in failures:
        browser_ok = install_playwright_browser(
            python_executable=args.python,
            download_host=args.playwright_download_host,
        )

    if failures or not browser_ok:
        print("安装未全部完成，请根据上方错误重试。")
        return 1
    print("依赖安装完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
