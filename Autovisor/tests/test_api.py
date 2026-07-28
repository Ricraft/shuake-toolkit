# encoding=utf-8
"""
Autovisor Web API Tests
Unit and integration tests for the dashboard API
"""

import json
import os
import sys
import tempfile
import unittest
import time
from pathlib import Path
from types import SimpleNamespace

# Ensure the standalone Autovisor modules are importable during collection,
# then restore sys.path so this test module does not shadow the Autovisor namespace.
_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from fastapi.testclient import TestClient

import web.api as api_module
from web.api import (
    app,
    app_state,
    config_manager,
    dashboard_logger,
)
from web.task_supervisor import TaskAlreadyRunning, TaskSnapshot

sys.path.remove(_AUTOVISOR_ROOT)


class _FakeTaskSupervisor:
    def __init__(self):
        self.snapshot = TaskSnapshot()
        self.launch_calls = []
        self.stop_calls = 0

    def refresh(self):
        return self.snapshot

    def launch(self, *, mode, config_path, course_url=None):
        if self.snapshot.is_running:
            raise TaskAlreadyRunning("已有任务正在运行")
        if mode == "multi" and course_url:
            raise ValueError("多账号模式不支持单课程临时覆盖")
        self.launch_calls.append((mode, Path(config_path), course_url))
        self.snapshot = TaskSnapshot(
            is_running=True,
            task_id=course_url or (
                "all_accounts" if mode == "multi" else "all_courses"
            ),
            mode=mode,
            pid=7788,
            start_time=time.time(),
        )
        return self.snapshot

    def stop(self, timeout=10):
        self.stop_calls += 1
        if not self.snapshot.is_running:
            return False, self.snapshot
        self.snapshot = TaskSnapshot(exit_code=-15)
        return True, self.snapshot

    def set_running(self, *, task_id="course_1", mode="single", started_at=None):
        self.snapshot = TaskSnapshot(
            is_running=True,
            task_id=task_id,
            mode=mode,
            pid=7788,
            start_time=started_at if started_at is not None else time.time(),
        )


class TestHealthEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_check(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["status"], "ok")
        self.assertIn("version", data["data"])


class TestConfigEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Use a temp config file for isolation
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_config = Path(self.temp_dir.name) / "test_configs.ini"
        # Write a minimal config
        self.temp_config.write_text(
            "[user-account]\nusername = testuser\npassword = testpass\n\n"
            "[browser-option]\ndriver = edge\nEXE_PATH = \n\n"
            "[script-option]\nenableAutoCaptcha = True\nenableHideWindow = False\n\n"
            "[course-option]\nlimitMaxTime = 30\nlimitSpeed = 1.5\nsoundOff = True\n\n"
            "[course-url]\nURL1 = https://example.com/course1\n",
            encoding="utf-8",
        )
        # Patch config manager path
        config_manager.config_path = self.temp_config
        config_manager._last_read = 0
        config_manager._config = None

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_config(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        cfg = data["data"]
        self.assertEqual(cfg["driver"], "edge")
        self.assertEqual(cfg["limit_max_time"], 30.0)
        self.assertEqual(cfg["limit_speed"], 1.5)
        # Password should be masked
        self.assertEqual(cfg["password"], "********")

    def test_update_config(self):
        payload = {
            "driver": "chrome",
            "limit_max_time": 60.0,
            "limit_speed": 2.0,
            "course_urls": ["https://example.com/course2"],
        }
        response = self.client.post("/api/config", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["updated"], True)

        # Verify update
        response = self.client.get("/api/config")
        cfg = response.json()["data"]
        self.assertEqual(cfg["driver"], "chrome")
        self.assertEqual(cfg["limit_max_time"], 60.0)
        self.assertEqual(cfg["limit_speed"], 2.0)
        self.assertEqual(cfg["course_urls"], ["https://example.com/course2"])


class TestTaskEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_supervisor = api_module.task_supervisor
        self.supervisor = _FakeTaskSupervisor()
        api_module.task_supervisor = self.supervisor
        # Reset state
        app_state.is_running = False
        app_state.current_task = None
        app_state.task_mode = None
        app_state.task_pid = None
        app_state.task_start_time = None
        app_state.last_exit_code = None

    def tearDown(self):
        api_module.task_supervisor = self.original_supervisor
        app_state.is_running = False
        app_state.current_task = None
        app_state.task_mode = None
        app_state.task_pid = None
        app_state.task_start_time = None
        app_state.last_exit_code = None

    def test_start_task(self):
        response = self.client.post("/api/tasks/start", json={"mode": "single"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["mode"], "single")
        self.assertEqual(data["data"]["pid"], 7788)
        self.assertTrue(app_state.is_running)
        self.assertEqual(len(self.supervisor.launch_calls), 1)

    def test_start_task_conflict(self):
        self.supervisor.set_running()
        response = self.client.post("/api/tasks/start", json={"mode": "single"})
        self.assertEqual(response.status_code, 409)

    def test_stop_task(self):
        self.supervisor.set_running(task_id="test_task")
        response = self.client.post("/api/tasks/stop", json={})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(app_state.is_running)
        self.assertEqual(data["data"]["exit_code"], -15)

    def test_stop_when_not_running(self):
        app_state.is_running = False
        response = self.client.post("/api/tasks/stop", json={})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_invalid_mode_is_rejected_without_launching(self):
        response = self.client.post("/api/tasks/start", json={"mode": "invalid"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supervisor.launch_calls, [])

    def test_invalid_course_url_is_rejected_without_launching(self):
        response = self.client.post(
            "/api/tasks/start",
            json={"mode": "single", "course_url": "file:///tmp/course"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supervisor.launch_calls, [])

    def test_multi_mode_rejects_single_course_override(self):
        response = self.client.post(
            "/api/tasks/start",
            json={
                "mode": "multi",
                "course_url": "https://example.test/course",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supervisor.launch_calls, [])


class TestStatusEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_supervisor = api_module.task_supervisor
        self.supervisor = _FakeTaskSupervisor()
        api_module.task_supervisor = self.supervisor
        app_state.is_running = False
        app_state.current_task = None
        app_state.task_start_time = None

    def tearDown(self):
        api_module.task_supervisor = self.original_supervisor

    def test_status_stopped(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["data"]["is_running"])
        self.assertEqual(data["data"]["uptime_seconds"], 0.0)

    def test_status_running(self):
        self.supervisor.set_running(
            task_id="course_1",
            started_at=time.time() - 120,
        )
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["data"]["is_running"])
        self.assertEqual(data["data"]["current_task"], "course_1")
        self.assertEqual(data["data"]["pid"], 7788)
        self.assertGreaterEqual(data["data"]["uptime_seconds"], 120)


class TestLifespanCleanup(unittest.TestCase):
    def test_shutdown_stops_running_task(self):
        original_supervisor = api_module.task_supervisor
        supervisor = _FakeTaskSupervisor()
        supervisor.set_running()
        api_module.task_supervisor = supervisor
        try:
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
        finally:
            api_module.task_supervisor = original_supervisor

        self.assertEqual(supervisor.stop_calls, 1)
        self.assertFalse(supervisor.snapshot.is_running)


class TestLogsEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        dashboard_logger.logs.clear()

    def _use_task_log(self, log_path: Path):
        original_supervisor = api_module.task_supervisor
        api_module.task_supervisor = SimpleNamespace(log_path=log_path)
        self.addCleanup(setattr, api_module, "task_supervisor", original_supervisor)

    def test_get_logs_empty(self):
        response = self.client.get("/api/logs")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["logs"], [])

    def test_get_logs_with_entries(self):
        dashboard_logger.info("Test info message")
        dashboard_logger.warn("Test warning message")
        dashboard_logger.error("Test error message")
        response = self.client.get("/api/logs")
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["data"]["logs"]), 3)
        # Test level filter
        response = self.client.get("/api/logs?level=ERROR")
        data = response.json()
        self.assertEqual(len(data["data"]["logs"]), 1)
        self.assertEqual(data["data"]["logs"][0]["level"], "ERROR")

    def test_get_logs_limit(self):
        for i in range(150):
            dashboard_logger.info(f"Log entry {i}")
        response = self.client.get("/api/logs?limit=10")
        data = response.json()
        self.assertEqual(len(data["data"]["logs"]), 10)

    def test_get_logs_includes_task_output_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_log = Path(temp_dir) / "DashboardTask.log"
            task_log.write_text(
                "dashboard child started\nTraceback: child failed\n",
                encoding="utf-8",
            )
            self._use_task_log(task_log)

            response = self.client.get("/api/logs?limit=10")

        data = response.json()
        self.assertTrue(data["success"])
        logs = data["data"]["logs"]
        self.assertTrue(any(log["message"] == "dashboard child started" for log in logs))
        error_logs = [log for log in logs if "Traceback" in log["message"]]
        self.assertEqual(error_logs[0]["level"], "ERROR")
        self.assertEqual(error_logs[0]["source"], "task")

    def test_get_logs_level_filter_applies_to_task_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_log = Path(temp_dir) / "DashboardTask.log"
            task_log.write_text(
                "normal child line\nERROR child line\n",
                encoding="utf-8",
            )
            self._use_task_log(task_log)

            response = self.client.get("/api/logs?level=ERROR")

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(
            [log["message"] for log in data["data"]["logs"]],
            ["ERROR child line"],
        )


class TestStatsEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_supervisor = api_module.task_supervisor
        dashboard_logger.logs.clear()
        app_state.stats = {
            "total_sessions": 0,
            "total_videos": 0,
            "total_tests": 0,
            "success_rate": None,
        }

    def tearDown(self):
        api_module.task_supervisor = self.original_supervisor
        app_state.stats = {
            "total_sessions": 0,
            "total_videos": 0,
            "total_tests": 0,
            "success_rate": None,
        }

    def _use_task_log(self, log_path: Path):
        api_module.task_supervisor = SimpleNamespace(log_path=log_path)

    def test_stats_default_success_rate_is_unknown(self):
        response = self.client.get("/api/stats")

        data = response.json()
        self.assertTrue(data["success"])
        self.assertIsNone(data["data"]["success_rate"])
        self.assertEqual(data["data"]["total_sessions"], 0)

    def test_stats_are_derived_from_task_log_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_log = Path(temp_dir) / "DashboardTask.log"
            task_log.write_text(
                "程序启动中...\n"
                "[OK] 视频播放完成\n"
                "[OK] 作业提交完成\n"
                "所有课程已学习完毕!\n",
                encoding="utf-8",
            )
            self._use_task_log(task_log)

            response = self.client.get("/api/stats")

        data = response.json()["data"]
        self.assertEqual(data["total_sessions"], 1)
        self.assertEqual(data["total_videos"], 1)
        self.assertEqual(data["total_tests"], 1)
        self.assertEqual(data["success_rate"], 1.0)
        self.assertEqual(data["stats_source"], "runtime_logs")

    def test_stats_success_rate_uses_observed_terminal_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_log = Path(temp_dir) / "DashboardTask.log"
            task_log.write_text(
                "所有课程已学习完毕!\n"
                "[ERROR] Autovisor 已退出，返回码: 3\n",
                encoding="utf-8",
            )
            self._use_task_log(task_log)

            response = self.client.get("/api/stats")

        self.assertEqual(response.json()["data"]["success_rate"], 0.5)

    def test_stats_deduplicates_repeated_runtime_log_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_log = Path(temp_dir) / "DashboardTask.log"
            task_log.write_text(
                "程序启动中...\n"
                "程序启动中...\n"
                "[OK] 视频播放完成\n"
                "[OK] 视频播放完成\n"
                "完成进度: 100%\n"
                "完成进度: 100%\n"
                "[OK] 作业提交完成\n"
                "[OK] 作业提交完成\n"
                "所有课程已学习完毕!\n"
                "所有课程已学习完毕!\n",
                encoding="utf-8",
            )
            self._use_task_log(task_log)

            response = self.client.get("/api/stats")

        data = response.json()["data"]
        self.assertEqual(data["total_sessions"], 1)
        self.assertEqual(data["total_videos"], 1)
        self.assertEqual(data["total_tests"], 1)
        self.assertEqual(data["success_rate"], 1.0)

    def test_dashboard_unknown_success_rate_renders_dash_placeholder(self):
        dashboard = (Path(_AUTOVISOR_ROOT) / "web" / "dashboard.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("data.success_rate == null ? '--'", dashboard)


class TestCoursesEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_supervisor = api_module.task_supervisor
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_config = Path(self.temp_dir.name) / "test_configs.ini"
        self.temp_config.write_text(
            "[user-account]\nusername = u\npassword = p\n\n"
            "[browser-option]\ndriver = edge\n\n"
            "[script-option]\nenableAutoCaptcha = True\nenableHideWindow = False\n\n"
            "[course-option]\nlimitMaxTime = 30\nlimitSpeed = 1.5\nsoundOff = True\n\n"
            "[course-url]\nURL1 = https://lc.zhihuishu.com/course1\nURL2 = https://lc.zhihuishu.com/course2\n",
            encoding="utf-8",
        )
        config_manager.config_path = self.temp_config
        config_manager._last_read = 0
        config_manager._config = None

    def tearDown(self):
        api_module.task_supervisor = self.original_supervisor
        self.temp_dir.cleanup()

    def _use_task_log(self, content: str):
        task_log = Path(self.temp_dir.name) / "DashboardTask.log"
        task_log.write_text(content, encoding="utf-8")
        api_module.task_supervisor = SimpleNamespace(log_path=task_log)

    def test_get_courses(self):
        response = self.client.get("/api/courses")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["data"]["courses"]), 2)
        self.assertEqual(data["data"]["courses"][0]["url"], "https://lc.zhihuishu.com/course1")

    def test_get_courses_uses_task_log_current_progress(self):
        self._use_task_log(
            "程序启动中...\n"
            "开始处理第 1/2 门课程（example.com）\n"
            "当前课程:<<大学生安全教育>>，是新版课程\n"
            "完成进度: 73%\n"
        )

        response = self.client.get("/api/courses")

        courses = response.json()["data"]["courses"]
        self.assertEqual(courses[0]["name"], "大学生安全教育")
        self.assertEqual(courses[0]["progress"], 73)
        self.assertEqual(courses[0]["status"], "running")
        self.assertEqual(courses[1]["status"], "pending")

    def test_get_courses_marks_completed_and_failed_rows_from_task_log(self):
        self._use_task_log(
            "开始处理第 1/2 门课程（one.example）\n"
            "第 1/2 门课程（one.example）执行完成\n"
            "开始处理第 2/2 门课程（two.example）\n"
            "第 2/2 门课程（two.example）执行失败: RuntimeError: boom\n"
        )

        response = self.client.get("/api/courses")

        courses = response.json()["data"]["courses"]
        self.assertEqual(courses[0]["progress"], 100)
        self.assertEqual(courses[0]["status"], "completed")
        self.assertEqual(courses[1]["status"], "failed")

    def test_get_courses_marks_all_rows_completed_when_task_finished(self):
        self._use_task_log("所有课程已学习完毕!\n")

        response = self.client.get("/api/courses")

        courses = response.json()["data"]["courses"]
        self.assertEqual([course["progress"] for course in courses], [100, 100])
        self.assertEqual(
            [course["status"] for course in courses],
            ["completed", "completed"],
        )

    def test_get_courses_keeps_highest_progress_without_guessing_completion(self):
        self._use_task_log(
            "开始处理第 1/2 门课程（one.example）\n"
            "完成进度: 80%\n"
            "完成进度: 60%\n"
            "开始处理第 2/2 门课程（two.example）\n"
            "完成进度: 10%\n"
        )

        response = self.client.get("/api/courses")

        courses = response.json()["data"]["courses"]
        self.assertEqual(courses[0]["progress"], 80)
        self.assertNotEqual(courses[0]["status"], "completed")
        self.assertEqual(courses[1]["progress"], 10)
        self.assertEqual(courses[1]["status"], "running")


class TestErrorHandling(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_404_handler(self):
        response = self.client.get("/api/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_invalid_json_payload(self):
        response = self.client.post(
            "/api/config",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
