import json
import os
import tempfile
import unittest
from contextlib import closing
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
        port_checker = kwargs.pop("port_checker", lambda _port: True)
        configure_auto_save_setting = kwargs.pop(
            "configure_auto_save_setting", lambda **_kwargs: None
        )
        controller = QuestionBankController(
            base_dir,
            log=logs.append,
            server_factory=_FakeServer,
            configure_models=lambda **_kwargs: None,
            configure_auto_save_setting=configure_auto_save_setting,
            port_checker=port_checker,
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

    def test_saved_settings_persist_ai_config_for_next_launch(self):
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
            self.assertEqual(saved["ai_type"], "OPENAI")
            self.assertEqual(saved["ai_url"], "https://example.invalid/v1")
            self.assertEqual(saved["ai_model"], "model")
            self.assertEqual(saved["ai_api_key"], "secret")
            self.assertEqual(controller.ai_api_key, "secret")

            reloaded, _logs = self.make_controller(temp_dir)
            self.assertEqual(reloaded.ai_type, "OPENAI")
            self.assertEqual(reloaded.ai_url, "https://example.invalid/v1")
            self.assertEqual(reloaded.ai_model, "model")
            self.assertEqual(reloaded.ai_api_key, "secret")

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

    def test_failed_port_restart_restores_old_file_state_and_server(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            controller, _logs = self.make_controller(
                temp_dir,
                port_checker=lambda port: port == 8083,
            )
            original = controller.get_settings()
            self.assertTrue(controller.start())

            result = controller.update_settings({"port": 8092})

            self.assertFalse(result["ok"])
            self.assertIn("已恢复旧设置", result["message"])
            self.assertEqual(controller.get_settings(), original)
            self.assertEqual(controller.port, 8083)
            self.assertTrue(controller.running)
            self.assertEqual(controller.server.port, 8083)
            self.assertFalse(controller.config_path.exists())
            self.assertEqual(len(_FakeServer.instances), 3)
            self.assertTrue(_FakeServer.instances[0].stopped)
            self.assertTrue(_FakeServer.instances[1].stopped)
            self.assertTrue(_FakeServer.instances[2].started)

    def test_failed_runtime_setting_callback_rolls_back_memory_and_file(self):
        calls = []

        def flaky_auto_save(**_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("callback failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller, _logs = self.make_controller(
                temp_dir,
                configure_auto_save_setting=flaky_auto_save,
            )
            original = controller.get_settings()

            result = controller.update_settings(
                {"port": 8083, "auto_save": False, "ai_enabled": False}
            )

            self.assertFalse(result["ok"])
            self.assertIn("已恢复旧设置", result["message"])
            self.assertEqual(controller.get_settings(), original)
            self.assertFalse(controller.config_path.exists())
            self.assertEqual(len(calls), 2)

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

    def test_import_export_deduplicate_and_clear_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "questions.json"
            exported = root / "exported.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "question": "测试 题目？",
                            "answer": ["A"],
                            "options": ["A", "B"],
                            "type": "single",
                        },
                        {
                            "question": "测试题目",
                            "answer": "A",
                            "options": ["A", "B"],
                            "type": "single",
                        },
                        {"question": "缺少答案"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            controller, _logs = self.make_controller(temp_dir)

            imported = controller.import_questions(source)
            deduplicated = controller.deduplicate_questions()
            exported_result = controller.export_questions(exported)
            cleared = controller.clear_questions()

            self.assertEqual(imported["count"], 2)
            self.assertEqual(deduplicated["count"], 1)
            self.assertEqual(exported_result["count"], 1)
            self.assertEqual(len(json.loads(exported.read_text(encoding="utf-8"))), 1)
            self.assertEqual(cleared["count"], 1)

    def test_import_rejects_non_array_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "bad.json"
            source.write_text('{"question": "not a list"}', encoding="utf-8")
            controller, _logs = self.make_controller(temp_dir)

            result = controller.import_questions(source)

            self.assertFalse(result["ok"])
            self.assertFalse(controller.database_path.exists())

    def test_zerror_environment_uses_configured_existing_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "airesponses.db"
            import sqlite3

            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE AIResponses (Id INTEGER)")
                connection.executemany(
                    "INSERT INTO AIResponses (Id) VALUES (?)",
                    [(1,), (2,)],
                )
                connection.commit()
            with patch.dict(os.environ, {"ZERROR_DB_PATH": str(database)}, clear=False):
                controller, logs = self.make_controller(temp_dir)
                controller.configure_zerror_environment()

                self.assertEqual(controller.detect_zerror_db_path(), str(database.resolve()))
                self.assertIn("2 条记录", controller.get_zerror_db_info())
                self.assertTrue(any("ZError" in entry for entry in logs))


if __name__ == "__main__":
    unittest.main()
