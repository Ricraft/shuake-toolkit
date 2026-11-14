"""Build the icon-bearing Windows GUI bootstrapper using the installed C# compiler."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXE_NAME = "统一启动器.exe"


def find_compiler() -> Path:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for framework in ("Framework64", "Framework"):
        path = windows / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe"
        if path.is_file():
            return path
    raise RuntimeError("请在 Windows 上使用 .NET Framework C# 编译器构建统一启动器.exe")


def build(out_dir: Path, *, root: Path = PROJECT_ROOT) -> Path:
    root = Path(root).resolve()
    source = root / "scripts/desktop_entry/Launcher.cs"
    icon = root / "web/assets/launcher.ico"
    if not source.is_file() or not icon.is_file():
        raise FileNotFoundError("缺少 Launcher.cs 或 launcher.ico")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / EXE_NAME
    result = subprocess.run([
        str(find_compiler()), "/nologo", "/target:winexe", "/platform:anycpu",
        "/optimize+", "/codepage:65001", "/reference:System.Windows.Forms.dll",
        "/reference:System.Drawing.dll", f"/win32icon:{icon}", f"/out:{target}", str(source),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("C# 编译失败:\n" + result.stdout + result.stderr)
    # A workspace may carry an inherited Low integrity label. Executing an EXE
    # with that label makes its Python children Low too, so normal user log/TEMP
    # directories become unwritable. Normalize only our generated artifact to
    # the standard desktop-user level, never the workspace or user data tree.
    label = subprocess.run(
        ["icacls.exe", str(target), "/setintegritylevel", "Medium"],
        capture_output=True, text=True, errors="replace",
    )
    if label.returncode:
        raise RuntimeError("无法将生成的启动入口设置为标准用户完整性级别:\n" + label.stdout + label.stderr)
    return target


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist/desktop-entry")
    args = parser.parse_args()
    print(build(Path(args.out)))


if __name__ == "__main__":
    main()
