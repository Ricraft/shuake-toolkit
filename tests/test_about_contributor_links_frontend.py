# encoding=utf-8
"""关于页感谢名单外链（后端白名单）的回归契约。"""

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.desktop_platform_service import DesktopPlatformService
from src.web_action_service import CONTRIBUTOR_LINKS, WebActionService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = PROJECT_ROOT / "web" / "app.js"
FRONTEND = APP_JS_PATH.read_text(encoding="utf-8")
STYLES = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
YATORI_DOCS_URL = "https://yatori-dev.github.io/yatori-docs/"
AUTOVISOR_URL = "https://github.com/CXRunfree/Autovisor"
ZHS_URL = "https://github.com/pjm314159/zhs"
ZERROR_URL = "https://app.zerror.cc/"
CONTRIBUTOR_KEYS = {
    "yatori": YATORI_DOCS_URL,
    "autovisor": AUTOVISOR_URL,
    "zhs": ZHS_URL,
}


def _about_page() -> str:
    for path in (PROJECT_ROOT / "web").glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "感谢名单" in text and 'id="latest-changelog"' in text:
            return text
    raise AssertionError("about page not found")


HTML = _about_page()


def make_launcher(**overrides):
    opened = []
    values = {
        "get_web_initial_state": lambda: {"runtime": {"yatori": False}},
        "_show_error": lambda title, message: None,
        "open_external_url": (
            lambda url: opened.append(url) or {"ok": True, "url": url}
        ),
    }
    values.update(overrides)
    launcher = SimpleNamespace(**values)
    launcher.opened = opened
    return launcher


def build_service(base_dir, **overrides):
    opened = []
    overrides.setdefault("url_opener", opened.append)
    service = DesktopPlatformService(
        str(base_dir),
        str(base_dir / "launcher.py"),
        log=lambda _message: None,
        platform_name="nt",
        environment={},
        **overrides,
    )
    return service, opened


@pytest.fixture()
def sandbox_dir():
    """本机 pytest 临时基目录不可访问，这里使用独立临时目录。"""
    path = tempfile.mkdtemp(prefix="dsh-about-links-")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------- 后端白名单外链


@pytest.mark.parametrize("key,url", sorted(CONTRIBUTOR_KEYS.items()))
def test_known_contributor_key_opens_the_allowlisted_url(key, url):
    launcher = make_launcher()

    result = WebActionService(launcher).perform("open_contributor_link", key)

    assert result["ok"] is True
    assert launcher.opened == [url]
    assert CONTRIBUTOR_LINKS[key] == url


def test_unknown_contributor_key_fails_without_opening_anything():
    launcher = make_launcher()

    result = WebActionService(launcher).perform("open_contributor_link", "evil")

    assert result["ok"] is False
    assert launcher.opened == []
    assert "未知" in result["message"]


def test_backend_rejects_a_raw_url_sent_from_the_frontend():
    launcher = make_launcher()

    for candidate in (YATORI_DOCS_URL, AUTOVISOR_URL, "https://example.com/"):
        result = WebActionService(launcher).perform(
            "open_contributor_link",
            candidate,
        )
        assert result["ok"] is False

    assert launcher.opened == []


def test_desktop_service_only_opens_allowlisted_https_urls(sandbox_dir):
    service, opened = build_service(sandbox_dir)

    assert service.open_external_url(YATORI_DOCS_URL)["ok"] is True
    assert service.open_external_url(AUTOVISOR_URL)["ok"] is True
    assert service.open_external_url(ZHS_URL)["ok"] is True
    assert opened == [YATORI_DOCS_URL, AUTOVISOR_URL, ZHS_URL]

    blocked = (
        "",
        "javascript:alert(1)",
        "http://yatori-dev.github.io/yatori-docs/",
        "https://evil.example.com/",
        "https://yatori-dev.github.io/other/",
        "https://github.com/CXRunfree/SomeOtherRepo/",
        "https://github.com@evil.example.com/CXRunfree/Autovisor",
    )
    for candidate in blocked:
        assert service.open_external_url(candidate)["ok"] is False

    assert opened == [YATORI_DOCS_URL, AUTOVISOR_URL, ZHS_URL]


def test_failed_opener_is_reported_without_raising(sandbox_dir):
    def fail(_url):
        raise OSError("no default browser")

    service, _opened = build_service(sandbox_dir, url_opener=fail)

    result = service.open_external_url(AUTOVISOR_URL)

    assert result["ok"] is False
    assert "no default browser" in result["message"]


# --------------------------------------------------------------- 前端契约


def test_linked_contributor_cards_match_the_backend_allowlist():
    assert HTML.count('<a class="contributor-card') == 4
    assert HTML.count('<div class="contributor-card" data-tilt>') == 4
    assert HTML.count('data-contributor="') == 4
    assert HTML.count("</a>") == 4
    for key, url in (
        ("yatori", YATORI_DOCS_URL),
        ("autovisor", AUTOVISOR_URL),
        ("zhs", ZHS_URL),
        ("zerror", ZERROR_URL),
    ):
        assert 'data-contributor="%s"' % key in HTML
        assert 'href="%s"' % url in HTML
    assert HTML.count('target="_blank" rel="noopener noreferrer"') == 4
    assert HTML.count('onclick="return openContributorLink(event, this)"') == 4


def test_remaining_contributor_cards_stay_plain_and_unclickable():
    for name in ("PyWebView", "Tailwind CSS", "Font Awesome", "所有用户"):
        assert ">%s</div>" % name in HTML
    assert "contributor-link-icon" not in HTML.split('</a>')[-1]


def test_frontend_passes_only_whitelist_keys_to_the_backend():
    assert "performAction('open_contributor_link', key)" in FRONTEND
    assert "anchor.dataset.contributor" in FRONTEND
    assert "evt.preventDefault()" in FRONTEND
    for url in (YATORI_DOCS_URL, AUTOVISOR_URL, ZHS_URL):
        assert "open_contributor_link', '%s'" % url not in FRONTEND
        assert 'open_contributor_link", "%s"' % url not in FRONTEND
