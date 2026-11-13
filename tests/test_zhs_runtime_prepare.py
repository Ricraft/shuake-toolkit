import sys
from types import SimpleNamespace

from src import zhs_runtime_prepare


def _install_fake_bootstrap(monkeypatch, **overrides):
    fake = SimpleNamespace(
        find_zhs_python=lambda preferred=None: "C:/Python313/python.exe",
        ensure_zhs_runtime=lambda *args, **kwargs: "venv-python",
    )
    for name, value in overrides.items():
        setattr(fake, name, value)
    monkeypatch.setitem(sys.modules, "bootstrap", fake)
    return fake


def test_prepare_succeeds_with_old_bootstrap_that_only_has_core_helpers(
    monkeypatch, capsys
):
    """自更新后 bootstrap.py 仍是 v1.7.0：没有 --prepare-zhs，但这两个函数在。"""
    fake = _install_fake_bootstrap(monkeypatch)
    called = {}

    def ensure_runtime(python_exe, *, venv_dir=None):
        called["python_exe"] = python_exe
        called["venv_dir"] = venv_dir

    fake.ensure_zhs_runtime = ensure_runtime

    assert zhs_runtime_prepare.prepare() == 0
    assert called["python_exe"] == "C:/Python313/python.exe"
    assert "ZHS 运行环境已就绪" in capsys.readouterr().out


def test_prepare_fails_when_bootstrap_is_too_old(monkeypatch, capsys):
    _install_fake_bootstrap(monkeypatch, ensure_zhs_runtime=None)

    assert zhs_runtime_prepare.prepare() == 1
    out = capsys.readouterr().out
    assert "bootstrap.py 版本过旧" in out
    assert "bootstrap.py --install" in out


def test_prepare_reports_missing_python_313(monkeypatch, capsys):
    _install_fake_bootstrap(monkeypatch, find_zhs_python=lambda preferred=None: None)

    assert zhs_runtime_prepare.prepare() == 1
    assert "未找到 Python 3.13/3.14" in capsys.readouterr().out


def test_prepare_reports_bootstrap_failure(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise RuntimeError("pip 源不可用")

    _install_fake_bootstrap(monkeypatch, ensure_zhs_runtime=boom)

    assert zhs_runtime_prepare.prepare() == 1
    assert "ZHS 核心环境准备失败：pip 源不可用" in capsys.readouterr().out


def test_quiet_suppresses_logs(monkeypatch, capsys):
    _install_fake_bootstrap(monkeypatch)

    assert zhs_runtime_prepare.prepare(quiet=True) == 0
    assert capsys.readouterr().out == ""


def test_main_parses_flags(monkeypatch):
    _install_fake_bootstrap(monkeypatch)
    called = {}
    monkeypatch.setattr(
        zhs_runtime_prepare,
        "prepare",
        lambda **kwargs: called.update(kwargs) or 0,
    )

    assert zhs_runtime_prepare.main(["--quiet", "--venv-dir", "custom"]) == 0
    assert called == {"quiet": True, "venv_dir": "custom"}
