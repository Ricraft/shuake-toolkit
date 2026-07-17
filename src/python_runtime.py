"""Python interpreter discovery for source and frozen launcher builds."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable


REGISTERED_VERSIONS = ("3.13", "3.12", "3.11", "3.10", "3.9")
COMMON_VERSION_DIRS = ("Python313", "Python312", "Python311", "Python310", "Python39")


def find_python_executable(
    *,
    frozen: bool | None = None,
    current_executable: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] = os.path.isfile,
    run: Callable[..., object] = subprocess.run,
    username: str | None = None,
) -> str | None:
    """Return a usable Python executable without launching project code."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    current_executable = current_executable or sys.executable
    if not frozen:
        return current_executable

    python_cmd = which("python") or which("python3")
    if python_cmd:
        return python_cmd

    py_launcher = which("py")
    if py_launcher:
        for version in REGISTERED_VERSIONS:
            try:
                result = run(
                    [
                        py_launcher,
                        f"-{version}",
                        "-c",
                        "import sys; print(sys.executable)",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                candidate = str(getattr(result, "stdout", "") or "").strip()
                if getattr(result, "returncode", 1) == 0 and is_file(candidate):
                    return candidate
            except (OSError, subprocess.SubprocessError):
                continue

    username = os.getenv("USERNAME", "") if username is None else username
    roots = []
    for version_dir in COMMON_VERSION_DIRS:
        roots.append(fr"C:\{version_dir}\python.exe")
        if username:
            roots.append(
                os.path.join(
                    r"C:\Users",
                    username,
                    "AppData",
                    "Local",
                    "Programs",
                    "Python",
                    version_dir,
                    "python.exe",
                )
            )
    return next((path for path in roots if is_file(path)), None)
