# encoding=utf-8
"""发布打包器（scripts/build_launcher_release.py）的契约测试。

覆盖：版本号规范化、版本一致性守卫、打包白名单/排除规则、清单与压缩包的
大小与 SHA-256 一致、以及中文入口文件名在 zip 中的往返正确性（自更新脚本
要按名字定位 ``统一启动器.py``，名字错了就会更新失败）。

注意：本环境里 pytest 的 ``tmp_path`` fixture 不可用（%TEMP%\\pytest-of-OMEN
权限异常，是既有环境问题），因此这里用 tempfile 自管目录。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_launcher_release.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_launcher_release",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_module()


class VersionNormalizationTests(unittest.TestCase):
    def test_accepts_plain_and_v_prefixed_versions(self):
        self.assertEqual(builder.normalize_version("v1.3.0"), "v1.3.0")
        self.assertEqual(builder.normalize_version("1.3.0"), "v1.3.0")
        self.assertEqual(builder.normalize_version(" v2.6.2-beta.11 "), "v2.6.2-beta.11")

    def test_rejects_unparseable_versions(self):
        for raw in ("", "   ", "latest", "1.3", "v1.3.0.0", "main"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    builder.normalize_version(raw)

    def test_declared_launcher_version_is_readable(self):
        declared = builder.declared_launcher_version()
        self.assertTrue(declared.startswith("v"), declared)


class PayloadTests(unittest.TestCase):
    def test_payload_is_limited_to_the_replace_whitelist(self):
        entries = [arcname for _, arcname in builder.iter_payload(PROJECT_ROOT)]
        self.assertIn("统一启动器.py", entries)
        self.assertIn("requirements.txt", entries)
        self.assertTrue(any(e.startswith("src/") for e in entries))
        self.assertTrue(any(e.startswith("web/") for e in entries))

        # 第三方核心与用户数据绝不能被纳入更新包。
        for entry in entries:
            for forbidden in ("Yatori/", "Autovisor/", "data/", "logs/", "dist/"):
                self.assertFalse(entry.startswith(forbidden), entry)

    def test_payload_skips_caches(self):
        entries = [arcname for _, arcname in builder.iter_payload(PROJECT_ROOT)]
        self.assertFalse([e for e in entries if "__pycache__" in e], "cache leaked")
        self.assertFalse([e for e in entries if e.endswith((".pyc", ".pyo"))])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="launcher-release-test-"))

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
        self.assertEqual(manifest["replaceWhitelist"], list(builder.WHITELIST))

        with zipfile.ZipFile(archive) as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(len(zf.namelist()), manifest["files"])

    def test_entry_point_name_round_trips_with_utf8_flag(self):
        """更新器按名字定位入口文件，中文名必须在 zip 里正确往返。"""
        result = builder.build(builder.declared_launcher_version(), self.tmpdir)

        with zipfile.ZipFile(result["archive"]) as zf:
            top_level = [n for n in zf.namelist() if "/" not in n]
            entry_point = [
                name
                for name in top_level
                if (PROJECT_ROOT / name).is_file()
                and (PROJECT_ROOT / name).suffix == ".py"
            ]
            self.assertTrue(entry_point, "entry point not found in archive")
            for name in entry_point:
                info = zf.getinfo(name)
                self.assertTrue(info.flag_bits & 0x800, f"{name}: UTF-8 flag missing")

    def test_version_match_guard_rejects_mismatched_tag(self):
        with self.assertRaises(RuntimeError) as ctx:
            builder.build("v99.99.99", self.tmpdir, require_version_match=True)
        self.assertIn("LAUNCHER_VERSION", str(ctx.exception))


    def test_archive_contains_every_required_entry_file(self):
        result = builder.build(builder.declared_launcher_version(), self.tmpdir)

        with zipfile.ZipFile(result["archive"]) as zf:
            names = set(zf.namelist())
        missing = [name for name in builder.REQUIRED_FILES if name not in names]
        self.assertEqual(missing, [], f"required files missing from archive: {missing}")
    def test_version_match_guard_accepts_declared_version(self):
        version = builder.declared_launcher_version()
        result = builder.build(version, self.tmpdir, require_version_match=True)
        self.assertEqual(result["version"], version)


if __name__ == "__main__":
    unittest.main()
