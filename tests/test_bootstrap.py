# encoding=utf-8
"""Unit tests for the first-run bootstrap.

These tests never touch the network and never install real packages.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "bootstrap.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bootstrap", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_module()


def fake_result(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class VersionTests(unittest.TestCase):
    def test_parse_and_validate_python_version(self):
        self.assertEqual(bootstrap.parse_version_output("3.13.1"), (3, 13, 1))
        self.assertTrue(bootstrap.is_supported_python((3, 13, 1)))
        self.assertFalse(bootstrap.is_supported_python((3, 8, 10)))
        self.assertFalse(bootstrap.is_supported_python(None))

    def test_venv_paths_for_windows_and_posix(self):
        win = bootstrap.venv_python_path("C:/demo/venv", os_name="nt")
        posix = bootstrap.venv_python_path("/tmp/venv", os_name="posix")
        self.assertEqual(win.as_posix(), "C:/demo/venv/Scripts/python.exe")
        self.assertEqual(posix.as_posix(), "/tmp/venv/bin/python")

    def test_find_python_uses_probe_result(self):
        def fake_run(command, **_kwargs):
            return fake_result(
                stdout="3 13 C:/Python313/python.exe\n",
            )

        found = bootstrap.find_python(run=fake_run, which=lambda _name: None)
        self.assertEqual(found, "C:/Python313/python.exe")

    def test_probe_python_preserves_executable_path_with_spaces(self):
        executable = r"C:\Users\Demo User\Python 3.13\python.exe"

        def fake_run(_command, **_kwargs):
            return fake_result(stdout=json.dumps([3, 13, executable]) + "\n")

        probe = bootstrap._probe_python(["python"], run=fake_run)
        self.assertIsNotNone(probe)
        self.assertEqual(probe.executable, executable)

    def test_check_mode_does_not_create_or_write_log_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "not-created" / "logs"
            with redirect_stdout(io.StringIO()):
                result = bootstrap.main(
                    ["--check", "--quiet", "--log-dir", str(log_dir)]
                )
            self.assertIn(result, (0, 1))
            self.assertFalse(log_dir.exists())

    def test_logger_initialization_failure_is_user_visible_and_nonzero(self):
        error_output = io.StringIO()
        with mock.patch.object(
            bootstrap, "BootstrapLogger", side_effect=OSError("disk full")
        ):
            with redirect_stderr(error_output):
                result = bootstrap.main(["--install", "--quiet"])

        self.assertEqual(result, 1)
        self.assertIn("无法初始化日志", error_output.getvalue())
        self.assertIn("disk full", error_output.getvalue())

    def test_launcher_immediate_nonzero_exit_is_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / bootstrap.LAUNCHER_NAME).write_text("", encoding="utf-8")
            venv_dir = root / "venv"
            python_exe = bootstrap.venv_python_path(venv_dir)

            def fake_popen(_command, **_kwargs):
                return types.SimpleNamespace(wait=lambda timeout: 7)

            with self.assertRaisesRegex(RuntimeError, "返回码: 7"):
                bootstrap.launch_launcher(
                    python_exe,
                    root=root,
                    run=fake_popen,
                )

    def test_launcher_early_zero_exit_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / bootstrap.LAUNCHER_NAME).write_text("", encoding="utf-8")
            python_exe = bootstrap.venv_python_path(root / "venv")

            def fake_popen(_command, **_kwargs):
                return types.SimpleNamespace(wait=lambda timeout: 0)

            with self.assertRaisesRegex(RuntimeError, "返回码: 0"):
                bootstrap.launch_launcher(
                    python_exe,
                    root=root,
                    run=fake_popen,
                )

    def test_launcher_accepts_simple_fake_popen_and_prefers_pythonw(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / bootstrap.LAUNCHER_NAME).write_text("", encoding="utf-8")
            venv_dir = root / "venv"
            python_exe = bootstrap.venv_python_path(venv_dir)
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.write_text("", encoding="utf-8")
            pythonw = bootstrap.venv_pythonw_path(venv_dir)
            pythonw.write_text("", encoding="utf-8")
            launched = {}

            def fake_popen(command, **kwargs):
                launched["command"] = command
                launched["kwargs"] = kwargs
                return types.SimpleNamespace(pid=1234)

            result = bootstrap.launch_launcher(
                python_exe,
                root=root,
                run=fake_popen,
            )

            self.assertEqual(result, 0)
            self.assertEqual(launched["command"][0], str(pythonw))
            self.assertEqual(launched["command"][1], str(root / bootstrap.LAUNCHER_NAME))


class DependencyTests(unittest.TestCase):
    def test_pip_command_targets_selected_mirror(self):
        command = bootstrap.build_pip_install_command(
            "C:/demo/venv/Scripts/python.exe",
            "https://mirror.example/simple",
            "requirements.txt",
        )
        self.assertIn("-r", command)
        self.assertIn("requirements.txt", command)
        self.assertIn("--prefer-binary", command)
        self.assertEqual(command[-1], "https://mirror.example/simple")

    def test_module_import_check_builds_import_code(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return fake_result()

        ok = bootstrap.module_imports_ok(
            "python",
            ["yaml", "requests"],
            run=fake_run,
        )

        self.assertTrue(ok)
        command, kwargs = calls[0]
        self.assertEqual(command[0], "python")
        self.assertEqual(command[1], "-c")
        self.assertIn("importlib.import_module('yaml')", command[2])
        self.assertIn("importlib.import_module('requests')", command[2])
        self.assertTrue(kwargs["capture_output"])

    def test_ensure_requirements_retries_next_mirror(self):
        mirrors = (
            ("first", "https://first.example/simple"),
            ("second", "https://second.example/simple"),
        )
        state = {"imports_ok": False}
        calls = []

        def fake_import_check(*_args, **_kwargs):
            return state["imports_ok"]

        def fake_run(command, **_kwargs):
            calls.append(list(command))
            if "install" not in command:
                return fake_result()
            if "https://first.example/simple" in command:
                return fake_result(returncode=1)
            state["imports_ok"] = True
            return fake_result()

        bootstrap.ensure_requirements(
            "python",
            mirrors=mirrors,
            run=fake_run,
            import_check=fake_import_check,
            requirements_file=PROJECT_ROOT / "requirements.txt",
        )

        self.assertIn("https://first.example/simple", calls[1])
        self.assertIn("https://second.example/simple", calls[2])

    def test_ensure_venv_reuses_existing_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            target = bootstrap.venv_python_path(venv_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
            returned = bootstrap.ensure_venv("python", venv_dir=venv_dir)
            self.assertEqual(returned, target)

    def test_read_full_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "full-manifest.json").write_text(
                json.dumps({"version": "v1.5.0", "files": 3}),
                encoding="utf-8",
            )
            manifest = bootstrap.read_full_manifest(root)
            self.assertEqual(manifest["version"], "v1.5.0")
            self.assertEqual(manifest["files"], 3)

    def test_yatori_release_info_from_manifest(self):
        info = bootstrap._build_yatori_release_info(
            {
                "yatoriCore": {
                    "url": "https://example.test/yatori.zip",
                    "sha256": "a" * 64,
                    "size": 123,
                    "version": "v1",
                }
            }
        )
        self.assertEqual(info["download_url"], "https://example.test/yatori.zip")
        self.assertEqual(info["digest"], "sha256:" + "a" * 64)
        self.assertEqual(info["asset_size"], 123)

    def test_webview2_is_considered_ready_off_windows(self):
        self.assertTrue(bootstrap.webview2_installed(os_name="posix"))


class BatchLauncherTests(unittest.TestCase):
    def test_batch_fallback_is_limited_to_interpreter_probe_failure(self):
        batch = (PROJECT_ROOT / "启动依赖.cmd").read_text(encoding="utf-8")
        lines = batch.splitlines()
        py_bootstrap_line = lines.index('py -3 "%~dp0bootstrap.py" --install')

        version_probe = (
            'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
        )
        self.assertIn(f'py -3 -c "{version_probe}" >nul 2>nul', lines)
        self.assertIn(f'python -c "{version_probe}" >nul 2>nul', lines)
        self.assertIn('if errorlevel 1 goto use_python', lines[:py_bootstrap_line])
        self.assertEqual(
            lines[py_bootstrap_line + 1],
            'set "BOOTSTRAP_EXIT_CODE=%errorlevel%"',
        )
        self.assertEqual(lines[py_bootstrap_line + 2], "goto report_result")
        self.assertIn('python "%~dp0bootstrap.py" --install', lines)


if __name__ == "__main__":
    unittest.main()

