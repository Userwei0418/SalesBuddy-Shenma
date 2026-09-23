"""One owner for each ffmpeg spawn, communication task and cancellation cleanup."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sales_backend.async_resources import settle_owned_task

FFMPEG_EXECUTABLE = "/usr/bin/ffmpeg"
PROCESS_CLEANUP_SECONDS = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stderr: bytes


async def _kill_and_reap(process, communication=None) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    if communication is None:
        communication = asyncio.create_task(process.communicate())
    # SIGKILL is followed by a join, not by dropping the Process object. A local
    # OS/pipe failure is surfaced instead of waiting forever during shutdown.
    async with asyncio.timeout(PROCESS_CLEANUP_SECONDS):
        await communication
        await process.wait()


async def _cleanup(process, communication=None) -> None:
    try:
        await settle_owned_task(asyncio.create_task(_kill_and_reap(process, communication)))
    except Exception:
        # The cancellation/timeout/conversion failure is the primary outcome.
        logger.exception("audio_process_cleanup_failed")


async def run_ffmpeg(*arguments: str, timeout: float) -> ProcessResult:
    """Run with a deadline; cancellation also covers the spawn-return boundary."""
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(  # noqa: S603
        FFMPEG_EXECUTABLE, *arguments,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    ))
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        try:
            process = await settle_owned_task(spawn)
        except Exception:
            logger.debug("audio_process_spawn_failed_during_cancellation", exc_info=True)
        else:
            await _cleanup(process)
        raise

    communication = asyncio.create_task(process.communicate())
    try:
        _, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout)
    except BaseException:
        await _cleanup(process, communication)
        raise
    return ProcessResult(process.returncode, stderr)
