# encoding=utf-8
"""stdlib regression tests for the launcher updater's official-page fallback."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.launcher_update_service import (
    LAUNCHER_LATEST_RELEASE_PAGE_URL,
    LAUNCHER_RELEASES_API_URL,
    LAUNCHER_REPO,
    LAUNCHER_MANIFEST_NAME,
    LauncherUpdateService,
    REQUIRED_STAGED_FILES,
)


TAG = "v1.3.0"
CURRENT = "v1.2.0"
ZIP_NAME = f"launcher-{TAG}.zip"
ZIP_URL = (
    f"https://github.com/{LAUNCHER_REPO}/releases/download/{TAG}/{ZIP_NAME}"
)
MANIFEST_URL = (
    f"https://github.com/{LAUNCHER_REPO}/releases/download/"
    f"{TAG}/{LAUNCHER_MANIFEST_NAME}"
)
ASSETS_URL = (
    f"https://github.com/{LAUNCHER_REPO}/releases/expanded_assets/{TAG}"
)
TAG_URL = f"https://github.com/{LAUNCHER_REPO}/releases/tag/{TAG}"


def _links_html(*urls):
    return "".join(f'<a href="{url}">asset</a>' for url in urls).encode("utf-8")


def _make_archive(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in REQUIRED_STAGED_FILES:
            archive.writestr(name.replace(os.sep, "/"), f"content:{name}\n")
    return Path(path).read_bytes()


class LauncherUpdateFallbackTests(unittest.TestCase):
    def make_service(
        self,
        *,
        api_status=403,
        api_exception=None,
        latest_status=200,
        latest_final=TAG_URL,
        latest_body=b"<html>release</html>",
        assets_status=200,
        assets_final=ASSETS_URL,
        assets_body=None,
        manifest=None,
        archive_data=None,
    ):
        calls = {"api": [], "pages": [], "downloads": []}
        if assets_body is None:
            # GitHub's expanded_assets response uses relative download links.
            assets_body = _links_html(
                f"../download/{TAG}/{ZIP_NAME}",
                f"../download/{TAG}/{LAUNCHER_MANIFEST_NAME}",
            )

        def opener(url, timeout=None):
            calls["api"].append(url)
            if url == LAUNCHER_RELEASES_API_URL:
                if api_exception is not None:
                    raise api_exception
                return api_status, b"{}"
            if url == MANIFEST_URL:
                payload = manifest if manifest is not None else {}
                return 200, json.dumps(payload).encode("utf-8")
            return 404, b""

        def page_reader(url, timeout=None):
            calls["pages"].append(url)
            if url == LAUNCHER_LATEST_RELEASE_PAGE_URL:
                return latest_status, latest_body, latest_final
            if url == ASSETS_URL:
                return assets_status, assets_body, assets_final
            raise AssertionError(f"unexpected page URL: {url}")

        def downloader(url, target_path, progress=None, timeout=None):
            calls["downloads"].append(url)
            if archive_data is None:
                return False
            Path(target_path).write_bytes(archive_data)
            if callable(progress):
                progress(len(archive_data), len(archive_data))
            return True

        service = LauncherUpdateService(
            tempfile.gettempdir(),
            current_version=CURRENT,
            opener=opener,
            page_reader=page_reader,
            downloader=downloader,
            pid=0,
        )
        service.test_calls = calls
        return service

    def test_api_403_falls_back_only_to_exact_official_assets(self):
        service = self.make_service(api_status=403)

        result = service.check_for_update()

        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["release"]["download_url"], ZIP_URL)
        self.assertEqual(result["release"]["manifest_url"], MANIFEST_URL)
        self.assertTrue(result["release"]["page_fallback"])
        self.assertIn("beta/prerelease", result["message"])
        self.assertEqual(service.test_calls["api"], [LAUNCHER_RELEASES_API_URL])
        self.assertEqual(
            service.test_calls["pages"], [LAUNCHER_LATEST_RELEASE_PAGE_URL, ASSETS_URL]
        )

    def test_latest_page_without_redirect_requires_one_official_tag_link(self):
        service = self.make_service(
            latest_final=LAUNCHER_LATEST_RELEASE_PAGE_URL,
            latest_body=_links_html(TAG_URL),
        )

        result = service.check_for_update()

        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["latestVersion"], TAG)

    def test_successful_api_does_not_request_html_pages(self):
        releases = [{
            "tag_name": TAG,
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [{
                "name": ZIP_NAME,
                "browser_download_url": "https://assets.example.invalid/launcher.zip",
                "digest": "sha256:" + "a" * 64,
            }],
        }]
        calls = []

        def opener(url, timeout=None):
            calls.append(url)
            return 200, json.dumps(releases).encode("utf-8")

        service = LauncherUpdateService(
            tempfile.gettempdir(),
            current_version=CURRENT,
            opener=opener,
            page_reader=lambda *_: self.fail("normal API path must not read HTML"),
        )

        result = service.check_for_update()

        self.assertEqual(result["status"], "update_available")
        self.assertEqual(calls, [LAUNCHER_RELEASES_API_URL])

    def test_api_429_and_5xx_can_use_page_fallback(self):
        for status in (429, 503):
            with self.subTest(status=status):
                service = self.make_service(api_status=status)
                result = service.check_for_update()
                self.assertEqual(result["status"], "update_available")

    def test_network_failure_can_use_page_fallback(self):
        service = self.make_service(api_exception=OSError("API offline"))

        result = service.check_for_update()

        self.assertEqual(result["status"], "update_available")
        self.assertTrue(service.test_calls["pages"])

    def test_external_and_spoof_asset_urls_are_not_installable(self):
        body = _links_html(
            "https://evil.example/launcher-v1.3.0.zip",
            "https://github.com.evil.invalid/Ricraft/shuake-toolkit/releases/"
            f"download/{TAG}/{LAUNCHER_MANIFEST_NAME}",
            f"https://github.com/{LAUNCHER_REPO}/releases/download-evil/"
            f"{TAG}/{ZIP_NAME}",
            f"https://github.com/{LAUNCHER_REPO}/releases/download/"
            f"{TAG}/launcher-v9.9.9.zip",
        )
        service = self.make_service(assets_body=body)

        result = service.check_for_update()
        release = result["release"]

        self.assertEqual(result["status"], "update_available")
        self.assertIsNone(release["download_url"])
        self.assertIsNone(release["manifest_url"])
        self.assertIn("未同时提供", release["install_unavailable_reason"])
        self.assertIn("仅显示版本", result["message"])

    def test_missing_manifest_only_displays_version_and_no_download_link(self):
        service = self.make_service(assets_body=_links_html(ZIP_URL))

        result = service.check_for_update()
        prepared = service.prepare_update_confirmation()
        dialog = prepared["updateDialog"]

        self.assertEqual(result["status"], "update_available")
        self.assertIsNone(result["release"]["download_url"])
        self.assertFalse(dialog["downloadable"])
        self.assertIn("不可自动安装", dialog["summary"])
        self.assertIn("正式版", dialog["releaseNotes"])

    def test_malicious_latest_redirect_is_a_fallback_failure(self):
        service = self.make_service(latest_final="https://evil.invalid/releases/tag/v9.9.9")

        result = service.check_for_update()

        self.assertEqual(result["status"], "error")
        self.assertIn("403", result["message"])
        self.assertIn("官方 Releases 页面回退失败", result["message"])
        self.assertIn("非官方", result["message"])

    def test_unavailable_latest_page_keeps_original_api_error(self):
        service = self.make_service(api_status=429, latest_status=503)

        result = service.check_for_update()

        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 429", result["message"])
        self.assertIn("回退失败", result["message"])
        self.assertIn("页面返回 HTTP 503", result["message"])

    def test_unparseable_tag_is_rejected(self):
        service = self.make_service(
            latest_final=f"https://github.com/{LAUNCHER_REPO}/releases/tag/nightly"
        )

        result = service.check_for_update()

        self.assertEqual(result["status"], "error")
        self.assertIn("不是目标仓库", result["message"])

    def test_fallback_install_still_requires_manifest_sha256_size_and_token(self):
        with tempfile.TemporaryDirectory(prefix="launcher-fallback-") as temp_dir:
            archive_path = Path(temp_dir) / ZIP_NAME
            archive_data = _make_archive(archive_path)
            cases = (
                {"size": len(archive_data)},  # no sha256
                {"sha256": hashlib.sha256(archive_data).hexdigest()},  # no size
            )
            for manifest in cases:
                with self.subTest(manifest=manifest):
                    service = self.make_service(
                        manifest={"version": TAG, **manifest},
                        archive_data=archive_data,
                    )
                    service.base_dir = temp_dir
                    prepared = service.prepare_update_confirmation()
                    dialog = prepared["updateDialog"]
                    self.assertTrue(dialog["downloadable"])
                    token = dialog["confirmationToken"]

                    result = service.install_confirmed(token)

                    self.assertFalse(result["ok"])
                    self.assertIn("已拒绝自动安装", result["message"])
                    self.assertEqual(service.test_calls["downloads"], [ZIP_URL])
                    self.assertFalse(
                        (Path(temp_dir) / ".update-staging" / "应用更新.cmd").exists()
                    )
                    self.assertFalse(service.install_confirmed(token)["ok"])

    def test_valid_fallback_download_is_sha256_and_size_verified(self):
        with tempfile.TemporaryDirectory(prefix="launcher-fallback-valid-") as temp_dir:
            archive_data = _make_archive(Path(temp_dir) / ZIP_NAME)
            manifest = {
                "version": TAG,
                "sha256": hashlib.sha256(archive_data).hexdigest(),
                "size": len(archive_data),
                "fileList": [path.replace(os.sep, "/") for path in REQUIRED_STAGED_FILES],
            }
            service = self.make_service(manifest=manifest, archive_data=archive_data)
            service.base_dir = temp_dir

            dialog = service.prepare_update_confirmation()["updateDialog"]
            result = service.install_confirmed(dialog["confirmationToken"])

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["staged"])
            self.assertEqual(service.test_calls["downloads"], [ZIP_URL])
            self.assertFalse(service.install_confirmed(dialog["confirmationToken"])["ok"])


if __name__ == "__main__":
    unittest.main()
