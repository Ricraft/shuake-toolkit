"""Startup update discovery, passive badges, and one-time launcher prompts."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.launcher_update_service import LauncherUpdateService
from src.update_controller import UpdateController


def controller(manager):
    notices = []
    return UpdateController(
        manager, log=lambda *a, **kw: None,
        show_error=lambda *a: notices.append(a), show_info=lambda *a: None,
        show_warning=lambda *a: None, is_core_running=lambda core: False,
        on_installed=lambda core: None, get_autovisor_version=lambda: 'local',
        schedule=lambda ms, cb: cb(), run_async=lambda cb: cb(),
    ), notices


def test_core_badges_require_an_installed_newer_version_and_clear_on_success():
    manager = SimpleNamespace(install_zhs=lambda info, progress: True)
    updates, _ = controller(manager)
    updates.yatori_update_info = {'installed': False, 'has_update': True}
    updates.zhs_update_info = {'installed': True, 'has_update': True, 'info': {'version': '0.1.6'}}
    assert updates.available_updates_snapshot()['yatori']['hasUpdate'] is False
    assert updates.available_updates_snapshot()['zhs']['hasUpdate'] is True
    updates.install_zhs_async({'version': '0.1.6'})
    assert updates.available_updates_snapshot()['zhs']['hasUpdate'] is False


@pytest.mark.parametrize('raises', [True, False])
def test_automatic_zhs_check_failure_is_quiet(raises):
    def check():
        if raises:
            raise OSError('offline')
        return None
    updates, notices = controller(SimpleNamespace(check_zhs_update=check))
    assert updates.check_zhs_async(explicit=False) is True
    assert notices == []
    assert updates.available_updates_snapshot()['zhs']['hasUpdate'] is False
    updates.check_zhs_async()
    assert notices  # Manual check failures still have user feedback.


def test_startup_launcher_check_is_async_once_and_keeps_confirmation_valid(tmp_path, monkeypatch):
    service = LauncherUpdateService(tmp_path, current_version='v1.7.2')
    queued = []
    monkeypatch.setattr('src.launcher_update_service.threading.Thread', lambda **kw: SimpleNamespace(start=lambda: queued.append(kw['target'])))
    def prepare():
        service._pending_confirmation = {'token': 'current-token'}
        return {'ok': True, 'updateDialog': {'latestVersion': 'v1.8.0', 'confirmationToken': 'current-token'}}
    monkeypatch.setattr(service, 'prepare_update_confirmation', prepare)
    assert service.check_startup_async() is True
    assert service.startup_update_snapshot() == {}
    assert service.check_startup_async() is False
    assert len(queued) == 1
    queued[0]()
    snapshot = service.startup_update_snapshot()
    assert snapshot['updateDialog']['latestVersion'] == 'v1.8.0'
    snapshot['updateDialog']['confirmationToken'] = 'external-mutation'
    assert service.startup_update_snapshot()['updateDialog']['confirmationToken'] == 'current-token'
    service._pending_confirmation = {'token': 'manual-check-new-token'}
    assert service.startup_update_snapshot() == {}


def test_startup_checks_launcher_even_without_core_manager():
    from 统一启动器 import UnifiedLauncher
    calls = []
    launcher = SimpleNamespace(core_manager=None, _get_launcher_update_service=lambda: SimpleNamespace(check_startup_async=lambda: calls.append('launcher')))
    UnifiedLauncher.auto_check_cores(launcher)
    assert calls == ['launcher']
    launcher.core_manager = SimpleNamespace(check_yatori_installed=lambda: True)
    launcher.check_yatori_update_async = lambda: calls.append('yatori')
    launcher.update_controller = SimpleNamespace(check_zhs_async=lambda **kw: calls.append(('zhs', kw)))
    UnifiedLauncher.auto_check_cores(launcher)
    assert calls[1:] == ['launcher', 'yatori', ('zhs', {'explicit': False})]


def test_frontend_badges_and_prompt_run_production_javascript():
    source = (Path(__file__).resolve().parents[1] / 'web/app.js').read_text(encoding='utf-8')
    source = source[source.index('        function renderAvailableUpdates('):]
    harness = r'''
const assert = require('assert');
const startupLauncherUpdatesShown = new Set();
let launcherUpdateConfirming = false, busyModal = false, shown = [];
const elements = {'yatori-update-badge': {}, 'zhs-update-badge': {}, 'launcher-update-modal': {}};
const document = {getElementById: id => elements[id], querySelector: () => busyModal};
function showLauncherUpdateModal(info) { shown.push(info); }
eval(SOURCE);
renderAvailableUpdates({yatori:{hasUpdate:true,latestVersion:'3.1.0'},zhs:{hasUpdate:false}});
assert.equal(elements['yatori-update-badge'].hidden, false);
assert.equal(elements['zhs-update-badge'].hidden, true);
renderAvailableUpdates({});
assert.equal(elements['yatori-update-badge'].hidden, true);
const result = {ok:true,updateDialog:{latestVersion:'v1.8.0',confirmationToken:'token'}};
busyModal = true;
handleStartupLauncherUpdate(result);
assert.equal(shown.length, 0);
busyModal = false;
handleStartupLauncherUpdate(result);
handleStartupLauncherUpdate(result);
assert.equal(shown.length, 1);
assert.equal(shown[0].confirmationToken, 'token');
handleStartupLauncherUpdate({ok:false});
assert.equal(shown.length, 1);
'''.replace('SOURCE', json.dumps(source))
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
