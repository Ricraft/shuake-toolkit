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

    def test_practice_mode_uses_shared_monitor_and_tracks_runtime_state(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.processes = {"yatori": None, "autovisor": None, "practice": None}
        launcher.running = {"yatori": False, "autovisor": False, "practice": False}
        launcher.starting = {"yatori": False, "autovisor": False, "practice": False}
        launcher.stop_requested = {"yatori": False, "autovisor": False, "practice": False}
        launcher.question_bank = SimpleNamespace(running=True)
        launcher.log_system = lambda _message: None
        launcher.get_python_executable = lambda: "python.exe"
        launcher._load_autovisor_config_data = lambda: {
            "accounts": [{"account_id": 7}]
        }
        launcher._as_int = lambda value, default=0: int(value or default)
        launcher._get_subprocess_window_kwargs = lambda: (0, None)
        launcher._build_encoding_candidates = lambda *_values: ("utf-8",)
        monitor_calls = []
        launcher._get_runtime_process_service = lambda: SimpleNamespace(
            monitor=lambda **kwargs: monitor_calls.append(kwargs) or True
        )
        process = SimpleNamespace(poll=lambda: None)

        with tempfile.TemporaryDirectory() as temp_dir:
            launcher.autovisor_path = temp_dir
            Path(temp_dir, "Practice_Mode.py").write_text(
                "# fixture",
                encoding="utf-8",
            )
            with patch.object(
                launcher_module.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                result = launcher.start_practice_mode_from_web(0)

        self.assertTrue(result["ok"])
        self.assertTrue(launcher.running["practice"])
        self.assertIs(launcher.processes["practice"], process)
        self.assertEqual(monitor_calls[0]["core"], "practice")
        self.assertEqual(monitor_calls[0]["output_source"], "autovisor")
        self.assertEqual(
            popen.call_args.args[0][-2:],
            ["--account-id", "7"],
        )

    def test_practice_mode_process_failure_releases_atomic_start_claim(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.processes = {"yatori": None, "autovisor": None, "practice": None}
        launcher.running = {"yatori": False, "autovisor": False, "practice": False}
        launcher.starting = {"yatori": False, "autovisor": False, "practice": False}
        launcher.stop_requested = {"yatori": False, "autovisor": False, "practice": False}
        launcher.question_bank = SimpleNamespace(running=True)
        launcher.log_system = lambda _message: None
        launcher.get_python_executable = lambda: "python.exe"
        launcher._load_autovisor_config_data = lambda: {
            "accounts": [{"account_id": 1}]
        }
        launcher._as_int = lambda value, default=0: int(value or default)
        launcher._get_subprocess_window_kwargs = lambda: (0, None)

        with tempfile.TemporaryDirectory() as temp_dir:
            launcher.autovisor_path = temp_dir
            Path(temp_dir, "Practice_Mode.py").write_text(
                "# fixture",
                encoding="utf-8",
            )
            with patch.object(
                launcher_module.subprocess,
                "Popen",
                side_effect=OSError("cannot spawn"),
            ):
                result = launcher.start_practice_mode_from_web()

        self.assertFalse(result["ok"])
        self.assertIn("cannot spawn", result["message"])
        self.assertFalse(launcher.starting["practice"])
        self.assertFalse(launcher.running["practice"])
        self.assertIsNone(launcher.processes["practice"])

    def test_practice_mode_question_bank_failure_releases_start_claim(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.processes = {"yatori": None, "autovisor": None, "practice": None}
        launcher.running = {"yatori": False, "autovisor": False, "practice": False}
        launcher.starting = {"yatori": False, "autovisor": False, "practice": False}
        launcher.stop_requested = {"yatori": False, "autovisor": False, "practice": False}
        launcher.question_bank = SimpleNamespace(running=False)
        launcher.log_system = lambda _message: None
        launcher.get_python_executable = lambda: "python.exe"
        launcher._load_autovisor_config_data = lambda: {
            "accounts": [{"account_id": 1}]
        }
        launcher._as_int = lambda value, default=0: int(value or default)
        launcher.start_question_bank = lambda **_kwargs: (_ for _ in ()).throw(
            OSError("question bank failed")
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            launcher.autovisor_path = temp_dir
            Path(temp_dir, "Practice_Mode.py").write_text(
                "# fixture",
                encoding="utf-8",
            )
            result = launcher.start_practice_mode_from_web()

        self.assertFalse(result["ok"])
        self.assertIn("question bank failed", result["message"])
        self.assertFalse(launcher.starting["practice"])
        self.assertFalse(launcher.running["practice"])

    def test_practice_mode_rejects_stale_account_index_before_claiming_start(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.processes = {"practice": None}
        launcher.running = {"practice": False}
        launcher.starting = {"practice": False}
        launcher.stop_requested = {"practice": False}
        launcher.autovisor_path = str(Path(launcher_module.__file__).parent / "Autovisor")
        launcher.log_system = lambda _message: None
        launcher.get_python_executable = lambda: "python.exe"
        launcher._load_autovisor_config_data = lambda: {
            "accounts": [{"account_id": 3}]
        }

        result = launcher.start_practice_mode_from_web(9)

        self.assertFalse(result["ok"])
        self.assertIn("账号不存在", result["message"])
        self.assertFalse(launcher.starting["practice"])

    def test_practice_mode_bridge_forwards_selected_account(self):
        calls = []
        bridge = WebLauncherAPI(
            SimpleNamespace(
                start_practice_mode_from_web=lambda index: calls.append(index)
                or {"ok": True}
            )
        )

        result = bridge.start_practice_mode(4)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, [4])

    def test_practice_mode_frontend_passes_account_index(self):
        frontend = (
            Path(launcher_module.__file__).resolve().parent / "web" / "app.js"
        ).read_text(encoding="utf-8")
        function_source = frontend.split("async function startPracticeMode", 1)[1].split(
            "function extractCourseKey", 1
        )[0]

        self.assertIn(
            "apiCall('start_practice_mode', accountIndex)",
            function_source,
        )

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

    def test_question_bank_runtime_updates_server_description(self):
        frontend = (
            Path(launcher_module.__file__).resolve().parent / "web" / "app.js"
        ).read_text(encoding="utf-8")
        status_function = frontend.split("function setQbStatus", 1)[1].split(
            "function renderSettingsTabContent", 1
        )[0]

        self.assertIn("e('qb-server-desc')", status_function)
        self.assertIn("服务运行正常，可供 Yatori 与 AutoVisor 查询", status_function)
        self.assertIn("服务已停止，点击右上角按钮启动", status_function)

    def test_automatic_update_check_only_logs_once_through_controller(self):
        source = Path(launcher_module.__file__).read_text(encoding="utf-8")
        method = source.split("def auto_check_cores", 1)[1].split(
            "def __init__", 1
        )[0]

        self.assertIn("self.check_yatori_update_async()", method)
        self.assertNotIn('self.log_system("正在检查 Yatori 更新...")', method)

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

    def test_save_all_rolls_back_yatori_when_autovisor_write_fails(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.autovisor_multi_mode = False
        launcher.log_system = lambda _message: None
        launcher.get_web_initial_state = lambda: {"runtime": {}}
        launcher._normalize_autovisor_speed = lambda value, _default: str(value)
        launcher._validate_autovisor_accounts = lambda _accounts: None
        launcher._default_yatori_user = lambda _index: {}
        launcher._default_autovisor_account = lambda _index: {}
        launcher._load_yatori_config_data = lambda: {
            "setting": {},
            "users": [{"username": "old", "coursesCustom": {}}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            yatori_path = root / "config.yaml"
            autovisor_path = root / "configs.ini"
            yatori_path.write_text("old-yatori", encoding="utf-8")
            autovisor_path.write_text("old-autovisor", encoding="utf-8")
            launcher._get_yatori_config_path = lambda: str(yatori_path)
            launcher._get_autovisor_config_path = lambda: str(autovisor_path)
            launcher._save_yatori_config_data = lambda _data: yatori_path.write_text(
                "new-yatori", encoding="utf-8"
            )

            def fail_autovisor_write(_data):
                autovisor_path.write_text("partial-autovisor", encoding="utf-8")
                raise OSError("disk full")

            launcher._save_autovisor_config_data = fail_autovisor_write
            result = launcher.save_settings_from_web(
                {
                    "yatori": {"setting": {}, "users": [{"username": "new"}]},
                    "autovisor": {
                        "multi_mode": True,
                        "accounts": [{"username": "user", "limit_speed": "1.0"}],
                    },
                }
            )

            self.assertFalse(result["ok"])
            self.assertIn("未保留任何部分改动", result["message"])
            self.assertEqual(yatori_path.read_text(encoding="utf-8"), "old-yatori")
            self.assertEqual(autovisor_path.read_text(encoding="utf-8"), "old-autovisor")
            self.assertFalse(launcher.autovisor_multi_mode)

    def test_save_all_rolls_back_every_file_when_question_bank_fails(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.autovisor_multi_mode = False
        launcher.log_system = lambda _message: None
        launcher.get_web_initial_state = lambda: {"runtime": {}}
        launcher._normalize_autovisor_speed = lambda value, _default: str(value)
        launcher._validate_autovisor_accounts = lambda _accounts: None
        launcher._default_yatori_user = lambda _index: {}
        launcher._default_autovisor_account = lambda _index: {}
        launcher._load_yatori_config_data = lambda: {
            "setting": {},
            "users": [{"username": "old", "coursesCustom": {}}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            yatori_path = root / "config.yaml"
            autovisor_path = root / "configs.ini"
            qb_path = root / "qb_config.json"
            yatori_path.write_text("old-yatori", encoding="utf-8")
            autovisor_path.write_text("old-autovisor", encoding="utf-8")
            qb_path.write_text("old-qb", encoding="utf-8")
            launcher.question_bank = SimpleNamespace(config_path=qb_path)
            launcher._get_yatori_config_path = lambda: str(yatori_path)
            launcher._get_autovisor_config_path = lambda: str(autovisor_path)
            launcher._save_yatori_config_data = lambda _data: yatori_path.write_text(
                "new-yatori", encoding="utf-8"
            )
            launcher._save_autovisor_config_data = lambda _data: autovisor_path.write_text(
                "new-autovisor", encoding="utf-8"
            )

            def fail_qb_write(_data):
                qb_path.write_text("partial-qb", encoding="utf-8")
                return {"ok": False, "message": "题库写入失败"}

            launcher.save_qb_settings_from_web = fail_qb_write
            result = launcher.save_settings_from_web(
                {
                    "yatori": {"setting": {}, "users": [{"username": "new"}]},
                    "autovisor": {
                        "multi_mode": True,
                        "accounts": [{"username": "user", "limit_speed": "1.0"}],
                    },
                    "questionbank": {"port": 8084},
                }
            )

            self.assertFalse(result["ok"])
            self.assertEqual(yatori_path.read_text(encoding="utf-8"), "old-yatori")
            self.assertEqual(autovisor_path.read_text(encoding="utf-8"), "old-autovisor")
            self.assertEqual(qb_path.read_text(encoding="utf-8"), "old-qb")
            self.assertFalse(launcher.autovisor_multi_mode)

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

    def test_ai_helpers_do_not_report_transport_failures_as_ok(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.log_system = lambda _message: None

        with patch(
            "scripts.ai_connectivity_test.AIConnectivityTester.test_connectivity",
            return_value=(False, "认证失败", {}),
        ), patch(
            "scripts.ai_connectivity_test.AIConnectivityTester.fetch_model_list",
            return_value=(False, "模型接口不可用", []),
        ):
            connectivity = launcher.test_ai_connectivity_from_web(
                {
                    "provider": "OTHER",
                    "api_url": "https://example.invalid/v1",
                    "api_key": "secret",
                    "model": "model",
                }
            )
            models = launcher.fetch_model_list_from_web(
                {
                    "provider": "OTHER",
                    "api_url": "https://example.invalid/v1",
                    "api_key": "secret",
                }
            )

        self.assertFalse(connectivity["ok"])
        self.assertFalse(connectivity["success"])
        self.assertFalse(models["ok"])
        self.assertFalse(models["success"])

    def test_empty_model_warning_is_only_used_for_successful_empty_response(self):
        frontend = (
            Path(launcher_module.__file__).resolve().parent / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "result?.success && Array.isArray(result.models) && result.models.length === 0",
            frontend,
        )

    def test_preference_web_api_delegates_to_preferences_service(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        calls = []

        class FakePreferences:
            values = {"autoStart": False}

            def update(self, payload):
                calls.append(payload)
                return {"ok": False, "preferences": dict(self.values)}

        launcher._preferences_service = FakePreferences()
        launcher.web_preferences = launcher._preferences_service.values

        result = launcher.save_web_preference({"autoStart": True})

        self.assertFalse(result["ok"])
        self.assertEqual(calls, [{"autoStart": True}])
        self.assertFalse(launcher.web_preferences["autoStart"])

    def test_frontend_preference_failure_restores_checkbox_and_reports_error(self):
        frontend = (
            Path(launcher_module.__file__).resolve().parent / "web" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("if (!result?.ok) throw new Error", frontend)
        self.assertIn("state.preferences[key] = previous", frontend)
        self.assertIn("input.checked = !!previous", frontend)
        self.assertIn("showToast(error?.message||'偏好设置保存失败', 'error')", frontend)
        self.assertIn("偏好仅临时保存在当前页面", frontend)


if __name__ == "__main__":
    unittest.main()
