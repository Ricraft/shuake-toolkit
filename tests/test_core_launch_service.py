from types import SimpleNamespace

from src.core_launch_service import CoreLaunchService


def make_python_runtime_launcher(*, install_accepted=True):
    logs = []
    installs = []
    rejections = []
    launcher = SimpleNamespace(
        autovisor_installing=False,
        get_python_executable=lambda: "python.exe",
        _check_autovisor_dependencies=lambda _python: [],
        log_system=logs.append,
        _reject_runtime_start=lambda core, message, **_kwargs: (
            rejections.append((core, message)) or False
        ),
    )

    def install(python, missing, *, ensure_playwright_browser=False):
        installs.append((python, missing, ensure_playwright_browser))
        return install_accepted

    launcher._install_autovisor_dependencies_async = install
    launcher.logs = logs
    launcher.installs = installs
    launcher.rejections = rejections
    return launcher


def test_browser_only_install_has_clear_log_and_explicit_installing_result():
    launcher = make_python_runtime_launcher()
    service = CoreLaunchService(launcher)

    status, executable = service._prepare_autovisor_python_runtime(
        runtime_state={"needs_playwright_browser": True},
    )

    assert (status, executable) == ("installing", "python.exe")
    assert launcher.installs == [("python.exe", [], True)]
    assert any("Playwright Chromium" in line for line in launcher.logs)
    assert not any("缺少 Autovisor 依赖:" in line for line in launcher.logs)
    assert launcher.rejections == []


def test_rejected_dependency_install_reports_failure():
    launcher = make_python_runtime_launcher(install_accepted=False)
    service = CoreLaunchService(launcher)

    status, executable = service._prepare_autovisor_python_runtime(
        runtime_state={"needs_playwright_browser": True},
    )

    assert (status, executable) == ("failed", None)
    assert launcher.rejections == [
        ("autovisor", "Autovisor 依赖安装任务未能启动")
    ]


def test_executable_autovisor_skips_python_checks_and_submits_process(tmp_path):
    entry = tmp_path / "Autovisor.exe"
    config = tmp_path / "configs.ini"
    entry.write_bytes(b"fixture")
    config.write_text("[course-url]\nURL1=https://example.test\n", encoding="utf-8")
    calls = []
    logs = []
    process_service = SimpleNamespace(
        start=lambda **kwargs: calls.append(kwargs) or True,
    )
    launcher = SimpleNamespace(
        running={"autovisor": False},
        starting={"autovisor": False},
        autovisor_installing=False,
        autovisor_path=str(tmp_path),
        _last_start_failure_kind={},
        _get_runtime_coordinator=lambda: SimpleNamespace(
            prepare_start=lambda _core: True
        ),
        find_autovisor_path=lambda _base: str(tmp_path),
        get_base_dir=lambda: str(tmp_path),
        _get_autovisor_multi_mode=lambda: False,
        _get_autovisor_entry_path=lambda _multi: (
            str(entry),
            entry.name,
            True,
            True,
        ),
        _load_autovisor_config_data=lambda: {
            "accounts": [
                {
                    "username": "alice",
                    "course_urls": ["https://example.test"],
                }
            ]
        },
        _validate_autovisor_runtime=lambda _accounts, _multi: None,
        _prepare_autovisor_config=lambda _path, _multi: {
            "browser_summaries": [],
            "needs_playwright_browser": False,
        },
        get_python_executable=lambda: (_ for _ in ()).throw(
            AssertionError("EXE 模式不应检查 Python")
        ),
        _claim_runtime_start=lambda _core: True,
        _get_runtime_process_service=lambda: process_service,
        _build_encoding_candidates=lambda *_values: ("utf-8",),
        _show_error=lambda *_args: None,
        _reject_runtime_start=lambda *_args, **_kwargs: False,
        log_system=logs.append,
        log=lambda *_args: None,
        question_bank=SimpleNamespace(running=True, port=8083),
        stop_requested={"autovisor": False},
        start_question_bank=lambda **_kwargs: None,
        get_question_bank_url=lambda: "http://127.0.0.1:8083",
    )

    accepted = CoreLaunchService(launcher).start_autovisor()

    assert accepted is True
    assert calls[0]["command"] == [str(entry)]
    assert calls[0]["cwd"] == str(tmp_path)
    assert callable(calls[0]["before_launch"])
    assert callable(calls[0]["env_factory"])
    assert any("跳过 Python 依赖检查" in line for line in logs)
