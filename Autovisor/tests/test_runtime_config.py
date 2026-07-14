# encoding=utf-8

import sys
from pathlib import Path

import pytest


AUTOVISOR_ROOT = Path(__file__).resolve().parent.parent
_AUTOVISOR_ROOT = str(AUTOVISOR_ROOT)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.configs import Config
from modules import tasks as task_module
from modules.multi_account_runner import MultiAccountManager
from modules.multi_config import MultiAccountConfig

sys.path.remove(_AUTOVISOR_ROOT)


def _write_config(path: Path, speed: float = 1.5) -> None:
    path.write_text(
        f"""
[browser-option]
driver = Chrome
windowWidth = 999

[script-option]
enableAutoCaptcha = False
enableHideWindow = False

[course-option]
limitMaxTime = 20
limitSpeed = 1.0
soundOff = False
windowWidth = 1440
windowHeight = 810

[user-account-2]
username = account2
password = secret2

[course-option-2]
limitSpeed = {speed}

[course-url-2]
URL1 = https://studyvideoh5.zhihuishu.com/studyVideoh5/course2

[user-account-5]
username = account5
password = secret5

[course-url-5]
URL1 = https://hike.zhihuishu.com/course5

[user-account-9]
username = account9
password = secret9

[course-url-9]
URL1 = https://fusioncourseh5.zhihuishu.com/course9
""".strip(),
        encoding="utf-8",
    )


def test_numbered_account_uses_shared_defaults_and_isolated_cookie(tmp_path):
    config_path = tmp_path / "configs.ini"
    _write_config(config_path)

    config = Config(str(config_path), account_id=2)

    assert config.username == "account2"
    assert config.driver == "chrome"
    assert config.enableAutoCaptcha is False
    assert config.soundOff is False
    assert config.windowWidth == 1440
    assert config.windowHeight == 810
    assert config.limitSpeed == 1.5
    assert config.cookies_file == "res/cookies_2.json"
    assert config.course_urls == [
        "https://studyvideoh5.zhihuishu.com/studyVideoh5/course2"
    ]


def test_single_entry_selects_first_numbered_account_when_base_is_absent(tmp_path):
    config_path = tmp_path / "configs.ini"
    _write_config(config_path)

    config = Config(str(config_path))

    assert config.account_id == 2
    assert config.username == "account2"
    assert config.cookies_file == "res/cookies_2.json"


def test_live_course_settings_stay_on_selected_account(tmp_path):
    config_path = tmp_path / "configs.ini"
    _write_config(config_path, speed=1.25)
    config = Config(str(config_path), account_id=2)
    assert config.limitSpeed == 1.25

    _write_config(config_path, speed=2.0)
    assert config.limitSpeed == 2.0


def test_multi_config_preserves_numbered_account_ids(tmp_path):
    config_path = tmp_path / "configs.ini"
    _write_config(config_path)

    config = MultiAccountConfig(str(config_path))

    assert [account.account_id for account in config.accounts] == [2, 5, 9]
    assert config.get_account(5).cookies_file == "res/cookies_5.json"


def test_multi_config_includes_unnumbered_first_account(tmp_path):
    config_path = tmp_path / "configs.ini"
    config_path.write_text(
        """
[user-account]
username = first
password = first-secret
[user-account-2]
username = second
password = second-secret
[browser-option]
driver = Chrome
[script-option]
enableAutoCaptcha = False
enableHideWindow = False
[course-option]
limitMaxTime = 30
limitSpeed = 1.0
soundOff = True
[course-url]
URL1 = https://studyvideoh5.zhihuishu.com/first
[course-url-2]
URL1 = https://studyvideoh5.zhihuishu.com/second
""".strip(),
        encoding="utf-8",
    )

    config = MultiAccountConfig(str(config_path))

    assert [account.account_id for account in config.accounts] == [1, 2]
    assert [account.username for account in config.accounts] == ["first", "second"]


class _FinishedProcess:
    def __init__(self, context, *, target, args, name):
        self.context = context
        self.target = target
        self.args = args
        self.name = name
        self.exitcode = 0
        self.started = False

    def start(self):
        self.started = True
        self.context.started_account_ids.append(self.args[1])

    def is_alive(self):
        return False

    def join(self):
        return None

    def terminate(self):
        self.exitcode = -1


class _ImmediateContext:
    def __init__(self):
        self.started_account_ids = []

    def Process(self, **kwargs):
        return _FinishedProcess(self, **kwargs)


def test_concurrency_limit_does_not_skip_later_accounts(tmp_path):
    config_path = tmp_path / "configs.ini"
    _write_config(config_path)
    context = _ImmediateContext()
    manager = MultiAccountManager(
        str(config_path),
        process_context=context,
        poll_interval=0,
        startup_delay=0,
    )

    exit_codes = manager.run_all(max_concurrent=2)

    assert context.started_account_ids == [2, 5, 9]
    assert exit_codes == {2: 0, 5: 0, 9: 0}


def test_invalid_concurrency_limit_is_rejected(tmp_path):
    config_path = tmp_path / "configs.ini"
    _write_config(config_path)
    manager = MultiAccountManager(
        str(config_path), process_context=_ImmediateContext(), startup_delay=0
    )

    with pytest.raises(ValueError, match="大于等于 1"):
        manager.run_all(max_concurrent=0)


def test_question_bank_query_uses_configured_endpoint(monkeypatch):
    captured = {}

    class Response:
        status = 200

        @staticmethod
        def read():
            return b'{"success": true, "data": [{"answer": "A", "is_ai": false}]}'

        @staticmethod
        def close():
            return None

    class Connection:
        def __init__(self, host, port, timeout):
            captured.update(host=host, port=port, timeout=timeout)

        def request(self, method, path, body, headers):
            captured.update(method=method, path=path, body=body, headers=headers)

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(task_module, "QB_URL", "http://127.0.0.1:19090/custom/query?v=1")
    monkeypatch.setattr(task_module.http.client, "HTTPConnection", Connection)

    answer, is_ai = task_module.query_question_bank("测试题")

    assert (answer, is_ai) == ("A", False)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 19090
    assert captured["path"] == "/custom/query?v=1"
