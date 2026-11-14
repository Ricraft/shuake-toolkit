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
        source_ranker=None,
        playwright_host_resolver=None,
    ):
        logs = []
        lines = []
        errors = []
        ready = []
        commands = []

        def run_command(command, cwd, **kwargs):
            commands.append((command, cwd, kwargs))
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
            source_ranker=source_ranker,
            playwright_host_resolver=playwright_host_resolver,
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
            self.assertEqual(manager.environment_snapshot()['phase'], 'failed')
            self.assertIn('没有安装规则', manager.environment_snapshot()['detail'])

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

    def test_install_with_mirrors_uses_ranked_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, _logs, _lines, _errors, _ready, _commands = self.make_manager(
                Path(temp_dir),
                mirrors=(
                    ("first", "https://first.invalid/simple"),
                    ("second", "https://second.invalid/simple"),
                ),
                source_ranker=lambda mirrors, logger=None: tuple(
                    reversed(tuple(mirrors))
                ),
            )

            with patch(
                "src.autovisor_dependency_manager.subprocess.Popen",
                side_effect=[_Process(0, [])],
            ) as popen:
                manager.install_with_mirrors(
                    "python.exe", ["requests>=2.32,<3"]
                )

            self.assertEqual(popen.call_count, 1)
            command = popen.call_args_list[0][0][0]
            self.assertIn("https://second.invalid/simple", command)

    def test_install_with_mirrors_survives_a_broken_ranker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            def broken(_mirrors, logger=None):
                raise RuntimeError("probe down")

            manager, logs, *_rest = self.make_manager(
                Path(temp_dir),
                source_ranker=broken,
            )

            with patch(
                "src.autovisor_dependency_manager.subprocess.Popen",
                side_effect=[_Process(0, [])],
            ) as popen:
                manager.install_with_mirrors(
                    "python.exe", ["requests>=2.32,<3"]
                )

            command = popen.call_args_list[0][0][0]
            self.assertIn("https://one.invalid/simple", command)
            self.assertTrue(any("镜像源测速失败" in line for line in logs))

    def test_playwright_browser_download_falls_back_to_next_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, logs, _lines, errors, ready, _commands = self.make_manager(
                Path(temp_dir),
                playwright_host_resolver=lambda python_exe, logger=None: (
                    ("mirror", "https://mirror.invalid"),
                    ("官方", None),
                ),
            )
            manager.install_with_mirrors = lambda *_args, **_kwargs: None
            calls = []

            def fake_command(command, cwd, env=None, **_kwargs):
                calls.append((command, cwd, env))
                return 1 if env else 0

            manager.run_logged_command = fake_command

            result = manager.install_async(
                "python.exe",
                [("playwright", "playwright", "missing")],
                ensure_playwright_browser=True,
            )

            self.assertTrue(result)
            self.assertEqual(errors, [])
            self.assertEqual(len(calls), 2)
            self.assertEqual(
                calls[0][2]["PLAYWRIGHT_DOWNLOAD_HOST"], "https://mirror.invalid"
            )
            self.assertIsNone(calls[1][2])
            self.assertEqual(
                calls[1][0][-3:], ["playwright", "install", "chromium"]
            )
            self.assertTrue(
                any("下载 Chromium 失败" in line for line in logs)
            )

    def test_playwright_browser_download_tolerates_two_argument_callback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, _logs, _lines, errors, ready, _commands = self.make_manager(
                Path(temp_dir),
                playwright_host_resolver=lambda python_exe, logger=None: (
                    ("mirror", "https://mirror.invalid"),
                ),
            )
            manager.install_with_mirrors = lambda *_args, **_kwargs: None

            def two_argument_command(command, cwd):
                return 0

            manager.run_logged_command = two_argument_command

            result = manager.install_async(
                "python.exe",
                [("playwright", "playwright", "missing")],
                ensure_playwright_browser=True,
            )

            self.assertTrue(result)
            self.assertEqual(errors, [])
            self.assertEqual(ready, [True])

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
