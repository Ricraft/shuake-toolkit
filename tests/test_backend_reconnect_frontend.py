from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def test_runtime_disconnect_enters_full_initialization_retry():
    disconnect_source = function_source(
        "markBackendDisconnected",
        "refreshRuntime",
    )
    refresh_source = function_source(
        "refreshRuntime",
        "scheduleInitRetry",
    )

    assert "backendConnected = false" in disconnect_source
    assert "initialized = false" in disconnect_source
    assert "backendWaitStartedAt = Date.now()" in disconnect_source
    assert "backendHintShown = false" in disconnect_source
    assert "clearInterval(runtimeTimer)" in disconnect_source
    assert "runtimeTimer = null" in disconnect_source
    assert "scheduleInitRetry(300)" in disconnect_source
    assert refresh_source.count("markBackendDisconnected(") == 2
    assert "正在重新初始化" in refresh_source


def test_successful_initialization_restores_connected_status_and_polling():
    init_source = function_source("init", "initAboutPageEffects")

    assert "const payload = await apiCall('get_initial_state')" in init_source
    assert "initialized = true" in init_source
    assert "backendConnected = true" in init_source
    assert "setBackendStatus('Python 后端已连接')" in init_source
    assert "startRuntimePolling()" in init_source
    assert init_source.index("setBackendStatus('Python 后端已连接')") < (
        init_source.index("startRuntimePolling()")
    )
