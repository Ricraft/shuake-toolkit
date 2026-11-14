import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src import application_bootstrap as startup


@pytest.mark.skipif(os.name != 'nt', reason='Windows console isolation')
def test_daily_launch_has_no_console_or_inherited_bootstrap_output(tmp_path):
    recorded = {}
    def run(command, **kwargs):
        recorded.update(command=command, kwargs=kwargs)
        kwargs['stdout'].write('启动诊断保留\n')
        return SimpleNamespace(pid=123)
    assert startup.launch_desktop_process([sys.executable, 'launcher.py'], root=tmp_path, run=run) == 0
    kwargs = recorded['kwargs']
    assert kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW
    assert kwargs['stdin'] == subprocess.DEVNULL
    assert kwargs['stderr'] == subprocess.STDOUT
    assert kwargs['stdout'].closed
    assert '启动诊断保留' in next((tmp_path / 'logs').glob('launcher_startup_*.log')).read_text(encoding='utf-8')


def test_debug_mode_keeps_console_output_and_does_not_create_startup_log(tmp_path):
    captured = {}
    startup.launch_desktop_process([sys.executable, 'launcher.py', '--dev'], root=tmp_path,
                                   debug=True, run=lambda command, **kw: captured.update(kw) or SimpleNamespace(pid=1))
    assert 'stdout' not in captured and 'creationflags' not in captured
    assert not (tmp_path / 'logs').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows process startup')
def test_immediate_failure_preserves_diagnostics_in_the_reported_log(tmp_path):
    with pytest.raises(RuntimeError, match='返回码: 7.*启动日志'):
        startup.launch_desktop_process([sys.executable, '-c', 'import sys; print("startup failed"); sys.exit(7)'], root=tmp_path)
    assert 'startup failed' in next((tmp_path / 'logs').glob('launcher_startup_*.log')).read_text(encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows process/console lifecycle')
def test_windowless_child_outlives_parent_and_does_not_keep_parent_pipes_open(tmp_path):
    root = tmp_path / '中文 安装目录 & 测试'
    root.mkdir()
    child = root / 'child.py'
    child.write_text('''import ctypes,json,time
from pathlib import Path
root=Path(__file__).parent
(root/'child.json').write_text(json.dumps({'console':bool(ctypes.windll.kernel32.GetConsoleWindow())}))
deadline=time.monotonic()+8
while not (root/'release').exists() and time.monotonic()<deadline: time.sleep(0.02)
print('child output after parent exit',flush=True)
(root/'finished').write_text('ok')
''', encoding='utf-8')
    project = Path(__file__).resolve().parents[1]
    parent = root / 'parent.py'
    parent.write_text('import sys\nsys.path.insert(0,' + repr(str(project)) + ')\n'
                      'from src.application_bootstrap import launch_desktop_process\n'
                      'from pathlib import Path\n'
                      'launch_desktop_process([sys.executable,str(Path(__file__).with_name("child.py"))],root=Path(__file__).parent)\n', encoding='utf-8')
    process = subprocess.Popen([sys.executable, str(parent)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr.decode('utf-8', errors='replace')
        assert json.loads((root / 'child.json').read_text())['console'] is False
        assert not (root / 'finished').exists(), 'child must still be running after bootstrap exits'
    finally:
        (root / 'release').touch()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    import time
    deadline = time.monotonic() + 3
    while not (root / 'finished').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (root / 'finished').is_file()
    assert 'child output after parent exit' in next((root / 'logs').glob('launcher_startup_*.log')).read_text(encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows console detection')
def test_direct_launch_forwards_arguments_and_defensive_child_does_not_loop(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(GetConsoleWindow=lambda: 1, FreeConsole=lambda: calls.append('detach') or 1)))
    monkeypatch.delenv('SHUAKE_WINDOWLESS_CHILD', raising=False)
    monkeypatch.setattr(startup, 'launch_desktop_process', lambda command, **kw: calls.append(command))
    assert startup.relaunch_without_console(tmp_path, args=['--example']) is True
    assert calls[-1][1:] == [str(tmp_path / '统一启动器.py'), '--example']
    monkeypatch.setenv('SHUAKE_WINDOWLESS_CHILD', '1')
    assert startup.relaunch_without_console(tmp_path, args=[]) is False
    assert calls[-1] == 'detach'
    assert 'SHUAKE_WINDOWLESS_CHILD' not in os.environ
    assert startup.relaunch_without_console(tmp_path, args=['--dev']) is False
