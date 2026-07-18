import importlib.util
import sys
from pathlib import Path


_AUTOVISOR_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AUTOVISOR_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "autovisor_practice_entry",
    _AUTOVISOR_ROOT / "Practice_Mode.py",
)
entry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(entry)

sys.path.remove(str(_AUTOVISOR_ROOT))


class _Logger:
    def __init__(self):
        self.errors = []
        self.logs = []
        self.saved = []

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def write_log(self, message):
        self.logs.append(message)

    def save(self, **kwargs):
        self.saved.append(kwargs)


def test_practice_entry_forwards_account_and_returns_success(monkeypatch):
    received = []

    async def successful_main(account_id=None):
        received.append(account_id)

    logger = _Logger()
    monkeypatch.setattr(entry, "main", successful_main)
    monkeypatch.setattr(entry, "Logger", lambda: logger)

    assert entry.run(account_id=9) == 0
    assert received == [9]
    assert logger.saved == [{"inform": True}]


def test_practice_entry_returns_failure_for_unhandled_exception(monkeypatch):
    async def failing_main(account_id=None):
        raise RuntimeError(f"account {account_id} failed")

    logger = _Logger()
    monkeypatch.setattr(entry, "main", failing_main)
    monkeypatch.setattr(entry, "Logger", lambda: logger)

    assert entry.run(account_id=7) == 1
    assert any("account 7 failed" in message for message in logger.errors)
    assert logger.logs
    assert logger.saved == [{"inform": True}]
