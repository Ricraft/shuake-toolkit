import asyncio
import hashlib
import json
import os
import ssl
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.atomic_io import (
    atomic_dump_json,
    atomic_write_text,
    capture_file_state,
    restore_file_state,
)
from src.core_manager import CoreManager
from src.dependencies import (
    CORE_DEPENDENCIES,
    OPTIONAL_DEPENDENCIES,
    WEB_DASHBOARD_DEPENDENCIES,
    Dependency,
    find_missing_dependencies,
)
from src.launcher_api import WebLauncherAPI
from scripts.install_dependencies import (
    install_dependencies,
    install_playwright_browser,
    selected_dependencies,
)


class InfrastructureTests(unittest.TestCase):
    def test_atomic_writers_create_parent_and_complete_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            text_path = root / "nested" / "value.txt"
            json_path = root / "value.json"
            atomic_write_text(text_path, "完整")
            atomic_dump_json(json_path, {"enabled": True})
            self.assertEqual(text_path.read_text(encoding="utf-8"), "完整")
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), {"enabled": True})

    def test_file_snapshot_restores_content_and_original_absence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = root / "existing.ini"
            originally_missing = root / "new.json"
            existing.write_bytes(b"old-content")
            snapshot = capture_file_state([existing, originally_missing])

            existing.write_bytes(b"partial-new-content")
            originally_missing.write_bytes(b"new-content")
            restore_file_state(snapshot)

            self.assertEqual(existing.read_bytes(), b"old-content")
            self.assertFalse(originally_missing.exists())

    def test_dependency_discovery_returns_requirement_names(self):
        def fake_import(name):
            if name == "missing":
                raise ImportError(name)
            return object()

        dependencies = [Dependency("available", "a>=1"), Dependency("missing", "b>=2")]
        self.assertEqual(find_missing_dependencies(dependencies, fake_import), ["b>=2"])

    def test_requirement_file_matches_launcher_dependency_policy(self):
        project_root = Path(__file__).resolve().parents[1]
        declared = {
            line.strip()
            for line in (project_root / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            dependency.requirement
            for dependency in CORE_DEPENDENCIES + OPTIONAL_DEPENDENCIES
        }
        self.assertEqual(declared, expected)

    def test_dashboard_requirement_file_matches_dependency_policy(self):
        project_root = Path(__file__).resolve().parents[1]
        declared = {
            line.strip()
            for line in (project_root / "Autovisor" / "requirements-web.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            dependency.requirement for dependency in WEB_DASHBOARD_DEPENDENCIES
        }
        self.assertEqual(declared, expected)

    def test_dependency_installer_is_web_only_and_dashboard_is_opt_in(self):
        project_root = Path(__file__).resolve().parents[1]
        source = (project_root / "scripts" / "install_dependencies.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("tkinter", source)
        self.assertEqual(selected_dependencies(), CORE_DEPENDENCIES)
        self.assertEqual(
            selected_dependencies(with_dashboard=True),
            CORE_DEPENDENCIES + WEB_DASHBOARD_DEPENDENCIES,
        )

    def test_dependency_installer_uses_selected_python_and_index(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return type("Result", (), {"returncode": 0})()

        failures = install_dependencies(
            ["example>=1,<2"],
            python_executable="custom-python",
            index_url="https://packages.example/simple",
            environment={"SAFE": "1"},
            run=fake_run,
            output=lambda _message: None,
        )

        self.assertEqual(failures, [])
        self.assertEqual(
            calls[0][0],
            [
                "custom-python",
                "-m",
                "pip",
                "install",
                "example>=1,<2",
                "--index-url",
                "https://packages.example/simple",
                "--no-cache-dir",
            ],
        )
        self.assertEqual(calls[0][1]["env"], {"SAFE": "1"})
        self.assertFalse(calls[0][1]["check"])

    def test_playwright_installer_scopes_download_mirror_to_child(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return type("Result", (), {"returncode": 0})()

        with patch.dict(os.environ, {}, clear=True):
            installed = install_playwright_browser(
                python_executable="custom-python",
                download_host="https://browser.example",
                run=fake_run,
                output=lambda _message: None,
            )

        self.assertTrue(installed)
        self.assertEqual(
            calls[0][0],
            ["custom-python", "-m", "playwright", "install", "chromium"],
        )
        self.assertEqual(
            calls[0][1]["env"]["PLAYWRIGHT_DOWNLOAD_HOST"],
            "https://browser.example",
        )
        self.assertNotIn("PLAYWRIGHT_DOWNLOAD_HOST", os.environ)

    def test_launcher_api_is_a_thin_delegate(self):
        class FakeLauncher:
            def get_web_runtime_state(self):
                return {"running": False}

        self.assertEqual(
            WebLauncherAPI(FakeLauncher()).get_runtime_state(),
            {"running": False},
        )

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../outside.txt", "bad")
            with zipfile.ZipFile(archive) as handle:
                with self.assertRaises(ValueError):
                    CoreManager._safe_extract_zip(handle, root / "output")

    def test_core_downloads_verify_tls_and_disable_proxies_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LAUNCHER_ALLOW_GITHUB_PROXIES", None)
                manager = CoreManager(temp_dir)
            self.assertEqual(manager.ssl_context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(manager.ssl_context.check_hostname)
            self.assertFalse(manager.allow_github_proxies)
            direct = manager._build_github_candidate_urls(
                "https://github.com/example/project/releases/latest"
            )
            self.assertEqual(
                direct,
                [
                    (
                        "GitHub 直连",
                        "https://github.com/example/project/releases/latest",
                    )
                ],
            )

            manager.allow_github_proxies = True
            self.assertEqual(
                len(manager._build_github_candidate_urls("https://github.com/a/b")),
                len(manager.GITHUB_MIRRORS),
            )

    def test_yatori_fallback_does_not_invent_obsolete_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            manager.allow_github_proxies = False
            with patch(
                "src.core_manager.urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ) as urlopen:
                result = manager._get_yatori_latest_fallback()

            self.assertIsNone(result)
            self.assertEqual(urlopen.call_count, 1)
            self.assertFalse(any("v1.2.8" in message for message in logs))

    def test_yatori_release_preserves_github_asset_digest_metadata(self):
        payload = b"publisher asset"
        digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            result = manager._parse_yatori_release(
                {
                    "tag_name": "v2.6.3",
                    "published_at": "2026-07-18T00:00:00Z",
                    "assets": [
                        {
                            "id": 17,
                            "name": "yatori-windows-amd64.zip",
                            "browser_download_url": "https://example.test/yatori.zip",
                            "size": len(payload),
                            "digest": digest,
                        }
                    ],
                }
            )

        self.assertEqual(result["digest"], digest)
        self.assertEqual(result["asset_size"], len(payload))
        self.assertEqual(result["asset_id"], 17)
        self.assertEqual(
            result["verification_source"],
            "github-release-asset-digest",
        )

    def test_yatori_release_list_selects_newest_prerelease_with_windows_asset(self):
        payload = b"publisher asset"
        digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        releases = [
            {
                "tag_name": "v2.6.1-beta.8",
                "published_at": "2026-07-20T10:00:00Z",
                "prerelease": True,
                "assets": [
                    {
                        "name": "yatori-windows-amd64.zip",
                        "browser_download_url": "https://example.test/beta8.zip",
                        "digest": digest,
                        "size": len(payload),
                    }
                ],
            },
            {
                "tag_name": "v2.6.1-beta.11",
                "published_at": "2026-07-25T10:00:00Z",
                "prerelease": True,
                "assets": [
                    {
                        "name": "yatori-windows-amd64.zip",
                        "browser_download_url": "https://example.test/beta11.zip",
                        "digest": digest,
                        "size": len(payload),
                    }
                ],
            },
            {
                "tag_name": "v9.9.9-draft",
                "published_at": "2026-07-26T10:00:00Z",
                "draft": True,
                "assets": [
                    {
                        "name": "yatori-windows-amd64.zip",
                        "browser_download_url": "https://example.test/draft.zip",
                    }
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            selected = manager._select_yatori_release(releases)

        self.assertEqual(selected["version"], "v2.6.1-beta.11")
        self.assertEqual(selected["download_url"], "https://example.test/beta11.zip")
        self.assertTrue(selected["prerelease"])
        self.assertEqual(selected["verification_source"], "github-release-asset-digest")

    def test_yatori_update_check_queries_releases_list_before_latest_endpoint(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        releases = [
            {
                "tag_name": "v2.6.1-beta.11",
                "published_at": "2026-07-25T10:00:00Z",
                "prerelease": True,
                "assets": [
                    {
                        "name": "yatori-windows-amd64.zip",
                        "browser_download_url": "https://example.test/beta11.zip",
                        "digest": "sha256:" + "a" * 64,
                        "size": 1024,
                    }
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            requested = []

            def open_url(request, **_kwargs):
                requested.append(request.full_url)
                return Response(releases)

            with patch(
                "src.core_manager.urllib.request.urlopen",
                side_effect=open_url,
            ):
                result = manager.get_yatori_latest_release()

        self.assertEqual(requested, [manager.YATORI_RELEASES_API_URL])
        self.assertEqual(result["version"], "v2.6.1-beta.11")

    def test_release_archive_requires_matching_sha256_and_size(self):
        payload = b"verified core archive"
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "core.zip"
            archive.write_bytes(payload)
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            release = {
                "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "asset_size": len(payload),
            }

            self.assertTrue(manager._verify_release_archive(archive, release))
            self.assertTrue(any("SHA-256 校验通过" in item for item in logs))

            release["digest"] = "sha256:" + "0" * 64
            self.assertFalse(manager._verify_release_archive(archive, release))
            self.assertTrue(any("SHA-256 校验失败" in item for item in logs))

            release["digest"] = f"sha256:{hashlib.sha256(payload).hexdigest()}"
            release["asset_size"] = len(payload) + 1
            self.assertFalse(manager._verify_release_archive(archive, release))
            self.assertTrue(any("资源大小不匹配" in item for item in logs))

    def test_unverified_core_archive_is_blocked_unless_explicitly_overridden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "core.zip"
            archive.write_bytes(b"unverified")
            manager = CoreManager(temp_dir)

            self.assertFalse(manager._verify_release_archive(archive, {}))

            manager.allow_unverified_core_updates = True
            self.assertTrue(manager._verify_release_archive(archive, {}))

    def test_unverified_yatori_download_never_replaces_existing_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            marker = Path(manager.yatori_path) / "existing-core.txt"
            marker.write_text("keep", encoding="utf-8")
            download_calls = []

            def fake_download(_url, target_path, _progress=None):
                download_calls.append(target_path)
                with zipfile.ZipFile(target_path, "w") as archive:
                    archive.writestr("new-core.txt", "new")
                return True

            manager.download_file = fake_download
            installed = manager.install_yatori(
                {
                    "version": "v9.9.9",
                    "download_url": "https://example.test/yatori.zip",
                }
            )

            self.assertFalse(installed)
            self.assertEqual(download_calls, [])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse((Path(manager.yatori_path) / "new-core.txt").exists())

    def test_yatori_scraper_uses_expanded_assets_before_release_page(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return (
                    b'<a href="/Yatori-Dev/yatori-go-console/releases/download/'
                    b'v2.6.2/yatori-windows-amd64.zip">download</a>'
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            manager.allow_github_proxies = False
            requested = []

            def open_url(request, **_kwargs):
                requested.append(request.full_url)
                return Response()

            with patch(
                "src.core_manager.urllib.request.urlopen",
                side_effect=open_url,
            ):
                result = manager._scrape_yatori_download_url(
                    "https://github.com/Yatori-Dev/yatori-go-console/releases/tag/v2.6.2"
                )

            self.assertEqual(
                result,
                "https://github.com/Yatori-Dev/yatori-go-console/releases/download/"
                "v2.6.2/yatori-windows-amd64.zip",
            )
            self.assertEqual(
                requested,
                [
                    "https://github.com/Yatori-Dev/yatori-go-console/releases/"
                    "expanded_assets/v2.6.2"
                ],
            )

    def test_background_tasks_are_cancelled(self):
        async def scenario():
            from Autovisor.modules.async_utils import cancel_background_tasks

            task = asyncio.create_task(asyncio.sleep(60))
            await cancel_background_tasks([task])
            self.assertTrue(task.cancelled())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
