"""Bounded recovery for the overview GET, never for report writes or job creation."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sales_backend.domain.work_report import WorkReportError

logger = logging.getLogger(__name__)
OVERVIEW_READ_BUDGET_SECONDS = 55.0


class OverviewReadTimeout(Exception):
    """Only the overview material read timed out, inside its transaction.

    The caller must let the complete transaction/connection context exit before
    this exception reaches recovery. Setup and cleanup timeouts must remain
    ordinary TimeoutError exceptions and must not be marked with this type.
    """


def _unavailable() -> WorkReportError:
    return WorkReportError(
        'WORK_REPORT_READ_TIMEOUT', '报告概览读取超时，请稍后重新读取。', status=503, retryable=True,
    )


async def read_overview_with_recovery[T](read_once: Callable[[], Awaitable[T]]) -> T:
    """Run at most two complete, fresh read transactions within one budget.

    read_once owns authorization, material collection, binding and cleanup.
    Only a marked material-read timeout permits another attempt. Cancellation,
    business errors and DatabaseBusy propagate unchanged. The budget excludes
    HTTP dependencies/auditing and may be exceeded while cancellation cleans up;
    it is not an HTTP response deadline.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + OVERVIEW_READ_BUDGET_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in (1, 2):
                if loop.time() >= deadline:
                    raise _unavailable()
                try:
                    result = await read_once()
                except OverviewReadTimeout:
                    retry = attempt == 1 and loop.time() < deadline
                    # Never log exception messages, facts, selectors or bindings.
                    logger.warning('work_report_overview_read_timeout attempt=%d retry=%s', attempt, retry)
                    if not retry:
                        raise _unavailable() from None
                else:
                    # A callback that finishes cleanup after the deadline must
                    # not return a late result or start another transaction.
                    if loop.time() >= deadline:
                        raise _unavailable()
                    return result
    except TimeoutError:
        # Includes the shared deadline and unmarked setup/cleanup timeouts.
        # Neither is evidence that a new transaction should be attempted.
        logger.warning('work_report_overview_read_timeout retry=False phase=budget_or_transaction')
        raise _unavailable() from None
    raise AssertionError('overview recovery exhausted without a result')
