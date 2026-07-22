import tempfile
import unittest
from pathlib import Path

from Autovisor.modules.configs import Config
from src.config_service import ConfigService, read_ini_config


class ConfigServiceTests(unittest.TestCase):
    def make_service(self, root: Path, logs=None):
        return ConfigService(
            lambda: root / "config.yaml",
            lambda: root / "configs.ini",
            browser_finder=lambda _name: None,
            logger=(logs.append if logs is not None else None),
        )

    def test_yatori_round_trip_preserves_special_characters_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self.make_service(root)
            config = service.default_yatori_config()
            config["users"][0]["account"] = "用户:一"
            config["users"][0]["password"] = "p%: # 密码"
            config["users"][0]["customPluginOption"] = {"enabled": True}
            config["users"][0]["coursesCustom"]["customCourseOption"] = "保留"
            config["setting"]["thirdParty"] = {"token": "x%#"}

            service.save_yatori(config)
            loaded = service.load_yatori()

            self.assertEqual(loaded["users"][0]["password"], "p%: # 密码")
            self.assertEqual(loaded["users"][0]["customPluginOption"], {"enabled": True})
            self.assertEqual(loaded["users"][0]["coursesCustom"]["customCourseOption"], "保留")
            self.assertEqual(loaded["setting"]["thirdParty"], {"token": "x%#"})

    def test_malformed_yatori_config_falls_back_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.yaml"
            broken = "setting: [unterminated"
            path.write_text(broken, encoding="utf-8")
            logs = []

            loaded = self.make_service(root, logs).load_yatori()

            self.assertEqual(loaded["users"][0]["remarkName"], "账号1")
            self.assertEqual(path.read_text(encoding="utf-8"), broken)
            self.assertTrue(any("回退默认值" in message for message in logs))

    def test_wrong_yatori_section_types_are_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.yaml").write_text(
                "setting: invalid\nusers: invalid\n",
                encoding="utf-8",
            )

            loaded = self.make_service(root).load_yatori()

            self.assertEqual(loaded["setting"]["basicSetting"]["logLevel"], "INFO")
            self.assertEqual(len(loaded["users"]), 1)

    def test_autovisor_round_trip_preserves_ids_percent_urls_and_unknown_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "configs.ini"
            path.write_text(
                "[plugin-option]\nkeep = yes\n"
                "[user-account-2]\nname = 第二账号\nusername = user2\npassword = p%word\ncookiesFile = custom.json\n"
                "[browser-option-2]\ndriver = Edge\nEXE_PATH = D:\\\\Edge\\\\edge.exe\n"
                "[script-option-2]\nenableAutoCaptcha = True\nenableHideWindow = False\n"
                "[course-option-2]\nlimitMaxTime = 20\nlimitSpeed = 1.25\nsoundOff = True\nwindowWidth = 1440\n"
                "[course-url-2]\nURL10 = https://example.com/10\nURL2 = https://example.com/2\nURL1 = https://example.com/1\n"
                "[user-account-7]\nname = 第七账号\nusername = user7\npassword = 七%号\n"
                "[browser-option-7]\ndriver = Chrome\nEXE_PATH = D:\\\\Chrome\\\\chrome.exe\n"
                "[script-option-7]\nenableAutoCaptcha = False\nenableHideWindow = True\n"
                "[course-option-7]\nlimitMaxTime = 40\nlimitSpeed = 1.5\nsoundOff = False\n"
                "[course-url-7]\nURL1 = https://example.com/7\n",
                encoding="utf-8",
            )
            service = self.make_service(root)

            loaded = service.load_autovisor()
            self.assertEqual([a["account_id"] for a in loaded["accounts"]], [2, 7])
            self.assertEqual(loaded["accounts"][0]["password"], "p%word")
            self.assertEqual(
                loaded["accounts"][0]["course_urls"],
                ["https://example.com/1", "https://example.com/2", "https://example.com/10"],
            )
            self.assertEqual(loaded["accounts"][0]["driver"], "Edge")
            self.assertEqual(loaded["accounts"][1]["driver"], "Chrome")

            service.save_autovisor(loaded)
            reloaded = service.load_autovisor()
            parser = read_ini_config(path)

            self.assertEqual([a["account_id"] for a in reloaded["accounts"]], [2, 7])
            self.assertEqual(reloaded["accounts"][1]["password"], "七%号")
            self.assertEqual(parser.get("plugin-option", "keep"), "yes")
            self.assertEqual(parser.get("user-account-2", "cookiesFile"), "custom.json")
            self.assertEqual(parser.get("course-option-2", "windowWidth"), "1440")

    def test_malformed_autovisor_config_falls_back_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "configs.ini"
            broken = "[missing-end\nvalue = 1"
            path.write_text(broken, encoding="utf-8")
            logs = []

            loaded = self.make_service(root, logs).load_autovisor()

            self.assertEqual(loaded["accounts"][0]["account_id"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), broken)
            self.assertTrue(any("回退默认值" in message for message in logs))

    def test_numbered_accounts_inherit_global_options_without_phantom_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "configs.ini").write_text(
                "[browser-option]\ndriver = Edge\nEXE_PATH = shared.exe\n"
                "[script-option]\nenableHideWindow = False\n"
                "[user-account-2]\nusername = two\npassword = p2\n"
                "[user-account-7]\nusername = seven\npassword = p7\n",
                encoding="utf-8",
            )

            accounts = self.make_service(root).load_autovisor()["accounts"]

            self.assertEqual([account["account_id"] for account in accounts], [2, 7])
            self.assertEqual([account["driver"] for account in accounts], ["Edge", "Edge"])
            self.assertEqual([account["exe_path"] for account in accounts], ["shared.exe", "shared.exe"])

    def test_invalid_ini_boolean_uses_safe_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "configs.ini").write_text(
                "[user-account]\nusername = user\n"
                "[script-option]\nenableHideWindow = perhaps\n",
                encoding="utf-8",
            )

            account = self.make_service(root).load_autovisor()["accounts"][0]

            self.assertFalse(account["enable_hide_window"])

    def test_runtime_config_reads_literal_percent_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "configs.ini"
            path.write_text(
                "[user-account]\nusername = user\npassword = 100%ready\n",
                encoding="utf-8",
            )

            self.assertEqual(Config(str(path)).password, "100%ready")

    def test_autovisor_course_urls_require_official_https_and_are_deduplicated(self):
        valid = "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=one"
        duplicate = "https://wisdom-mooc.zhihuishu.com/study/index?secret=one"
        live = "https://lc.zhihuishu.com/live/vod_room.html?liveId=live-1"

        self.assertTrue(ConfigService.is_zhihuishu_course_url(valid))
        self.assertTrue(ConfigService.is_zhihuishu_course_url(live))
        self.assertFalse(
            ConfigService.is_zhihuishu_course_url(
                "http://studyvideoh5.zhihuishu.com/stuStudy"
            )
        )
        self.assertFalse(
            ConfigService.is_zhihuishu_course_url(
                "https://zhihuishu.com.example.com/stuStudy"
            )
        )
        self.assertFalse(ConfigService.is_zhihuishu_course_url("not-a-url"))

        error = ConfigService.validate_autovisor_accounts(
            [{"course_urls": [valid, "https://example.com/course"]}]
        )
        self.assertIn("第 2 个课程链接无效", error)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self.make_service(root)
            config = {"accounts": [{"account_id": 1, "course_urls": [valid, duplicate, live]}]}

            service.save_autovisor(config)
            saved_urls = service.load_autovisor()["accounts"][0]["course_urls"]

            self.assertEqual(saved_urls, [valid, live])


if __name__ == "__main__":
    unittest.main()
