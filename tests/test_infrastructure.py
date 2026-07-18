import asyncio
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
    Dependency,
    find_missing_dependencies,
)
from src.launcher_api import WebLauncherAPI


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
