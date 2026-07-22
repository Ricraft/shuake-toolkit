import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.login_selectors import (
    LEGACY_LOGIN_PANEL,
    LEGACY_LOGIN_SUBMIT,
    LEGACY_PASSWORD_INPUT,
    LEGACY_USERNAME_INPUT,
    LOGIN_AGREEMENT_CHECKBOX,
    LOGIN_PANEL,
    LOGIN_RETURN_URL,
    LOGIN_SERVICE_URL,
    LOGIN_SUBMIT,
    LOGIN_URL,
    MODERN_LOGIN_PANEL,
    MODERN_LOGIN_SUBMIT,
    MODERN_PASSWORD_INPUT,
    MODERN_USERNAME_INPUT,
    PASSWORD_INPUT,
    USERNAME_INPUT,
)

sys.path.remove(_AUTOVISOR_ROOT)


def test_login_selectors_match_public_login_page_contract():
    assert LOGIN_URL.startswith("https://login.zhihuishu.com/")
    assert "onlineservice-api.zhihuishu.com" in LOGIN_URL
    outer_query = parse_qs(urlsplit(LOGIN_URL).query)
    assert outer_query["origin"] == ["zhs"]
    assert outer_query["service"] == [LOGIN_SERVICE_URL]
    assert parse_qs(urlsplit(LOGIN_SERVICE_URL).query)["fromurl"] == [
        LOGIN_RETURN_URL
    ]
    assert LOGIN_RETURN_URL == "https://onlineweb.zhihuishu.com/"
    assert MODERN_LOGIN_PANEL == "#login_center_app .login-container"
    assert MODERN_USERNAME_INPUT.endswith('input[name="mobile"]')
    assert MODERN_PASSWORD_INPUT.endswith('input[type="password"]')
    assert MODERN_LOGIN_SUBMIT.endswith(".btn-block__grandient_login")
    assert LOGIN_AGREEMENT_CHECKBOX.startswith(f"{MODERN_LOGIN_PANEL} ")


def test_legacy_login_page_contract_remains_available_as_fallback():
    assert LEGACY_LOGIN_PANEL == ".wall-main"
    assert LEGACY_USERNAME_INPUT == "#lUsername"
    assert LEGACY_PASSWORD_INPUT == "#lPassword"
    assert LEGACY_LOGIN_SUBMIT == ".wall-main .wall-sub-btn"
    assert LOGIN_PANEL == f"{MODERN_LOGIN_PANEL}, {LEGACY_LOGIN_PANEL}"
    assert USERNAME_INPUT == f"{MODERN_USERNAME_INPUT}, {LEGACY_USERNAME_INPUT}"
    assert PASSWORD_INPUT == f"{MODERN_PASSWORD_INPUT}, {LEGACY_PASSWORD_INPUT}"
    assert LOGIN_SUBMIT == f"{MODERN_LOGIN_SUBMIT}, {LEGACY_LOGIN_SUBMIT}"


def test_submit_selector_is_scoped_to_login_panel():
    assert MODERN_LOGIN_SUBMIT.startswith(f"{MODERN_LOGIN_PANEL} ")
    assert LEGACY_LOGIN_SUBMIT.startswith(f"{LEGACY_LOGIN_PANEL} ")
