"""Persistent apply results, independent of the files being replaced."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from src.atomic_io import atomic_dump_json

RECEIPT_NAME = "launcher-update-result.json"


def apply_receipt_command(record: dict, status: str) -> str:
    """Use inbox Windows PowerShell, so even a failed Python replacement is recorded."""
    encoded = base64.b64encode(json.dumps({**record, "status": status, "acknowledged": False}).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        f"$r=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))|ConvertFrom-Json;"
        "$r|Add-Member updatedAt ([DateTime]::UtcNow.ToString('o'));"
        "$r|Add-Member environmentReady ($env:ZHS_PREPARE_FAILED -ne '1');"
        "$r|Add-Member detail ([string]$env:FAILURE_REASON);"
        "$d=Join-Path $env:ROOT 'data';[IO.Directory]::CreateDirectory($d)|Out-Null;"
        f"$p=Join-Path $d '{RECEIPT_NAME}';$t=$p+'.'+[Guid]::NewGuid().ToString('N')+'.tmp';"
        "[IO.File]::WriteAllText($t,($r|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)));"
        "if([IO.File]::Exists($p)){$b=$t+'.old';[IO.File]::Replace($t,$p,$b);[IO.File]::Delete($b)}else{[IO.File]::Move($t,$p)}"
    )
    command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + command


def _read(base_dir) -> dict:
    path = Path(base_dir) / 'data' / RECEIPT_NAME
    try:
        if path.stat().st_size > 65536:
            return {}
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or not isinstance(data.get('id'), str):
            return {}
        return data
    except (OSError, ValueError):
        return {}


def receipt_snapshot(base_dir, actual_version: str, *, pid: int = 0) -> dict:
    data = _read(base_dir)
    if not data or data.get('acknowledged'):
        return {}
    status = data.get('status')
    if status not in ('success', 'failed', 'applying'):
        return {}
    if status == 'applying':
        if data.get('launcherPid') == pid:
            return {}
        status = 'interrupted'
    target = str(data.get('targetVersion') or '')[:100]
    if status == 'success' and target.lstrip('vV') != str(actual_version).lstrip('vV'):
        status = 'version_mismatch'
    messages = {
        'success': f'启动器已成功更新到 {actual_version}',
        'failed': '上次启动器更新失败，请查看日志后重试',
        'interrupted': '上次更新尚未确认完成，可能仍在应用；请等待或查看日志，若已中断可重新检查更新',
        'version_mismatch': f'更新后的实际版本为 {actual_version}，与目标 {target} 不一致，请查看日志',
    }
    if status == 'success' and data.get('environmentReady') is False:
        messages[status] += '；ZHS 环境尚未准备完成，可在核心卡片重试'
    log_path = receipt_log_path(base_dir, data)
    return {
        'id': data['id'], 'status': status, 'message': messages[status],
        'actualVersion': str(actual_version), 'targetVersion': target,
        'detail': str(data.get('detail') or '')[:500],
        'logPath': str(log_path) if log_path else '',
    }


def receipt_log_path(base_dir, data: dict) -> Path | None:
    try:
        staging = (Path(base_dir) / '.update-staging').resolve()
        path = (Path(base_dir) / str(data.get('logPath') or '')).resolve()
        path.relative_to(staging)
        return path if path.is_file() and path.suffix.lower() == '.log' else None
    except (ValueError, OSError):
        return None


def acknowledge_receipt(base_dir, receipt_id: str) -> dict:
    data = _read(base_dir)
    if not data or data.get('id') != receipt_id:
        return {'ok': False, 'message': '更新结果已变化，请刷新后重试'}
    try:
        atomic_dump_json(Path(base_dir) / 'data' / RECEIPT_NAME, {**data, 'acknowledged': True})
        return {'ok': True}
    except OSError:
        return {'ok': False, 'message': '无法保存更新结果的已读状态'}
