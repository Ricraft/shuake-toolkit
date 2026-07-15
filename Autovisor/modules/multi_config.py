# encoding=utf-8
"""多账号配置发现与展示。

实际字段解析由 :class:`modules.configs.Config` 统一完成，避免单账号和多账号
对同一份 INI 产生不同解释。
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from typing import List, Optional

from modules.configs import Config


@dataclass(frozen=True)
class AccountConfig:
    """供多账号编排器展示和序列化的账号配置快照。"""

    account_id: int = 1
    username: str = ""
    password: str = ""
    driver: str = "edge"
    exe_path: str = ""
    enable_auto_captcha: bool = True
    enable_hide_window: bool = False
    sound_off: bool = True
    limit_max_time: float = 30.0
    limit_speed: float = 1.0
    window_width: int = 1600
    window_height: int = 900
    course_urls: List[str] = field(default_factory=list)
    cookies_file: str = ""

    @classmethod
    def from_runtime(cls, config: Config, account_id: int) -> "AccountConfig":
        return cls(
            account_id=account_id,
            username=config.username,
            password=config.password,
            driver=config.driver,
            exe_path=config.exe_path,
            enable_auto_captcha=config.enableAutoCaptcha,
            enable_hide_window=config.enableHideWindow,
            sound_off=config.soundOff,
            limit_max_time=config.limitMaxTime,
            limit_speed=config.limitSpeed,
            window_width=config.windowWidth,
            window_height=config.windowHeight,
            course_urls=list(config.course_urls),
            cookies_file=config.cookies_file,
        )


class MultiAccountConfig:
    """发现 ``user-account-N``，并为每个账号创建统一运行配置。"""

    _ACCOUNT_SECTION = re.compile(r"^user-account-(\d+)$")

    def __init__(self, config_path: str = "configs.ini"):
        self.config_path = config_path
        self._config = configparser.ConfigParser(interpolation=None)
        self._read_config()
        self.accounts: List[AccountConfig] = []
        self._parse_accounts()

    def _read_config(self) -> None:
        try:
            loaded = self._config.read(self.config_path, encoding="utf-8")
        except UnicodeDecodeError:
            self._config.clear()
            loaded = self._config.read(self.config_path, encoding="gbk")
        if not loaded:
            raise FileNotFoundError(self.config_path)

    def _discover_account_ids(self) -> list[int]:
        numbered_ids = []
        for section in self._config.sections():
            match = self._ACCOUNT_SECTION.fullmatch(section)
            if match:
                numbered_ids.append(int(match.group(1)))

        if numbered_ids:
            account_ids = set(numbered_ids)
            # 统一启动器使用未编号小节表示第一个账号、-2 表示第二个账号。
            # 如果已经存在显式 -1，则未编号小节只作为它的公共回退。
            if self._config.has_section("user-account") and 1 not in account_ids:
                account_ids.add(1)
            return sorted(account_ids)
        if self._config.has_section("user-account"):
            return [1]
        return []

    def _parse_accounts(self) -> None:
        for account_id in self._discover_account_ids():
            runtime = Config(self.config_path, account_id=account_id)
            account = AccountConfig.from_runtime(runtime, account_id)
            if account.username or account.course_urls:
                self.accounts.append(account)

    def get_account_count(self) -> int:
        return len(self.accounts)

    def get_account(self, account_id: int) -> Optional[AccountConfig]:
        return next(
            (account for account in self.accounts if account.account_id == account_id), None
        )


def generate_multi_account_config_example() -> str:
    return """; ========== Autovisor 多账号配置示例 ==========
; 带编号账号使用完全相同的主程序流程；未编号选项可作为公共回退。

[browser-option]
driver = Chrome
EXE_PATH =

[script-option]
enableAutoCaptcha = True
enableHideWindow = False

[course-option]
limitMaxTime = 30
limitSpeed = 1.0
soundOff = True
windowWidth = 1600
windowHeight = 900

[user-account-1]
username =
password =

[course-url-1]
URL1 =

[user-account-2]
username =
password =

[course-url-2]
URL1 =
"""


if __name__ == "__main__":
    config = MultiAccountConfig("../configs.ini")
    print(f"解析到 {config.get_account_count()} 个账号")
    for account in config.accounts:
        print(f"账号 {account.account_id}: {account.username or '(未填写)'}")
