import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules import practice_mode

sys.path.remove(_AUTOVISOR_ROOT)


class _PlaywrightContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False


def test_practice_main_loads_selected_account(monkeypatch):
    captured = {}

    class FakeConfig:
        def __init__(self, config_path, account_id=None):
            captured["config"] = (config_path, account_id)
            self.enableAutoCaptcha = False
            self.username = "selected-user"
            self.password = "selected-password"

    async def fake_init_page(_playwright, config):
        captured["init_config"] = config
        return object(), object(), object()

    async def fake_auto_login(_context, _page, config, _modules):
        captured["login_config"] = config
        return True

    async def fake_practice_loop(_page, _context, config):
        captured["loop_config"] = config

    monkeypatch.setattr(practice_mode, "Config", FakeConfig)
    monkeypatch.setattr(
        practice_mode,
        "async_playwright",
        lambda: _PlaywrightContext(),
    )
    monkeypatch.setattr(practice_mode, "init_page", fake_init_page)
    monkeypatch.setattr(practice_mode, "auto_login", fake_auto_login)
    monkeypatch.setattr(practice_mode, "practice_loop", fake_practice_loop)
    monkeypatch.setattr(
        practice_mode.logger,
        "configure",
        lambda account_id, **kwargs: captured.setdefault(
            "logger",
            (account_id, kwargs),
        ),
    )

    asyncio.run(
        practice_mode.main(
            account_id=7,
            config_path="selected-config.ini",
        )
    )

    self_config = captured["init_config"]
    assert captured["config"] == ("selected-config.ini", 7)
    assert captured["logger"] == (7, {"force": True, "clear": True})
    assert captured["login_config"] is self_config
    assert captured["loop_config"] is self_config


def test_practice_login_uses_account_specific_cookie_file(monkeypatch):
    captured = {}

    async def fake_login(context, page, config, logger, **kwargs):
        captured.update(
            context=context,
            page=page,
            config=config,
            logger=logger,
            kwargs=kwargs,
        )
        return True

    config = SimpleNamespace(cookies_file="res/cookies_7.json")
    monkeypatch.setattr(practice_mode, "login_to_zhihuishu", fake_login)

    result = asyncio.run(
        practice_mode.auto_login("context", "page", config, ["slider"])
    )

    assert result is True
    assert captured["kwargs"]["cookie_path"] == "res/cookies_7.json"
