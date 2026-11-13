import asyncio
import hashlib
import json
import os
import ssl
import tempfile
import time
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
    ensure_core_dependencies,
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

    def test_dependency_discovery_treats_broken_binary_import_as_unavailable(self):
        dependency = Dependency("broken_binary", "broken-binary>=1")

        def fake_import(_name):
            raise OSError("required DLL could not be loaded")

        self.assertEqual(
            find_missing_dependencies([dependency], fake_import),
            ["broken-binary>=1"],
        )

    def test_dependency_auto_install_rechecks_import_before_success(self):
        installed_requirements = []
        messages = []

        def fake_import(_name):
            if installed_requirements:
                raise OSError("installed package still cannot load")
            raise ImportError("missing")

        def fake_install(command, **_kwargs):
            installed_requirements.append(command[4])

        failures = ensure_core_dependencies(
            include_optional=False,
            python_executable="custom-python",
            import_module=fake_import,
            install=fake_install,
            output=messages.append,
        )

        expected = [dependency.requirement for dependency in CORE_DEPENDENCIES]
        self.assertEqual(installed_requirements, expected)
        self.assertEqual(failures, expected)
        self.assertTrue(any("安装后仍无法加载" in message for message in messages))

    def test_dependency_auto_install_returns_success_only_after_verified_import(self):
        module_by_requirement = {
            dependency.requirement: dependency.module
            for dependency in CORE_DEPENDENCIES
        }
        installed_modules = set()

        def fake_import(name):
            if name not in installed_modules:
                raise ImportError(name)
            return object()

        def fake_install(command, **_kwargs):
            installed_modules.add(module_by_requirement[command[4]])

        failures = ensure_core_dependencies(
            include_optional=False,
            python_executable="custom-python",
            import_module=fake_import,
            install=fake_install,
            output=lambda _message: None,
        )

        self.assertEqual(failures, [])
        self.assertEqual(
            installed_modules,
            {dependency.module for dependency in CORE_DEPENDENCIES},
        )

    def test_dependency_auto_install_falls_back_through_ranked_mirrors(self):
        attempts = []
        messages = []

        def fake_import(name):
            raise ImportError(name)

        def fake_install(command, **_kwargs):
            attempts.append(list(command))
            if "https://slow.example/simple" in command:
                raise OSError("slow mirror rejected")

        ensure_core_dependencies(
            include_optional=False,
            python_executable="custom-python",
            mirror_ranker=lambda: [
                "https://slow.example/simple",
                "https://fast.example/simple",
            ],
            import_module=fake_import,
            install=fake_install,
            output=messages.append,
        )

        self.assertEqual(attempts[0][6], "https://slow.example/simple")
        self.assertEqual(attempts[1][6], "https://fast.example/simple")
        self.assertEqual(attempts[1][4], CORE_DEPENDENCIES[0].requirement)
        self.assertEqual(
            len(attempts),
            len(CORE_DEPENDENCIES) * 2,
            "每个依赖都要在两个镜像之间降级重试",
        )
        self.assertTrue(
            any("换下一个镜像重试" in message for message in messages)
        )

    def test_dependency_auto_install_survives_a_broken_mirror_ranker(self):
        messages = []
        installed = []
        state = {"installed": False}

        def fake_import(name):
            if name == "yaml" and not state["installed"]:
                raise ImportError(name)
            return object()

        def fake_install(command, **_kwargs):
            installed.append(list(command))
            state["installed"] = True
            return None

        def broken_ranker():
            raise RuntimeError("probe down")

        failures = ensure_core_dependencies(
            include_optional=False,
            python_executable="custom-python",
            mirror_ranker=broken_ranker,
            import_module=fake_import,
            install=fake_install,
            output=messages.append,
        )

        self.assertEqual(failures, [])
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0][6], "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple")
        self.assertTrue(
            any("镜像源测速失败，使用默认源" in message for message in messages)
        )

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

    def test_dependency_installer_falls_back_to_the_next_index(self):
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(list(command))
            if "https://slow.example/simple" in command:
                return type("Result", (), {"returncode": 1})()
            return type("Result", (), {"returncode": 0})()

        failures = install_dependencies(
            ["example>=1,<2"],
            python_executable="custom-python",
            index_url="https://slow.example/simple",
            fallback_index_urls=["https://fast.example/simple"],
            run=fake_run,
            output=lambda _message: None,
        )

        self.assertEqual(failures, [])
        self.assertEqual(len(calls), 2)
        self.assertIn("https://slow.example/simple", calls[0])
        self.assertIn("https://fast.example/simple", calls[1])

    def test_playwright_installer_falls_back_to_the_official_cdn(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            code = 1 if "PLAYWRIGHT_DOWNLOAD_HOST" in kwargs["env"] else 0
            return type("Result", (), {"returncode": code})()

        with patch.dict(os.environ, {}, clear=True):
            installed = install_playwright_browser(
                python_executable="custom-python",
                download_host="https://browser.example",
                fallback_download_hosts=[""],
                run=fake_run,
                output=lambda _message: None,
            )

        self.assertTrue(installed)
        self.assertEqual(len(calls), 2)
        self.assertIn("PLAYWRIGHT_DOWNLOAD_HOST", calls[0][1]["env"])
        self.assertNotIn("PLAYWRIGHT_DOWNLOAD_HOST", calls[1][1]["env"])

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

    def test_core_downloads_verify_tls_and_offer_mirrors_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LAUNCHER_ALLOW_GITHUB_PROXIES", None)
                manager = CoreManager(temp_dir)
            self.assertEqual(manager.ssl_context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(manager.ssl_context.check_hostname)
            self.assertTrue(manager.allow_github_proxies)
            self.assertTrue(manager.probe_github_sources)
            self.assertEqual(
                manager._build_github_candidate_urls(
                    "https://github.com/example/project/releases/latest"
                ),
                [
                    (
                        "GitHub 直连",
                        "https://github.com/example/project/releases/latest",
                    ),
                    *[
                        (name, prefix + "https://github.com/example/project/releases/latest")
                        for name, prefix in manager.GITHUB_MIRRORS
                        if prefix
                    ],
                ],
            )
            self.assertEqual(
                len(manager._build_github_candidate_urls("https://github.com/a/b")),
                len(manager.GITHUB_MIRRORS),
            )

    def test_mirror_downloads_can_be_disabled_by_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ, {"LAUNCHER_ALLOW_GITHUB_PROXIES": "0"}, clear=False
            ):
                manager = CoreManager(temp_dir)
            self.assertFalse(manager.allow_github_proxies)
            self.assertEqual(
                manager._build_github_candidate_urls("https://github.com/a/b"),
                [("GitHub 直连", "https://github.com/a/b")],
            )
            self.assertFalse(
                manager._is_github_asset_url(
                    "https://api.github.com/repos/a/b/releases/latest"
                )
            )
            self.assertTrue(
                manager._is_github_asset_url(
                    "https://github.com/a/b/releases/download/v1/core.zip"
                )
            )

    def test_download_sources_are_ranked_by_measured_speed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)

            def fake_probe(name, url, expected_size=None):
                speeds = {"GitHub 直连": 0.2, "ghfast.top": 3.0}
                speed = speeds.get(name, 0.0)
                return {
                    "name": name,
                    "url": url,
                    "reachable": speed > 0,
                    "speed": speed * 1024 * 1024,
                    "latency": 0.1,
                    "error": None,
                }

            manager._probe_download_source = fake_probe
            ranked = manager._ranked_download_sources(
                "https://github.com/a/b/releases/download/v1/core.zip",
                expected_size=123,
            )

            self.assertEqual(
                [name for name, _url in ranked[:2]],
                ["ghfast.top", "GitHub 直连"],
            )
            self.assertIn("gh-proxy.com", [name for name, _url in ranked])
            self.assertTrue(any("已选择 ghfast.top 加速 下载" in item for item in logs))
            self.assertTrue(any("gh-proxy.com 不可用" in item for item in logs))

            # 探测结果短时缓存，重复下载不再重新测速
            calls = []
            manager._probe_download_source = lambda *args, **kwargs: calls.append(args)
            manager._ranked_download_sources(
                "https://github.com/a/b/releases/download/v1/core.zip",
                expected_size=123,
            )
            self.assertEqual(calls, [])

    def test_slow_sources_do_not_block_the_probe_deadline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            manager.SOURCE_PROBE_DEADLINE = 0.3

            def fake_probe(name, url, expected_size=None):
                if name == "ghproxy.net":
                    time.sleep(0.8)
                speed = 2.0 if name == "ghfast.top" else 0.5
                return {
                    "name": name,
                    "url": url,
                    "reachable": True,
                    "speed": speed * 1024 * 1024,
                    "latency": 0.1,
                    "error": None,
                }

            manager._probe_download_source = fake_probe
            started = time.monotonic()
            ranked = manager._ranked_download_sources(
                "https://github.com/a/b/releases/download/v1/core.zip",
                expected_size=123,
            )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.75, "慢源不能拖住整轮探测")
            self.assertEqual(ranked[0][0], "ghfast.top")
            self.assertIn("ghproxy.net", [name for name, _url in ranked])
            self.assertTrue(any("ghproxy.net 不可用" in item for item in logs))
            self.assertTrue(any("已选择 ghfast.top 加速 下载" in item for item in logs))

    def test_download_file_uses_fastest_source_and_falls_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            payload = b"x" * 2048
            target = Path(temp_dir) / "core.zip"
            manager._ranked_download_sources = lambda url, expected_size=None: [
                ("ghfast.top", "https://ghfast.top/" + url),
                ("GitHub 直连", url),
            ]

            class FakeResponse:
                def __init__(self):
                    self.headers = {"Content-Length": str(len(payload))}
                    self.reads = 0

                def read(self, size):
                    offset = self.reads * size
                    self.reads += 1
                    return payload[offset : offset + size]

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            requested = []

            def fake_urlopen(request, **_kwargs):
                requested.append(request.full_url)
                if "ghfast.top" in request.full_url:
                    raise urllib.error.URLError("mirror down")
                return FakeResponse()

            with patch("src.core_manager.urllib.request.urlopen", side_effect=fake_urlopen):
                self.assertTrue(
                    manager.download_file(
                        "https://github.com/a/b/releases/download/v1/core.zip",
                        str(target),
                        expected_size=len(payload),
                    )
                )

            self.assertEqual(
                requested,
                [
                    "https://ghfast.top/https://github.com/a/b/releases/download/v1/core.zip",
                    "https://github.com/a/b/releases/download/v1/core.zip",
                ],
            )
            self.assertEqual(target.read_bytes(), payload)
            self.assertTrue(any("ghfast.top 加速 失败" in item for item in logs))
            self.assertTrue(any("下载成功" in item for item in logs))

    def test_download_file_switches_away_from_a_throttled_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            manager.DOWNLOAD_SLOW_GRACE_SECONDS = 0.05
            manager.DOWNLOAD_SLOW_WINDOW_SECONDS = 0.05
            manager.DOWNLOAD_SLOW_MIN_SPEED = 1024 * 1024
            payload = b"y" * 4096
            target = Path(temp_dir) / "core.zip"
            manager._ranked_download_sources = lambda url, expected_size=None: [
                ("ghfast.top", "https://ghfast.top/" + url),
                ("GitHub 直连", url),
            ]

            class SlowResponse:
                """先返回一点数据，随后每个分块都拖慢窗口速度。"""

                def __init__(self):
                    self.headers = {"Content-Length": str(len(payload))}
                    self.sent = 0

                def read(self, size):
                    time.sleep(0.04)
                    chunk = payload[self.sent : self.sent + 64]
                    self.sent += len(chunk)
                    return chunk

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            class FastResponse:
                def __init__(self):
                    self.headers = {"Content-Length": str(len(payload))}
                    self.sent = 0

                def read(self, size):
                    chunk = payload[self.sent : self.sent + size]
                    self.sent += len(chunk)
                    return chunk

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            requested = []

            def fake_urlopen(request, **_kwargs):
                requested.append(request.full_url)
                if "ghfast.top" in request.full_url:
                    return SlowResponse()
                return FastResponse()

            with patch("src.core_manager.urllib.request.urlopen", side_effect=fake_urlopen):
                self.assertTrue(
                    manager.download_file(
                        "https://github.com/a/b/releases/download/v1/core.zip",
                        str(target),
                        expected_size=len(payload),
                    )
                )

            self.assertEqual(len(requested), 2)
            self.assertIn("ghfast.top", requested[0])
            self.assertEqual(target.read_bytes(), payload)
            self.assertTrue(any("速度过慢" in item for item in logs))
            self.assertTrue(any("下载成功" in item for item in logs))

    def test_non_object_core_versions_fall_back_for_downstream_consumers(self):
        defaults = {"yatori": None, "autovisor": None, "zhs": None, "last_check": None}
        for payload in ("[]", "null"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / ".core_versions.json").write_text(
                    payload, encoding="utf-8"
                )
                logs = []

                manager = CoreManager(temp_dir, logs.append)

                self.assertEqual(manager.local_versions, defaults)
                self.assertTrue(
                    any("顶层格式无效" in message for message in logs)
                )
                info = manager.get_autovisor_local_version_info()
                self.assertEqual(info["source"], "inferred")
                self.assertEqual(
                    info["compare"], manager.AUTOVISOR_FALLBACK_LOCAL_VERSION
                )
                self.assertTrue(manager._save_local_versions())
                self.assertEqual(
                    json.loads((root / ".core_versions.json").read_text(encoding="utf-8")),
                    defaults,
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

    def test_yatori_release_list_prefers_highest_version_over_publish_time(self):
        digest = "sha256:" + "a" * 64
        releases = [
            {
                "tag_name": "v2.6.2-beta.11",
                "published_at": "2026-07-20T10:00:00Z",
                "prerelease": True,
                "assets": [
                    {
                        "name": "yatori-windows-amd64.zip",
                        "browser_download_url": "https://example.test/beta11.zip",
                        "digest": digest,
                        "size": 1024,
                    }
                ],
            },
            {
                "tag_name": "v2.6.2-beta.8",
                "published_at": "2026-07-30T10:00:00Z",
                "prerelease": True,
                "assets": [
                    {
                        "name": "yatori-windows-amd64.zip",
                        "browser_download_url": "https://example.test/beta8.zip",
                        "digest": digest,
                        "size": 1024,
                    }
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            selected = manager._select_yatori_release(releases)

        self.assertEqual(selected["version"], "v2.6.2-beta.11")
        self.assertEqual(selected["download_url"], "https://example.test/beta11.zip")

    def test_yatori_release_rejects_darwin_and_non_windows_zip_fallback(self):
        digest = "sha256:" + "a" * 64
        release = {
            "tag_name": "v2.6.3",
            "assets": [
                None,
                {
                    "name": "yatori-darwin-amd64.zip",
                    "browser_download_url": "https://example.test/darwin.zip",
                    "digest": digest,
                    "size": 1024,
                },
                {
                    "name": "yatori-linux-amd64.zip",
                    "browser_download_url": "https://example.test/linux.zip",
                    "digest": digest,
                    "size": 1024,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            self.assertTrue(
                manager._is_yatori_windows_amd64_asset(
                    "yatori-go-console.v2.6.2-beta.11-windows-amd64-release.zip"
                )
            )
            self.assertFalse(
                manager._is_yatori_windows_amd64_asset(
                    "yatori-go-console.v2.6.2-beta.11-darwin-amd64-release.zip"
                )
            )
            selected = manager._parse_yatori_release(release)

        self.assertIsNone(selected)
        self.assertTrue(any("Windows amd64/x64" in message for message in logs))

    def test_yatori_update_check_prefers_releases_list_over_latest(self):
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
                if "per_page" in request.full_url:
                    return Response(releases)
                return Response({"tag_name": "v2.0.0", "assets": []})

            with patch(
                "src.core_manager.urllib.request.urlopen",
                side_effect=open_url,
            ):
                result = manager.get_yatori_latest_release()

        # releases 列表与 latest 现在并行请求，列表结果优先。
        self.assertEqual(
            set(requested),
            {manager.YATORI_RELEASES_API_URL, manager.YATORI_API_URL},
        )
        self.assertEqual(result["version"], "v2.6.1-beta.11")

    def test_yatori_update_check_falls_back_to_api_mirror_when_rate_limited(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        release = {
            "tag_name": "v2.6.2-beta.14",
            "published_at": "2026-10-26T16:10:44Z",
            "assets": [
                {
                    "name": "yatori-go-console.v2.6.2-beta.14-windows-amd64-release.zip",
                    "browser_download_url": "https://github.com/a/b/releases/download/v1/core.zip",
                    "digest": "sha256:" + "b" * 64,
                    "size": 2048,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            requested = []

            def open_url(request, **_kwargs):
                url = request.full_url
                requested.append(url)
                if url.startswith("https://api.github.com"):
                    raise urllib.error.HTTPError(
                        url, 403, "rate limit exceeded", {"x-ratelimit-remaining": "0"}, None
                    )
                return Response(release)

            with patch(
                "src.core_manager.urllib.request.urlopen",
                side_effect=open_url,
            ):
                result = manager.get_yatori_latest_release()

        self.assertEqual(result["version"], "v2.6.2-beta.14")
        self.assertTrue(
            any(url.startswith("https://gh-proxy.com/https://api.github.com") for url in requested)
        )
        self.assertTrue(any("403" in message for message in logs))

    def test_yatori_update_check_skips_direct_api_after_rate_limit(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        release = {
            "tag_name": "v2.6.2-beta.14",
            "assets": [
                {
                    "name": "yatori-windows-amd64.zip",
                    "browser_download_url": "https://example.test/core.zip",
                    "digest": "sha256:" + "c" * 64,
                    "size": 10,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            direct_calls = []

            def open_url(request, **_kwargs):
                url = request.full_url
                if url.startswith("https://api.github.com"):
                    direct_calls.append(url)
                    raise urllib.error.HTTPError(
                        url, 403, "rate limit exceeded", {"x-ratelimit-remaining": "0"}, None
                    )
                return Response(release)

            with patch("src.core_manager.urllib.request.urlopen", side_effect=open_url):
                manager.get_yatori_latest_release()
            first_round = len(direct_calls)
            direct_calls.clear()
            with patch("src.core_manager.urllib.request.urlopen", side_effect=open_url):
                manager.get_yatori_latest_release()

        # 第一轮可能命中一次或两次直连 403（两个请求并行），
        # 第二轮必须完全跳过直连，避免每次都白白挨一次限流。
        self.assertGreaterEqual(first_round, 1)
        self.assertLessEqual(first_round, 2)
        self.assertEqual(direct_calls, [])

    def test_yatori_update_uses_fallback_local_version_when_record_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            manager.local_versions["yatori"] = None
            manager.check_yatori_installed = lambda: True
            manager.get_yatori_latest_release = lambda: {
                "version": "v2.6.2-beta.11",
                "download_url": "https://example.test/yatori.zip",
                "body": "beta.11 release notes",
            }

            result = manager.check_yatori_update()

        self.assertTrue(result["installed"])
        self.assertTrue(result["has_update"])
        self.assertEqual(result["version"], manager.YATORI_FALLBACK_LOCAL_VERSION)
        self.assertEqual(result["info"]["version"], "v2.6.2-beta.11")
        self.assertTrue(any("内置版本" in item for item in logs))

    def test_yatori_version_comparison_avoids_downgrades_and_equivalent_tags(self):
        cases = [
            ("v2.6.2-beta.12", "v2.6.2-beta.11", False, True),
            ("2.6.2-beta.11", "v2.6.2-beta.11", False, False),
            ("v2.6.2-beta11", "v2.6.2-beta.11", False, False),
            ("v2.6.2", "v2.6.2-beta.11", False, True),
            ("v2.6.2-beta.8", "v2.6.2-beta.11", True, False),
            ("v2.6.2", "v2.6.3-beta.1", True, False),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            manager.check_yatori_installed = lambda: True

            for local_version, remote_version, has_update, local_newer in cases:
                with self.subTest(
                    local_version=local_version,
                    remote_version=remote_version,
                ):
                    manager.local_versions["yatori"] = local_version
                    manager.get_yatori_latest_release = lambda version=remote_version: {
                        "version": version,
                        "download_url": "https://example.test/yatori.zip",
                    }

                    result = manager.check_yatori_update()

                    self.assertEqual(result["has_update"], has_update)
                    self.assertEqual(
                        bool(result.get("local_newer")),
                        local_newer,
                    )

        self.assertTrue(any("跳过降级" in item for item in logs))

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

            def fake_download(_url, target_path, _progress=None, **_kwargs):
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

    def test_yatori_install_stages_nested_release_and_preserves_live_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            yatori = Path(manager.yatori_path)
            (yatori / "config.yaml").write_text("account: current\n", encoding="utf-8")
            (yatori / "old-core.txt").write_text("old", encoding="utf-8")
            Path(temp_dir, "config.yaml.bak").write_text(
                "account: stale\n",
                encoding="utf-8",
            )

            source_archive = Path(temp_dir, "publisher.zip")
            with zipfile.ZipFile(source_archive, "w") as archive:
                archive.writestr(
                    "bundle/command/yatori-go-console.exe",
                    b"new executable",
                )
                archive.writestr("bundle/command/assets/runtime.txt", b"asset")
            payload = source_archive.read_bytes()
            release = {
                "version": "v2.6.2-beta.11",
                "download_url": "https://example.test/yatori.zip",
                "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "asset_size": len(payload),
            }

            def fake_download(_url, target_path, _progress=None, **_kwargs):
                Path(target_path).write_bytes(payload)
                return True

            manager.download_file = fake_download

            self.assertTrue(manager.install_yatori(release))
            self.assertEqual(
                (yatori / "config.yaml").read_text(encoding="utf-8"),
                "account: current\n",
            )
            self.assertEqual(
                (yatori / "yatori-go-console.exe").read_bytes(),
                b"new executable",
            )
            self.assertTrue((yatori / "assets" / "runtime.txt").is_file())
            self.assertFalse((yatori / "old-core.txt").exists())
            self.assertEqual(manager.local_versions["yatori"], "v2.6.2-beta.11")
            self.assertEqual(
                list(Path(temp_dir).glob(".yatori-update-*")),
                [],
            )

    def test_yatori_install_does_not_restore_stale_global_config_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            Path(temp_dir, "config.yaml.bak").write_text(
                "account: stale\n",
                encoding="utf-8",
            )
            source_archive = Path(temp_dir, "publisher.zip")
            with zipfile.ZipFile(source_archive, "w") as archive:
                archive.writestr("yatori-go-console.exe", b"new executable")
            payload = source_archive.read_bytes()
            manager.download_file = lambda _url, target, _progress=None, **_kwargs: (
                Path(target).write_bytes(payload) >= 0
            )

            installed = manager.install_yatori(
                {
                    "version": "v2.6.2-beta.11",
                    "download_url": "https://example.test/yatori.zip",
                    "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                    "asset_size": len(payload),
                }
            )

            self.assertTrue(installed)
            self.assertFalse(Path(manager.yatori_path, "config.yaml").exists())

    def test_invalid_yatori_release_never_removes_existing_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CoreManager(temp_dir)
            marker = Path(manager.yatori_path, "existing-core.txt")
            marker.write_text("keep", encoding="utf-8")
            source_archive = Path(temp_dir, "publisher.zip")
            with zipfile.ZipFile(source_archive, "w") as archive:
                archive.writestr("README.txt", "missing executable")
            payload = source_archive.read_bytes()
            manager.download_file = lambda _url, target, _progress=None, **_kwargs: (
                Path(target).write_bytes(payload) >= 0
            )

            installed = manager.install_yatori(
                {
                    "version": "v2.6.2-beta.11",
                    "download_url": "https://example.test/yatori.zip",
                    "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                    "asset_size": len(payload),
                }
            )

            self.assertFalse(installed)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertIsNone(manager.local_versions["yatori"])

    def test_yatori_activation_failure_rolls_back_previous_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            yatori = Path(manager.yatori_path)
            marker = yatori / "existing-core.txt"
            marker.write_text("keep", encoding="utf-8")
            (yatori / "config.yaml").write_text("account: old\n", encoding="utf-8")
            source_archive = Path(temp_dir, "publisher.zip")
            with zipfile.ZipFile(source_archive, "w") as archive:
                archive.writestr("yatori-go-console.exe", b"new executable")
            payload = source_archive.read_bytes()
            manager.download_file = lambda _url, target, _progress=None, **_kwargs: (
                Path(target).write_bytes(payload) >= 0
            )
            release = {
                "version": "v2.6.2-beta.11",
                "download_url": "https://example.test/yatori.zip",
                "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "asset_size": len(payload),
            }
            real_replace = os.replace

            def fail_new_activation(source, destination):
                if (
                    Path(source).name == "stage"
                    and os.path.normcase(os.path.abspath(destination))
                    == os.path.normcase(os.path.abspath(manager.yatori_path))
                ):
                    raise OSError("simulated activation failure")
                return real_replace(source, destination)

            with patch("src.core_manager.os.replace", side_effect=fail_new_activation):
                installed = manager.install_yatori(release)

            self.assertFalse(installed)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(
                (yatori / "config.yaml").read_text(encoding="utf-8"),
                "account: old\n",
            )
            self.assertFalse((yatori / "yatori-go-console.exe").exists())
            self.assertTrue(any("已回滚到更新前的 Yatori" in item for item in logs))
            self.assertEqual(list(Path(temp_dir).glob(".yatori-update-*")), [])

    def test_yatori_version_record_failure_rolls_back_previous_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = []
            manager = CoreManager(temp_dir, logs.append)
            yatori = Path(manager.yatori_path)
            marker = yatori / "existing-core.txt"
            marker.write_text("keep", encoding="utf-8")
            manager.local_versions["yatori"] = "v2.6.2-beta.8"
            source_archive = Path(temp_dir, "publisher.zip")
            with zipfile.ZipFile(source_archive, "w") as archive:
                archive.writestr("yatori-go-console.exe", b"new executable")
            payload = source_archive.read_bytes()
            manager.download_file = lambda _url, target, _progress=None, **_kwargs: (
                Path(target).write_bytes(payload) >= 0
            )
            manager._save_local_versions = lambda: False

            installed = manager.install_yatori(
                {
                    "version": "v2.6.2-beta.11",
                    "download_url": "https://example.test/yatori.zip",
                    "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                    "asset_size": len(payload),
                }
            )

            self.assertFalse(installed)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse((yatori / "yatori-go-console.exe").exists())
            self.assertEqual(manager.local_versions["yatori"], "v2.6.2-beta.8")
            self.assertTrue(any("无法保存 Yatori 版本记录" in item for item in logs))
            self.assertTrue(any("已回滚到更新前的 Yatori" in item for item in logs))

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
