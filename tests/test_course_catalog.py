import asyncio
import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.course_catalog import (
    CourseCatalogCache,
    CourseCatalogError,
    CourseCatalogService,
    autovisor_account_section,
    get_zhs_course_access_id,
    is_xuexitong_course_payload,
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
                        "recruitAndCourseId": "shared-id",
                        "courseType": 7,
                        "courseName": "共享课程",
                    },
                    {
                        "courseType": 1,
                        "courseName": "缺少访问标识",
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

    def test_zhs_course_access_id_supports_current_and_legacy_fields(self):
        self.assertEqual(get_zhs_course_access_id({"secret": " old "}), "old")
        self.assertEqual(
            get_zhs_course_access_id({"recruitAndCourseId": " current "}),
            "current",
        )
        self.assertEqual(get_zhs_course_access_id({}), "")

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
                output_log = io.StringIO()
                with redirect_stdout(output_log):
                    loaded = fetch_zhs_courses.load_config(6)
                self.assertEqual(loaded, ("sixth", "p%word"))
                self.assertNotIn("sixth", output_log.getvalue())
                self.assertIn("s***h", output_log.getvalue())
                self.assertEqual(
                    fetch_zhs_courses.load_config(0),
                    (None, None),
                )
            finally:
                fetch_zhs_courses.SCRIPT_DIR = original_root

            output = root / "data" / "zhs_course.json"
            self.assertTrue(
                fetch_zhs_courses.save_course_data(
                    output,
                    "sixth",
                    [{"courseName": "课程"}],
                    [],
                )
            )
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["sixth"]["courses"][0]["courseName"], "课程")

    def test_zhs_fetch_response_contract_distinguishes_empty_success_from_failure(self):
        self.assertEqual(
            fetch_zhs_courses.extract_share_course_rows(
                {"code": "200", "result": {"courseOpenDtos": []}}
            ),
            [],
        )
        self.assertIsNone(
            fetch_zhs_courses.extract_share_course_rows(
                {"code": 500, "result": {"courseOpenDtos": []}}
            )
        )
        self.assertIsNone(
            fetch_zhs_courses.extract_share_course_rows(
                {
                    "code": 200,
                    "result": {
                        "courseOpenDtos": [{"courseName": "字段已变化"}]
                    },
                }
            )
        )
        self.assertIsNone(
            fetch_zhs_courses.extract_share_course_rows(
                {"code": 200, "result": {"courseOpenDtos": ["invalid-row"]}}
            )
        )

        normalized = fetch_zhs_courses.normalize_share_course(
            {"recruitAndCourseId": "current-id", "courseName": "课程"}
        )
        self.assertEqual(normalized["secret"], "current-id")

    def test_zhs_fetch_course_api_discovery_is_host_and_path_scoped(self):
        self.assertTrue(
            fetch_zhs_courses.is_course_api_candidate(
                "https://onlineservice-api.zhihuishu.com/gateway/queryShareCourseInfo"
            )
        )
        self.assertTrue(
            fetch_zhs_courses.is_course_api_candidate(
                "https://onlineservice-api.zhihuishu.com/gateway/newCourseList"
            )
        )
        self.assertFalse(
            fetch_zhs_courses.is_course_api_candidate(
                "https://onlineservice-api.zhihuishu.com.example.com/course/list"
            )
        )
        self.assertFalse(
            fetch_zhs_courses.is_course_api_candidate(
                "https://onlineservice-api.zhihuishu.com/gateway/user/profile"
            )
        )
        self.assertIsNone(
            fetch_zhs_courses.extract_share_course_rows(
                {"code": 200, "result": {"unexpected": []}}
            )
        )

    def test_zhs_fetch_merges_repeated_course_responses_without_duplicates(self):
        courses = {}
        fetch_zhs_courses.merge_share_courses(
            courses,
            [
                {
                    "secret": "same-course",
                    "courseName": "课程",
                    "progress": "10%",
                    "courseType": 0,
                    "courseStartTime": 0,
                }
            ],
        )
        fetch_zhs_courses.merge_share_courses(
            courses,
            [
                {
                    "secret": "same-course",
                    "courseName": "课程",
                    "progress": "20%",
                    "courseType": 0,
                    "courseStartTime": 0,
                }
            ],
        )

        self.assertEqual(len(courses), 1)
        course = courses["same-course"]
        self.assertEqual(course["progress"], "20%")
        self.assertEqual(course["courseType"], 0)
        self.assertEqual(course["courseStartTime"], 0)

    def test_zhs_fetch_waits_for_course_response_with_a_bounded_timeout(self):
        ready = asyncio.Event()
        ready.set()
        self.assertTrue(
            asyncio.run(fetch_zhs_courses.wait_for_course_response(ready, timeout=0.1))
        )
        self.assertFalse(
            asyncio.run(
                fetch_zhs_courses.wait_for_course_response(
                    asyncio.Event(),
                    timeout=0.001,
                )
            )
        )

    def test_zhs_fetch_waits_until_repeated_responses_settle(self):
        class SequencedEvent:
            def __init__(self):
                self.wait_count = 0
                self.clear_count = 0

            async def wait(self):
                self.wait_count += 1
                if self.wait_count <= 2:
                    return
                await asyncio.sleep(1)

            def clear(self):
                self.clear_count += 1

        event = SequencedEvent()
        result = asyncio.run(
            fetch_zhs_courses.wait_for_course_response(
                event,
                timeout=0.2,
                settle_timeout=0.01,
            )
        )

        self.assertTrue(result)
        self.assertEqual(event.wait_count, 3)
        self.assertEqual(event.clear_count, 2)

    def test_zhs_fetch_save_failure_is_reported_to_the_caller(self):
        with patch.object(
            fetch_zhs_courses,
            "atomic_dump_json",
            side_effect=OSError("disk full"),
        ):
            self.assertFalse(
                fetch_zhs_courses.save_course_data(
                    "unused.json",
                    "alice",
                    [],
                    [],
                )
            )

    def test_zhs_fetch_script_reuses_shared_portal_navigation_contract(self):
        source = Path(fetch_zhs_courses.__file__).read_text(encoding="utf-8")

        self.assertIn("navigate_to_my_course(page, _ConsoleLogger())", source)
        self.assertIn("login_to_zhihuishu(", source)
        self.assertIn("headless=False", source)
        self.assertIn("create_session_context(browser, runtime_config)", source)
        self.assertIn("raise SystemExit(asyncio.run(main()))", source)
        self.assertNotIn("wait_for_timeout(3000)", source)
        self.assertNotIn("#sharingClassed > div:nth-child", source)
        self.assertNotIn("page.click('text=\"我的学堂\"')", source)
        self.assertNotIn("lesson['secret']", source)

    def test_zhs_fetch_context_reuses_account_specific_cookies(self):
        class Context:
            def __init__(self):
                self.cookies = []

            async def add_cookies(self, cookies):
                self.cookies.extend(cookies)

        class Browser:
            def __init__(self, context):
                self.context = context

            async def new_context(self):
                return self.context

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cookie_file = root / "Autovisor" / "res" / "cookies_6.json"
            cookie_file.parent.mkdir(parents=True)
            cookie_file.write_text(
                json.dumps([{"name": "session", "value": "token"}]),
                encoding="utf-8",
            )
            original_root = fetch_zhs_courses.SCRIPT_DIR
            fetch_zhs_courses.SCRIPT_DIR = str(root)
            context = Context()
            try:
                loaded_context, resolved_path = asyncio.run(
                    fetch_zhs_courses.create_session_context(
                        Browser(context),
                        SimpleNamespace(cookies_file="res/cookies_6.json"),
                    )
                )
            finally:
                fetch_zhs_courses.SCRIPT_DIR = original_root

            self.assertIs(loaded_context, context)
            self.assertEqual(resolved_path, cookie_file)
            self.assertEqual(context.cookies[0]["name"], "session")

    def test_zhs_browser_candidates_follow_account_preference(self):
        chrome_first = fetch_zhs_courses._browser_candidates("chrome")
        edge_first = fetch_zhs_courses._browser_candidates("edge")

        self.assertIn("Google\\Chrome", chrome_first[0])
        self.assertIn("Microsoft\\Edge", edge_first[0])
        self.assertEqual(set(chrome_first), set(edge_first))

    def test_zhs_account_log_masking_never_returns_the_original_identifier(self):
        for value in ("a", "ab", "alice", "13800138000"):
            masked = fetch_zhs_courses._mask_identifier(value)
            self.assertNotEqual(masked, value)
            self.assertIn("*", masked)

    def test_zhs_course_credential_log_only_reports_presence(self):
        status = fetch_zhs_courses._credential_status("raw-course-secret")

        self.assertEqual(status, "已获取")
        self.assertNotIn("raw-course-secret", status)
        self.assertEqual(fetch_zhs_courses._credential_status(""), "未提供")


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
            refreshed = service.get_xuexitong_courses(
                0,
                "alice",
                "secret-a",
                force_refresh=True,
            )
            second_user = service.get_xuexitong_courses(0, "bob", "secret-b")

            self.assertTrue(first["ok"])
            self.assertEqual(cached, first)
            self.assertEqual(refreshed, first)
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
            self.assertEqual(len(sessions[0].posts), 2)
            self.assertEqual(len(sessions[0].gets), 2)

    def test_xuexitong_response_contract_distinguishes_empty_from_failure(self):
        self.assertTrue(is_xuexitong_course_payload({"channelList": []}))
        self.assertFalse(is_xuexitong_course_payload({"status": False}))
        self.assertFalse(is_xuexitong_course_payload({"channelList": {}}))

        class EmptySession(_Session):
            def get(self, url, **kwargs):
                self.gets.append((url, kwargs))
                return _Response(200, {"channelList": []})

        class InvalidSession(_Session):
            def get(self, url, **kwargs):
                self.gets.append((url, kwargs))
                return _Response(200, {"status": False, "message": "未登录"})

        with tempfile.TemporaryDirectory() as temp_dir:
            empty_service = CourseCatalogService(
                Path(temp_dir) / "empty",
                session_factory=lambda: EmptySession(""),
            )
            empty_result = empty_service.get_xuexitong_courses(
                0, "empty-user", "secret"
            )
            self.assertEqual(empty_result, {"ok": True, "courses": []})
            self.assertEqual(
                empty_service.get_cached("xxt", 0, "empty-user"),
                empty_result,
            )

            invalid_service = CourseCatalogService(
                Path(temp_dir) / "invalid",
                session_factory=lambda: InvalidSession(""),
            )
            invalid_result = invalid_service.get_xuexitong_courses(
                0, "invalid-user", "secret"
            )
            self.assertFalse(invalid_result["ok"])
            self.assertIn("登录已失效或接口已变化", invalid_result["message"])
            self.assertIsNone(
                invalid_service.get_cached("xxt", 0, "invalid-user")
            )


if __name__ == "__main__":
    unittest.main()
