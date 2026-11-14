from types import SimpleNamespace
import subprocess

from 统一启动器 import UnifiedLauncher
from src.web_state_service import WebStateService


def test_zhs_preparation_failure_can_retry_and_success_requires_probe(tmp_path, monkeypatch):
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher.running, launcher.starting = {}, {}
    launcher.get_base_dir = lambda: str(tmp_path)
    launcher._zhs_runtime_sources_ready = lambda: True
    launcher.log_system = lambda *a: None
    pending = []
    monkeypatch.setattr('统一启动器.threading.Thread', lambda **kw: SimpleNamespace(start=lambda: pending.append(kw['target'])))
    monkeypatch.setattr('统一启动器.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='未找到 Python 3.13/3.14'.encode(), stderr=b''))
    assert launcher._request_zhs_runtime_prepare(force=True)
    assert launcher._zhs_environment_status['phase'] == 'preparing'
    assert not launcher._request_zhs_runtime_prepare(force=True)
    pending.pop()()
    assert launcher._zhs_environment_status['phase'] == 'failed'
    assert 'Python 3.13/3.14' in launcher._zhs_environment_status['detail']
    assert not launcher._zhs_runtime_prepare_running
    monkeypatch.setattr('统一启动器.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=0))
    launcher._get_zhs_service = lambda: SimpleNamespace(environment_state=lambda **kw: {'ready':False,'message':'导入检查失败'})
    assert launcher._request_zhs_runtime_prepare(force=True)
    pending.pop()()
    assert launcher._zhs_environment_status['phase'] == 'failed'
    def broken_probe(**kwargs):
        raise RuntimeError('环境校验异常')
    launcher._get_zhs_service = lambda: SimpleNamespace(environment_state=broken_probe)
    assert launcher._request_zhs_runtime_prepare(force=True)
    pending.pop()()
    assert launcher._zhs_environment_status['phase'] == 'failed'
    assert '环境校验异常' in launcher._zhs_environment_status['detail']
    launcher._get_zhs_service = lambda: SimpleNamespace(environment_state=lambda **kw: {'ready':True})
    assert launcher._request_zhs_runtime_prepare(force=True)
    pending.pop()()
    assert launcher._zhs_environment_status['phase'] == 'ready'


def test_environment_timeout_preserves_failure_detail_and_releases_retry(tmp_path, monkeypatch):
    launcher = UnifiedLauncher.__new__(UnifiedLauncher)
    launcher.get_base_dir = lambda: str(tmp_path)
    launcher.log_system = lambda *a: None
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('prepare', kw['timeout'])
    monkeypatch.setattr('统一启动器.subprocess.run', timeout)
    launcher._zhs_runtime_prepare_running = True
    launcher._zhs_runtime_prepare_worker()
    assert not launcher._zhs_runtime_prepare_running
    assert '超时' in launcher._zhs_environment_status['detail']


def test_environment_snapshot_shows_running_preparation_and_keeps_failure_until_ready():
    launcher = SimpleNamespace(_zhs_environment_status={'phase':'failed','message':'failed','detail':'reason'})
    service = WebStateService(launcher)
    snap = lambda ready: service._core_environment_snapshot({'environment':{'ready':ready}}, {}, {}, False)
    assert snap(False)['zhs']['phase'] == 'failed'
    assert snap(True)['zhs']['phase'] == 'ready'
    launcher._zhs_runtime_prepare_running = True
    assert snap(True)['zhs']['phase'] == 'preparing'
    assert service._core_environment_snapshot({}, {}, {}, True)['autovisor']['phase'] == 'preparing'
