import subprocess
import shutil
import zipfile
from types import SimpleNamespace
from unittest import mock

import pytest

from src import zhs_vendor
from src.core_manager import CoreManager
from src.web_action_service import ZHS_UPDATE_LINKS, WebActionService


def write_pyproject(root, dependencies):
    lines = [
        "[project]",
        'name = "zhs"',
        'version = "0.1.6"',
        "dependencies = [",
    ]
    for dep in dependencies:
        lines.append(f'  "{dep}",')
    lines.append("]")
    path = root / "pyproject.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_vendor(root, version="0.1.5", dependencies=("httpx>=0.28", "typer>=0.15")):
    write_pyproject(root, dependencies)
    pkg = root / "src" / "zhs"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text('__version__ = "%s"\n' % version, encoding="utf-8")
    return root


def test_read_dependencies_parses_project_table(tmp_path):
    make_vendor(tmp_path, dependencies=("httpx>=0.28", "qrcode>=8.2"))
    assert zhs_vendor.read_dependencies(tmp_path) == ["httpx>=0.28", "qrcode>=8.2"]


@pytest.mark.parametrize('failure', ['reinstall', 'requirements', 'smoke', 'version_record'])
def test_core_update_restores_source_dependencies_and_version_record(tmp_path, monkeypatch, failure):
    root = tmp_path / 'installed'
    target = make_vendor(root / 'zhs', dependencies=('httpx>=0.28',))
    zhs_vendor.write_manifest(target)
    requirements = root / 'requirements-zhs.txt'
    requirements.write_bytes(b'# old exact bytes\r\nhttpx>=0.28\r\n')
    source = make_vendor(tmp_path / 'upstream', version='0.1.6', dependencies=('httpx>=0.29',))
    archive = tmp_path / 'upstream.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        for path in source.rglob('*'):
            if path.is_file():
                package.write(path, 'upstream/' + path.relative_to(source).as_posix())
    manager = CoreManager(str(root))
    manager.local_versions = {'zhs': '0.1.5', 'last_check': 'before'}
    manager._save_local_versions()
    before_version = (root / '.core_versions.json').read_bytes()
    before_source = (target / 'pyproject.toml').read_bytes()
    monkeypatch.setattr(manager, 'download_file', lambda url, dest, *a, **kw: bool(shutil.copyfile(archive, dest)))
    monkeypatch.setattr(zhs_vendor, 'find_venv_python', lambda *a: 'fake-python')
    for key, method in [('reinstall', 'reinstall_editable'), ('requirements', 'ensure_requirements'), ('smoke', 'smoke_check')]:
        monkeypatch.setattr(zhs_vendor, method, lambda *a, _key=key, **kw: (_key != failure, 'injected failure'))
    if failure == 'version_record':
        monkeypatch.setattr(manager, '_save_local_versions', lambda: False)
    assert manager.install_zhs({'version': '0.1.6', 'tag': 'v0.1.6', 'download_url': 'https://example.invalid/source.zip'}) is False
    assert (target / 'pyproject.toml').read_bytes() == before_source
    assert requirements.read_bytes() == b'# old exact bytes\r\nhttpx>=0.28\r\n'
    assert (root / '.core_versions.json').read_bytes() == before_version
    assert manager.local_versions == {'zhs': '0.1.5', 'last_check': 'before'}
    assert zhs_vendor.check_drift(target) == ([], [])


def test_read_dependencies_missing_file_returns_empty(tmp_path):
    assert zhs_vendor.read_dependencies(tmp_path) == []


def test_write_requirements_file_is_pure_ascii(tmp_path):
    path = zhs_vendor.write_requirements_file(tmp_path / "requirements-zhs.txt", ["httpx>=0.28"])
    data = path.read_bytes()
    assert all(byte < 128 for byte in data)
    text = data.decode("ascii")
    assert "AUTO-GENERATED" in text
    assert text.rstrip().endswith("httpx>=0.28")


def test_sync_from_source_regenerates_requirements_sibling(tmp_path):
    source = make_vendor(tmp_path / "upstream", version="0.1.6", dependencies=("httpx>=0.28", "pydantic>=2.10"))
    target = make_vendor(tmp_path / "project" / "zhs", version="0.1.5", dependencies=("httpx>=0.28",))
    logs = []

    plan = zhs_vendor.sync_from_source(source, target, force=True, record_source_path=False, log=logs.append)

    requirements = tmp_path / "project" / "requirements-zhs.txt"
    assert plan["requirements"] == str(requirements)
    assert requirements.is_file()
    text = requirements.read_text(encoding="ascii")
    assert "pydantic>=2.10" in text
    assert any("[依赖]" in item for item in logs)


def test_sync_from_source_dry_run_writes_no_requirements(tmp_path):
    source = make_vendor(tmp_path / "upstream", version="0.1.6")
    target = make_vendor(tmp_path / "project" / "zhs")

    plan = zhs_vendor.sync_from_source(source, target, force=True, dry_run=True)

    assert "requirements" not in plan
    assert not (tmp_path / "project" / "requirements-zhs.txt").exists()


def test_ensure_requirements_without_venv_python(tmp_path):
    ok, message = zhs_vendor.ensure_requirements(tmp_path / "zhs")
    assert ok is False
    assert "venv-zhs" in message


def test_ensure_requirements_retries_mirrors(tmp_path):
    project = tmp_path / "project"
    requirements = project / "requirements-zhs.txt"
    requirements.parent.mkdir(parents=True)
    requirements.write_text("httpx>=0.28\n", encoding="ascii")
    python = project / ".runtime" / "venv-zhs" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")

    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 else 0, "", "boom")

    logs = []
    with mock.patch("src.zhs_vendor.subprocess.run", side_effect=fake_run):
        ok, message = zhs_vendor.ensure_requirements(
            project,
            venv_python=python,
            requirements_path=requirements,
            mirrors=("https://mirror-a/simple", "https://mirror-b/simple"),
            log=logs.append,
        )

    assert ok is True
    assert message == ""
    assert len(calls) == 2
    assert calls[0][:3] == [str(python), "-m", "pip"]
    assert "-r" in calls[1] and str(requirements) in calls[1]
    assert calls[1][calls[1].index("-i") + 1] == "https://mirror-b/simple"
    assert any("[通过]" in item for item in logs)


def test_open_zhs_link_accepts_dialog_keys():
    opened = []
    def open_external_url(url):
        opened.append(url)
        return {"ok": True}

    launcher = SimpleNamespace(open_external_url=open_external_url)
    service = WebActionService(launcher)

    for key in ("zhs_github", "zhs_project", "zhs_releases"):
        result = service.perform("open_zhs_link", key)
        assert result["ok"] is True, key

    assert opened == [ZHS_UPDATE_LINKS["zhs_github"]] * 2 + [ZHS_UPDATE_LINKS["zhs_releases"]]
