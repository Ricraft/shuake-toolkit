import json
import tempfile
import threading
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


def test_nested_defaults_and_returned_snapshots_are_fully_detached(tmp_path):
    first = PreferencesService(tmp_path / "first.json")
    second = PreferencesService(tmp_path / "second.json")
    first.values["achievements"]["first"] = {"unlockedAt": "now"}

    payload = {
        "tianyiChatHistory": [{"role": "user", "text": "hello"}],
        "achievements": {"saved": {"unlockedAt": "today"}},
    }
    result = second.update(payload)
    payload["tianyiChatHistory"][0]["text"] = "mutated payload"
    result["preferences"]["achievements"]["saved"]["unlockedAt"] = "mutated result"
    snapshot = second.get()
    snapshot["tianyiChatHistory"][0]["text"] = "mutated snapshot"

    assert "first" not in second.get()["achievements"]
    assert second.get()["tianyiChatHistory"][0]["text"] == "hello"
    assert second.get()["achievements"]["saved"]["unlockedAt"] == "today"


def test_writer_receives_a_detached_snapshot(tmp_path):
    def mutating_writer(_path, values):
        values["achievements"]["writer"] = {"unlockedAt": "changed"}

    service = PreferencesService(
        tmp_path / "preferences.json",
        writer=mutating_writer,
    )

    assert service.save() is True
    assert "writer" not in service.get()["achievements"]


def test_failed_transaction_cannot_erase_a_later_concurrent_update(tmp_path):
    first_effect_started = threading.Event()
    release_first_effect = threading.Event()
    second_done = threading.Event()
    results = {}

    def apply_side_effects(payload):
        if "autoStart" not in payload:
            return []
        first_effect_started.set()
        assert release_first_effect.wait(timeout=2)
        return ["simulated failure"]

    service = PreferencesService(
        tmp_path / "preferences.json",
        apply_side_effects=apply_side_effects,
    )

    first = threading.Thread(
        target=lambda: results.update(
            first=service.update({"autoStart": True})
        )
    )

    def update_second():
        results["second"] = service.update({"theme": "light"})
        second_done.set()

    second = threading.Thread(target=update_second)
    first.start()
    assert first_effect_started.wait(timeout=2)
    second.start()
    assert not second_done.wait(timeout=0.05)
    release_first_effect.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results["first"]["ok"] is False
    assert results["second"]["ok"] is True
    assert service.get()["autoStart"] is False
    assert service.get()["theme"] == "light"


def test_readers_do_not_observe_a_provisional_transaction_value(tmp_path):
    effect_started = threading.Event()
    release_effect = threading.Event()
    reader_done = threading.Event()
    observed = {}

    def fail_after_pause(_payload):
        effect_started.set()
        assert release_effect.wait(timeout=2)
        return ["simulated failure"]

    service = PreferencesService(
        tmp_path / "preferences.json",
        apply_side_effects=fail_after_pause,
    )
    updater = threading.Thread(
        target=lambda: service.update({"autoStart": True})
    )

    def read_preferences():
        observed.update(service.get())
        reader_done.set()

    reader = threading.Thread(target=read_preferences)
    updater.start()
    assert effect_started.wait(timeout=2)
    reader.start()
    assert not reader_done.wait(timeout=0.05)
    release_effect.set()
    updater.join(timeout=2)
    reader.join(timeout=2)

    assert not updater.is_alive()
    assert not reader.is_alive()
    assert observed["autoStart"] is False
