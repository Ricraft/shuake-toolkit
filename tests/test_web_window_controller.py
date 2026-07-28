from types import SimpleNamespace

from src.web_window_controller import WebWindowController


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class Window:
    def __init__(self, *, evaluate_result=True, destroy_error=None):
        self.events = SimpleNamespace(closing=Event())
        self.evaluate_result = evaluate_result
        self.destroy_error = destroy_error
        self.evaluate_calls = 0
        self.destroy_calls = 0

    def evaluate_js(self, _script):
        self.evaluate_calls += 1
        return self.evaluate_result

    def destroy(self):
        self.destroy_calls += 1
        if self.destroy_error:
            raise self.destroy_error


def build_controller(window=None, **overrides):
    state = {"window": window}
    logs = []
    scheduled = []
    exits = []
    minimized = []
    controller = WebWindowController(
        get_window=lambda: state["window"],
        set_window=lambda value: state.update(window=value),
        schedule=overrides.pop(
            "schedule",
            lambda delay, callback: scheduled.append((delay, callback)),
        ),
        log=logs.append,
        should_minimize_to_tray=overrides.pop(
            "should_minimize_to_tray",
            lambda: False,
        ),
        minimize_window=lambda: minimized.append(True),
        confirmed_exit=overrides.pop(
            "confirmed_exit",
            lambda: exits.append(True),
        ),
        save_geometry=overrides.pop("save_geometry", lambda: None),
        **overrides,
    )
    return controller, state, logs, scheduled, exits, minimized


def test_attach_resets_close_state_and_registers_handler():
    window = Window()
    controller, state, _logs, _scheduled, _exits, _minimized = build_controller()
    controller.allow_close = True
    controller.confirmation_pending = True
    preferences = []

    def closing_handler():
        return False

    controller.attach(
        window,
        closing_handler=closing_handler,
        apply_preferences=lambda initial=False: preferences.append(initial),
    )

    assert state["window"] is window
    assert controller.allow_close is False
    assert controller.confirmation_pending is False
    assert window.events.closing.handlers == [closing_handler]
    assert preferences == [True]


def test_confirmation_requests_are_coalesced_until_modal_is_shown():
    window = Window(evaluate_result=True)
    controller, _state, _logs, scheduled, exits, _minimized = build_controller(
        window
    )

    assert controller.request_exit_confirmation() is True
    assert controller.request_exit_confirmation() is True
    assert len(scheduled) == 1
    assert controller.confirmation_pending is True

    assert scheduled[0][0] == 100
    assert scheduled[0][1]() is True
    assert controller.confirmation_pending is False
    assert window.evaluate_calls == 1
    assert exits == []


def test_missing_modal_cleanup_failure_is_logged_without_escaping():
    def fail_exit():
        raise RuntimeError("cleanup unavailable")

    window = Window(evaluate_result=False)
    controller, _state, logs, _scheduled, _exits, _minimized = build_controller(
        window,
        confirmed_exit=fail_exit,
    )

    assert controller.show_exit_confirmation() is False

    assert any("退出确认弹窗不可用" in message for message in logs)
    assert any("cleanup unavailable" in message for message in logs)


def test_close_failure_restores_guard_so_user_can_retry_safely():
    window = Window(destroy_error=RuntimeError("renderer busy"))
    saved = []
    controller, _state, logs, _scheduled, _exits, _minimized = build_controller(
        window,
        save_geometry=lambda: saved.append(True),
    )

    assert controller.close_window() is False

    assert saved == [True]
    assert window.destroy_calls == 1
    assert controller.allow_close is False
    assert any("renderer busy" in message for message in logs)


def test_geometry_failure_does_not_prevent_safe_window_close():
    window = Window()

    def fail_geometry():
        raise OSError("geometry unavailable")

    controller, _state, logs, _scheduled, _exits, _minimized = build_controller(
        window,
        save_geometry=fail_geometry,
    )

    assert controller.close_window() is True

    assert window.destroy_calls == 1
    assert controller.allow_close is True
    assert any("geometry unavailable" in message for message in logs)


def test_close_event_minimizes_without_requesting_exit():
    window = Window()
    controller, _state, logs, _scheduled, exits, minimized = build_controller(
        window,
        should_minimize_to_tray=lambda: True,
    )

    assert controller.handle_window_closing() is False

    assert minimized == [True]
    assert exits == []
    assert any("最小化窗口" in message for message in logs)
