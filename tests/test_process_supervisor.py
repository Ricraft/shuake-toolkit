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

    def test_late_old_exit_preserves_new_start_reservation(self):
        processes = {"core": None}
        running = {"core": False}
        starting = {"core": False}
        stop_requested = {"core": False}
        supervisor = ProcessSupervisor(
            processes=processes,
            running=running,
            starting=starting,
            stop_requested=stop_requested,
            state_lock=threading.RLock(),
        )
        old_process = object()
        new_process = object()

        supervisor.mark_running("core", old_process)
        self.assertTrue(supervisor.mark_stopped("core", old_process))
        self.assertIsNone(processes["core"])
        self.assertFalse(running["core"])
        self.assertFalse(starting["core"])

        self.assertTrue(supervisor.claim_start("core"))
        self.assertFalse(supervisor.mark_stopped("core", old_process))
        self.assertIsNone(processes["core"])
        self.assertFalse(running["core"])
        self.assertTrue(starting["core"])

        supervisor.mark_running("core", new_process)
        self.assertIs(processes["core"], new_process)
        self.assertTrue(running["core"])
        self.assertFalse(starting["core"])

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

    def test_rich_style_progress_lines_are_recognized(self):
        # ZHS（rich 风格进度条）用方括号画条、百分比带小数，靠 \r 原地刷新。
        self.assertTrue(is_progress_log("playing 78613499 [##########    ] 32.7%"))
        self.assertTrue(
            is_progress_log("[19:46:22] playing 78613499 [#####     ] 33.2%")
        )
        self.assertTrue(is_progress_log("下载中 \u2501\u2501\u2501 12%"))
        self.assertFalse(is_progress_log("登录状态有效"))
        self.assertFalse(is_progress_log("[19:46:22] 已发现 4 门课程"))

    def test_stream_merges_consecutive_carriage_return_progress_frames(self):
        frames = "".join(
            "\rplaying 78613499 [####      ] " + percent + "%"
            for percent in ("32.7", "32.9", "33.2")
        )
        emitted = []
        supervisor = ProcessSupervisor(
            log_line=lambda source, line, **kwargs: emitted.append(
                (line, kwargs.get("replace_last"))
            )
        )
        process = SimpleNamespace(
            stdout=_ChunkedOutput([frames.encode("utf-8") + b"\r\n"])
        )

        supervisor.stream_process_output(process, "zhs", ["utf-8"])

        # 每一帧都由 \r 结束，是原地刷新；是否替换上一行由日志层按上一条内容判断。
        self.assertEqual(len(emitted), 3)
        self.assertTrue(all(replace for _line, replace in emitted))
        self.assertTrue(emitted[-1][0].endswith("33.2%"))

    def test_subprocess_window_kwargs_do_not_request_detached_process(self):
        class FakeSubprocess:
            CREATE_NEW_PROCESS_GROUP = 0x200
            CREATE_NO_WINDOW = 0x08000000
            DETACHED_PROCESS = 0x8
            STARTF_USESHOWWINDOW = 0x1

            class STARTUPINFO:
                def __init__(self):
                    self.dwFlags = 0
                    self.wShowWindow = 1

        flags, startupinfo = ProcessSupervisor(
            platform="win32",
            subprocess_module=FakeSubprocess,
        ).subprocess_window_kwargs()

        # 叠加 DETACHED_PROCESS 会让 Windows 弹出可见控制台窗口。
        self.assertFalse(flags & FakeSubprocess.DETACHED_PROCESS)
        self.assertTrue(flags & FakeSubprocess.CREATE_NO_WINDOW)
        self.assertTrue(flags & FakeSubprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertEqual(startupinfo.wShowWindow, 0)


if __name__ == "__main__":
    unittest.main()
