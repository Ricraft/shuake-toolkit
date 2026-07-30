"""Tracked delayed callbacks used during launcher startup and Web actions."""

from __future__ import annotations

import threading
from collections.abc import Callable


class ScheduledTaskService:
    """Schedule daemon timers with visible failures and complete cancellation."""

    def __init__(
        self,
        *,
        log: Callable[[str], None] | None = None,
        timer_factory=threading.Timer,
        lock=None,
        pending=None,
    ):
        self.log = log or (lambda _message: None)
        self.timer_factory = timer_factory
        self.lock = lock or threading.RLock()
        self.pending = pending if pending is not None else set()

    def _log(self, message: str) -> None:
        try:
            self.log(message)
        except Exception:
            pass

    @staticmethod
    def _callback_name(callback) -> str:
        return str(
            getattr(callback, "__qualname__", None)
            or getattr(callback, "__name__", None)
            or type(callback).__name__
        )

    def schedule(self, delay_ms: int | float, callback: Callable[[], None]):
        delay_seconds = max(float(delay_ms), 0.0) / 1000.0
        if delay_seconds <= 0:
            callback()
            return None

        timer = None
        callback_name = self._callback_name(callback)

        def run_callback():
            try:
                callback()
            except Exception as exc:
                self._log(
                    f"[延迟任务] {callback_name} 执行失败: "
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
            finally:
                with self.lock:
                    self.pending.discard(timer)

        try:
            timer = self.timer_factory(delay_seconds, run_callback)
            timer.daemon = True
            with self.lock:
                self.pending.add(timer)
            timer.start()
            return timer
        except Exception as exc:
            if timer is not None:
                with self.lock:
                    self.pending.discard(timer)
            self._log(
                f"[延迟任务] {callback_name} 注册失败: "
                f"{type(exc).__name__}: {str(exc)[:160]}"
            )
            return None

    def cancel_all(self) -> int:
        with self.lock:
            timers = list(self.pending)
            self.pending.clear()

        for timer in timers:
            try:
                timer.cancel()
            except Exception as exc:
                self._log(
                    "[延迟任务] 取消任务失败: "
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
        return len(timers)
