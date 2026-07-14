"""Lifecycle helpers shared by single- and multi-account runners."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable


async def cancel_background_tasks(tasks: Iterable[asyncio.Task | None]) -> None:
    """Cancel long-running helper tasks and consume cancellation results."""
    current = asyncio.current_task()
    cancellable = [task for task in tasks if task is not None and task is not current]
    for task in cancellable:
        if not task.done():
            task.cancel()
    if cancellable:
        await asyncio.gather(*cancellable, return_exceptions=True)
