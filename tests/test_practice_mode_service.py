import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.practice_mode_service import PracticeModeService
from src.process_supervisor import ProcessSupervisor


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


class PracticeModeServiceTests(unittest.TestCase):
    def test_stop_during_popen_terminates_process_and_returns_cancelled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "Practice_Mode.py").write_text(
                "# isolated test fixture", encoding="utf-8"
            )
            launcher = SimpleNamespace(
                processes={"practice": None},
                running={"practice": False},
                starting={"practice": False},
                stop_requested={"practice": False},
                practice_account_id=None,
                question_bank=SimpleNamespace(running=True),
                autovisor_path=temp_dir,
                log_system=lambda message: logs.append(message),
                get_python_executable=lambda: "python-test",
                _load_autovisor_config_data=lambda: {
                    "accounts": [{"account_id": 7}]
                },
                _as_int=lambda value, default=0: int(value or default),
                _get_runtime_coordinator=lambda: SimpleNamespace(
                    prepare_start=lambda _runtime: True
                ),
                _get_subprocess_window_kwargs=lambda: (0, None),
                _build_encoding_candidates=lambda *_values: ("utf-8",),
                _get_runtime_process_service=lambda: runtime_service,
            )
            logs = []
            process = FakeProcess()
            monitor_calls = []
            runtime_service = SimpleNamespace(
                monitor=lambda **kwargs: monitor_calls.append(kwargs)
            )
            supervisor = ProcessSupervisor(
                processes=launcher.processes,
                running=launcher.running,
                starting=launcher.starting,
                stop_requested=launcher.stop_requested,
                state_lock=threading.RLock(),
            )
            launcher._claim_runtime_start = supervisor.claim_start
            launcher._mark_runtime_running = supervisor.mark_running

            def mark_stopped(runtime, child=None):
                stopped = supervisor.mark_stopped(runtime, child)
                if stopped and runtime == "practice":
                    launcher.practice_account_id = None
                return stopped

            launcher._mark_runtime_stopped = mark_stopped
            terminated = []

            def terminate(child, label):
                terminated.append((child, label))
                child.terminate()

            launcher._terminate_process_tree = terminate

            def popen_with_concurrent_stop(*_args, **_kwargs):
                # Model stop() winning while Popen is blocked, before it returns.
                launcher.stop_requested["practice"] = True
                return process

            with patch(
                "src.practice_mode_service.subprocess.Popen",
                side_effect=popen_with_concurrent_stop,
            ):
                result = PracticeModeService(launcher).start(0)

        self.assertEqual(
            result,
            {"ok": False, "message": "刷题模式启动已取消"},
        )
        self.assertEqual(terminated, [(process, "刷题模式")])
        self.assertTrue(process.terminated)
        self.assertEqual(monitor_calls, [])
        self.assertIsNone(launcher.processes["practice"])
        self.assertFalse(launcher.running["practice"])
        self.assertFalse(launcher.starting["practice"])
        self.assertIsNone(launcher.practice_account_id)
        self.assertIn("[刷题模式] 启动已取消", logs)
        self.assertFalse(
            any("刷题模式已启动" in message for message in logs)
        )


if __name__ == "__main__":
    unittest.main()
