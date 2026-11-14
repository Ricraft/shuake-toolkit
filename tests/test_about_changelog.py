"""Online latest-only release notes, independent of update confirmations."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.launcher_api import WebLauncherAPI
from src.launcher_update_service import LauncherUpdateService

ROOT = Path(__file__).resolve().parents[1]


def service(tmp_path, releases):
    calls = []
    def opener(url, timeout):
        calls.append(url)
        return 200, json.dumps(releases).encode()
    return LauncherUpdateService(tmp_path, current_version='v1.7.3', opener=opener), calls


def test_about_has_only_an_online_container_and_release_notes_live_in_markdown():
    html = (ROOT / 'web/现代启动器_UI_预览.html').read_text(encoding='utf-8')
    assert 'id="latest-changelog"' in html
    assert 'id="latest-changelog-refresh"' in html
    assert 'changelog-item' not in html and 'changelog-more-btn' not in html
    assert not re.search(r'v1\.\d+\.\d+\s*-', html)
    version = re.search(r'LAUNCHER_VERSION\s*=\s*"([^"]+)"', (ROOT / '统一启动器.py').read_text(encoding='utf-8'))[1]
    notes = (ROOT / 'docs/releases' / (version + '.md')).read_text(encoding='utf-8')
    assert notes.startswith('# 统一启动器 ' + version)


def test_latest_only_semantic_selection_skips_drafts_and_preserves_confirmation(tmp_path):
    updater, _ = service(tmp_path, [
        {'tag_name':'v1.9.0','body':'OLD'}, {'tag_name':'v1.10.0','body':'LATEST'},
        {'tag_name':'v2.0.0','body':'DRAFT','draft':True},
    ])
    updater._pending_confirmation = {'token':'keep-current-token'}
    result = updater.get_latest_changelog()
    assert result['ok'] and result['version'] == 'v1.10.0' and result['notes'] == 'LATEST'
    assert 'OLD' not in json.dumps(result) and 'DRAFT' not in json.dumps(result)
    assert updater._pending_confirmation == {'token':'keep-current-token'}
    assert not updater.installing


def test_success_is_cached_but_manual_refresh_fetches_again_and_returns_copies(tmp_path):
    updater, calls = service(tmp_path, [{'tag_name':'v1.7.3','body':'latest'}])
    updater.get_latest_changelog()['notes'] = 'external mutation'
    assert updater.get_latest_changelog()['notes'] == 'latest'
    assert len(calls) == 1
    assert updater.get_latest_changelog(refresh=True)['ok']
    assert len(calls) == 2


def test_failed_refresh_does_not_disguise_previous_content_as_latest(tmp_path):
    updater, _ = service(tmp_path, [{'tag_name':'v1.7.3','body':'old response'}])
    updater.get_latest_changelog()
    updater._fetch_releases = lambda: (None, 'offline')
    result = updater.get_latest_changelog(refresh=True)
    assert not result['ok'] and 'offline' in result['message']
    assert 'notes' not in result and updater._changelog_cache is None


def test_missing_release_body_reads_only_same_tag_markdown_not_main(tmp_path):
    updater, _ = service(tmp_path, [])
    updater._fetch_releases = lambda: ([{'tag_name':'v1.7.3','body':''}], '')
    requested = []
    updater.opener = lambda url, timeout: (requested.append(url) or 200, b'# Current notes')
    assert updater.get_latest_changelog()['notes'] == '# Current notes'
    assert requested == ['https://raw.githubusercontent.com/Ricraft/shuake-toolkit/v1.7.3/docs/releases/v1.7.3.md']


def test_missing_notes_never_fall_back_to_previous_version(tmp_path):
    updater, _ = service(tmp_path, [])
    updater._fetch_releases = lambda: ([{'tag_name':'v1.7.2','body':'OLD'},{'tag_name':'v1.7.3','body':''}], '')
    updater.opener = lambda *a: (404, b'')
    result = updater.get_latest_changelog()
    assert result['ok'] and result['version'] == 'v1.7.3' and result['notes'] == ''
    updater.opener = lambda *a: (503, b'error')
    assert not updater.get_latest_changelog(refresh=True)['ok']


def test_no_releases_is_explicit_and_api_is_read_only(tmp_path):
    updater, _ = service(tmp_path, [])
    api = WebLauncherAPI(SimpleNamespace(_get_launcher_update_service=lambda: updater))
    assert not api.get_latest_launcher_changelog()['ok']
    assert not updater.get_latest_changelog(refresh='not-a-boolean')['ok']


def test_official_page_notes_exclude_navigation_and_other_markdown_bodies():
    from src.launcher_update_service import _ReleasePageNotes
    parser = _ReleasePageNotes()
    parser.feed('<nav>OLD NAVIGATION</nav><div class="markdown-body my-3"><h2>Current</h2><ul><li>Fix &amp; improve</li></ul></div><div class="markdown-body">OTHER</div>')
    assert parser.text() == 'Current\n- Fix & improve'


def test_real_frontend_replaces_latest_only_handles_failure_retry_and_treats_remote_html_as_text(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for frontend behavior')
    source = (ROOT / 'web/app.js').read_text(encoding='utf-8')
    source = source[source.index('        let latestChangelogLoading'):source.index('        let themePillObserver')]
    script = r'''
const assert=require('assert');
class Element {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.hidden=false;this.disabled=false;this.dataset={};this.attrs={};this.value='';}
 set textContent(v){this.value=String(v);this.children=[];}
 get textContent(){return this.value+this.children.map(x=>x.textContent).join('');}
 replaceChildren(){this.children=[];this.value='';}
 appendChild(x){this.children.push(x);}
 setAttribute(k,v){this.attrs[k]=v;}
}
const ids=['latest-changelog','latest-changelog-refresh','latest-changelog-status','latest-changelog-meta','latest-changelog-content','latest-changelog-version','latest-changelog-date'];
const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
const tags=[];
const document={getElementById:id=>elements[id],createElement:tag=>{tags.push(tag);return new Element(tag);}};
let calls=[], release;
let responder=()=>new Promise(resolve=>{release=resolve;});
async function apiCall(...args){calls.push(args);return responder();}
eval(SOURCE + `
(async()=>{
 const first=loadLatestChangelog();
 await loadLatestChangelog(true);
 assert.equal(calls.length,1);
 assert.equal(elements['latest-changelog-refresh'].disabled,true);
 release({ok:true,version:'v1.7.3',notes:['## Current','- <img src=x onerror="boom()">'].join(String.fromCharCode(10)),publishedAt:'2026-11-14'});
 await first;
 assert.equal(elements['latest-changelog'].dataset.state,'ready');
 assert(elements['latest-changelog-content'].textContent.includes('<img'));
 assert(!tags.includes('img')&&!tags.includes('script'));
 assert(tags.includes('ul')&&tags.includes('li'));
 await loadLatestChangelog(); assert.equal(calls.length,1);
 responder=()=>({ok:false,message:'offline'});
 await loadLatestChangelog(true);
 assert.equal(elements['latest-changelog'].dataset.state,'error');
 assert.equal(elements['latest-changelog-content'].textContent,'');
 assert.equal(elements['latest-changelog-content'].hidden,true);
 assert.equal(elements['latest-changelog-refresh'].disabled,false);
 responder=()=>({ok:true,version:'v1.7.4',notes:'New only'});
 await loadLatestChangelog(true);
 assert.equal(elements['latest-changelog-content'].textContent,'New only');
 assert(elements['latest-changelog-version'].textContent.includes('v1.7.4'));
 assert.equal(calls.at(-1)[1],true);
})().catch(error=>{console.error(error);process.exitCode=1;});
`);
'''.replace('SOURCE', json.dumps(source))
    file = tmp_path / 'online-changelog.js'
    file.write_text(script, encoding='utf-8')
    completed = subprocess.run([node, str(file)], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
