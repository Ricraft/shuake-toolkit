import asyncio
import sys
from pathlib import Path

import pytest


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.async_utils import background_task_scope

sys.path.remove(_AUTOVISOR_ROOT)


def test_background_task_scope_cancels_workers_after_success():
    states = []

    async def run():
        async def worker():
            try:
                await asyncio.Event().wait()
            finally:
                states.append("cancelled")

        async with background_task_scope() as tasks:
            tasks.append(asyncio.create_task(worker()))
            await asyncio.sleep(0)

    asyncio.run(run())
    assert states == ["cancelled"]


def test_background_task_scope_cancels_workers_when_operation_fails():
    states = []

    async def run():
        async def worker():
            try:
                await asyncio.Event().wait()
            finally:
                states.append("cancelled")

        async with background_task_scope() as tasks:
            tasks.append(asyncio.create_task(worker()))
            await asyncio.sleep(0)
            raise RuntimeError("course failed")

    with pytest.raises(RuntimeError, match="course failed"):
        asyncio.run(run())
    assert states == ["cancelled"]
