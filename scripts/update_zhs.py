# -*- coding: utf-8 -*-
r"""升级内置的 zhs 源码副本（zhs/ 目录）。

设计目标：zhs 目录保持“上游原样、零补丁”，启动器的集成代码全部放在自己的文件里，
因此升级 zhs 时不需要逐版改源码，执行本脚本整体覆盖即可。

用法：
    python scripts/update_zhs.py --check                          # 只检查本地是否被改动
    python scripts/update_zhs.py --source "D:\Python\zhs2123\zhs"
    python scripts/update_zhs.py --source <新版目录> --dry-run     # 只比较不写入
    python scripts/update_zhs.py --source <新版目录> --force       # 覆盖本地改动

流程（实现位于 src/zhs_vendor.py）：
1. 用 zhs/UPSTREAM.sha256 校验本地副本是否被改动（改动过且无 --force 则中止）；
2. 与新版目录逐文件比较（CRLF/LF 归一化），打印新增/删除/修改清单；
3. 覆盖文件、删除上游已移除的文件、重写 UPSTREAM.sha256 与 UPSTREAM.json；
4. 若 .runtime/venv-zhs 存在：重装 editable 包、按新版 pyproject 同步
   requirements-zhs.txt 并安装依赖、跑冒烟检查（--version / play --help）。

退出码：0 成功、1 参数或目录错误、2 本地有改动、3 重装失败、4 冒烟失败、
5 依赖安装失败。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import zhs_vendor as vendor  # noqa: E402


def _print_warning(drift: list[str], missing: list[str]) -> None:
    print("[警告] 内置副本与记录不一致（说明有人改过）：")
    for name in drift[:40]:
        print(f"  ~ {name}")
    for name in missing[:40]:
        print(f"  ! 缺失 {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="升级内置 zhs 源码副本")
    parser.add_argument("--source", help="新版 zhs 仓库目录（含 pyproject.toml）")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--check", action="store_true", help="只检查本地副本是否被改动")
    parser.add_argument("--dry-run", action="store_true", help="只比较，不写文件")
    parser.add_argument("--force", action="store_true", help="忽略本地改动强行覆盖")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过重装与冒烟检查")
    parser.add_argument("--git-rev", default="", help="写入 UPSTREAM.json 的上游提交标识")
    parser.add_argument("--source-url", default="", help="写入 UPSTREAM.json 的上游来源链接")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    target = root / "zhs"
    if not (target / "pyproject.toml").exists():
        print(f"[错误] 未找到内置副本：{target}")
        return 1

    drift, missing = vendor.check_drift(target)
    if drift or missing:
        _print_warning(drift, missing)
        if args.check:
            return 2
        if not args.force and not args.dry_run:
            print("[中止] 请确认这些改动是否需要保留；确认后加 --force 覆盖。")
            return 2
    elif args.check:
        print("[通过] 内置副本与 UPSTREAM.sha256 完全一致（零补丁）。")
        return 0

    if not args.source:
        print("[错误] 需要 --source 指定新版 zhs 目录")
        return 1

    source = Path(args.source).resolve()
    if not (source / "pyproject.toml").exists():
        print(f"[错误] --source 不是 zhs 仓库目录：{source}")
        return 1

    try:
        vendor.sync_from_source(
            source,
            target,
            force=args.force,
            dry_run=args.dry_run,
            git_rev=args.git_rev or None,
            source_url=args.source_url,
            log=print,
        )
    except vendor.ZhsVendorError as exc:
        print(f"[错误] {exc}")
        return 1

    if args.dry_run:
        return 0

    if args.skip_smoke:
        print("[跳过] 重装与冒烟检查（指定 --skip-smoke）")
        return 0

    venv_python = vendor.find_venv_python(root)
    if venv_python is None:
        print("[跳过] 冒烟检查（未找到 .runtime/venv-zhs）")
        return 0

    ok, message = vendor.reinstall_editable(root, venv_python=venv_python, log=print)
    if not ok:
        print(f"[失败] {message}")
        return 3
    ok, message = vendor.ensure_requirements(root, venv_python=venv_python, log=print)
    if not ok:
        print(f"[失败] {message}")
        return 5
    ok, message = vendor.smoke_check(root, venv_python=venv_python, log=print)
    if not ok:
        print(f"[失败] {message}")
        return 4
    print("[完成] 冒烟检查全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
