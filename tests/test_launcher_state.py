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


if __name__ == "__main__":
    unittest.main()
