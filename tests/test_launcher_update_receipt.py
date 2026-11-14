import json
import os
from pathlib import Path
import subprocess

import pytest

from src.launcher_update_receipt import RECEIPT_NAME, apply_receipt_command, receipt_snapshot, acknowledge_receipt
from src.launcher_update_service import build_apply_script, write_apply_script


def write_receipt(root, **values):
    path = root / 'data' / RECEIPT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'id': 'receipt-1', 'status': 'success', 'targetVersion': 'v1.8.0', **values}
    path.write_text(json.dumps(data), encoding='utf-8')
    return data


def test_restart_verifies_actual_version_and_acknowledges_only_current_result(tmp_path):
    write_receipt(tmp_path)
    assert receipt_snapshot(tmp_path, '1.8.0')['status'] == 'success'
    assert receipt_snapshot(tmp_path, 'v1.7.2')['status'] == 'version_mismatch'
    assert not acknowledge_receipt(tmp_path, 'stale-id')['ok']
    assert acknowledge_receipt(tmp_path, 'receipt-1')['ok']
    assert receipt_snapshot(tmp_path, 'v1.8.0') == {}


def test_failed_interrupted_and_environment_warning_are_distinct(tmp_path):
    write_receipt(tmp_path, status='failed')
    assert receipt_snapshot(tmp_path, 'v1.7.2')['status'] == 'failed'
    write_receipt(tmp_path, status='applying', launcherPid=99)
    assert receipt_snapshot(tmp_path, 'v1.7.2', pid=99) == {}
    assert receipt_snapshot(tmp_path, 'v1.7.2', pid=100)['status'] == 'interrupted'
    write_receipt(tmp_path, environmentReady=False)
    assert 'ZHS' in receipt_snapshot(tmp_path, 'v1.8.0')['message']


def test_receipt_cannot_open_logs_outside_update_staging(tmp_path):
    outside = tmp_path / 'private.log'
    outside.write_text('private', encoding='utf-8')
    write_receipt(tmp_path, logPath=str(outside))
    assert receipt_snapshot(tmp_path, 'v1.8.0')['logPath'] == ''
    log = tmp_path / '.update-staging' / 'update.log'
    log.parent.mkdir()
    log.write_text('update', encoding='utf-8')
    write_receipt(tmp_path, logPath='.update-staging/update.log')
    assert receipt_snapshot(tmp_path, 'v1.8.0')['logPath'] == str(log.resolve())


@pytest.mark.skipif(os.name != 'nt', reason='Windows apply script')
@pytest.mark.parametrize('valid_payload', [True, False])
def test_real_apply_script_records_result_and_log_without_running_accounts(tmp_path, valid_payload):
    root = tmp_path / '安装 测试 & 目录'
    staging = root / '.update-staging'
    payload = staging / 'v1.8.0'
    for folder in ('src', 'web'):
        (root / folder).mkdir(parents=True, exist_ok=True)
        (payload / folder).mkdir(parents=True, exist_ok=True)
        (payload / folder / 'marker.txt').write_text('new', encoding='utf-8')
    (root / '统一启动器.py').write_text('old', encoding='utf-8')
    if valid_payload:
        (payload / '统一启动器.py').write_text('new', encoding='utf-8')
    script = staging / '应用更新.cmd'
    write_apply_script(script, build_apply_script(
        version='v1.8.0', current_version='v1.7.2', required_files=['统一启动器.py'],
    ))
    # Isolated synthetic files; no bootstrap, Python restart or account process.
    result = subprocess.run(f'call "{script}"', shell=True, input='\n\n',
                            capture_output=True, text=True, errors='replace', timeout=60)
    receipt = receipt_snapshot(root, 'v1.8.0' if valid_payload else 'v1.7.2')
    assert receipt.get('status') == ('success' if valid_payload else 'failed'), result.stdout[-1000:] + result.stderr[-1000:]
    assert Path(receipt['logPath']).is_file()
    assert result.returncode == (0 if valid_payload else 1)
    assert (root / '统一启动器.py').read_text(encoding='utf-8') == ('new' if valid_payload else 'old')
