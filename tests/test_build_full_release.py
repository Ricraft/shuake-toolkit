# encoding=utf-8
"""Contract tests for the full runtime package builder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_full_release.py"


def _load_module():
    if str(SCRIPT_PATH.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT_PATH.parent))
    spec = importlib.util.spec_from_file_location(
        "build_full_release",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module()


class PayloadTests(unittest.TestCase):
    def test_payload_contains_bootstrap_and_runtime_files(self):
        entries = {arcname for _, arcname in builder.iter_payload(PROJECT_ROOT)}
        for required in builder.REQUIRED_FILES:
            with self.subTest(required=required):
                self.assertIn(required, entries)

        self.assertTrue(any(name.startswith("scripts/") for name in entries))
        self.assertTrue(any(name.startswith("Autovisor/") for name in entries))
        self.assertTrue(any(name.startswith("Yatori/") for name in entries))

    def test_nested_secrets_qr_and_attachments_cannot_enter_full_release(self):
        with tempfile.TemporaryDirectory(prefix="release-secrets-") as folder:
            root = Path(folder)
            for relative in (
                "Autovisor/res/QRcode.jpg", "Autovisor/res/cookies_123.json",
                "Autovisor/extra/configs.ini", "Yatori/extra/config.yaml",
                "Autovisor/res/attachments/private.txt", "src/.env.local",
                "web/uploads/user.txt", "web/launcher_preferences.json",
                "Yatori/assets/cmap.json", "Autovisor/res/stealth.min.js",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            entries = {name for _, name in builder.iter_payload(root)}
            self.assertEqual(entries, {
                "Yatori/assets/cmap.json", "Autovisor/res/stealth.min.js"
            })

    def test_payload_excludes_user_data_and_runtime_binaries(self):
        entries = {arcname for _, arcname in builder.iter_payload(PROJECT_ROOT)}
        forbidden = (
            "Autovisor/configs.ini",
            "Autovisor/res/cookies.json",
            "Autovisor/res/QRcode.jpg",
            "Yatori/config.yaml",
            "data/",
            "logs/",
            "output/",
            "runtime_deps/",
        )
        for entry in entries:
            for prefix in forbidden:
                with self.subTest(entry=entry, prefix=prefix):
                    self.assertFalse(entry.startswith(prefix), entry)

        self.assertFalse([entry for entry in entries if entry.endswith(".exe")])
        self.assertFalse([entry for entry in entries if entry.endswith(".zip")])
        self.assertFalse([entry for entry in entries if "__pycache__" in entry])
        self.assertFalse([entry for entry in entries if entry.endswith((".pyc", ".pyo"))])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="full-release-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_manifest_matches_archive(self):
        version = builder.declared_launcher_version()
        result = builder.build(version, self.tmpdir)

        archive = Path(result["archive"])
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertTrue(archive.is_file())

        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(manifest["sha256"], digest)
        self.assertEqual(manifest["size"], archive.stat().st_size)
        self.assertEqual(manifest["version"], version)
        self.assertEqual(manifest["asset"], archive.name)
        self.assertEqual(manifest["fileList"], sorted(manifest["fileList"]))
        self.assertEqual(manifest["files"], len(manifest["fileList"]))

        with zipfile.ZipFile(archive) as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(sorted(zf.namelist()), manifest["fileList"])

    def test_yatori_core_metadata_is_written(self):
        result = builder.build(
            builder.declared_launcher_version(),
            self.tmpdir,
            yatori_core={
                "url": "https://example.test/yatori-core.zip",
                "sha256": "a" * 64,
                "size": 123456,
                "version": "v2.6.2-beta.11",
            },
        )
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["yatoriCore"]["url"], "https://example.test/yatori-core.zip")
        self.assertEqual(manifest["yatoriCore"]["sha256"], "a" * 64)
        self.assertEqual(manifest["yatoriCore"]["size"], 123456)

    def test_version_match_guard_rejects_mismatched_tag(self):
        with self.assertRaises(RuntimeError):
            builder.build("v99.99.99", self.tmpdir, require_version_match=True)

    def test_missing_required_files_is_rejected(self):
        empty_root = self.tmpdir / "empty"
        empty_root.mkdir()
        with self.assertRaises(RuntimeError):
            builder.build("v1.5.0", self.tmpdir / "out", root=empty_root)


if __name__ == "__main__":
    unittest.main()
