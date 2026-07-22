"""智慧树新旧登录页的稳定 URL 与 DOM 契约。"""

LOGIN_URL = (
    "https://login.zhihuishu.com/?origin=zhs&service="
    "https://onlineservice-api.zhihuishu.com/gateway/f/v1/login/gologin"
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

LOGIN_AGREEMENT_CHECKBOX = (
    f'{MODERN_LOGIN_PANEL} .login-bottom input[type="checkbox"]'
)
MODERN_LOGIN_SUBMIT = f"{MODERN_LOGIN_PANEL} .btn-block__grandient_login"
LEGACY_LOGIN_SUBMIT = f"{LEGACY_LOGIN_PANEL} .wall-sub-btn"
LOGIN_SUBMIT = f"{MODERN_LOGIN_SUBMIT}, {LEGACY_LOGIN_SUBMIT}"

LOGIN_FORM_TIMEOUT_MS = 30_000
LOGIN_REDIRECT_TIMEOUT_MS = 15_000
AUTO_LOGIN_TIMEOUT_MS = 120_000
MANUAL_LOGIN_TIMEOUT_MS = 24 * 3600 * 1000
