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
from pathlib import Path

# Ensure the standalone Autovisor modules are importable during collection,
# then restore sys.path so this test module does not shadow the Autovisor namespace.
_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from fastapi.testclient import TestClient

from web.api import (
    app,
    app_state,
    config_manager,
    dashboard_logger,
)

sys.path.remove(_AUTOVISOR_ROOT)


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
        # Reset state
        app_state.is_running = False
        app_state.current_task = None
        app_state.task_start_time = None

    def tearDown(self):
        app_state.is_running = False
        app_state.current_task = None
        app_state.task_start_time = None

    def test_start_task(self):
        response = self.client.post("/api/tasks/start", json={"mode": "single"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["mode"], "single")
        self.assertTrue(app_state.is_running)

    def test_start_task_conflict(self):
        app_state.is_running = True
        response = self.client.post("/api/tasks/start", json={"mode": "single"})
        self.assertEqual(response.status_code, 409)

    def test_stop_task(self):
        app_state.is_running = True
        app_state.current_task = "test_task"
        response = self.client.post("/api/tasks/stop", json={})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(app_state.is_running)

    def test_stop_when_not_running(self):
        app_state.is_running = False
        response = self.client.post("/api/tasks/stop", json={})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])


class TestStatusEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app_state.is_running = False
        app_state.current_task = None
        app_state.task_start_time = None

    def test_status_stopped(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["data"]["is_running"])
        self.assertEqual(data["data"]["uptime_seconds"], 0.0)

    def test_status_running(self):
        import time
        app_state.is_running = True
        app_state.current_task = "course_1"
        app_state.task_start_time = time.time() - 120  # 2 minutes ago
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["data"]["is_running"])
        self.assertEqual(data["data"]["current_task"], "course_1")
        self.assertGreaterEqual(data["data"]["uptime_seconds"], 120)


class TestLogsEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        dashboard_logger.logs.clear()

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


class TestCoursesEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
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
        self.temp_dir.cleanup()

    def test_get_courses(self):
        response = self.client.get("/api/courses")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["data"]["courses"]), 2)
        self.assertEqual(data["data"]["courses"][0]["url"], "https://lc.zhihuishu.com/course1")


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
