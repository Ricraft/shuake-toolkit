import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from src.process_supervisor import (
    ProcessSupervisor,
    build_encoding_candidates,
    clean_log_text,
    decode_output_line,
    is_progress_log,
    normalize_progress_log,
)


class _ChunkedOutput:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read1(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class ProcessSupervisorTests(unittest.TestCase):
    def test_encoding_cleaning_and_progress_normalization(self):
        encodings = build_encoding_candidates("gbk", "GBK", None, "utf-8")
        self.assertEqual(encodings.count("gbk"), 1)
        self.assertEqual(decode_output_line("中文".encode("gbk"), encodings), "中文")
        self.assertEqual(clean_log_text("\x1b[31m错误\x1b[0m\t \r\n"), "错误")

        raw = "完成进度: |████| 42%  1/2"
        self.assertEqual(normalize_progress_log(raw), "完成进度: 42% | 1/2")
        self.assertTrue(is_progress_log(raw))
        self.assertFalse(is_progress_log("普通日志"))

    def test_stream_output_handles_chunk_boundaries_and_carriage_progress(self):
        emitted = []
        progress = "完成进度: |██| 50% 剩余".encode("utf-8")
        process = SimpleNamespace(
            stdout=_ChunkedOutput(
                [
                    b"\x1b[32mhello\x1b[0m\n" + progress[:7],
                    progress[7:] + b"\r",
                    "尾行".encode("utf-8"),
                ]
            )
        )
        supervisor = ProcessSupervisor(
            log_line=lambda source, line, **kwargs: emitted.append(
                (source, line, kwargs.get("replace_last"))
            )
        )

        supervisor.stream_process_output(process, "autovisor", ["utf-8"])

        self.assertEqual(emitted[0], ("autovisor", "hello", False))
        self.assertEqual(emitted[1][0], "autovisor")
        self.assertTrue(emitted[1][2])
        self.assertEqual(emitted[2], ("autovisor", "尾行", False))

    def test_runtime_start_claim_is_atomic_and_stale_exit_cannot_clear_new_process(self):
        processes = {"core": None}
        running = {"core": False}
        starting = {"core": False}
        stop_requested = {"core": True}
        supervisor = ProcessSupervisor(
            processes=processes,
            running=running,
            starting=starting,
            stop_requested=stop_requested,
            state_lock=threading.RLock(),
        )

        with ThreadPoolExecutor(max_workers=8) as executor:
            claims = list(executor.map(lambda _index: supervisor.claim_start("core"), range(32)))
        self.assertEqual(claims.count(True), 1)
        self.assertFalse(stop_requested["core"])

        old_process = object()
        new_process = object()
        supervisor.mark_running("core", old_process)
        supervisor.mark_running("core", new_process)
        self.assertFalse(supervisor.mark_stopped("core", old_process))
        self.assertIs(processes["core"], new_process)
        self.assertTrue(running["core"])
        self.assertTrue(supervisor.mark_stopped("core", new_process))
        self.assertFalse(running["core"])

    def test_windows_taskkill_failure_falls_back_to_process_terminate(self):
        logs = []

        class FakeSubprocess:
            DEVNULL = object()
            CREATE_NEW_PROCESS_GROUP = 1
            CREATE_NO_WINDOW = 2
            DETACHED_PROCESS = 4

            @staticmethod
            def run(*_args, **_kwargs):
                return SimpleNamespace(returncode=1)

        class Process:
            pid = 123

            def __init__(self):
                self.terminated = False
                self.wait_timeout = None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                self.wait_timeout = timeout
                return 0

        process = Process()
        supervisor = ProcessSupervisor(
            platform="win32",
            subprocess_module=FakeSubprocess,
            log_system=logs.append,
        )

        self.assertTrue(supervisor.terminate_process_tree(process, "核心"))
        self.assertTrue(process.terminated)
        self.assertEqual(process.wait_timeout, 5)
        self.assertTrue(any("回退到普通终止" in message for message in logs))

    def test_terminate_failure_uses_kill_and_reports_final_failure(self):
        class Process:
            pid = 456

            def __init__(self, kill_fails=False):
                self.killed = False
                self.kill_fails = kill_fails

            def terminate(self):
                raise RuntimeError("terminate failed")

            def wait(self, timeout=None):
                raise RuntimeError(timeout)

            def kill(self):
                if self.kill_fails:
                    raise RuntimeError("kill failed")
                self.killed = True

        process = Process()
        supervisor = ProcessSupervisor(platform="linux")
        self.assertTrue(supervisor.terminate_process_tree(process, "核心"))
        self.assertTrue(process.killed)

        logs = []
        self.assertFalse(
            ProcessSupervisor(
                platform="linux",
                log_system=logs.append,
            ).terminate_process_tree(Process(kill_fails=True), "核心")
        )
        self.assertTrue(any("强制终止失败" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
