import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.question_bank_controller import QuestionBankController


class _FakeServer:
    instances = []

    def __init__(self, port):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get_stats(self):
        return {"total": 3}


class QuestionBankControllerTests(unittest.TestCase):
    def setUp(self):
        _FakeServer.instances = []

    def make_controller(self, base_dir, **kwargs):
        logs = kwargs.pop("logs", [])
        controller = QuestionBankController(
            base_dir,
            log=logs.append,
            server_factory=_FakeServer,
            configure_models=lambda **_kwargs: None,
            configure_auto_save_setting=lambda **_kwargs: None,
            port_checker=lambda _port: True,
            sleep=lambda _seconds: None,
            available=True,
            **kwargs,
        )
        return controller, logs

    def test_load_normalizes_port_and_uses_environment_api_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "data" / "qb_config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps({"port": "bad", "ai_enabled": True}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"QB_AI_API_KEY": "from-env"}):
                controller, _logs = self.make_controller(temp_dir)

            self.assertEqual(controller.port, 8083)
            self.assertEqual(controller.ai_api_key, "from-env")

    def test_saved_settings_do_not_persist_api_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            controller, _logs = self.make_controller(temp_dir)
            result = controller.update_settings(
                {
                    "port": 8090,
                    "auto_start": False,
                    "ai_enabled": True,
                    "ai_type": "OPENAI",
                    "ai_url": "https://example.invalid/v1",
                    "ai_model": "model",
                    "ai_api_key": "secret",
                    "auto_save": False,
                }
            )

            saved = json.loads(controller.config_path.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertNotIn("ai_api_key", saved)
            self.assertEqual(controller.ai_api_key, "secret")

    def test_invalid_port_is_rejected_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            controller, _logs = self.make_controller(temp_dir)
            original = controller.get_settings()

            result = controller.update_settings({"port": 80})

            self.assertFalse(result["ok"])
            self.assertEqual(controller.get_settings(), original)

    def test_write_failure_rolls_back_in_memory_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            controller, _logs = self.make_controller(temp_dir)
            original = controller.get_settings()

            with patch(
                "src.question_bank_controller.atomic_dump_json",
                side_effect=OSError("disk full"),
            ):
                result = controller.update_settings({"port": 8091})

            self.assertFalse(result["ok"])
            self.assertEqual(controller.get_settings(), original)

    def test_running_server_restarts_when_port_changes(self):
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            controller, _logs = self.make_controller(
                temp_dir,
                prepare_environment=lambda: events.append("prepare"),
                on_status_change=lambda: events.append("status"),
                sync_external_url=lambda: events.append("sync"),
            )
            self.assertTrue(controller.start())

            result = controller.update_settings({"port": 8092})

            self.assertTrue(result["ok"])
            self.assertTrue(controller.running)
            self.assertEqual(controller.port, 8092)
            self.assertEqual(len(_FakeServer.instances), 2)
            self.assertTrue(_FakeServer.instances[0].stopped)
            self.assertTrue(_FakeServer.instances[1].started)
            self.assertEqual(controller.get_query_url(), "http://127.0.0.1:8092/query")
            self.assertEqual(controller.get_stats(), {"total": 3})
            self.assertEqual(events.count("sync"), 2)

    def test_unreachable_started_server_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = QuestionBankController(
                temp_dir,
                log=lambda _message: None,
                server_factory=_FakeServer,
                configure_models=lambda **_kwargs: None,
                configure_auto_save_setting=lambda **_kwargs: None,
                port_checker=lambda _port: False,
                sleep=lambda _seconds: None,
                available=True,
            )

            self.assertFalse(controller.start())
            self.assertFalse(controller.running)
            self.assertIsNone(controller.server)
            self.assertTrue(_FakeServer.instances[0].stopped)


if __name__ == "__main__":
    unittest.main()
