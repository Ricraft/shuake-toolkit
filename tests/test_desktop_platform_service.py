import os
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.desktop_platform_service import DesktopPlatformService


def build_service(tmp_path, **overrides):
    logs = overrides.pop("logs", [])
    service = DesktopPlatformService(
        str(tmp_path),
        str(tmp_path / "统一启动器.py"),
        log=logs.append,
        platform_name=overrides.pop("platform_name", "nt"),
        environment=overrides.pop("environment", {}),
        executable=overrides.pop("executable", r"C:\Python\python.exe"),
        frozen=overrides.pop("frozen", False),
        **overrides,
    )
    return service, logs


def test_windows_auto_start_is_created_and_removed_atomically(tmp_path):
    appdata = tmp_path / "appdata"
    startup_dir = (
        appdata
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )
    startup_dir.mkdir(parents=True)
    service, logs = build_service(
        tmp_path,
        environment={"APPDATA": str(appdata)},
    )

    assert service.set_windows_auto_start(True) is True

    startup_file = startup_dir / "统一刷课启动器.bat"
    content = startup_file.read_text(encoding="utf-8")
    assert f'cd /d "{tmp_path}"' in content
    assert r'"C:\Python\python.exe"' in content
    assert f'"{tmp_path / "统一启动器.py"}"' in content
    assert logs[-1] == "已启用开机自动启动"

    assert service.set_windows_auto_start(False) is True
    assert not startup_file.exists()
    assert logs[-1] == "已关闭开机自动启动"


def test_auto_start_rejects_missing_windows_startup_directory(tmp_path):
    service, logs = build_service(
        tmp_path,
        environment={"APPDATA": str(tmp_path / "missing")},
    )

    assert service.set_windows_auto_start(True) is False
    assert "未找到 Windows 启动目录" in logs[-1]


def test_runtime_log_cleanup_only_removes_expired_files(tmp_path):
    old_time = datetime(2026, 7, 1, 12, 0, 0)
    service, logs = build_service(
        tmp_path,
        now=lambda: datetime(2026, 7, 28, 12, 0, 0),
    )
    log_dir = tmp_path / "Autovisor" / "logs"
    log_dir.mkdir(parents=True)
    expired = log_dir / "expired.log"
    recent = log_dir / "recent.log"
    expired.write_text("old", encoding="utf-8")
    recent.write_text("new", encoding="utf-8")
    os.utime(expired, (old_time.timestamp(), old_time.timestamp()))
    recent_time = service.now() - timedelta(days=1)
    os.utime(recent, (recent_time.timestamp(), recent_time.timestamp()))

    removed = service.clean_old_runtime_logs(days=7)

    assert removed == 1
    assert not expired.exists()
    assert recent.exists()
    assert logs[-1] == "已自动清理 1 个 7 天前的日志文件"


def test_shutdown_is_not_marked_pending_when_windows_rejects_command(tmp_path):
    commands = []

    def reject(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=5)

    service, _logs = build_service(tmp_path, command_runner=reject)

    result = service.schedule_shutdown(delay_seconds=60)

    assert result["ok"] is False
    assert "返回码: 5" in result["message"]
    assert service.shutdown_pending is False
    assert commands[0][0][:4] == ["shutdown", "/s", "/t", "60"]


def test_shutdown_is_marked_pending_after_windows_accepts_command(tmp_path):
    def accept(_command, **_kwargs):
        return SimpleNamespace(returncode=0)

    service, _logs = build_service(tmp_path, command_runner=accept)

    result = service.schedule_shutdown(delay_seconds=45)

    assert result == {"ok": True, "message": "计算机将在 45 秒后关闭"}
    assert service.shutdown_pending is True


def test_cancel_shutdown_only_clears_pending_after_system_accepts(tmp_path):
    return_codes = iter((5, 0))

    def runner(_command, **_kwargs):
        return SimpleNamespace(returncode=next(return_codes))

    service, _logs = build_service(tmp_path, command_runner=runner)
    service.shutdown_pending = True

    rejected = service.cancel_shutdown()
    assert rejected["ok"] is False
    assert service.shutdown_pending is True

    accepted = service.cancel_shutdown()
    assert accepted == {"ok": True, "message": "已取消关机"}
    assert service.shutdown_pending is False


def test_feedback_sound_failure_is_logged_once(tmp_path):
    class BrokenSound:
        MB_ICONHAND = 1
        MB_OK = 2

        @staticmethod
        def MessageBeep(_value):
            raise OSError("audio unavailable")

    service, logs = build_service(tmp_path, sound_module=BrokenSound)

    assert service.play_feedback_sound(enabled=True) is False
    assert service.play_feedback_sound(enabled=True, error=True) is False

    assert len(logs) == 1
    assert "audio unavailable" in logs[0]
