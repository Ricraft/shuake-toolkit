"""Prepare the isolated ZHS runtime from any launcher-side bootstrap version.

旧更新器只复制四项白名单，可能遗漏 bootstrap.py 与 ZHS 源码。服务装配前由
launcher_migration 从随 src 下发的兼容包补齐文件，本模块随后复用 bootstrap 的
find_zhs_python / ensure_zhs_runtime。对已有这两个 helper 的旧 bootstrap 也兼容，
无需启动整个安装流程或重复打开桌面窗口。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _log(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(message, flush=True)


def prepare(*, quiet: bool = False, venv_dir: str | None = None) -> int:
    """Create/refresh the ZHS venv; return a process exit code."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import bootstrap
    except Exception as exc:  # pragma: no cover - defensive: broken bootstrap.py
        _log(f"ZHS 运行环境准备失败：无法导入 bootstrap.py（{exc}）", quiet=quiet)
        return 1

    find_python = getattr(bootstrap, "find_zhs_python", None)
    ensure_runtime = getattr(bootstrap, "ensure_zhs_runtime", None)
    if not callable(find_python) or not callable(ensure_runtime):
        _log(
            "ZHS 运行环境准备失败：bootstrap.py 版本过旧，不支持 ZHS 环境准备；"
            "请重新运行 bootstrap.py --install",
            quiet=quiet,
        )
        return 1

    try:
        python_exe = find_python(preferred=sys.executable)
    except Exception as exc:
        _log(f"ZHS 运行环境准备失败：{exc}", quiet=quiet)
        return 1
    if not python_exe:
        _log(
            "未找到 Python 3.13/3.14，无法准备 ZHS 运行环境；"
            "请安装 Python 3.13 后重试。",
            quiet=quiet,
        )
        return 1

    try:
        ensure_runtime(python_exe, venv_dir=venv_dir or None)
    except Exception as exc:
        _log(f"ZHS 核心环境准备失败：{exc}", quiet=quiet)
        return 1
    _log("ZHS 运行环境已就绪", quiet=quiet)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="准备 .runtime/venv-zhs（复用 bootstrap.py 的 ZHS 环境逻辑）"
    )
    parser.add_argument("--quiet", action="store_true", help="不打印日志")
    parser.add_argument("--venv-dir", default="", help="覆盖 ZHS 虚拟环境目录")
    args = parser.parse_args(argv)
    return prepare(quiet=args.quiet, venv_dir=args.venv_dir or None)


if __name__ == "__main__":
    raise SystemExit(main())
