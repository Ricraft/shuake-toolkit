# encoding=utf-8
"""Autovisor 运行配置。

单账号和多账号入口必须使用同一个配置对象，避免后台任务重新读取默认账号配置。
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Optional


_NUMBERED_SECTION_RE = re.compile(r"^(?P<base>.+)-(?P<account_id>\d+)$")
_COURSE_URL_RE = re.compile(
    r"https://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]"
)


class Config:
    """一个账号的一次运行配置。

    ``account_id`` 为空时读取传统单账号小节；指定账号时优先读取带编号小节，
    并回退到未编号的全局选项。这让单账号与多账号共享完全相同的执行路径。
    """

    def __init__(self, config_path: Optional[str] = None, account_id: Optional[int] = None):
        self.config_path = str(config_path) if config_path else None
        self.requested_account_id = account_id
        self.account_id: Optional[int] = account_id
        self._config = configparser.ConfigParser(interpolation=None)

        # 安全默认值也供 installer 等只使用镜像配置的调用方使用。
        self.driver = "edge"
        self.username = ""
        self.password = ""
        self.exe_path = ""
        self.enableAutoCaptcha = True
        self.enableHideWindow = False
        self.soundOff = True
        self.windowWidth = 1600
        self.windowHeight = 900
        self.course_urls: list[str] = []
        self.cookies_file = "res/cookies.json"

        self._account_section = "user-account"
        self._browser_section = "browser-option"
        self._script_section = "script-option"
        self._course_section = "course-option"
        self._course_url_section = "course-url"

        if self.config_path:
            self._read_config()
            self._select_sections()
            self._load_values()

        # 登录
        self.login_url = "https://passport.zhihuishu.com/login"
        self.block_js = "return document.getElementsByClassName('yidun_jigsaw')[0].src"
        self.bg_js = "return document.getElementsByClassName('yidun_bg-img')[0].src"
        # 弹窗
        self.pop_js = "document.getElementsByClassName('iconfont iconguanbi')[0].click();"
        self.close_ques = (
            "document.dispatchEvent(new KeyboardEvent('keydown', "
            "{bubbles: true, keyCode: 27 }));"
        )
        # 视频元素修改
        self.remove_pause = "document.querySelector('video').pause = ()=>{}"
        self.play_video = "const video = document.querySelector('video');video.play();"
        self.volume_none = "document.querySelector('video').volume=0;"
        self.set_none_icon = "document.querySelector('.volumeBox').classList.add('volumeNone')"
        self.reset_curtime = "document.querySelector('video').currentTime=0;"
        self.night_js = "document.getElementsByClassName('Patternbtn-div')[0].click()"

        self.mirrors = {
            "华为": "https://mirrors.huaweicloud.com/repository/pypi",
            "阿里": "https://mirrors.aliyun.com/pypi",
            "清华": "https://pypi.tuna.tsinghua.edu.cn",
            "官方": "https://pypi.org",
        }
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 "
                "Safari/537.36 Edg/134.0.0.0"
            )
        }

    def _read_config(self) -> None:
        """重新读取配置，确保被删除的选项不会残留在解析器中。"""
        if not self.config_path:
            return
        self._config.clear()
        try:
            loaded = self._config.read(self.config_path, encoding="utf-8")
        except UnicodeDecodeError:
            self._config.clear()
            loaded = self._config.read(self.config_path, encoding="gbk")
        if not loaded:
            raise FileNotFoundError(self.config_path)

    @staticmethod
    def _section_account_id(section: str, base: str) -> Optional[int]:
        match = _NUMBERED_SECTION_RE.fullmatch(section)
        if not match or match.group("base") != base:
            return None
        return int(match.group("account_id"))

    def _first_numbered_account_id(self) -> Optional[int]:
        ids = [
            account_id
            for section in self._config.sections()
            if (account_id := self._section_account_id(section, "user-account")) is not None
        ]
        return min(ids) if ids else None

    def _resolve_section(self, base: str, account_id: Optional[int]) -> str:
        if account_id is not None:
            numbered = f"{base}-{account_id}"
            if self._config.has_section(numbered):
                return numbered
        return base

    def _select_sections(self) -> None:
        selected_id = self.requested_account_id
        if selected_id is None and not self._config.has_section("user-account"):
            selected_id = self._first_numbered_account_id()
            self.account_id = selected_id

        self._account_section = self._resolve_section("user-account", selected_id)
        self._browser_section = self._resolve_section("browser-option", selected_id)
        self._script_section = self._resolve_section("script-option", selected_id)
        self._course_section = self._resolve_section("course-option", selected_id)
        self._course_url_section = self._resolve_section("course-url", selected_id)

    def _get(self, section: str, option: str, fallback: str = "") -> str:
        if self._config.has_option(section, option):
            return self._config.get(section, option, raw=True).strip()
        match = _NUMBERED_SECTION_RE.fullmatch(section)
        if match and self._config.has_option(match.group("base"), option):
            return self._config.get(match.group("base"), option, raw=True).strip()
        return fallback

    def _get_bool(self, section: str, option: str, fallback: bool) -> bool:
        try:
            value = self._get(section, option)
            if not value:
                return fallback
            return self._config.BOOLEAN_STATES[value.lower()]
        except (KeyError, ValueError):
            return fallback

    def _get_int(self, section: str, option: str, fallback: int) -> int:
        try:
            value = self._get(section, option)
            return int(value) if value else fallback
        except ValueError:
            return fallback

    def _load_values(self) -> None:
        self.driver = self._get(self._browser_section, "driver", "edge").lower() or "edge"
        self.username = self._get(self._account_section, "username")
        self.password = self._get(self._account_section, "password")
        self.exe_path = self._get(self._browser_section, "EXE_PATH")
        self.enableAutoCaptcha = self._get_bool(
            self._script_section, "enableAutoCaptcha", True
        )
        self.enableHideWindow = self._get_bool(
            self._script_section, "enableHideWindow", False
        )
        self.soundOff = self._get_bool(self._course_section, "soundOff", True)
        self.windowWidth = self._get_int(self._course_section, "windowWidth", 1600)
        self.windowHeight = self._get_int(self._course_section, "windowHeight", 900)
        self.course_urls = self.get_course_urls()

        default_cookie = (
            f"res/cookies_{self.account_id}.json"
            if self.account_id is not None
            else "res/cookies.json"
        )
        self.cookies_file = self._get(self._account_section, "cookiesFile", default_cookie)

    def get_driver(self) -> str:
        return self.driver

    def get_bool_field(self, section: str, option: str) -> bool:
        return self._get_bool(section, option, False)

    @staticmethod
    def _option_sort_key(option: str) -> tuple[str, int]:
        match = re.fullmatch(r"([^0-9]*)(\d+)", option)
        return (match.group(1), int(match.group(2))) if match else (option, 0)

    def get_course_urls(self) -> list[str]:
        if not self._config.has_section(self._course_url_section):
            return []

        course_urls = []
        options = sorted(
            self._config.options(self._course_url_section), key=self._option_sort_key
        )
        for option in options:
            if not option.lower().startswith("url"):
                continue
            course_url = self._get(self._course_url_section, option)
            if not course_url:
                continue
            if not _COURSE_URL_RE.fullmatch(course_url):
                print(f'"{course_url}"\n不是一个有效网址,将忽略该网址.')
                continue
            course_urls.append(course_url)
        return course_urls

    def _get_live_float(self, option: str, fallback: float) -> float:
        self._read_config()
        try:
            value = self._get(self._course_section, option)
            return float(value) if value else fallback
        except ValueError:
            return fallback

    @property
    def limitMaxTime(self) -> float:
        return self._get_live_float("limitMaxTime", 30.0)

    @property
    def limitSpeed(self) -> float:
        value = self._get_live_float("limitSpeed", 1.0)
        return value if value > 0 else 1.0

    @property
    def revise_speed(self) -> str:
        return f"document.querySelector('video').playbackRate={self.limitSpeed};"

    @property
    def revise_speed_name(self) -> str:
        return f'''document.querySelector(".speedBox span").innerText = "X {self.limitSpeed}";'''

    @property
    def config_directory(self) -> Path:
        return Path(self.config_path).resolve().parent if self.config_path else Path.cwd()
