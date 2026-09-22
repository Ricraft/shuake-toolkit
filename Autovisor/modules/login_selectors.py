"""智慧树新旧登录页的稳定 URL 与 DOM 契约。"""

from urllib.parse import urlencode

LOGIN_RETURN_URL = "https://onlineweb.zhihuishu.com/"
LOGIN_SERVICE_URL = (
    "https://onlineservice-api.zhihuishu.com/gateway/f/v1/login/gologin?"
    + urlencode({"fromurl": LOGIN_RETURN_URL})
)
LOGIN_URL = "https://login.zhihuishu.com/?" + urlencode(
    {
        "origin": "zhs",
        "service": LOGIN_SERVICE_URL,
    }
)
LOGIN_HOSTS = frozenset(
    {
        "login.zhihuishu.com",
        "passport.zhihuishu.com",
    }
)

MODERN_LOGIN_PANEL = "#login_center_app .login-container"
LEGACY_LOGIN_PANEL = ".wall-main"
LOGIN_PANEL = f"{MODERN_LOGIN_PANEL}, {LEGACY_LOGIN_PANEL}"

MODERN_USERNAME_INPUT = f'{MODERN_LOGIN_PANEL} input[name="mobile"]'
LEGACY_USERNAME_INPUT = "#lUsername"
USERNAME_INPUT = f"{MODERN_USERNAME_INPUT}, {LEGACY_USERNAME_INPUT}"

MODERN_PASSWORD_INPUT = f'{MODERN_LOGIN_PANEL} input[type="password"]'
LEGACY_PASSWORD_INPUT = "#lPassword"
PASSWORD_INPUT = f"{MODERN_PASSWORD_INPUT}, {LEGACY_PASSWORD_INPUT}"

# 2026-09 实测：新版面板的协议勾选框是 Element-UI 结构
#   <span class="el-checkbox__input">
#     <input class="el-checkbox__original" type="checkbox">   <- 0x0、opacity:0
#     <span class="el-checkbox__inner"></span>                <- 真正可点击的节点
#   </span>
# 隐藏 input 的包围盒是 0x0，Playwright 的 check(force=True) 会以
# “Element is outside of the viewport” 失败；必须点击可见的 .el-checkbox__inner。
# input 本身仍用于读取/确认勾选状态。
LOGIN_AGREEMENT_CHECKBOX = (
    f'{MODERN_LOGIN_PANEL} .login-bottom input[type="checkbox"]'
)
LOGIN_AGREEMENT_CLICKABLE = (
    f'{MODERN_LOGIN_PANEL} .login-bottom .el-checkbox__inner'
)
MODERN_LOGIN_SUBMIT = f"{MODERN_LOGIN_PANEL} .btn-block__grandient_login"
LEGACY_LOGIN_SUBMIT = f"{LEGACY_LOGIN_PANEL} .wall-sub-btn"
LOGIN_SUBMIT = f"{MODERN_LOGIN_SUBMIT}, {LEGACY_LOGIN_SUBMIT}"

LOGIN_FORM_TIMEOUT_MS = 30_000
# 保留供外部引用：实测静默 SSO 链路约 20 秒，登录跳转已改由 login_flow 的
# PASSIVE_LOGIN_TIMEOUT_MS / AUTO_LOGIN_TIMEOUT_MS 控制，不再使用这个 15 秒上限。
LOGIN_REDIRECT_TIMEOUT_MS = 15_000
AUTO_LOGIN_TIMEOUT_MS = 120_000
MANUAL_LOGIN_TIMEOUT_MS = 24 * 3600 * 1000
