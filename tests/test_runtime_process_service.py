import unittest

from src.runtime_process_service import RuntimeProcessService


class ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


class BrokenThread(ImmediateThread):
    def start(self):
        raise RuntimeError("thread unavailable")


class FakeProcess:
    def __init__(self, return_code=0):
        self.return_code = return_code
        self.terminated = False

    def wait(self):
        return self.return_code

    def poll(self):
        return self.return_code if self.terminated else None


class FakeLauncher:
    def __init__(self):
        self.stop_requested = {"core": False}
        self.logs = []
        self.running = []
        self.stopped = []
        self.exits = []
        self.notifications = []
        self.failures = []
        self.runtime_failures = []
        self.terminated = []
        self.stream_error = None

    def _get_subprocess_window_kwargs(self):
        return 7, "startup"

    def _mark_runtime_running(self, core, process):
        self.running.append((core, process))

    def _mark_runtime_stopped(self, core, process=None):
        self.stopped.append((core, process))

    def _stream_process_output(self, process, core, encodings):
        if self.stream_error:
            raise self.stream_error
        self.streamed = (process, core, tuple(encodings))

    def _handle_runtime_exit(self, core, return_code, stop_requested):
        self.exits.append((core, return_code, stop_requested))

    def _notify_runtime_event(self, title, message, error=False):
        self.notifications.append((title, message, error))

    def _record_runtime_failure(self, core, message):
        self.failures.append((core, message))

    def _handle_runtime_failure(
        self,
        core,
        title,
        message,
        *,
        notification_message=None,
    ):
        self.runtime_failures.append((core, title, message))
        self._record_runtime_failure(core, message)
        self._notify_runtime_event(
            title,
            notification_message or message,
            error=True,
        )

    def _terminate_process_tree(self, process, label):
        process.terminated = True
        self.terminated.append((process, label))

    def log_system(self, message):
        self.logs.append(message)


class RuntimeProcessServiceTests(unittest.TestCase):
    def make_service(self, launcher, process_factory, thread_factory=ImmediateThread):
        return RuntimeProcessService(
            launcher,
            process_factory=process_factory,
            thread_factory=thread_factory,
        )

    def test_normal_exit_streams_output_reconciles_state_and_notifies_host(self):
        launcher = FakeLauncher()
        process = FakeProcess(return_code=0)
        calls = []

        def create_process(command, **kwargs):
            calls.append((command, kwargs))
            return process

        result = self.make_service(launcher, create_process).start(
            core="core",
            label="Core",
            command=["core.exe", "--run"],
            cwd="runtime",
            encodings=["utf-8", "gbk"],
            env_factory=lambda: {"QB_URL": "http://127.0.0.1:8083"},
        )

        self.assertTrue(result)
        self.assertEqual(calls[0][0], ["core.exe", "--run"])
        self.assertEqual(calls[0][1]["cwd"], "runtime")
        self.assertEqual(
            calls[0][1]["env"],
            {"QB_URL": "http://127.0.0.1:8083"},
        )
        self.assertEqual(calls[0][1]["creationflags"], 7)
        self.assertEqual(launcher.running, [("core", process)])
        self.assertEqual(launcher.stopped, [("core", process)])
        self.assertEqual(launcher.exits, [("core", 0, False)])
        self.assertEqual(launcher.streamed[1:], ("core", ("utf-8", "gbk")))
        self.assertIn("Core 已停止", launcher.logs)

    def test_cancel_before_launch_skips_preparation_and_process_creation(self):
        launcher = FakeLauncher()
        launcher.stop_requested["core"] = True
        calls = []

        self.make_service(
            launcher,
            lambda *_args, **_kwargs: calls.append(True),
        ).start(
            core="core",
            label="Core",
            command=["core.exe"],
            cwd="runtime",
            encodings=["utf-8"],
            before_launch=lambda: calls.append("prepare"),
        )

        self.assertEqual(calls, [])
        self.assertEqual(launcher.stopped, [("core", None)])
        self.assertIn("Core 启动已取消", launcher.logs)

    def test_cancel_during_preparation_prevents_process_creation(self):
        launcher = FakeLauncher()
        calls = []

        def prepare():
            launcher.stop_requested["core"] = True
            calls.append("prepare")

        self.make_service(
            launcher,
            lambda *_args, **_kwargs: calls.append("process"),
        ).start(
            core="core",
            label="Core",
            command=["core.exe"],
            cwd="runtime",
            encodings=["utf-8"],
            before_launch=prepare,
        )

        self.assertEqual(calls, ["prepare"])
        self.assertEqual(launcher.stopped, [("core", None)])

    def test_output_failure_terminates_live_process_before_clearing_state(self):
        launcher = FakeLauncher()
        launcher.stream_error = OSError("pipe closed")
        process = FakeProcess()

        self.make_service(launcher, lambda *_args, **_kwargs: process).start(
            core="core",
            label="Core",
            command=["core.exe"],
            cwd="runtime",
            encodings=["utf-8"],
        )

        self.assertEqual(launcher.terminated, [(process, "Core")])
        self.assertEqual(launcher.stopped, [("core", process)])
        self.assertEqual(
            launcher.notifications,
            [("Core 启动失败", "pipe closed", True)],
        )
        self.assertEqual(
            launcher.failures,
            [("core", "Core 启动失败: pipe closed")],
        )
        self.assertIn("Core 启动失败: pipe closed", launcher.logs)
        self.assertEqual(
            launcher.runtime_failures,
            [
                (
                    "core",
                    "Core 启动失败",
                    "Core 启动失败: pipe closed",
                )
            ],
        )

    def test_thread_start_failure_releases_claim_and_is_raised(self):
        launcher = FakeLauncher()
        service = self.make_service(
            launcher,
            lambda *_args, **_kwargs: FakeProcess(),
            thread_factory=BrokenThread,
        )

        with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
            service.start(
                core="core",
                label="Core",
                command=["core.exe"],
                cwd="runtime",
                encodings=["utf-8"],
            )

        self.assertEqual(launcher.stopped, [("core", None)])
        self.assertEqual(len(launcher.runtime_failures), 1)

    def test_thread_construction_failure_releases_claim_and_is_raised(self):
        launcher = FakeLauncher()

        def broken_thread_factory(**_kwargs):
            raise RuntimeError("thread construction failed")

        service = self.make_service(
            launcher,
            lambda *_args, **_kwargs: FakeProcess(),
            thread_factory=broken_thread_factory,
        )

        with self.assertRaisesRegex(RuntimeError, "thread construction failed"):
            service.start(
                core="core",
                label="Core",
                command=["core.exe"],
                cwd="runtime",
                encodings=["utf-8"],
            )

        self.assertEqual(launcher.stopped, [("core", None)])
        self.assertEqual(len(launcher.runtime_failures), 1)

    def test_monitor_uses_requested_output_channel_and_clears_state(self):
        launcher = FakeLauncher()
        process = FakeProcess(return_code=0)

        result = self.make_service(
            launcher,
            lambda *_args, **_kwargs: None,
        ).monitor(
            core="practice",
            label="刷题模式",
            process=process,
            encodings=["utf-8"],
            output_source="autovisor",
            exit_message="[刷题模式] 已退出",
        )

        self.assertTrue(result)
        self.assertEqual(launcher.streamed[1], "autovisor")
        self.assertEqual(launcher.stopped, [("practice", process)])
        self.assertIn("[刷题模式] 已退出", launcher.logs)

    def test_monitor_reports_unrequested_nonzero_exit(self):
        launcher = FakeLauncher()
        process = FakeProcess(return_code=3)

        self.make_service(
            launcher,
            lambda *_args, **_kwargs: None,
        ).monitor(
            core="core",
            label="刷题模式",
            process=process,
            encodings=["utf-8"],
        )

        message = "刷题模式 已退出，返回码: 3"
        self.assertIn(message, launcher.logs)
        self.assertEqual(
            launcher.notifications,
            [("刷题模式 运行异常", message, True)],
        )
        self.assertEqual(
            launcher.failures,
            [("core", message)],
        )

    def test_monitor_thread_failure_terminates_process_and_clears_state(self):
        launcher = FakeLauncher()
        process = FakeProcess()
        service = self.make_service(
            launcher,
            lambda *_args, **_kwargs: None,
            thread_factory=BrokenThread,
        )

        with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
            service.monitor(
                core="practice",
                label="刷题模式",
                process=process,
                encodings=["utf-8"],
            )

        self.assertEqual(launcher.terminated, [(process, "刷题模式")])
        self.assertEqual(launcher.stopped, [("practice", process)])


if __name__ == "__main__":
    unittest.main()
