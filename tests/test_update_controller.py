import unittest

from src.update_controller import UpdateController


class _Manager:
    def __init__(self):
        self.yatori_result = {
            "installed": True,
            "has_update": True,
            "version": "old",
            "info": {"version": "new"},
        }
        self.autovisor_result = {
            "installed": True,
            "has_update": True,
            "version": "old",
            "info": {"version": "new"},
        }
        self.installed = []
        self.install_error = None

    def check_yatori_update(self):
        return self.yatori_result

    def check_autovisor_update(self):
        return self.autovisor_result

    def _install(self, core, release_info, progress):
        if self.install_error:
            raise self.install_error
        progress({"progress": 50, "downloaded": 5, "total": 10})
        self.installed.append((core, release_info["version"]))
        return True

    def install_yatori(self, release_info, progress):
        return self._install("yatori", release_info, progress)

    def install_autovisor(self, release_info, progress):
        return self._install("autovisor", release_info, progress)


class UpdateControllerTests(unittest.TestCase):
    def make_controller(self, manager=None, *, run_async=None, running=None):
        manager = manager or _Manager()
        logs = []
        notices = []
        installed = []

        def log(message, **kwargs):
            logs.append((message, kwargs))

        controller = UpdateController(
            manager,
            log=log,
            show_info=lambda title, message: notices.append(("info", title, message)),
            show_warning=lambda title, message: notices.append(("warning", title, message)),
            show_error=lambda title, message: notices.append(("error", title, message)),
            is_core_running=lambda core: bool((running or {}).get(core)),
            on_installed=installed.append,
            get_autovisor_version=lambda: "current",
            schedule=lambda _delay, callback: callback(),
            run_async=run_async or (lambda target: target()),
        )
        return controller, manager, logs, notices, installed

    def test_yatori_update_confirmation_does_not_install_until_confirmed(self):
        controller, manager, _logs, _notices, installed = self.make_controller()

        result = controller.prepare_yatori_update_confirmation()

        self.assertTrue(result["ok"])
        self.assertEqual(result["updateDialog"]["latestVersion"], "new")
        self.assertEqual(result["updateDialog"]["currentVersion"], "old")
        confirmation_token = result["updateDialog"]["confirmationToken"]
        self.assertTrue(confirmation_token)
        self.assertEqual(manager.installed, [])
        self.assertEqual(installed, [])
        self.assertEqual(controller.yatori_update_info["info"]["version"], "new")

        self.assertTrue(
            controller.install_yatori_confirmed_async(confirmation_token)
        )

        self.assertEqual(manager.installed, [("yatori", "new")])
        self.assertEqual(installed, ["yatori"])
        self.assertIsNone(controller.yatori_update_info)
        self.assertFalse(controller.installing)

    def test_yatori_update_confirmation_rejects_missing_or_wrong_token(self):
        controller, manager, _logs, notices, _installed = self.make_controller()
        result = controller.prepare_yatori_update_confirmation()

        self.assertFalse(controller.install_yatori_confirmed_async(None))
        self.assertFalse(controller.install_yatori_confirmed_async("wrong-token"))

        self.assertEqual(manager.installed, [])
        self.assertTrue(any("确认已失效" in item[2] for item in notices))
        self.assertTrue(result["updateDialog"]["confirmationToken"])

    def test_yatori_confirmation_installs_the_release_shown_in_dialog(self):
        controller, manager, _logs, _notices, _installed = self.make_controller()
        result = controller.prepare_yatori_update_confirmation()
        confirmation_token = result["updateDialog"]["confirmationToken"]
        controller.yatori_update_info = {
            "info": {"version": "newer-background-release"}
        }

        self.assertTrue(
            controller.install_yatori_confirmed_async(confirmation_token)
        )

        self.assertEqual(manager.installed, [("yatori", "new")])
        self.assertFalse(
            controller.install_yatori_confirmed_async(confirmation_token)
        )

    def test_yatori_update_confirmation_includes_current_version_and_release_notes(self):
        manager = _Manager()
        manager.yatori_result = {
            "installed": True,
            "has_update": True,
            "version": "v2.6.2-beta.8",
            "info": {
                "version": "v2.6.2-beta.11",
                "body": "新增：修复智慧树登录适配\n修复：提升更新检测稳定性",
            },
        }
        controller, _manager, logs, _notices, _installed = self.make_controller(manager)

        result = controller.prepare_yatori_update_confirmation()

        joined_logs = "\n".join(item[0] for item in logs)
        self.assertIn("当前版本: v2.6.2-beta.8", joined_logs)
        self.assertIn("等待用户确认更新", joined_logs)
        self.assertIn("修复智慧树登录适配", result["updateDialog"]["releaseNotes"])

    def test_yatori_current_version_result_includes_release_notes(self):
        manager = _Manager()
        manager.yatori_result = {
            "installed": True,
            "has_update": False,
            "version": "v2.6.2-beta.11",
            "info": {
                "version": "v2.6.2-beta.11",
                "body": "版本介绍第一行\n版本介绍第二行",
            },
        }
        controller, _manager, _logs, notices, _installed = self.make_controller(manager)

        result = controller.prepare_yatori_update_confirmation()

        self.assertTrue(result["upToDate"])
        self.assertIn("版本介绍第一行", result["message"])
        self.assertTrue(any("最新版本介绍" in item[2] for item in notices))
        self.assertTrue(any("版本介绍第一行" in item[2] for item in notices))

    def test_yatori_local_newer_result_reports_skipped_downgrade(self):
        manager = _Manager()
        manager.yatori_result = {
            "installed": True,
            "has_update": False,
            "local_newer": True,
            "version": "v2.6.2-beta.12",
            "info": {
                "version": "v2.6.2-beta.11",
                "body": "older release notes",
            },
        }
        controller, _manager, _logs, notices, _installed = self.make_controller(manager)

        result = controller.prepare_yatori_update_confirmation()

        self.assertTrue(result["upToDate"])
        self.assertTrue(result["localNewer"])
        self.assertIn("跳过降级", result["message"])
        self.assertIn("本地版本高于远端", result["toast"])
        self.assertNotIn("older release notes", result["message"])
        self.assertTrue(any("跳过降级" in item[2] for item in notices))

    def test_duplicate_yatori_check_is_rejected_while_first_is_pending(self):
        tasks = []
        controller, _manager, _logs, notices, _installed = self.make_controller(
            run_async=tasks.append
        )

        self.assertTrue(controller.check_yatori_async(explicit=True))
        self.assertFalse(controller.check_yatori_async(explicit=True))
        self.assertTrue(controller.yatori_checking)
        self.assertEqual(len(tasks), 1)
        self.assertTrue(any("重复" in item[2] for item in notices))

        tasks.pop()()
        self.assertFalse(controller.yatori_checking)

    def test_running_core_blocks_install(self):
        controller, _manager, _logs, notices, _installed = self.make_controller(
            running={"autovisor": True}
        )
        controller.autovisor_update_info = {"info": {"version": "new"}}

        self.assertFalse(controller.install_autovisor_async())
        self.assertFalse(controller.installing)
        self.assertTrue(any(item[0] == "warning" for item in notices))

    def test_install_exception_resets_shared_install_state(self):
        manager = _Manager()
        manager.install_error = RuntimeError("network failed")
        controller, _manager, logs, notices, installed = self.make_controller(manager)
        controller.yatori_update_info = {"info": {"version": "new"}}

        self.assertTrue(controller.install_yatori_async())

        self.assertFalse(controller.installing)
        self.assertIsNone(controller.installing_core)
        self.assertEqual(installed, [])
        self.assertTrue(any("network failed" in item[0] for item in logs))
        self.assertTrue(any(item[0] == "error" for item in notices))

    def test_latest_autovisor_check_clears_stale_install_candidate(self):
        manager = _Manager()
        manager.autovisor_result = {
            "installed": True,
            "has_update": False,
            "version": "current",
            "info": {"version": "current"},
        }
        controller, _manager, _logs, notices, _installed = self.make_controller(manager)
        controller.autovisor_update_info = {"info": {"version": "stale"}}

        self.assertTrue(controller.check_autovisor_async())

        self.assertIsNone(controller.autovisor_update_info)
        self.assertFalse(controller.autovisor_checking)
        self.assertTrue(any(item[0] == "info" for item in notices))

    def test_autovisor_update_log_marks_release_as_reference_only(self):
        controller, _manager, logs, _notices, _installed = self.make_controller()

        self.assertTrue(controller.check_autovisor_async())

        joined_logs = "\n".join(item[0] for item in logs)
        self.assertIn("本地适配版", joined_logs)
        self.assertIn("不会用上游包覆盖安装", joined_logs)
        self.assertNotIn("可通过 install_autovisor_update", joined_logs)

    def test_check_exception_resets_checking_state(self):
        class BrokenManager(_Manager):
            def check_yatori_update(self):
                raise RuntimeError("offline")

        controller, _manager, logs, _notices, _installed = self.make_controller(
            BrokenManager()
        )

        self.assertTrue(controller.check_yatori_async(explicit=False))
        self.assertFalse(controller.yatori_checking)
        self.assertTrue(any("offline" in item[0] for item in logs))


if __name__ == "__main__":
    unittest.main()
