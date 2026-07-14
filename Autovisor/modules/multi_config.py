# encoding=utf-8
"""
多账号配置管理模块
支持从 configs.ini 读取多个账号配置
"""
import configparser
import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class AccountConfig:
    """单个账号配置"""
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
    window_width: int = 1400
    window_height: int = 800
    course_urls: List[str] = field(default_factory=list)
    cookies_file: str = ""


class MultiAccountConfig:
    """多账号配置管理器"""
    
    def __init__(self, config_path: str = "configs.ini"):
        self.config_path = config_path
        self._config = configparser.ConfigParser()
        self._read_config()
        self.accounts: List[AccountConfig] = []
        self._parse_accounts()
    
    def _read_config(self) -> None:
        """读取配置文件"""
        try:
            self._config.read(self.config_path, encoding='utf-8')
        except UnicodeDecodeError:
            self._config.read(self.config_path, encoding='gbk')
    
    def _parse_accounts(self) -> None:
        """解析所有账号配置"""
        # 获取所有小节
        sections = self._config.sections()
        
        # 查找所有账号配置小节 (user-account, user-account-1, user-account-2, ...)
        account_sections = [s for s in sections if s.startswith('user-account')]
        
        # 如果没有带序号的账号配置，使用默认的 user-account
        if not any(s != 'user-account' for s in account_sections):
            account_sections = ['user-account']
        
        # 排序，确保顺序正确
        def sort_key(s):
            if s == 'user-account':
                return 0
            try:
                return int(s.split('-')[-1])
            except ValueError:
                return 999
        
        account_sections.sort(key=sort_key)
        
        # 解析每个账号
        for idx, section in enumerate(account_sections, 1):
            account = self._parse_single_account(idx, section)
            if account.username or account.course_urls:  # 只添加有效账号
                self.accounts.append(account)
        
        # 如果没有解析到任何账号，创建一个空账号
        if not self.accounts:
            self.accounts.append(AccountConfig(account_id=1))
    
    def _parse_single_account(self, account_id: int, section: str) -> AccountConfig:
        """解析单个账号配置"""
        account = AccountConfig(account_id=account_id)
        
        # 账号信息
        if self._config.has_section(section):
            account.username = self._get_raw(section, 'username', '')
            account.password = self._get_raw(section, 'password', '')
        
        # 浏览器选项（全局或账号特定）
        browser_section = f'browser-option-{account_id}' if self._config.has_section(f'browser-option-{account_id}') else 'browser-option'
        if self._config.has_section(browser_section):
            account.driver = self._get_raw(browser_section, 'driver', 'edge').lower()
            account.exe_path = self._get_raw(browser_section, 'EXE_PATH', '')
        
        # 脚本选项（全局或账号特定）
        script_section = f'script-option-{account_id}' if self._config.has_section(f'script-option-{account_id}') else 'script-option'
        if self._config.has_section(script_section):
            account.enable_auto_captcha = self._get_bool(script_section, 'enableAutoCaptcha', True)
            account.enable_hide_window = self._get_bool(script_section, 'enableHideWindow', False)
        
        # 课程选项（全局或账号特定）
        course_section = f'course-option-{account_id}' if self._config.has_section(f'course-option-{account_id}') else 'course-option'
        if self._config.has_section(course_section):
            account.sound_off = self._get_bool(course_section, 'soundOff', True)
            account.limit_max_time = self._get_float(course_section, 'limitMaxTime', 30.0)
            account.limit_speed = self._get_float(course_section, 'limitSpeed', 1.0)
            account.window_width = self._get_int(course_section, 'windowWidth', 1400)
            account.window_height = self._get_int(course_section, 'windowHeight', 800)
        
        # 课程链接（账号特定或全局）
        course_url_section = f'course-url-{account_id}' if self._config.has_section(f'course-url-{account_id}') else 'course-url'
        account.course_urls = self._get_course_urls(course_url_section)
        
        # Cookies 文件路径（每个账号独立）
        account.cookies_file = f"res/cookies_{account_id}.json"
        
        return account
    
    def _get_raw(self, section: str, option: str, default: str = '') -> str:
        """获取原始字符串值"""
        try:
            return self._config.get(section, option, raw=True)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default
    
    def _get_bool(self, section: str, option: str, default: bool = False) -> bool:
        """获取布尔值"""
        try:
            value = self._config.get(section, option, raw=True).lower()
            return value == 'true'
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default
    
    def _get_float(self, section: str, option: str, default: float = 0.0) -> float:
        """获取浮点数值"""
        try:
            return float(self._config.get(section, option, raw=True))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default
    
    def _get_int(self, section: str, option: str, default: int = 0) -> int:
        """获取整数值"""
        try:
            return int(self._config.get(section, option, raw=True))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default
    
    def _get_course_urls(self, section: str) -> List[str]:
        """获取课程URL列表"""
        urls = []
        url_pattern = re.compile(r"https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]")
        
        if not self._config.has_section(section):
            return urls
        
        options = self._config.options(section)
        for option in options:
            if option.startswith('URL'):
                url = self._config.get(section, option, raw=True).strip()
                if url and url_pattern.match(url):
                    urls.append(url)
        
        return urls
    
    def get_account_count(self) -> int:
        """获取账号数量"""
        return len(self.accounts)
    
    def get_account(self, account_id: int) -> AccountConfig:
        """获取指定账号配置"""
        for account in self.accounts:
            if account.account_id == account_id:
                return account
        return None


def generate_multi_account_config_example() -> str:
    """生成多账号配置示例"""
    return '''; ========== Autovisor 多账号配置文件 ==========
; 支持多账号同时刷课，每个账号独立配置
; 格式：user-account-数字，数字从1开始

; ========== 账号 1 ==========
[user-account-1]
username = 账号1
password = 密码1

[browser-option-1]
driver = Chrome
EXE_PATH = 

[script-option-1]
enableAutoCaptcha = True
enableHideWindow = False

[course-option-1]
limitMaxTime = 30
limitSpeed = 1.0
soundOff = True
windowWidth = 1400
windowHeight = 800

[course-url-1]
URL1 = https://studyvideoh5.zhihuishu.com/studyVideoh5/...

; ========== 账号 2 ==========
[user-account-2]
username = 账号2
password = 密码2

[browser-option-2]
driver = Edge
EXE_PATH = 

[script-option-2]
enableAutoCaptcha = True
enableHideWindow = True

[course-option-2]
limitMaxTime = 60
limitSpeed = 1.5
soundOff = True
windowWidth = 1400
windowHeight = 800

[course-url-2]
URL1 = https://studyvideoh5.zhihuishu.com/studyVideoh5/...

; ========== 账号 3 (可选) ==========
; [user-account-3]
; username = 账号3
; password = 密码3
; ...
'''


if __name__ == "__main__":
    # 测试配置解析
    config = MultiAccountConfig("../configs.ini")
    print(f"解析到 {config.get_account_count()} 个账号")
    for acc in config.accounts:
        print(f"\n账号 {acc.account_id}:")
        print(f"  用户名: {acc.username}")
        print(f"  浏览器: {acc.driver}")
        print(f"  课程数: {len(acc.course_urls)}")
