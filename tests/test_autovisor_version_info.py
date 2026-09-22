# encoding=utf-8
"""Autovisor 只读版本信息弹窗 + ZError 致谢外链的回归契约。

覆盖：弹窗载荷（版本号 / 声明 / 原作者下载引导 / 无安装令牌）、失败与忙路径、
web 动作分发、外链白名单（含新增主机）、前端结构契约，以及"不在启动时自动检查
Autovisor 更新"这条政策。
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.desktop_platform_service import DesktopPlatformService
from src.update_controller import (
    AUTOVISOR_ADAPTED_NOTICE,
    AUTOVISOR_INSTALL_DISABLED_REASON,
    AUTOVISOR_UPSTREAM_LINKS,
    AUTOVISOR_UPSTREAM_LINK_MAP,
    UpdateController,
)
from src.web_action_service import CONTRIBUTOR_LINKS, WebActionService

PAGE = (PROJECT_ROOT / "web" / "现代启动器_UI_预览.html").read_text(encoding="utf-8")
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")

RELEASE = {
    "version": "v9.9.9",
    "asset_name": "Autovisor.zip",
    "published_at": "2026-09-01T00:00:00Z",
    "body": "修复了若干问题\n新增了若干功能\n<!-- 注释应被忽略 -->",
}


class FakeCoreManager:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def check_autovisor_update(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def make_controller(core_manager, *, errors=None, version="20260424 修复版"):
    return UpdateController(
        core_manager,
        log=lambda *_a, **_k: None,
        show_info=lambda *_a: None,
        show_warning=lambda *_a: None,
        show_error=(lambda *a: errors.append(a)) if errors is not None else (lambda *_a: None),
        is_core_running=lambda _core: False,
        on_installed=lambda _core: None,
        get_autovisor_version=lambda: version,
        schedule=lambda _delay, callback: callback(),
        run_async=lambda target: target(),
    )


class AutovisorDialogTests(unittest.TestCase):
    def test_dialog_is_read_only_and_carries_version_and_guidance(self):
        controller = make_controller(
            FakeCoreManager({"has_update": True, "installed": True,
                             "version": "20260424 修复版", "info": RELEASE})
        )

        result = controller.show_autovisor_update_dialog()

        self.assertTrue(result["ok"])
        dialog = result["updateDialog"]
        self.assertEqual(dialog["core"], "autovisor")
        self.assertEqual(dialog["latestVersion"], "v9.9.9")
        self.assertEqual(dialog["currentVersion"], "20260424 修复版")
        self.assertEqual(dialog["assetName"], "Autovisor.zip")
        self.assertEqual(dialog["publishedAt"], "2026-09-01T00:00:00Z")
        self.assertIn("修复了若干问题", dialog["releaseNotes"])
        self.assertNotIn("<!--", dialog["releaseNotes"])
        self.assertIs(dialog["installDisabled"], True)
        self.assertIn("不提供直接覆盖安装", dialog["installDisabledReason"])

    def test_dialog_carries_no_install_token(self):
        """只读：绝不能下发可供安装的确认令牌。"""
        controller = make_controller(
            FakeCoreManager({"has_update": True, "installed": True,
                             "version": "20260422", "info": RELEASE})
        )

        dialog = controller.show_autovisor_update_dialog()["updateDialog"]

        self.assertNotIn("confirmationToken", dialog)
        self.assertFalse(hasattr(controller, "_autovisor_confirmation"))
        self.assertFalse(controller.installing)

    def test_notice_and_upstream_links_are_attached(self):
        controller = make_controller(
            FakeCoreManager({"has_update": True, "installed": True,
                             "version": "20260422", "info": RELEASE})
        )

        dialog = controller.show_autovisor_update_dialog()["updateDialog"]

        self.assertEqual(dialog["notice"], AUTOVISOR_ADAPTED_NOTICE)
        self.assertIn("声明：", dialog["notice"])
        self.assertIn("账号样本缺失", dialog["notice"])
        self.assertIn("zhs", dialog["notice"])

        links = {item["key"]: item for item in dialog["downloadLinks"]}
        self.assertEqual(
            links["autovisor_github"]["url"],
            "https://github.com/CXRunfree/Autovisor/releases",
        )
        self.assertEqual(
            links["autovisor_lanzou"]["url"], "https://wwk.lanzouj.com/b05evsxif"
        )
        self.assertEqual(links["autovisor_lanzou"]["password"], "492l")
        self.assertEqual(
            AUTOVISOR_UPSTREAM_LINK_MAP["autovisor_github"],
            links["autovisor_github"]["url"],
        )

    def test_dialog_shows_upstream_even_when_reported_up_to_date(self):
        """本地适配版与上游 tag 不可比，手动点击时应照常展示最新版本号。"""
        controller = make_controller(
            FakeCoreManager({"has_update": False, "installed": True,
                             "version": "20260424 修复版", "info": RELEASE})
        )

        dialog = controller.show_autovisor_update_dialog()["updateDialog"]

        self.assertEqual(dialog["latestVersion"], "v9.9.9")
        self.assertIn("v9.9.9", dialog["summary"])

    def test_failures_are_reported_without_raising(self):
        errors = []
        raising = make_controller(FakeCoreManager(error=RuntimeError("boom")), errors=errors)
        result = raising.show_autovisor_update_dialog()
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["message"])

        empty = make_controller(FakeCoreManager(None), errors=errors)
        result = empty.show_autovisor_update_dialog()
        self.assertFalse(result["ok"])
        self.assertIn("未能获取", result["message"])

        self.assertTrue(errors, "失败路径应通过 show_error 反馈")


class FrontendContractTests(unittest.TestCase):
    def test_page_exposes_a_read_only_autovisor_modal(self):
        self.assertIn('id="autovisor-update-modal"', PAGE)
        self.assertIn("Autovisor 版本信息", PAGE)
        self.assertGreaterEqual(PAGE.count("openAutovisorUpdateDialog()"), 2)
        self.assertNotIn("performAction('check_autovisor_update')", PAGE)
        self.assertNotIn("performAction('install_autovisor_update')", PAGE)
        self.assertIn("本地适配版：不提供直接覆盖安装", PAGE)
        # 既有契约不能被破坏
        self.assertGreaterEqual(PAGE.count("Autovisor 本地适配版"), 1)
        self.assertIn('fa-magnifying-glass"></i> 检查版本', PAGE)

    def test_frontend_calls_the_read_only_action(self):
        self.assertIn("async function openAutovisorUpdateDialog", FRONTEND)
        self.assertIn("function showAutovisorUpdateModal", FRONTEND)
        self.assertIn("function closeAutovisorUpdateModal", FRONTEND)
        self.assertIn("'show_autovisor_update_dialog'", FRONTEND)
        self.assertIn("'open_update_link'", FRONTEND)
        self.assertNotIn("'install_autovisor_update'", FRONTEND)

    def test_zerror_card_links_to_its_site(self):
        self.assertEqual(CONTRIBUTOR_LINKS["zerror"], "https://app.zerror.cc/")
        self.assertIn('data-contributor="zerror"', PAGE)
        self.assertIn('href="https://app.zerror.cc/"', PAGE)
        # 图标改为随包分发的 SVG（不再使用占位字母）
        self.assertIn("assets/zerror_logo.svg", PAGE)
        self.assertNotIn('">Z</div>', PAGE)
        self.assertTrue(
            (PROJECT_ROOT / "web" / "assets" / "zerror_logo.svg").is_file()
        )

    def test_startup_does_not_auto_check_autovisor(self):
        launcher = (PROJECT_ROOT / "统一启动器.py").read_text(encoding="utf-8")
        body = launcher.split("def auto_check_cores(self):", 1)[1].split(
            "def __init__(self):", 1
        )[0]
        self.assertIn("check_yatori_update_async", body)
        self.assertNotIn("autovisor", body.lower())


class ActionAndAllowlistTests(unittest.TestCase):
    def test_web_action_passes_the_dialog_through(self):
        class FakeLauncher:
            def __init__(self):
                self.opened = []

            def show_autovisor_update_dialog(self):
                return {"ok": True, "updateDialog": {"core": "autovisor"}}

            def open_external_url(self, url):
                self.opened.append(url)
                return {"ok": True, "url": url}

            def get_web_initial_state(self):
                return {"ok": True}

        launcher = FakeLauncher()
        service = WebActionService(launcher)

        result = service.perform("show_autovisor_update_dialog")
        self.assertTrue(result["ok"])
        self.assertEqual(result["updateDialog"], {"core": "autovisor"})

        opened = service.perform("open_update_link", "autovisor_lanzou")
        self.assertTrue(opened["ok"])
        self.assertEqual(launcher.opened, ["https://wwk.lanzouj.com/b05evsxif"])

        unknown = service.perform("open_update_link", "nope")
        self.assertFalse(unknown["ok"])
        self.assertEqual(len(launcher.opened), 1, "未知键不得打开任何链接")

    def test_external_url_allowlist_covers_new_hosts(self):
        base = Path(tempfile.mkdtemp(prefix="dsh-allowlist-"))
        try:
            opened = []
            service = DesktopPlatformService(
                str(base),
                str(base / "统一启动器.py"),
                url_opener=opened.append,
            )

            for url in (
                "https://app.zerror.cc/",
                "https://wwk.lanzouj.com/b05evsxif",
                "https://github.com/CXRunfree/Autovisor/releases",
                "https://yatori-dev.github.io/yatori-docs/",
            ):
                with self.subTest(url=url):
                    self.assertTrue(service.open_external_url(url)["ok"], url)

            for url in (
                "https://evil.example.com/",
                "https://app.zerror.cc.evil.com/",
                "http://app.zerror.cc/",
                "https://wwk.lanzouj.com/other",
            ):
                with self.subTest(url=url):
                    self.assertFalse(service.open_external_url(url)["ok"], url)

            self.assertEqual(len(opened), 4)
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
