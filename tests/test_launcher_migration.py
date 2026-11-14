"""Real package regressions for legacy four-path updates and ZIP retention."""
import io
import zipfile
from pathlib import Path

import pytest

from scripts import build_launcher_release as builder
from src import launcher_migration as migration


def compat(root, extra=None):
    contents = {name: b'fixture' for name in migration.REQUIRED_COMPAT_FILES}
    contents['bootstrap.py'] = b'def ensure_zhs_runtime(): pass\n'
    contents.update(extra or {})
    archive = root / migration.COMPAT_PAYLOAD
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w') as package:
        for name, data in contents.items():
            package.writestr(name, data)
    return contents


def test_real_package_repairs_four_path_update_and_cleans_both_zip_locations(tmp_path):
    result = builder.build(builder.declared_launcher_version(), tmp_path / 'build')
    root = tmp_path / '旧版 安装目录'
    root.mkdir()
    (root / 'bootstrap.py').write_text('# old bootstrap without ZHS helpers')
    config = root / 'zhs/config.toml'
    config.parent.mkdir()
    config.write_text('api_key = "preserved"')
    with zipfile.ZipFile(result['archive']) as package:
        assert migration.COMPAT_PAYLOAD in result['fileList']
        with zipfile.ZipFile(io.BytesIO(package.read(migration.COMPAT_PAYLOAD))) as nested:
            assert 'zhs/config.toml' not in nested.namelist()
            assert migration.REQUIRED_COMPAT_FILES.issubset(nested.namelist())
        # Exactly what the old updater copies, not a full manual extraction.
        for name in result['fileList']:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(name))
    assert not (root / 'requirements-zhs.txt').exists()
    archive = root / Path(result['archive']).name
    archive.write_bytes(Path(result['archive']).read_bytes())
    staging_zip = root / '.update-staging' / (result['version'] + '.zip')
    staging_zip.parent.mkdir()
    staging_zip.write_bytes(archive.read_bytes())
    # Before migration it is incomplete, so the archive must stay.
    assert migration.cleanup_applied_archives(root) == []
    migration.finish_legacy_update(root)
    assert 'def ensure_zhs_runtime(' in (root / 'bootstrap.py').read_text(encoding='utf-8')
    assert (root / 'requirements-zhs.txt').is_file()
    assert (root / 'zhs/src/zhs/__init__.py').is_file()
    assert config.read_text() == 'api_key = "preserved"'
    assert not archive.exists()
    assert not staging_zip.exists()
    assert migration.restore_legacy_layout(root) is False


@pytest.mark.parametrize('name', ['../outside.py', 'zhs/../../outside.py', 'zhs/config.toml', 'zhs/.venv/private', 'C:/outside.py'])
def test_compat_rejects_unsafe_paths_before_any_writes(tmp_path, name):
    compat(tmp_path, {name: b'bad'})
    with pytest.raises(ValueError):
        migration.restore_legacy_layout(tmp_path)
    assert not (tmp_path / 'bootstrap.py').exists()


def test_compat_rolls_back_partial_write_failure(tmp_path, monkeypatch):
    compat(tmp_path)
    bootstrap = tmp_path / 'bootstrap.py'
    bootstrap.write_bytes(b'old')
    write = migration.atomic_write_bytes
    count = 0

    def fail_second(path, data):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('disk full')
        write(path, data)

    monkeypatch.setattr(migration, 'atomic_write_bytes', fail_second)
    with pytest.raises(OSError, match='disk full'):
        migration.restore_legacy_layout(tmp_path)
    assert bootstrap.read_bytes() == b'old'
    assert not (tmp_path / 'requirements-zhs.txt').exists()


def test_cleanup_retains_failed_older_unrelated_and_corrupt_packages(tmp_path):
    contents = {'统一启动器.py': b'new', 'requirements.txt': b'deps', 'src/x.py': b'new', 'web/app.js': b'new'}
    for name, data in contents.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (tmp_path / 'src/x.py').write_bytes(b'old')
    for filename in ('launcher-v1.7.2.zip', 'yatori-v1.7.2.zip', 'shuake-toolkit-v1.7.2.zip'):
        with zipfile.ZipFile(tmp_path / filename, 'w') as package:
            for name, data in contents.items():
                package.writestr(name, data)
    (tmp_path / 'launcher-v1.7.1.zip').write_bytes(b'corrupt')
    assert migration.cleanup_applied_archives(tmp_path) == []
    assert len(list(tmp_path.glob('*.zip'))) == 4


def test_apply_script_cleans_only_after_validation():
    from src.launcher_update_service import build_apply_script
    script = build_apply_script(version='v1.7.2', current_version='v1.7.0', launcher_pid=0)
    cleanup = script.index('del /Q "%~dp0v1.7.2.zip"')
    assert script.index('if "%FAILED%"=="1" goto rollback') < cleanup
    assert cleanup < script.index(':rollback')
    assert 'del /Q "%ROOT%\\*.zip"' not in script


def test_login_requests_preparation_instead_of_suggesting_manual_install():
    from types import SimpleNamespace
    from 统一启动器 import UnifiedLauncher
    requested = []
    service = SimpleNamespace(environment_state=lambda **kw: {'ready': False})
    launcher = SimpleNamespace(
        _get_zhs_service=lambda: service,
        _request_zhs_runtime_prepare=lambda **kw: requested.append(kw) or True,
    )
    result = UnifiedLauncher.start_zhs_login_from_web(launcher)
    assert result['preparing'] is True
    assert requested == [{'force': True}]
    assert '后台准备' in result['message']


def test_login_uses_ready_environment_without_reinstalling():
    from types import SimpleNamespace
    from 统一启动器 import UnifiedLauncher
    service = SimpleNamespace(
        environment_state=lambda **kw: {'ready': True},
        start_login=lambda account: {'ok': True, 'account': account},
    )
    launcher = SimpleNamespace(_get_zhs_service=lambda: service)
    assert UnifiedLauncher.start_zhs_login_from_web(launcher, 2) == {'ok': True, 'account': 2}
