import inspect
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import 统一启动器 as launcher_module
from src.dependencies import CORE_DEPENDENCIES, OPTIONAL_DEPENDENCIES
from src.launcher_api import WebLauncherAPI
from 统一启动器 import UnifiedLauncher


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _Events:
    def __init__(self):
        self.closing = _Event()


class _Window:
    def __init__(self, selection=None):
        self.events = _Events()
        self.selection = selection

    def create_file_dialog(self, *_args, **_kwargs):
        return self.selection


class WebOnlyLauncherTests(unittest.TestCase):
    def test_launcher_has_no_tk_runtime_contract(self):
        source = Path(launcher_module.__file__).read_text(encoding="utf-8")
        forbidden = (
            "import tkinter",
            "from tkinter",
            "tk.Tk(",
            "ui_mode",
            "messagebox",
            "filedialog",
            "_init_apple_ui",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

        self.assertEqual(
            list(inspect.signature(UnifiedLauncher.__init__).parameters),
            ["self"],
        )

    def test_pywebview_is_a_required_launcher_dependency(self):
        core_modules = {dependency.module for dependency in CORE_DEPENDENCIES}
        self.assertIn("webview", core_modules)
        self.assertEqual(OPTIONAL_DEPENDENCIES, ())

    def test_frontend_api_calls_exist_on_web_bridge(self):
        project_root = Path(launcher_module.__file__).resolve().parent
        frontend = (project_root / "web" / "app.js").read_text(encoding="utf-8")
        called_methods = set(re.findall(r"apiCall\(['\"]([a-z_]+)['\"]", frontend))
        called_methods.update(re.findall(r"\bapi\.([a-z_]+)\(", frontend))
        bridge_methods = {
            name
            for name, member in inspect.getmembers(WebLauncherAPI, inspect.isfunction)
            if not name.startswith("_")
        }

        self.assertTrue(called_methods)
        self.assertEqual(called_methods - bridge_methods, set())

    def test_attach_web_window_registers_close_handler_and_applies_preferences(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        window = _Window()
        calls = []
        launcher._apply_window_preferences = lambda initial=False: calls.append(initial)

        launcher.attach_web_window(window)

        self.assertIs(launcher.web_window, window)
        self.assertEqual(window.events.closing.handlers, [launcher._handle_web_window_closing])
        self.assertEqual(calls, [True])

    def test_browser_path_dialog_uses_webview_window(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.web_window = _Window(selection=[r"C:\Program Files\Browser\browser.exe"])

        with patch.object(launcher_module, "webview", object()):
            result = launcher.browse_browser_path_for_web()

        self.assertEqual(
            result,
            {"ok": True, "path": r"C:\Program Files\Browser\browser.exe"},
        )

    def test_unconfirmed_headless_exit_does_not_stop_running_core(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.web_window = None
        launcher.running = {"yatori": True, "autovisor": False, "practice": False}
        events = []
        launcher._preference_enabled = lambda *_args, **_kwargs: False
        launcher.log_system = events.append
        launcher.stop_all = lambda: events.append("stop_all")
        launcher.stop_question_bank = lambda: events.append("stop_question_bank")
        launcher._close_main_window = lambda: events.append("close")

        launcher.on_closing(confirmed=False)

        self.assertNotIn("stop_all", events)
        self.assertNotIn("close", events)
        self.assertTrue(any("取消未确认" in entry for entry in events))

    def test_web_start_action_returns_the_actual_launch_rejection(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher._last_start_error = {}
        launcher.start_yatori = lambda: launcher._reject_runtime_start(
            "yatori",
            "缺少 Yatori 配置",
        )
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        result = launcher.perform_web_action("start", "yatori")

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "缺少 Yatori 配置")
        self.assertIn("state", result)

    def test_start_yatori_rejects_missing_config_immediately(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.running = {"yatori": False}
        launcher.starting = {"yatori": False}
        launcher._last_start_error = {}
        launcher.log_system = lambda _message: None
        launcher._show_error = lambda _title, _message: None

        with tempfile.TemporaryDirectory() as temp_dir:
            launcher.get_base_dir = lambda: temp_dir
            launcher.find_yatori_path = lambda _base_dir: temp_dir

            accepted = launcher.start_yatori()

        self.assertFalse(accepted)
        self.assertIn("config.yaml", launcher._last_start_error["yatori"])

    def test_question_bank_start_failure_is_visible_to_web(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.question_bank = SimpleNamespace(running=False)
        launcher.start_question_bank = lambda: False
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        result = launcher.perform_web_action("start_question_bank")

        self.assertFalse(result["ok"])
        self.assertIn("启动失败", result["message"])

    def test_question_bank_toggle_stop_is_treated_as_success(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.question_bank = SimpleNamespace(running=True)
        launcher.toggle_question_bank = lambda: False
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        result = launcher.perform_web_action("toggle_question_bank")

        self.assertTrue(result["ok"])

    def test_frontend_stops_after_save_failure_and_displays_action_errors(self):
        frontend = (
            Path(launcher_module.__file__).resolve().parent / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertGreaterEqual(frontend.count("if (!sr?.ok) return;"), 2)
        self.assertIn("function handleWebActionResult", frontend)
        self.assertIn("showToast(result?.message || fallbackMessage, 'error')", frontend)

        core_action = frontend.split("async function handleCoreAction", 1)[1].split(
            "async function toggleQuestionBank", 1
        )[0]
        self.assertIn("if (action === 'start')", core_action)
        self.assertLess(core_action.index("const action"), core_action.index("saveSettings"))

    def test_start_all_reports_partial_failure_to_web(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.question_bank = SimpleNamespace(running=True, available=True)
        launcher.running = {"yatori": False, "autovisor": False}
        launcher._last_start_error = {}
        launcher.log_system = lambda _message: None
        launcher.start_yatori = lambda: launcher._reject_runtime_start(
            "yatori", "Yatori 配置缺失"
        )
        launcher.start_autovisor = lambda: True
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        result = launcher.perform_web_action("start_all")

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Yatori 配置缺失")
        self.assertIn("state", result)

    def test_cancelled_question_bank_import_is_not_reported_as_failure(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.question_bank = SimpleNamespace(available=True)
        launcher.web_window = _Window(selection=None)
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        with patch.object(launcher_module, "webview", object()):
            result = launcher.perform_web_action("import_question_bank")

        self.assertTrue(result["ok"])
        self.assertTrue(result["cancelled"])

    def test_unknown_config_directory_target_is_rejected(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        result = launcher.perform_web_action("open_config_dir", "unknown")

        self.assertFalse(result["ok"])
        self.assertIn("未知核心类型", result["message"])

    def test_question_bank_deduplicate_failure_is_not_reported_as_success(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher._deduplicate_question_bank = lambda: (False, "题库文件被占用")
        launcher.get_web_initial_state = lambda: {"runtime": {}}

        result = launcher.perform_web_action("deduplicate_question_bank")

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "题库文件被占用")
        self.assertNotIn("toast", result)

    def test_question_bank_clear_requires_frontend_confirmation(self):
        project_root = Path(launcher_module.__file__).resolve().parent
        frontend = (project_root / "web" / "app.js").read_text(encoding="utf-8")
        page = (project_root / "web" / "现代启动器_UI_预览.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("async function confirmAndPerform", frontend)
        self.assertIn("confirmAndPerform('clear_question_bank'", page)
        self.assertIn("此操作无法撤销", page)
        self.assertIn("清空题库", page)
        self.assertIn("删除所有本地题目数据", page)

    def test_frontend_connection_lifecycle_retries_without_stale_runtime(self):
        frontend = (
            Path(launcher_module.__file__).resolve().parent / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("if (initialized || initInFlight) return;", frontend)
        self.assertIn("scheduleInitRetry(error?.silent ? 200 : 1000)", frontend)
        self.assertIn(
            "DOMContentLoaded', () => { renderConsole(); loadPreferences(); init(); }",
            frontend,
        )
        self.assertIn("if (runtimeRefreshInFlight || document.hidden) return;", frontend)
        self.assertIn("if (effectiveId < runtimeAppliedSequence) return false;", frontend)
        self.assertIn("applyRuntimeState(runtime, requestId)", frontend)
        self.assertIn("document.addEventListener('visibilitychange'", frontend)

    def test_autovisor_activity_has_backend_and_dashboard_contract(self):
        project_root = Path(launcher_module.__file__).resolve().parent
        frontend = (project_root / "web" / "app.js").read_text(encoding="utf-8")
        page = (project_root / "web" / "现代启动器_UI_预览.html").read_text(
            encoding="utf-8"
        )
        launcher_source = Path(launcher_module.__file__).read_text(encoding="utf-8")

        self.assertIn("'autovisor_activity': autovisor_activity", launcher_source)
        self.assertIn("function renderAutovisorActivity(activity)", frontend)
        self.assertIn("renderAutovisorActivity(n.autovisor_activity)", frontend)
        for element_id in (
            "autovisor-activity",
            "autovisor-activity-label",
            "autovisor-activity-detail",
            "autovisor-activity-percent",
            "autovisor-progress-track",
            "autovisor-progress-bar",
        ):
            self.assertIn(f'id="{element_id}"', page)


if __name__ == "__main__":
    unittest.main()
