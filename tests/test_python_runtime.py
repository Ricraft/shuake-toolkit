import subprocess
from types import SimpleNamespace

from src.python_runtime import find_python_executable


def test_source_build_uses_current_interpreter_without_probing_path():
    probes = []

    result = find_python_executable(
        frozen=False,
        current_executable=r"D:\Python\python.exe",
        which=lambda name: probes.append(name),
    )

    assert result == r"D:\Python\python.exe"
    assert probes == []


def test_frozen_build_prefers_python_available_on_path():
    result = find_python_executable(
        frozen=True,
        which=lambda name: r"C:\Tools\python.exe" if name == "python" else None,
        is_file=lambda _path: False,
    )

    assert result == r"C:\Tools\python.exe"


def test_windows_launcher_skips_failed_versions_and_returns_registered_python():
    calls = []

    def run(command, **_kwargs):
        calls.append(command[1])
        if command[1] == "-3.13":
            raise subprocess.TimeoutExpired(command, 5)
        if command[1] == "-3.12":
            return SimpleNamespace(returncode=1, stdout=None)
        return SimpleNamespace(returncode=0, stdout="C:\\Python311\\python.exe\n")

    result = find_python_executable(
        frozen=True,
        which=lambda name: "py.exe" if name == "py" else None,
        is_file=lambda path: path == r"C:\Python311\python.exe",
        run=run,
    )

    assert result == r"C:\Python311\python.exe"
    assert calls == ["-3.13", "-3.12", "-3.11"]


def test_common_install_fallback_prefers_newest_supported_version():
    existing = {
        r"C:\Python311\python.exe",
        r"C:\Users\alice\AppData\Local\Programs\Python\Python313\python.exe",
    }

    result = find_python_executable(
        frozen=True,
        which=lambda _name: None,
        is_file=existing.__contains__,
        username="alice",
    )

    assert result == (
        r"C:\Users\alice\AppData\Local\Programs\Python\Python313\python.exe"
    )


def test_frozen_build_returns_none_when_no_interpreter_exists():
    result = find_python_executable(
        frozen=True,
        which=lambda _name: None,
        is_file=lambda _path: False,
        username="",
    )

    assert result is None
