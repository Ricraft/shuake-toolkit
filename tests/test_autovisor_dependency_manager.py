import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.autovisor_dependency_manager import AutovisorDependencyManager
from src.config_service import read_ini_config


class _Process:
    def __init__(self, return_code, lines=()):
        self.return_code = return_code
        self.stdout = list(lines)

    def wait(self):
        return self.return_code


class AutovisorDependencyManagerTests(unittest.TestCase):
    def make_manager(
        self,
        root,
        *,
        run_async=None,
        schedule=None,
        mirrors=(("one", "https://one.invalid/simple"),),
    ):
        logs = []
        lines = []
        errors = []
        ready = []
        commands = []

        def run_command(command, cwd):
            commands.append((command, cwd))
            return 0

        manager = AutovisorDependencyManager(
            get_autovisor_path=lambda: str(root),
            log_system=logs.append,
            log_line=lambda source, line, **kwargs: lines.append((source, line, kwargs)),
            is_progress_log=lambda line: "%" in line,
            run_logged_command=run_command,
            schedule=schedule or (lambda _delay, callback: callback()),
            on_ready=lambda: ready.append(True),
            show_error=lambda title, message: errors.append((title, message)),
            run_async=run_async or (lambda target: target()),
            mirrors=mirrors,
        )
        return manager, logs, lines, errors, ready, commands

    def test_install_accepts_dependency_tuples_and_restarts_when_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, _logs, _lines, errors, ready, commands = self.make_manager(
                Path(temp_dir)
            )
            installed = []
            manager.install_with_mirrors = (
                lambda python, args: installed.append((python, args))
            )

            result = manager.install_async(
                "python.exe",
                [("playwright", "playwright", "missing")],
                ensure_playwright_browser=True,
            )

            self.assertTrue(result)
            self.assertFalse(manager.installing)
            self.assertEqual(installed[0][1], ["playwright>=1.52,<2"])
            self.assertEqual(commands[0][0][-3:], ["playwright", "install", "chromium"])
            self.assertEqual(ready, [True])
            self.assertEqual(errors, [])

    def test_legacy_package_name_list_is_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, *_rest = self.make_manager(Path(temp_dir))
            captured = []
            manager.install_with_mirrors = lambda _python, args: captured.extend(args)

            self.assertTrue(manager.install_async("python.exe", ["requests"]))

            self.assertEqual(captured, ["requests>=2.32,<3"])

    def test_duplicate_install_is_rejected_while_worker_is_pending(self):
        tasks = []
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, logs, *_rest = self.make_manager(
                Path(temp_dir), run_async=tasks.append
            )

            self.assertTrue(manager.install_async("python.exe", []))
            self.assertFalse(manager.install_async("python.exe", []))
            self.assertTrue(manager.installing)
            self.assertEqual(len(tasks), 1)
            self.assertTrue(any("正在进行" in entry for entry in logs))

            tasks.pop()()
            self.assertFalse(manager.installing)

    def test_unknown_package_failure_releases_state_and_delays_safe_message(self):
        callbacks = []
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, _logs, _lines, errors, ready, _commands = self.make_manager(
                Path(temp_dir), schedule=lambda _delay, callback: callbacks.append(callback)
            )

            self.assertTrue(manager.install_async("python.exe", ["unknown-package"]))
            self.assertFalse(manager.installing)
            self.assertEqual(errors, [])
            self.assertEqual(ready, [])

            callbacks.pop()()
            self.assertIn("没有安装规则", errors[0][1])

    def test_pip_install_falls_back_to_next_mirror(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, logs, lines, _errors, _ready, _commands = self.make_manager(
                Path(temp_dir),
                mirrors=(
                    ("first", "https://first.invalid/simple"),
                    ("second", "https://second.invalid/simple"),
                ),
            )
            processes = [
                _Process(1, ["failed\n"]),
                _Process(0, ["50%\n", "done\n"]),
            ]

            with patch(
                "src.autovisor_dependency_manager.subprocess.Popen",
                side_effect=processes,
            ) as popen:
                manager.install_with_mirrors(
                    "python.exe", ["requests>=2.32,<3"]
                )

            self.assertEqual(popen.call_count, 2)
            self.assertTrue(any("second" in entry and "完成" in entry for entry in logs))
            self.assertTrue(any(item[2].get("replace_last") for item in lines))

    def test_prepare_config_preserves_data_and_writes_detected_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            browser = root / "chrome.exe"
            browser.write_bytes(b"")
            config = root / "configs.ini"
            config.write_text(
                "[user-account-1]\nusername = alice\n"
                "[custom]\nkeep = yes\n",
                encoding="utf-8",
            )
            manager, *_rest = self.make_manager(root)
            manager.find_browser_executable = lambda _name: str(browser)

            result = manager.prepare_config(config)
            loaded = read_ini_config(config)

            self.assertTrue(result["changed"])
            self.assertTrue(loaded.has_section("user-account"))
            self.assertEqual(loaded.get("user-account", "username"), "alice")
            self.assertEqual(loaded.get("custom", "keep"), "yes")
            self.assertEqual(
                loaded.get("browser-option", "EXE_PATH"), str(browser)
            )


if __name__ == "__main__":
    unittest.main()
