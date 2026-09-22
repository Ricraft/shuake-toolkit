# encoding=utf-8
"""Unit tests for the first-run bootstrap.

These tests never touch the network and never install real packages.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

