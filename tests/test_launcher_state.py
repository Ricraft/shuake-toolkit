import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from 统一启动器 import UnifiedLauncher


class LauncherStateTests(unittest.TestCase):
    def test_failed_runtime_exit_is_visible_and_never_triggers_auto_shutdown(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.running = {"yatori": False, "autovisor": True}
        visible_failures = []
        notifications = []
        shutdowns = []
        system_logs = []
        launcher.log_history = {"yatori": [], "autovisor": [], "system": []}
        launcher.log = lambda core, message: visible_failures.append((core, message))
        launcher.log_system = system_logs.append
        launcher._notify_runtime_event = (
            lambda title, message, error=False: notifications.append(
                (title, message, error)
            )
        )
        launcher._maybe_shutdown_after_completion = lambda: shutdowns.append(True)

        launcher._handle_runtime_exit("autovisor", 3, False)

        self.assertEqual(launcher._last_runtime_event["kind"], "crash")
        self.assertEqual(launcher._last_runtime_event["core"], "autovisor")
        self.assertEqual(launcher._last_runtime_event["return_code"], 3)
        self.assertEqual(
            visible_failures,
            [("autovisor", "[ERROR] Autovisor 已退出，返回码: 3")],
        )
        self.assertEqual(
            notifications,
            [("Autovisor 运行异常", "Autovisor 已退出，返回码: 3", True)],
        )
        self.assertEqual(shutdowns, [])

        visible_failures.clear()
        notifications.clear()
        launcher.running["autovisor"] = False
        launcher._handle_runtime_exit("autovisor", 0, False)
        self.assertEqual(visible_failures, [])
        self.assertEqual(len(notifications), 1)
        self.assertFalse(notifications[0][2])
        self.assertEqual(shutdowns, [])
        self.assertIn("本轮任务存在异常退出，已跳过自动关机", system_logs)

        launcher._runtime_failure_since_batch = False
        launcher._handle_runtime_exit("autovisor", 0, False)
        self.assertEqual(shutdowns, [True])

    def test_shutdown_failure_is_visible_and_not_marked_pending(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher._preference_enabled = lambda key, default=False: (
            key == "autoShutdown"
        )
        logs = []
        launcher.log_system = logs.append
        launcher._shutdown_pending = False
        platform = SimpleNamespace(
            shutdown_pending=False,
            schedule_shutdown=lambda **_kwargs: {
                "ok": False,
                "message": "自动关机指令被系统拒绝，返回码: 5",
            },
        )
        launcher._desktop_platform_service = platform

        launcher._maybe_shutdown_after_completion()

        self.assertFalse(launcher._shutdown_pending)
        self.assertEqual(
            logs,
            ["自动关机指令被系统拒绝，返回码: 5"],
        )

    def test_cancel_shutdown_keeps_pending_state_when_system_rejects(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        logs = []
        launcher.log_system = logs.append
        launcher._shutdown_pending = True
        platform = SimpleNamespace(
            shutdown_pending=True,
            cancel_shutdown=lambda: {
                "ok": False,
                "message": "取消关机失败，系统返回码: 1116",
            },
        )
        launcher._desktop_platform_service = platform

        result = launcher._cancel_shutdown()

        self.assertFalse(result["ok"])
        self.assertTrue(launcher._shutdown_pending)
        self.assertEqual(logs, ["取消关机失败，系统返回码: 1116"])

    def test_runtime_state_exposes_active_practice_account(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.running = {
            "yatori": False,
            "autovisor": False,
            "practice": True,
        }
        launcher.starting = {
            "yatori": False,
            "autovisor": False,
            "practice": False,
        }
        launcher.practice_account_id = 7
        launcher.log_history = {"yatori": [], "autovisor": [], "system": []}
        launcher.yatori_path = "missing-yatori"
        launcher.autovisor_path = "missing-autovisor"
        launcher.question_bank = SimpleNamespace(
            running=True,
            port=8083,
            get_stats=lambda: {"total": 0},
        )
        launcher._shutdown_pending = False
        launcher._get_yatori_display_version = lambda: "y"
        launcher._get_autovisor_display_version = lambda: "a"
        launcher._get_autovisor_multi_mode = lambda: True

        runtime = launcher.get_web_runtime_state()

        self.assertTrue(runtime["running"]["practice"])
        self.assertEqual(runtime["practice_account_id"], 7)

    def test_runtime_start_claim_is_exclusive_and_resets_stop_request(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.running = {"yatori": False}
        launcher.starting = {"yatori": False}
        launcher.stop_requested = {"yatori": True}
        launcher._runtime_failure_since_batch = True
        launcher._runtime_lock = threading.RLock()
        self.assertTrue(launcher._claim_runtime_start("yatori"))
        self.assertFalse(launcher._claim_runtime_start("yatori"))
        self.assertFalse(launcher.stop_requested["yatori"])
        self.assertFalse(launcher._runtime_failure_since_batch)

    def test_autovisor_account_name_round_trip(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.log_system = lambda message: None
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "configs.ini"
            launcher._get_autovisor_config_path = lambda: str(config_path)
            launcher._save_autovisor_config_data(
                {
                    "multi_mode": False,
                    "browser_driver": "Chrome",
                    "browser_path": "",
                    "accounts": [
                        {
                            "name": "主账号",
                            "username": "user",
                            "password": "secret",
                            "enable_auto_captcha": True,
                            "enable_hide_window": False,
                            "limit_max_time": "30",
                            "limit_speed": "1.0",
                            "sound_off": True,
                            "course_urls": [],
                        }
                    ],
                }
            )
            loaded = launcher._load_autovisor_config_data()
            self.assertEqual(loaded["accounts"][0]["name"], "主账号")

    def test_autovisor_course_cache_is_checked_before_runtime_requirements(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.log_system = lambda _message: None

        class Catalog:
            def __init__(self, base_dir):
                self.base_dir = Path(base_dir)

            def get_cached(self, provider, index, identity):
                self.request = (provider, index, identity)
                return {"ok": True, "courses": [{"name": "缓存课程"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "Autovisor"
            config_dir.mkdir()
            (config_dir / "configs.ini").write_text(
                "[user-account-2]\nusername = second\n",
                encoding="utf-8",
            )
            catalog = Catalog(root)
            launcher._course_catalog_service = catalog
            launcher.get_base_dir = lambda: str(root)
            launcher.get_python_executable = lambda: self.fail(
                "缓存命中时不应检查 Python"
            )

            result = launcher.get_autovisor_courses_from_web(0)

            self.assertEqual(result["courses"][0]["name"], "缓存课程")
            self.assertEqual(catalog.request, ("zhs", 1, "second"))

    def test_autovisor_course_fetch_maps_visible_position_to_numbered_account(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.log_system = lambda _message: None

        class Catalog:
            def __init__(self, base_dir):
                self.base_dir = Path(base_dir)

            def get_cached(self, provider, index, identity):
                self.request = (provider, index, identity)
                return {"ok": True, "courses": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Autovisor").mkdir()
            (root / "Autovisor" / "configs.ini").write_text(
                "[user-account-2]\nusername = second\npassword = p2\n"
                "[user-account-7]\nusername = seventh\npassword = p7\n",
                encoding="utf-8",
            )
            catalog = Catalog(root)
            launcher._course_catalog_service = catalog
            launcher.get_base_dir = lambda: str(root)

            self.assertTrue(launcher.get_autovisor_courses_from_web(1)["ok"])
            self.assertEqual(catalog.request, ("zhs", 6, "seventh"))

    def test_autovisor_course_fetch_rejects_unconfigured_account(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.log_system = lambda _message: None
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Autovisor").mkdir()
            (root / "Autovisor" / "configs.ini").write_text(
                "[user-account]\nusername =\n",
                encoding="utf-8",
            )
            launcher.get_base_dir = lambda: str(root)
            launcher._get_course_catalog_service = lambda: self.fail(
                "未配置账号不应读取课程缓存或旧数据"
            )

            result = launcher.get_autovisor_courses_from_web(0)

            self.assertFalse(result["ok"])
            self.assertIn("未配置用户名", result["message"])

    def test_xuexitong_course_wrapper_validates_and_delegates_identity(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.log_system = lambda _message: None
        launcher._load_yatori_config_data = lambda: {
            "users": [
                {
                    "accountType": "XUEXITONG",
                    "account": "alice",
                    "password": "secret",
                }
            ]
        }

        class Catalog:
            def get_xuexitong_courses(
                self, index, username, password, *, force_refresh=False
            ):
                self.request = (index, username, password, force_refresh)
                return {"ok": True, "courses": []}

        catalog = Catalog()
        launcher._get_course_catalog_service = lambda: catalog

        self.assertTrue(launcher.get_xuexitong_courses_from_web(0)["ok"])
        self.assertEqual(catalog.request, (0, "alice", "secret", False))
        self.assertFalse(launcher.get_xuexitong_courses_from_web(-1)["ok"])


if __name__ == "__main__":
    unittest.main()
