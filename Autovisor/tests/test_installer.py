import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules import installer

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.errors = []
        self.logs = []
        self.warnings = []

    def info(self, _message):
        return None

    def error(self, message):
        self.errors.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def write_log(self, message):
        self.logs.append(message)


def _patch_install(monkeypatch, *, import_result=None, import_error=None):
    logger = _Logger()
    wheel_path = "synthetic-package.whl"
    monkeypatch.setattr(installer, "logger", logger)
    monkeypatch.setattr(installer.os, "makedirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(installer, "cleanup_package_targets", lambda *_args: None)
    monkeypatch.setattr(
        installer,
        "download_wheel",
        lambda *_args, **_kwargs: wheel_path,
    )
    monkeypatch.setattr(installer, "extract_whl", lambda *_args: None)
    monkeypatch.setattr(installer.importlib, "invalidate_caches", lambda: None)

    def fake_import(_alias):
        if import_error is not None:
            raise import_error
        return import_result

    monkeypatch.setattr(installer, "import_module", fake_import)
    monkeypatch.setattr(installer.os.path, "exists", lambda path: path == wheel_path)

    def fail_remove(path):
        assert path == wheel_path
        raise PermissionError("file is locked")

    monkeypatch.setattr(installer.os, "remove", fail_remove)
    return logger


def test_cleanup_failure_does_not_override_successful_install(monkeypatch):
    installed_module = object()
    logger = _patch_install(monkeypatch, import_result=installed_module)

    result = installer.install_package("numpy", "1.0", "mirror", "https://mirror")

    assert result is installed_module
    assert any("清理临时 wheel 文件失败" in item for item in logger.warnings)


def test_cleanup_failure_does_not_override_install_failure(monkeypatch):
    logger = _patch_install(
        monkeypatch,
        import_error=ImportError("binary module is broken"),
    )

    result = installer.install_package("numpy", "1.0", "mirror", "https://mirror")

    assert result is None
    assert any("处理失败" in item for item in logger.errors)
    assert any("清理临时 wheel 文件失败" in item for item in logger.warnings)
