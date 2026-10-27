"""Bound cumulative business reads without cancelling mutations or streaming.

Enter before the read-only transaction so cancellation unwinds its rollback and
connection release before TimeoutError reaches the HTTP handler. The main app's
permission dependency shares the budget across authentication and the endpoint;
direct router users retain an endpoint budget. Bounded cleanup is additional.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar

BUSINESS_READ_SECONDS = 35
READ_ROLLBACK_SECONDS = 2
logger = logging.getLogger(__name__)
BOUNDED_READ_ENDPOINTS = frozenset({
    ("business", "opportunity_create_customers"),
    ("business", "opportunity_create_access"),
    ("opportunity_access", "company_follow_up_options"),
    ("opportunity_access", "company_follow_up_customers"),
    ("opportunity_access", "company_follow_up_access"),
    ("customer_assets", "map_customers"),
    ("customer_assets", "read_assets"),
    ("customers", "customer_portfolio"),
    ("customers", "customer_claim_pool"),
    ("customers", "list_customers"),
    ("profile", "scoped_performance"),
    ("business", "opportunities"),
    ("business", "opportunities_overview"),
})
_read_deadline: ContextVar[float | None] = ContextVar("read_deadline", default=None)


class _BoundedReadTransactionExit:
    def __init__(self, connection, transaction):
        self.connection = connection
        self.transaction = transaction

    async def __aenter__(self):
        return await self.transaction.__aenter__()

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
            try:
                async with asyncio.timeout(READ_ROLLBACK_SECONDS):
                    return await self.transaction.__aexit__(exc_type, exc, traceback)
            except Exception as failure:
                # A failed/cancelled rollback must never leave an ambiguous
                # connection reusable. terminate is synchronous and closes it.
                self.connection.terminate()
                logger.warning("bounded_read_rollback_failed exception=%s", type(failure).__name__)
                return False  # Preserve the original cancellation/deadline.
        return await self.transaction.__aexit__(exc_type, exc, traceback)


def bound_read_transaction_exit(connection, transaction):
    if _read_deadline.get() is not None:
        return _BoundedReadTransactionExit(connection, transaction)
    return transaction


@asynccontextmanager
async def read_budget():
    if _read_deadline.get() is not None:
        # The permission dependency already owns this task's cancellation timer.
        # A nested route must not start a fresh budget or extend the deadline.
        yield
        return
    deadline = asyncio.get_running_loop().time() + BUSINESS_READ_SECONDS
    token = _read_deadline.set(deadline)
    try:
        async with asyncio.timeout_at(deadline):
            yield
    finally:
        _read_deadline.reset(token)
