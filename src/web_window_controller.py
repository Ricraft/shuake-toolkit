"""WebView window lifecycle orchestration for the Web-only launcher."""

from __future__ import annotations

from typing import Callable, Optional


class WebWindowController:
    """Coordinate close confirmation without coupling it to runtime cleanup."""

    EXIT_MODAL_SCRIPT = """
        (function () {
            if (typeof showExitModal === 'function') {
                showExitModal();
                return true;
            }
            return false;
        })()
    """

    def __init__(
        self,
        *,
        get_window: Callable[[], object],
        set_window: Callable[[object], None],
        schedule: Callable[[int, Callable[[], object]], None],
        log: Callable[[str], None],
        should_minimize_to_tray: Callable[[], bool],
        minimize_window: Callable[[], object],
        confirmed_exit: Callable[[], object],
        save_geometry: Optional[Callable[[], object]] = None,
    ):
        self.get_window = get_window
        self.set_window = set_window
        self.schedule = schedule
        self.log = log
        self.should_minimize_to_tray = should_minimize_to_tray
        self.minimize_window = minimize_window
        self.confirmed_exit = confirmed_exit
        self.save_geometry = save_geometry or (lambda: None)
        self.allow_close = False
        self.confirmation_pending = False

    def attach(self, window, *, closing_handler, apply_preferences) -> None:
        self.set_window(window)
        self.allow_close = False
        self.confirmation_pending = False
        if not window:
            return
        window.events.closing += closing_handler
        apply_preferences(initial=True)

    def _run_confirmed_exit(self, failure_prefix: str) -> bool:
        try:
            self.confirmed_exit()
            return True
        except Exception as exc:
            self.log(f"{failure_prefix}: {repr(exc)[:120]}")
            return False

    def show_exit_confirmation(self) -> bool:
        """Show the Web modal or fall back to the already-requested safe exit."""
        self.confirmation_pending = False
        window = self.get_window()
        if not window:
            return False

        try:
            shown = bool(window.evaluate_js(self.EXIT_MODAL_SCRIPT))
        except Exception as exc:
            self.log(
                "显示退出确认弹窗失败，将按本次退出请求安全关闭: "
                f"{repr(exc)[:120]}"
            )
            shown = False

        if shown:
            return True

        self.log("退出确认弹窗不可用，将按本次退出请求安全关闭。")
        self._run_confirmed_exit("退出清理失败，窗口保持打开")
        return False

    def request_exit_confirmation(self) -> bool:
        """Queue one non-blocking Web confirmation request."""
        if not self.get_window():
            return False
        if self.confirmation_pending:
            return True

        self.confirmation_pending = True
        try:
            self.schedule(100, self.show_exit_confirmation)
            return True
        except Exception as exc:
            self.confirmation_pending = False
            self.log(f"退出确认任务创建失败: {repr(exc)[:120]}")
            return False

    def handle_window_closing(self, *, request_confirmation=None):
        if self.allow_close:
            return None

        if self.should_minimize_to_tray():
            self.log("已按偏好设置最小化窗口，核心任务继续运行。")
            self.minimize_window()
            return False

        request_confirmation = (
            request_confirmation or self.request_exit_confirmation
        )
        if request_confirmation():
            return False

        # Even when the modal cannot be queued, cleanup must run before closing.
        self._run_confirmed_exit("退出清理失败，窗口保持打开")
        return False

    def close_window(self) -> bool:
        """Destroy the WebView while restoring the guard if destruction fails."""
        try:
            self.save_geometry()
        except Exception as exc:
            self.log(f"保存窗口状态失败，将继续关闭: {repr(exc)[:120]}")
        window = self.get_window()
        if not window:
            return True

        self.allow_close = True
        try:
            window.destroy()
            return True
        except Exception as exc:
            self.allow_close = False
            self.log(f"关闭主窗口失败，窗口仍可安全重试: {repr(exc)[:120]}")
            return False
