import threading
from pathlib import Path
from types import SimpleNamespace

from src.web_state_service import EMPTY_QB_STATS, WebStateService


class _Launcher:
    LAUNCHER_VERSION = "2026-test"
    YATORI_DISPLAY_VERSION = "y-fallback"
    AUTOVISOR_DISPLAY_VERSION = "a-fallback"
    YATORI_ENTRY_FILES = ("yatori.exe",)
    AUTOVISOR_ENTRY_FILES = ("autovisor.py",)

    def __init__(self, root: Path):
        self.root = root
        self.yatori_path = str(root / "Yatori")
        self.autovisor_path = str(root / "Autovisor")
        Path(self.yatori_path).mkdir()
        Path(self.autovisor_path).mkdir()
        Path(self.yatori_path, "yatori.exe").write_text("core", encoding="utf-8")
        self.running = {
            "yatori": True,
            "autovisor": False,
            "practice": False,
        }
        self.starting = {
            "yatori": False,
            "autovisor": True,
            "practice": False,
        }
        self._runtime_lock = threading.RLock()
        self.log_history = {
            "yatori": ["started"],
            "autovisor": "正在启动任务\n当前课程:<<英语>>",
            "system": [],
        }
        self.practice_account_id = 2
        self._shutdown_pending = False
        self._last_runtime_event = {"sequence": 7, "kind": "started"}
        self.autovisor_dependencies = SimpleNamespace(installing=True)
        self.question_bank = SimpleNamespace(
            running=True,
            port=8083,
            get_stats=lambda: {"total": 3, "local": 2, "ai_cached": 1},
        )
        self.logs = []

    def log_system(self, message):
        self.logs.append(message)

    def _get_yatori_display_version(self):
        return "y-current"

    def _get_autovisor_display_version(self):
        return "a-current"

    def _directory_has_any_file(self, directory, file_names):
        return any(Path(directory, name).is_file() for name in file_names)

    @property
    def autovisor_installing(self):
        return self.autovisor_dependencies.installing

    def _get_autovisor_multi_mode(self):
        return True


def test_runtime_snapshot_is_complete_consistent_and_json_friendly(tmp_path):
    launcher = _Launcher(tmp_path)

    state = WebStateService(launcher).runtime_state()

    assert state["backend_ready"] is True
    assert state["running"]["yatori"] is True
    assert state["starting"]["autovisor"] is True
    assert state["installed"] == {"yatori": True, "autovisor": False}
    assert state["dependency_installing"]["autovisor"] is True
    assert state["qb_stats"]["total"] == 3
    assert state["practice_account_id"] == 2
    assert state["autovisor_activity"]["course"] == "英语"
    assert state["logs"]["autovisor"] == ["正在启动任务", "当前课程:<<英语>>"]
    assert state["runtime_event"] == {"sequence": 7, "kind": "started"}
    assert state["runtime_event"] is not launcher._last_runtime_event
    assert state["state_issues"] == []


def test_optional_probe_failures_do_not_disconnect_runtime_state(tmp_path):
    launcher = _Launcher(tmp_path)
    launcher.question_bank.get_stats = lambda: (_ for _ in ()).throw(
        OSError("database busy")
    )
    launcher._get_yatori_display_version = lambda: (_ for _ in ()).throw(
        RuntimeError("version file busy")
    )
    launcher._directory_has_any_file = lambda *_args: (_ for _ in ()).throw(
        PermissionError("scan denied")
    )
    now = [100.0]
    service = WebStateService(
        launcher,
        clock=lambda: now[0],
        error_log_interval=30,
    )

    first = service.runtime_state()
    second = service.runtime_state()

    assert first["backend_ready"] is True
    assert first["yatori_version"] == launcher.YATORI_DISPLAY_VERSION
    assert first["installed"] == {"yatori": False, "autovisor": False}
    assert first["qb_stats"] == EMPTY_QB_STATS
    assert any("题库统计" in issue for issue in first["state_issues"])
    assert len(launcher.logs) == 4
    assert len(second["state_issues"]) == 4
    assert len(launcher.logs) == 4

    now[0] += 31
    service.runtime_state()
    assert len(launcher.logs) == 8


def test_runtime_flags_are_copied_while_holding_the_shared_lock(tmp_path):
    launcher = _Launcher(tmp_path)

    class RecordingLock:
        def __init__(self):
            self.entered = 0
            self.exited = 0

        def __enter__(self):
            self.entered += 1
            return self

        def __exit__(self, *_args):
            self.exited += 1

    lock = RecordingLock()
    launcher._runtime_lock = lock

    state = WebStateService(launcher).runtime_state()

    assert state["running"]["yatori"] is True
    assert lock.entered == 1
    assert lock.exited == 1


def test_initial_state_delegates_configuration_without_hiding_failures(tmp_path):
    launcher = _Launcher(tmp_path)
    launcher.get_web_preferences = lambda: {"autoStart": True}
    launcher._load_yatori_config_data = lambda: {"users": [{"account": "u"}]}
    launcher._load_autovisor_config_data = lambda: {"accounts": [{"username": "a"}]}
    launcher.get_qb_settings_from_web = lambda: {"port": 8084}

    state = WebStateService(launcher).initial_state()

    assert state["runtime"]["backend_ready"] is True
    assert state["preferences"] == {"autoStart": True}
    assert state["settings"]["yatori"]["users"][0]["account"] == "u"
    assert state["settings"]["autovisor"]["accounts"][0]["username"] == "a"
    assert state["settings"]["questionbank"]["port"] == 8084
