# encoding=utf-8
"""关于页感谢名单外链（后端白名单）与更新日志折叠的回归契约。"""

import json
import shutil
import subprocess
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
ZERROR_URL = "https://app.zerror.cc/"
CONTRIBUTOR_KEYS = {"yatori": YATORI_DOCS_URL, "autovisor": AUTOVISOR_URL}


def _about_page() -> str:
    for path in (PROJECT_ROOT / "web").glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "感谢名单" in text and "changelog-item" in text:
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
    assert opened == [YATORI_DOCS_URL, AUTOVISOR_URL]

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

    assert opened == [YATORI_DOCS_URL, AUTOVISOR_URL]


def test_failed_opener_is_reported_without_raising(sandbox_dir):
    def fail(_url):
        raise OSError("no default browser")

    service, _opened = build_service(sandbox_dir, url_opener=fail)

    result = service.open_external_url(AUTOVISOR_URL)

    assert result["ok"] is False
    assert "no default browser" in result["message"]


# --------------------------------------------------------------- 前端契约


def test_linked_contributor_cards_match_the_backend_allowlist():
    assert HTML.count('<a class="contributor-card') == 3
    assert HTML.count('<div class="contributor-card" data-tilt>') == 4
    assert HTML.count('data-contributor="') == 3
    assert HTML.count("</a>") == 3
    for key, url in (
        ("yatori", YATORI_DOCS_URL),
        ("autovisor", AUTOVISOR_URL),
        ("zerror", ZERROR_URL),
    ):
        assert 'data-contributor="%s"' % key in HTML
        assert 'href="%s"' % url in HTML
    assert HTML.count('target="_blank" rel="noopener noreferrer"') == 3
    assert HTML.count('onclick="return openContributorLink(event, this)"') == 3


def test_remaining_contributor_cards_stay_plain_and_unclickable():
    for name in ("PyWebView", "Tailwind CSS", "Font Awesome", "所有用户"):
        assert ">%s</div>" % name in HTML
    assert "contributor-link-icon" not in HTML.split('</a>')[-1]


def test_frontend_passes_only_whitelist_keys_to_the_backend():
    assert "performAction('open_contributor_link', key)" in FRONTEND
    assert "anchor.dataset.contributor" in FRONTEND
    assert "evt.preventDefault()" in FRONTEND
    for url in (YATORI_DOCS_URL, AUTOVISOR_URL):
        assert "open_contributor_link', '%s'" % url not in FRONTEND
        assert 'open_contributor_link", "%s"' % url not in FRONTEND


# --------------------------------------------------------------- 更新日志折叠


def test_changelog_entries_stay_in_static_dom_and_hide_by_css_only():
    # 条数会随版本迭代增长，这里只要求折叠有意义（多于默认可见条数）；
    # 具体阈值由 CHANGELOG_DEFAULT_VISIBLE 单独断言。
    entries = HTML.count('<div class="changelog-item">')
    assert entries > 15, entries
    assert 'id="changelog-list"' in HTML
    assert 'id="changelog-more-btn"' in HTML
    assert 'id="changelog-more-label"' in HTML
    assert HTML.index('id="changelog-more-btn"') > HTML.rindex(
        '<div class="changelog-item">'
    )


def test_new_changelog_hooks_avoid_the_reserved_item_class_name():
    assert 'class="changelog-list"' not in HTML
    assert "changelog-items" not in HTML
    assert "changelog-item-" not in HTML
    assert ".changelog-item.is-overflow { display: none; }" in STYLES
    assert (
        "#changelog-list.is-expanded .changelog-item.is-overflow { display: flex; }"
        in STYLES
    )
    assert "@media print" in STYLES
    assert ".changelog-item.is-overflow { display: flex !important; }" in STYLES


def test_changelog_collapse_is_idempotent_and_wired_into_the_about_page():
    assert "const CHANGELOG_DEFAULT_VISIBLE = 15;" in FRONTEND
    assert "if (list.dataset.changelogReady === '1') return;" in FRONTEND
    assert "classList.add('is-overflow')" in FRONTEND
    assert "classList.toggle('is-expanded', !!expanded)" in FRONTEND
    assert "label.textContent = expanded" in FRONTEND

    setup_source = FRONTEND.split("function setupChangelogCollapse", 1)[1].split(
        "function setChangelogExpanded",
        1,
    )[0]
    assert "innerHTML" not in setup_source
    assert "removeChild" not in setup_source
    assert "changelog-list').innerHTML" not in FRONTEND

    about_source = FRONTEND.split("function initAboutPageEffects", 1)[1].split(
        "function initAmbientLight",
        1,
    )[0]
    assert "setupChangelogCollapse();" in about_source


HARNESS_JS = r"""
const fs = require('fs');
const src = fs.readFileSync('__APP_JS__', 'utf8');

const constMatch = src.match(/const CHANGELOG_DEFAULT_VISIBLE = \d+;/);
if (!constMatch) { throw new Error('CHANGELOG_DEFAULT_VISIBLE not found'); }
const setupSrc = 'function setupChangelogCollapse'
  + src.split('function setupChangelogCollapse', 2)[1];
if (setupSrc.indexOf('function setChangelogExpanded') === -1) {
  throw new Error('setChangelogExpanded not found');
}

function makeClassList(el) {
  return {
    add: (name) => { el._classes.add(name); },
    remove: (name) => { el._classes.delete(name); },
    contains: (name) => el._classes.has(name),
    toggle: (name, force) => {
      const want = force === undefined ? !el._classes.has(name) : !!force;
      if (want) { el._classes.add(name); } else { el._classes.delete(name); }
      return want;
    },
  };
}

const items = [];
for (let i = 0; i < 17; i += 1) {
  const item = { _classes: new Set() };
  item.classList = makeClassList(item);
  items.push(item);
}

const listeners = [];
const button = {
  hidden: true,
  attrs: {},
  addEventListener: (type, handler) => listeners.push({ type, handler }),
  setAttribute: (name, value) => { button.attrs[name] = String(value); },
};
const label = { textContent: '' };
const list = { dataset: {}, _classes: new Set(), querySelectorAll: () => items };
list.classList = makeClassList(list);
const byId = {
  'changelog-list': list,
  'changelog-more-btn': button,
  'changelog-more-label': label,
};
const documentStub = { getElementById: (id) => byId[id] || null };

const body = constMatch[0] + '\n' + setupSrc + `
const visibleCount = () => items.filter(
  (item) => !item._classes.has('is-overflow') || list._classes.has('is-expanded')
).length;
setupChangelogCollapse();
const visibleAfterFirstInit = visibleCount();
const labelAfterFirstInit = label.textContent;
const buttonHiddenAfterFirstInit = button.hidden;
setupChangelogCollapse();
const listenersAfterSecondInit = listeners.length;
const visibleAfterSecondInit = visibleCount();
const click = listeners[0].handler;
click();
const visibleWhenExpanded = visibleCount();
const expandedClass = list._classes.has('is-expanded');
const expandedAria = button.attrs['aria-expanded'];
const expandedLabel = label.textContent;
click();
const visibleAfterCollapse = visibleCount();
const collapsedClass = list._classes.has('is-expanded');
const collapsedLabel = label.textContent;
console.log(JSON.stringify({
  total: items.length,
  visibleAfterFirstInit,
  labelAfterFirstInit,
  buttonHiddenAfterFirstInit,
  listenersAfterSecondInit,
  visibleAfterSecondInit,
  visibleWhenExpanded,
  expandedClass,
  expandedAria,
  expandedLabel,
  visibleAfterCollapse,
  collapsedClass,
  collapsedLabel,
}));
`;

new Function(
  'document', 'items', 'list', 'button', 'label', 'listeners', body
)(documentStub, items, list, button, label, listeners);
"""


def test_changelog_toggle_runs_the_real_frontend_javascript(sandbox_dir):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the changelog behaviour contract")

    harness = sandbox_dir / "changelog_harness.js"
    harness.write_text(
        HARNESS_JS.replace("__APP_JS__", str(APP_JS_PATH).replace("\\", "/")),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node, str(harness)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert payload["total"] == 17
    assert payload["visibleAfterFirstInit"] == 15
    assert payload["labelAfterFirstInit"].startswith("显示更多")
    assert payload["buttonHiddenAfterFirstInit"] is False
    assert payload["listenersAfterSecondInit"] == 1
    assert payload["visibleAfterSecondInit"] == 15
    assert payload["visibleWhenExpanded"] == 17
    assert payload["expandedClass"] is True
    assert payload["expandedAria"] == "true"
    assert payload["expandedLabel"] == "收起"
    assert payload["visibleAfterCollapse"] == 15
    assert payload["collapsedClass"] is False
    assert payload["collapsedLabel"].startswith("显示更多")