import tempfile
import threading
import unittest
from pathlib import Path

from 统一启动器 import UnifiedLauncher


class LauncherStateTests(unittest.TestCase):
    def test_runtime_start_claim_is_exclusive_and_resets_stop_request(self):
        launcher = UnifiedLauncher.__new__(UnifiedLauncher)
        launcher.running = {"yatori": False}
        launcher.starting = {"yatori": False}
        launcher.stop_requested = {"yatori": True}
        launcher._runtime_lock = threading.RLock()
        self.assertTrue(launcher._claim_runtime_start("yatori"))
        self.assertFalse(launcher._claim_runtime_start("yatori"))
        self.assertFalse(launcher.stop_requested["yatori"])

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

            result = launcher.get_autovisor_courses_from_web(1)

            self.assertEqual(result["courses"][0]["name"], "缓存课程")
            self.assertEqual(catalog.request, ("zhs", 1, "second"))

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
            def get_xuexitong_courses(self, index, username, password):
                self.request = (index, username, password)
                return {"ok": True, "courses": []}

        catalog = Catalog()
        launcher._get_course_catalog_service = lambda: catalog

        self.assertTrue(launcher.get_xuexitong_courses_from_web(0)["ok"])
        self.assertEqual(catalog.request, (0, "alice", "secret"))
        self.assertFalse(launcher.get_xuexitong_courses_from_web(-1)["ok"])


if __name__ == "__main__":
    unittest.main()
