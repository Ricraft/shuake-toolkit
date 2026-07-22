import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.course_catalog import (
    CourseCatalogCache,
    CourseCatalogError,
    CourseCatalogService,
    autovisor_account_section,
    normalize_account_index,
    parse_xuexitong_course_data,
    parse_zhs_course_data,
)
from scripts import fetch_zhs_courses


class CourseCatalogTests(unittest.TestCase):
    def test_module_import_keeps_requests_and_crypto_lazy(self):
        project_root = Path(__file__).resolve().parents[1]
        code = """
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'requests', 'Crypto'}:
        raise ImportError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import src.course_catalog
print('COURSE_CATALOG_IMPORT_OK')
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("COURSE_CATALOG_IMPORT_OK", completed.stdout)

    def test_account_index_validation_and_unbounded_section_mapping(self):
        self.assertEqual(normalize_account_index("6"), 6)
        self.assertEqual(autovisor_account_section(0), "user-account")
        self.assertEqual(autovisor_account_section(6), "user-account-7")
        with self.assertRaises(CourseCatalogError):
            normalize_account_index(-1)
        with self.assertRaises(CourseCatalogError):
            normalize_account_index(True)

    def test_cache_is_bound_to_identity_and_uses_atomic_valid_json(self):
        now = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "course_cache.json"
            cache = CourseCatalogCache(path, now=lambda: now)
            result = {"ok": True, "courses": [{"name": "课程A"}]}
            cache.put("zhs", 0, "alice", result)

            self.assertEqual(cache.get("zhs", 0, "alice"), result)
            self.assertIsNone(cache.get("zhs", 0, "bob"))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("alice", raw)
            self.assertEqual(json.loads(raw)["zhs_account_0"]["data"], result)

    def test_cache_rejects_expired_legacy_and_corrupt_entries(self):
        current = [datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "course_cache.json"
            cache = CourseCatalogCache(path, now=lambda: current[0])
            cache.put("xxt", 1, "alice", {"ok": True})
            current[0] += timedelta(minutes=31)
            self.assertIsNone(cache.get("xxt", 1, "alice"))

            path.write_text(
                json.dumps(
                    {
                        "xxt_account_1": {
                            "cached_at": current[0].isoformat(),
                            "data": {"ok": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(cache.get("xxt", 1, "alice"))
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(cache.get("xxt", 1, "alice"))

    def test_zhs_parser_selects_exact_identity_and_encodes_urls(self):
        source = {
            "alice": {
                "courses": [
                    {
                        "secret": "a&b",
                        "courseType": 1,
                        "courseName": "普通课程",
                    },
                    {
                        "secret": "shared-id",
                        "courseType": 7,
                        "courseName": "共享课程",
                    },
                ],
                "notices": [
                    {
                        "liveCourseId": "live&1",
                        "courseId": "course 2",
                        "recruitId": "recruit/3",
                        "courseName": "课程",
                        "taskName": "见面课一",
                    }
                ],
            },
            "bob": {"courses": []},
        }
        courses, identity = parse_zhs_course_data(source, "alice")

        self.assertEqual(identity, "alice")
        self.assertEqual([course["type"] for course in courses], ["普通课", "共享课", "见面课"])
        self.assertIn("a%26b", courses[0]["url"])
        self.assertIn("liveId=live%261", courses[2]["url"])
        self.assertIn("courseId=course+2", courses[2]["url"])
        self.assertEqual(parse_zhs_course_data(source, "missing"), ([], "missing"))
        with self.assertRaises(CourseCatalogError):
            parse_zhs_course_data(source)

    def test_xuexitong_parser_deduplicates_and_tolerates_bad_channels(self):
        payload = {
            "channelList": [
                None,
                {
                    "content": {
                        "name": "外层课程",
                        "isstart": True,
                        "course": {
                            "data": [
                                {"id": 1, "name": "课程A", "schools": "学校"},
                                {"id": 2, "name": "课程A"},
                            ]
                        },
                    }
                },
                {"key": 3, "content": {"name": "课程B"}},
            ]
        }
        courses = parse_xuexitong_course_data(payload)

        self.assertEqual([course["name"] for course in courses], ["课程A", "课程B"])
        self.assertEqual(courses[0]["courseId"], "1")
        self.assertTrue(courses[0]["isstart"])
        self.assertEqual(courses[1]["courseId"], "3")

    def test_zhs_fetch_script_uses_dynamic_account_section_and_atomic_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "Autovisor"
            config_dir.mkdir()
            (config_dir / "configs.ini").write_text(
                "[user-account-6]\nusername = sixth\npassword = p%word\n",
                encoding="utf-8",
            )
            original_root = fetch_zhs_courses.SCRIPT_DIR
            fetch_zhs_courses.SCRIPT_DIR = str(root)
            try:
                self.assertEqual(
                    fetch_zhs_courses.load_config(6),
                    ("sixth", "p%word"),
                )
                self.assertEqual(
                    fetch_zhs_courses.load_config(0),
                    (None, None),
                )
            finally:
                fetch_zhs_courses.SCRIPT_DIR = original_root

            output = root / "data" / "zhs_course.json"
            fetch_zhs_courses.save_course_data(
                output,
                "sixth",
                [{"courseName": "课程"}],
                [],
            )
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["sixth"]["courses"][0]["courseName"], "课程")

    def test_zhs_fetch_script_reuses_shared_portal_navigation_contract(self):
        source = Path(fetch_zhs_courses.__file__).read_text(encoding="utf-8")

        self.assertIn("navigate_to_my_course(page, _ConsoleLogger())", source)
        self.assertIn("wait_for_login_completion", source)
        self.assertNotIn("#sharingClassed > div:nth-child", source)
        self.assertNotIn("page.click('text=\"我的学堂\"')", source)


class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, course_name):
        self.course_name = course_name
        self.trust_env = True
        self.cookies = {"session": "ok"}
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _Response(200, {"status": True})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return _Response(
            200,
            {
                "channelList": [
                    {
                        "content": {
                            "course": {
                                "data": [
                                    {"id": 1, "name": self.course_name}
                                ]
                            }
                        }
                    }
                ]
            },
        )


class CourseCatalogServiceTests(unittest.TestCase):
    def test_xuexitong_cache_and_session_are_isolated_by_username(self):
        sessions = []

        def session_factory():
            session = _Session(f"课程{len(sessions) + 1}")
            sessions.append(session)
            return session

        with tempfile.TemporaryDirectory() as temp_dir:
            service = CourseCatalogService(
                temp_dir,
                session_factory=session_factory,
            )
            first = service.get_xuexitong_courses(0, "alice", "secret-a")
            cached = service.get_xuexitong_courses(0, "alice", "changed-password")
            second_user = service.get_xuexitong_courses(0, "bob", "secret-b")

            self.assertTrue(first["ok"])
            self.assertEqual(cached, first)
            self.assertEqual(first["courses"][0]["name"], "课程1")
            self.assertEqual(second_user["courses"][0]["name"], "课程2")
            self.assertEqual(len(sessions), 2)
            self.assertFalse(sessions[0].trust_env)
            self.assertTrue(all(
                url.startswith("https://")
                for session in sessions
                for url, _ in session.gets
            ))
            self.assertFalse(sessions[0].posts[0][1]["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
