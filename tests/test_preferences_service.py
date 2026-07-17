import json
import tempfile
from pathlib import Path

from src.preferences_service import PreferencesService


def test_load_merges_saved_values_with_defaults():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "preferences.json"
        path.write_text(
            json.dumps({"theme": "light", "customFutureKey": 3}),
            encoding="utf-8",
        )

        service = PreferencesService(path)

        assert service.get()["theme"] == "light"
        assert service.get()["notifyOnError"] is True
        assert service.get()["customFutureKey"] == 3


def test_damaged_file_falls_back_without_overwriting_source(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text("{damaged", encoding="utf-8")
    logs = []

    service = PreferencesService(path, log=logs.append)

    assert service.get()["theme"] == "dark"
    assert path.read_text(encoding="utf-8") == "{damaged"
    assert any("加载启动器偏好失败" in message for message in logs)


def test_write_failure_restores_values_without_running_side_effects(tmp_path):
    calls = []

    def fail_write(_path, _values):
        raise OSError("disk full")

    service = PreferencesService(
        tmp_path / "preferences.json",
        writer=fail_write,
        apply_side_effects=lambda payload: calls.append(payload),
    )
    alias = service.values

    result = service.update({"autoStart": True})

    assert result["ok"] is False
    assert result["preferences"]["autoStart"] is False
    assert service.values is alias
    assert calls == []


def test_side_effect_failure_restores_file_values_and_runtime_effects(tmp_path):
    writes = []
    rollbacks = []

    def record_write(_path, values):
        writes.append(dict(values))

    service = PreferencesService(
        tmp_path / "preferences.json",
        writer=record_write,
        apply_side_effects=lambda _payload: ["开机自动启动未能应用"],
        rollback_side_effects=lambda previous, payload: rollbacks.append(
            (dict(previous), dict(payload))
        ),
    )
    previous = service.get()

    result = service.update({"autoStart": True})

    assert result["ok"] is False
    assert service.enabled("autoStart") is False
    assert writes[0]["autoStart"] is True
    assert writes[1]["autoStart"] is False
    assert rollbacks == [(previous, {"autoStart": True})]


def test_successful_update_persists_and_returns_detached_copy(tmp_path):
    writes = []
    service = PreferencesService(
        tmp_path / "preferences.json",
        writer=lambda _path, values: writes.append(dict(values)),
    )

    result = service.update({"soundEnabled": False})
    result["preferences"]["soundEnabled"] = True

    assert result["ok"] is True
    assert service.enabled("soundEnabled", True) is False
    assert writes[-1]["soundEnabled"] is False
