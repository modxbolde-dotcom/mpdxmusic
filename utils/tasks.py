from __future__ import annotations
import asyncio
import logging
from typing import Any, Coroutine, Optional, Set
LOGGER = logging.getLogger(__name__)
_BACKGROUND_TASKS: Set[asyncio.Task] = set()

def _on_done(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        LOGGER.exception('background task %s failed: %s', task.get_name(), exc, exc_info=exc)

def spawn(coro: Coroutine[Any, Any, Any], *, name: Optional[str]=None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_done)
    return task

def pending_tasks() -> int:
    return len(_BACKGROUND_TASKS)

async def cancel_all(timeout: float=5.0) -> None:
    tasks = list(_BACKGROUND_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        try:
            await asyncio.wait(tasks, timeout=timeout)
        except Exception:
            pass
