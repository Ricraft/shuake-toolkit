"""Bounded diagnostics for recoverable browser-automation failures."""

from __future__ import annotations

import time
from collections.abc import Callable


def summarize_exception(error: BaseException, limit: int = 160) -> str:
    """Return a single-line, bounded exception summary for runtime logs."""
    detail = " ".join(str(error).split()) or "无详细信息"
    if len(detail) > limit:
        detail = detail[: limit - 1] + "…"
    return f"{type(error).__name__}: {detail}"


class RateLimitedDiagnostics:
    """Emit the first warning immediately and bound repeats by key."""

    def __init__(
        self,
        logger,
        *,
        interval_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.logger = logger
        self.interval_seconds = max(float(interval_seconds), 0)
        self.clock = clock
        self._last_emitted: dict[str, float] = {}

    def warn(self, key: str, message: str, error: BaseException) -> bool:
        now = self.clock()
        previous = self._last_emitted.get(key)
        if previous is not None and now - previous < self.interval_seconds:
            return False
        self._last_emitted[key] = now
        self.logger.warn(f"{message}: {summarize_exception(error)}")
        return True

    def clear(self) -> None:
        self._last_emitted.clear()
