"""Finish owned file operations before their files/directories can be released."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress


async def settle_owned_task[T](task: asyncio.Task[T]) -> T:
    """Join an already owned operation even if cleanup receives another cancel.

    Only use for finite local operations or cleanup with its own timeout. This is
    not a shield for normal application work, DB commits or external API calls.
    """
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def file_io[T](operation: Callable[..., T], *args) -> T:
    """Cancellation cannot stop a thread; join it before releasing its file.

    The operation is a bounded-size local file operation. A kernel/filesystem
    stall can delay cleanup; abandoning the thread would make cleanup unsafe.
    """
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Retrieve an eventual error without replacing the original cancellation.
        with suppress(Exception):
            await settle_owned_task(task)
        raise
