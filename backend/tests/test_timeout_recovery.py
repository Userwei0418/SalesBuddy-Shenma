"""Bounded reads unwind transactions; uncertain writes are never replayed."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from sales_backend import read_budget
from sales_backend.api import permission_gate
from sales_backend.db import Database, DatabaseBusy
from sales_backend.main import database_busy_handler
from sales_backend.observability import request_id_middleware, request_timeout_handler
from tests.test_database_wait_budget import BoundedPool
from tests.test_tokens import settings


@pytest.mark.asyncio
async def test_nested_read_budget_reuses_one_deadline_and_does_not_leak_to_next_request(monkeypatch):
    timer = Mock(wraps=asyncio.timeout_at)
    monkeypatch.setattr(read_budget.asyncio, "timeout_at", timer)
    async with read_budget.read_budget():
        deadline = read_budget._read_deadline.get()
        async with read_budget.read_budget():
            assert read_budget._read_deadline.get() == deadline
    timer.assert_called_once_with(deadline)
    assert read_budget._read_deadline.get() is None
    async with read_budget.read_budget():
        assert read_budget._read_deadline.get() > deadline
    assert timer.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method,name,should_timeout", [
    ("GET", "map_customers", True),
    ("POST", "map_customers", False),
    ("GET", "some_stream", False),
])
async def test_only_allowlisted_reads_share_budget_across_permission_dependency_and_endpoint(
    monkeypatch, method, name, should_timeout,
):
    events = []

    async def gate(request):
        events.append("authorize")
        try:
            await asyncio.sleep(.005)
            yield
        finally:
            events.append("permission_cleanup")

    monkeypatch.setattr(permission_gate, "_enforce_route_permission", gate)
    monkeypatch.setattr(read_budget, "BUSINESS_READ_SECONDS", .02)
    request = SimpleNamespace(method=method, scope={"endpoint": SimpleNamespace(
        __module__="sales_backend.api.customer_assets", __name__=name,
    )})

    async def run():
        async with asynccontextmanager(permission_gate.enforce_route_permission)(request):
            events.append("endpoint")
            await asyncio.sleep(.03)
            events.append("complete")

    if should_timeout:
        with pytest.raises(TimeoutError):
            await run()
        assert events == ["authorize", "endpoint", "permission_cleanup"]
    else:
        await run()
        assert events == ["authorize", "endpoint", "complete", "permission_cleanup"]


@pytest.mark.asyncio
async def test_total_read_budget_cancels_work_and_unwinds_before_releasing_connection(monkeypatch):
    events = []

    @asynccontextmanager
    async def transaction(**kwargs):
        assert kwargs["readonly"] is True
        events.append("begin")
        try:
            yield
        except asyncio.CancelledError:
            events.append("rollback")
            raise

    class Pool(BoundedPool):
        async def release(self, connection, *, timeout):
            events.append("release")
            await super().release(connection, timeout=timeout)

    pool = Pool()
    pool.connection = SimpleNamespace(transaction=transaction)
    database = Database(settings(), pool)
    monkeypatch.setattr("sales_backend.db.set_request_context", AsyncMock())
    monkeypatch.setattr(read_budget, "BUSINESS_READ_SECONDS", .03)
    completed_reads = 0
    with pytest.raises(TimeoutError):
        async with read_budget.read_budget(), database.transaction(object(), readonly=True):
            # Each read is short; the aggregate still has a finite budget.
            for _ in range(100):
                await asyncio.sleep(.002)
                completed_reads += 1
    assert 0 < completed_reads < 100
    assert events == ["begin", "rollback", "release"]
    async with database.connection():
        pass
    assert pool.releases == 2


@pytest.mark.asyncio
async def test_external_cancellation_remains_cancellation():
    entered = asyncio.Event()

    async def reading():
        async with read_budget.read_budget():
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(reading())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancelled_read_with_stalled_rollback_terminates_connection_before_release(monkeypatch):
    events = []

    @asynccontextmanager
    async def transaction(**kwargs):
        try:
            yield
        finally:
            events.append("rollback_started")
            await asyncio.Future()

    class Pool(BoundedPool):
        async def release(self, connection, *, timeout):
            assert events == ["rollback_started", "terminated"]
            await super().release(connection, timeout=timeout)

    pool = Pool()
    pool.connection = SimpleNamespace(transaction=transaction, terminate=lambda: events.append("terminated"))
    database = Database(settings(), pool)
    monkeypatch.setattr("sales_backend.db.set_request_context", AsyncMock())
    monkeypatch.setattr(read_budget, "BUSINESS_READ_SECONDS", .01)
    monkeypatch.setattr(read_budget, "READ_ROLLBACK_SECONDS", .01)
    with pytest.raises(TimeoutError):
        async with read_budget.read_budget(), database.transaction(object(), readonly=True):
            await asyncio.Future()
    assert pool.releases == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("failure,status,code", [
    (TimeoutError("private SQL"), 504, "REQUEST_TIMEOUT"),
    (asyncpg.QueryCanceledError("private SQL"), 504, "REQUEST_TIMEOUT"),
    (DatabaseBusy("private host"), 503, "DATABASE_BUSY"),
])
async def test_transient_errors_are_traceable_and_mutations_are_unknown_not_replayed(method, failure, status, code):
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(TimeoutError, request_timeout_handler)
    app.add_exception_handler(asyncpg.QueryCanceledError, request_timeout_handler)
    app.add_exception_handler(DatabaseBusy, database_busy_handler)
    executions = []

    @app.api_route("/test", methods=["GET", "POST"])
    async def operation():
        # An earlier write transaction could already have committed.
        executions.append("executed")
        raise failure

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.request(method, "/test")
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["retryable"] is (method == "GET")
    assert body["recovery_action"] == ("retry_read" if method == "GET" else "check_status")
    assert response.headers.get("retry-after") == ("5" if method == "GET" else None)
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
    assert executions == ["executed"]
