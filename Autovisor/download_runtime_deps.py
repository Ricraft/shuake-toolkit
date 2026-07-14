#!/usr/bin/env python3
# encoding=utf-8
"""Download Autovisor's optional binary wheels into ``runtime_deps``.

The actual wheel selection and extraction policy is shared with runtime startup
in ``modules.installer``.  Keeping one implementation prevents the old helper
from drifting to an unrelated package list.
"""

from __future__ import annotations

import sys

from runtime_bootstrap import activate_runtime_dependencies


def main() -> int:
    runtime_path = activate_runtime_dependencies()
    from modules import installer

    print("=" * 60)
    print("Autovisor 可选运行时依赖下载器")
    print(f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"目标目录: {runtime_path}")
    print("=" * 60)
    modules = installer.start()
    if len(modules) != len(installer.packages):
        print("依赖准备不完整")
        return 1
    print("运行时依赖已就绪: " + ", ".join(installer.packages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
