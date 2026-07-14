import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.login_selectors import LOGIN_PANEL, LOGIN_SUBMIT, PASSWORD_INPUT, USERNAME_INPUT

sys.path.remove(_AUTOVISOR_ROOT)


def test_login_selectors_match_public_login_page_contract():
    assert LOGIN_PANEL == ".wall-main"
    assert USERNAME_INPUT == "#lUsername"
    assert PASSWORD_INPUT == "#lPassword"
    assert LOGIN_SUBMIT == ".wall-main .wall-sub-btn"


def test_submit_selector_is_scoped_to_login_panel():
    assert LOGIN_SUBMIT.startswith(f"{LOGIN_PANEL} ")
