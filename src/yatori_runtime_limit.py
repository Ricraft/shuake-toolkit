"""Yatori's per-process maximum runtime, independent of completion handling."""

from __future__ import annotations

import threading


class YatoriRuntimeLimit:
    """Guard a daemon timer by process identity and the supervisor's state lock."""

    def __init__(self, launcher, *, timer_factory=threading.Timer):
        self.launcher = launcher
        self.timer_factory = timer_factory
        self.lock = getattr(launcher, "_runtime_lock", None) or threading.RLock()
        self._process = None
        self._timer = None
        self._token = None

    def _cancel_locked(self):
        timer = self._timer
        self._timer = None
        self._process = None
        self._token = None
        if timer is not None:
            timer.cancel()

    def cancel(self, process=None):
        """An old monitor must never cancel a newer process's timer."""
        with self.lock:
            if process is None or self._process is process:
                self._cancel_locked()

    def started(self, process, minutes):
        """Called immediately after mark_running; edits affect the next run only."""
        with self.lock:
            self._cancel_locked()
            if (
                not minutes
                or self.launcher.processes.get("yatori") is not process
                or not self.launcher.running.get("yatori")
                or self.launcher.stop_requested.get("yatori")
            ):
                return
            token = object()
            timer = self.timer_factory(minutes * 60, lambda: self._expire(process, token))
            timer.daemon = True
            self._process = process
            self._token = token
            self._timer = timer
            timer.start()

    def _expire(self, process, token):
        with self.lock:
            if self._token is not token or self._process is not process:
                return
            poll = getattr(process, "poll", None)
            if (
                self.launcher.processes.get("yatori") is not process
                or not self.launcher.running.get("yatori")
                or self.launcher.stop_requested.get("yatori")
                or (callable(poll) and poll() is not None)
            ):
                self._cancel_locked()
                return
            self._cancel_locked()
            self.launcher.log_system("Yatori 已达到最长运行时间，正在停止；不代表课程已完成。")
            # stop_yatori sets stop_requested before termination, so the normal
            # exit path cannot treat a timeout as successful course completion.
            self.launcher.stop_yatori()
