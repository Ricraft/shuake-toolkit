"""Configuration defaults, normalization, and atomic persistence."""

from __future__ import annotations

import configparser
import importlib
import io
import re
from pathlib import Path
from typing import Callable, Iterable

from src.atomic_io import atomic_write_text


AUTOVISOR_SECTION_PREFIXES = (
    "user-account",
    "browser-option",
    "script-option",
    "course-option",
    "course-url",
)


def new_ini_parser() -> configparser.ConfigParser:
    """Create an INI parser that treats passwords and URLs literally."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    return parser


def read_ini_config(path: str | Path) -> configparser.ConfigParser:
    """Read an INI file using the encodings found in legacy distributions."""
    config_path = Path(path)
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        parser = new_ini_parser()
        try:
            with config_path.open("r", encoding=encoding) as handle:
                parser.read_file(handle)
            return parser
        except (UnicodeDecodeError, configparser.Error):
            continue

    parser = new_ini_parser()
    with config_path.open("r", encoding="utf-8", errors="replace") as handle:
        parser.read_file(handle)
    return parser


class ConfigService:
    """Own the non-UI part of Yatori and Autovisor configuration handling."""

    def __init__(
        self,
        yatori_path: Callable[[], str | Path],
        autovisor_path: Callable[[], str | Path],
        *,
        browser_finder: Callable[[str], str | None] | None = None,
        logger: Callable[[str], None] | None = None,
        speed_options: Iterable[str] = ("1.0", "1.25", "1.5", "1.8"),
    ):
        self._yatori_path = yatori_path
        self._autovisor_path = autovisor_path
        self._browser_finder = browser_finder or (lambda _name: None)
        self._logger = logger or (lambda _message: None)
        self.speed_options = tuple(speed_options)

    @staticmethod
    def as_int(value, default=0):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def as_float(value, default=0.0):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def split_lines(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [
            line.strip()
            for line in str(value or "").replace("\r", "").split("\n")
            if line.strip()
        ]

    @staticmethod
    def split_csv(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value or "").split(",") if item.strip()]

    def normalize_autovisor_speed(self, value, default="1.0") -> str:
        normalized = str(value or "").strip()
        return normalized if normalized in self.speed_options else default

    @staticmethod
    def validate_autovisor_accounts(accounts) -> str | None:
        for index, account in enumerate(accounts or [], start=1):
            if account.get("enable_hide_window") and (
                not str(account.get("username", "") or "").strip()
                or not str(account.get("password", "") or "")
            ):
                return f"Autovisor 账号 {index} 开启隐藏窗口时必须填写账号和密码"
        return None

    @staticmethod
    def default_yatori_user(index=None) -> dict:
        return {
            "accountType": "XUEXITONG",
            "url": "",
            "remarkName": f"账号{index}" if index else "",
            "account": "",
            "password": "",
            "isProxy": 0,
            "informEmails": [],
            "coursesCustom": {
                "studyTime": "",
                "cxNode": 3,
                "cxChapterTestSw": 1,
                "cxWorkSw": 1,
                "cxExamSw": 1,
                "shuffleSw": 0,
                "videoModel": 1,
                "autoExam": 0,
                "examAutoSubmit": 0,
                "includeCourses": [],
                "excludeCourses": [],
                "coursesSettings": [],
            },
        }

    @classmethod
    def default_yatori_config(cls) -> dict:
        return {
            "setting": {
                "basicSetting": {
                    "completionTone": 1,
                    "colorLog": 1,
                    "logOutFileSw": 1,
                    "logLevel": "INFO",
                    "logModel": 0,
                    "WebModel": 0,
                },
                "emailInform": {
                    "sw": 0,
                    "SMTPHost": "",
                    "SMTPPort": 0,
                    "userName": "",
                    "password": "",
                },
                "aiSetting": {
                    "aiType": "TONGYI",
                    "aiUrl": "",
                    "model": "",
                    "API_KEY": "",
                },
                "apiQueSetting": {"url": "http://127.0.0.1:8083/query"},
            },
            "users": [cls.default_yatori_user(1)],
        }

    def default_autovisor_account(self, index=1) -> dict:
        detected = self._browser_finder("chrome") or self._browser_finder("edge") or ""
        return {
            "account_id": index,
            "name": f"账号 {index}",
            "username": "",
            "password": "",
            "driver": "Chrome",
            "exe_path": detected,
            "enable_auto_captcha": True,
            "enable_hide_window": False,
            "limit_max_time": "30",
            "limit_speed": "1.0",
            "sound_off": True,
            "course_urls": [],
        }

    @staticmethod
    def _yaml():
        try:
            return importlib.import_module("yaml")
        except ImportError as exc:  # pragma: no cover - dependency check handles this in app
            raise RuntimeError("读写 Yatori 配置需要安装 PyYAML") from exc

    def load_yatori(self) -> dict:
        config = self.default_yatori_config()
        config_path = Path(self._yatori_path())
        if config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as handle:
                    loaded = self._yaml().safe_load(handle) or {}
                if isinstance(loaded, dict):
                    for key, value in loaded.items():
                        if key in config and isinstance(config[key], dict) and isinstance(value, dict):
                            config[key].update(value)
                        else:
                            config[key] = value
            except Exception as exc:
                self._logger(f"读取 Yatori 配置失败，已回退默认值: {exc}")

        setting = config.get("setting")
        if not isinstance(setting, dict):
            setting = {}
            config["setting"] = setting
        defaults = self.default_yatori_config()["setting"]
        for section_name in ("basicSetting", "emailInform", "aiSetting", "apiQueSetting"):
            section = setting.get(section_name)
            if not isinstance(section, dict):
                section = {}
                setting[section_name] = section
            for key, value in defaults[section_name].items():
                section.setdefault(key, value)

        users = config.get("users") if isinstance(config.get("users"), list) else []
        normalized_users = []
        default_user = self.default_yatori_user(0)
        for index, user in enumerate(users, start=1):
            if not isinstance(user, dict):
                continue
            user_data = dict(default_user)
            user_data["remarkName"] = user.get("remarkName", f"账号{index}")
            user_data.update({key: value for key, value in user.items() if key != "coursesCustom"})
            courses_custom = dict(default_user["coursesCustom"])
            loaded_custom = user.get("coursesCustom")
            if isinstance(loaded_custom, dict):
                courses_custom.update(loaded_custom)
            user_data["coursesCustom"] = courses_custom
            normalized_users.append(user_data)
        config["users"] = normalized_users or [self.default_yatori_user(1)]
        return config

    def save_yatori(self, config_data: dict) -> None:
        buffer = io.StringIO()
        self._yaml().safe_dump(config_data, buffer, allow_unicode=True, sort_keys=False)
        atomic_write_text(self._yatori_path(), buffer.getvalue())

    @staticmethod
    def extract_autovisor_index(section_name: str, prefix: str) -> int | None:
        if section_name == prefix:
            return 1
        if section_name.startswith(prefix + "-"):
            suffix = section_name[len(prefix) + 1 :]
            if suffix.isdigit() and int(suffix) > 0:
                return int(suffix)
        return None

    @staticmethod
    def sorted_url_keys(option_names) -> list[str]:
        def sort_key(name):
            match = re.search(r"(\d+)", name or "")
            return (0, int(match.group(1))) if match else (1, name)

        return sorted(option_names, key=sort_key)

    @staticmethod
    def _section(prefix: str, account_id: int) -> str:
        return prefix if account_id == 1 else f"{prefix}-{account_id}"

    @classmethod
    def _read_section(cls, parser, prefix: str, account_id: int) -> str:
        preferred = cls._section(prefix, account_id)
        if parser.has_section(preferred):
            return preferred
        explicit_first = f"{prefix}-1"
        if account_id == 1 and parser.has_section(explicit_first):
            return explicit_first
        if account_id > 1 and parser.has_section(prefix):
            return prefix
        return preferred

    @staticmethod
    def _getboolean(parser, section: str, option: str, fallback: bool) -> bool:
        try:
            return parser.getboolean(section, option, fallback=fallback)
        except (TypeError, ValueError):
            return fallback

    def load_autovisor(self) -> dict:
        parser = new_ini_parser()
        config_path = Path(self._autovisor_path())
        if config_path.exists():
            try:
                parser = read_ini_config(config_path)
            except Exception as exc:
                self._logger(f"读取 Autovisor 配置失败，已回退默认值: {exc}")
                parser = new_ini_parser()

        user_indices = set()
        fallback_indices = set()
        for section in parser.sections():
            for prefix in AUTOVISOR_SECTION_PREFIXES:
                index = self.extract_autovisor_index(section, prefix)
                if index:
                    fallback_indices.add(index)
                    if prefix == "user-account":
                        user_indices.add(index)
                    break
        indices = user_indices or fallback_indices
        if not indices:
            indices = {1}

        accounts = []
        for account_id in sorted(indices):
            account = self.default_autovisor_account(account_id)
            user_section = self._read_section(parser, "user-account", account_id)
            browser_section = self._read_section(parser, "browser-option", account_id)
            script_section = self._read_section(parser, "script-option", account_id)
            course_section = self._read_section(parser, "course-option", account_id)
            url_section = self._read_section(parser, "course-url", account_id)
            if parser.has_section(user_section):
                account["name"] = parser.get(user_section, "name", fallback=account["name"]).strip() or account["name"]
                account["username"] = parser.get(user_section, "username", fallback="").strip()
                account["password"] = parser.get(user_section, "password", fallback="")
            if parser.has_section(browser_section):
                account["driver"] = parser.get(browser_section, "driver", fallback=account["driver"]).strip() or account["driver"]
                account["exe_path"] = parser.get(browser_section, "EXE_PATH", fallback=account["exe_path"]).strip()
            if parser.has_section(script_section):
                account["enable_auto_captcha"] = self._getboolean(parser, script_section, "enableAutoCaptcha", account["enable_auto_captcha"])
                account["enable_hide_window"] = self._getboolean(parser, script_section, "enableHideWindow", account["enable_hide_window"])
            if parser.has_section(course_section):
                account["limit_max_time"] = parser.get(course_section, "limitMaxTime", fallback=account["limit_max_time"]).strip()
                account["limit_speed"] = parser.get(course_section, "limitSpeed", fallback=account["limit_speed"]).strip()
                account["sound_off"] = self._getboolean(parser, course_section, "soundOff", account["sound_off"])
            if parser.has_section(url_section):
                account["course_urls"] = [
                    value
                    for option in self.sorted_url_keys(parser.options(url_section))
                    if option.lower().startswith("url")
                    and (value := parser.get(url_section, option, fallback="").strip())
                ]
            accounts.append(account)

        return {
            "multi_mode": len(accounts) > 1,
            "browser_driver": accounts[0]["driver"] if accounts else "Chrome",
            "browser_path": accounts[0]["exe_path"] if accounts else "",
            "accounts": accounts or [self.default_autovisor_account(1)],
        }

    @staticmethod
    def _account_ids(accounts: list[dict]) -> list[int]:
        used = set()
        resolved = []
        for position, account in enumerate(accounts, start=1):
            try:
                candidate = int(account.get("account_id", position))
            except (TypeError, ValueError):
                candidate = position
            if candidate < 1 or candidate in used:
                candidate = 1
                while candidate in used:
                    candidate += 1
            used.add(candidate)
            resolved.append(candidate)
        return resolved

    @staticmethod
    def _ensure_section(parser: configparser.ConfigParser, section: str) -> None:
        if not parser.has_section(section):
            parser.add_section(section)

    def save_autovisor(self, config_data: dict) -> None:
        config_path = Path(self._autovisor_path())
        if config_path.exists():
            try:
                parser = read_ini_config(config_path)
            except Exception:
                parser = new_ini_parser()
        else:
            parser = new_ini_parser()

        accounts = config_data.get("accounts") or [self.default_autovisor_account(1)]
        account_ids = self._account_ids(accounts)
        desired_sections = {
            self._section(prefix, account_id)
            for account_id in account_ids
            for prefix in AUTOVISOR_SECTION_PREFIXES
        }
        if 1 not in account_ids:
            desired_sections.update(
                section
                for section in ("browser-option", "script-option", "course-option")
                if parser.has_section(section)
            )
        for section in list(parser.sections()):
            if any(
                section == prefix or section.startswith(prefix + "-")
                for prefix in AUTOVISOR_SECTION_PREFIXES
            ) and section not in desired_sections:
                parser.remove_section(section)

        shared_driver = str(config_data.get("browser_driver", "Chrome") or "Chrome")
        shared_path = str(config_data.get("browser_path", "") or "").strip()
        for account, account_id in zip(accounts, account_ids):
            user_section = self._section("user-account", account_id)
            browser_section = self._section("browser-option", account_id)
            script_section = self._section("script-option", account_id)
            course_section = self._section("course-option", account_id)
            url_section = self._section("course-url", account_id)
            for section in (user_section, browser_section, script_section, course_section, url_section):
                self._ensure_section(parser, section)

            parser.set(user_section, "name", str(account.get("name", f"账号 {account_id}")).strip() or f"账号 {account_id}")
            parser.set(user_section, "username", str(account.get("username", "")).strip())
            parser.set(user_section, "password", str(account.get("password", "")))
            parser.set(browser_section, "driver", str(account.get("driver") or shared_driver))
            parser.set(browser_section, "EXE_PATH", str(account.get("exe_path") or shared_path).strip())
            parser.set(script_section, "enableAutoCaptcha", "True" if account.get("enable_auto_captcha", True) else "False")
            parser.set(script_section, "enableHideWindow", "True" if account.get("enable_hide_window", False) else "False")
            parser.set(course_section, "limitMaxTime", str(self.as_int(account.get("limit_max_time", "30"), 30)))
            parser.set(course_section, "limitSpeed", str(self.as_float(account.get("limit_speed", "1.0"), 1.0)))
            parser.set(course_section, "soundOff", "True" if account.get("sound_off", True) else "False")

            for option in list(parser.options(url_section)):
                if option.lower().startswith("url"):
                    parser.remove_option(url_section, option)
            course_urls = self.split_lines(account.get("course_urls", []))
            if course_urls:
                for url_index, course_url in enumerate(course_urls, start=1):
                    parser.set(url_section, f"URL{url_index}", course_url)
            else:
                parser.set(url_section, "URL1", "")

        buffer = io.StringIO()
        parser.write(buffer)
        atomic_write_text(config_path, buffer.getvalue())
