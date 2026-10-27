import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from sales_backend.config import get_settings
from sales_backend.db import Database, DatabaseBusy
from tests.integration.connection_pool import FixtureConnectionPool, ScopedRolePool


@pytest.mark.asyncio
async def test_fixture_connection_wait_timeout_does_not_steal_the_current_lease():
    connection = object()
    pool = FixtureConnectionPool(connection)
    first = await pool.acquire(timeout=1)
    database = Database(replace(get_settings(), database_pool_wait_seconds=0.01), pool=pool)
    with pytest.raises(DatabaseBusy):
        async with database.connection():
            pytest.fail('A busy fixture connection was borrowed concurrently')
    await pool.release(first, timeout=5)
    async with database.connection() as borrowed:
        assert borrowed is connection


@pytest.mark.asyncio
async def test_cancelled_fixture_waiter_leaves_the_connection_available_after_release():
    connection = object()
    pool = FixtureConnectionPool(connection)
    first = await pool.acquire(timeout=1)
    waiting = asyncio.create_task(pool.acquire(timeout=1))
    await asyncio.sleep(0)
    assert not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await pool.release(first, timeout=5)
    assert await pool.acquire(timeout=1) is connection
    await pool.release(connection, timeout=5)


@pytest.mark.asyncio
async def test_handler_failure_propagates_and_release_preserves_fixture_transaction():
    connection = AsyncMock()
    pool = FixtureConnectionPool(connection)
    database = Database(get_settings(), pool=pool)
    problem = RuntimeError('synthetic SQL failure')
    with pytest.raises(RuntimeError) as caught:
        async with database.connection():
            raise problem
    assert caught.value is problem
    async with database.connection() as borrowed:
        assert borrowed is connection
    connection.reset.assert_not_awaited()
    connection.close.assert_not_awaited()
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_or_double_release_is_not_silently_accepted():
    connection = object()
    pool = FixtureConnectionPool(connection)
    await pool.acquire(timeout=1)
    with pytest.raises(ValueError):
        await pool.release(object(), timeout=5)
    with pytest.raises(TimeoutError):
        await pool.acquire(timeout=0.01)
    await pool.release(connection, timeout=5)
    with pytest.raises(ValueError):
        await pool.release(connection, timeout=5)


@pytest.mark.asyncio
async def test_scoped_pool_forwards_borrow_return_budgets_and_resets_restricted_role():
    underlying = AsyncMock()
    connection = underlying.acquire.return_value
    pool = ScopedRolePool(underlying, "salegent_verify_role_abc123")
    assert await pool.acquire(timeout=2) is connection
    underlying.acquire.assert_awaited_once_with(timeout=2)
    connection.execute.assert_awaited_once_with('SET ROLE "salegent_verify_role_abc123"')
    await pool.release(connection, timeout=5)
    assert connection.execute.await_args.args == ("RESET ROLE",)
    underlying.release.assert_awaited_once_with(connection, timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("SQL role error"), asyncio.CancelledError()])
async def test_scoped_pool_setup_error_releases_connection_and_propagates(failure):
    underlying = AsyncMock()
    connection = underlying.acquire.return_value
    connection.execute.side_effect = failure
    pool = ScopedRolePool(underlying, "salegent_verify_role_abc123")
    with pytest.raises(type(failure)) as caught:
        await pool.acquire(timeout=2)
    assert caught.value is failure
    underlying.release.assert_awaited_once_with(connection, timeout=5)


@pytest.mark.asyncio
async def test_scoped_pool_failed_reset_still_returns_connection_and_reports_failure():
    underlying = AsyncMock()
    connection = underlying.acquire.return_value
    pool = ScopedRolePool(underlying, "salegent_verify_role_abc123")
    await pool.acquire(timeout=2)
    failure = RuntimeError("reset failed")
    connection.execute.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        await pool.release(connection, timeout=5)
    assert caught.value is failure
    underlying.release.assert_awaited_once_with(connection, timeout=5)


def test_scoped_pool_rejects_non_fixture_role_before_borrowing():
    underlying = AsyncMock()
    with pytest.raises(ValueError):
        ScopedRolePool(underlying, 'postgres"; DROP DATABASE unsafe; --')
    underlying.acquire.assert_not_called()
