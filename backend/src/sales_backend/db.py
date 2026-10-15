from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from time import perf_counter
import math

import asyncpg

from sales_backend.config import Settings
from sales_backend.domain.agent import ActorContext
from sales_backend.job_context import JobLeaseLost, current_job_lease
from sales_backend.request_metadata import request_metadata
from sales_backend.performance import record_pool_wait, record_query
from sales_backend.permission_context import current_feature


class TenantInactive(PermissionError):
    """A suspended/closed tenant cannot execute an authenticated transaction."""


class DatabaseBusy(RuntimeError):
    """No connection was acquired within the configured wait budget.

    This describes this acquisition only, not the outcome of any earlier
    transaction in the request. Callers must not blindly retry business writes.
    """


class JobLeaseLockBusy(RuntimeError):
    """The lease fence could not take its row lock without waiting."""


async def _initialize_connection(connection: asyncpg.Connection) -> None:
    import json

    connection.add_query_logger(record_query)
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name,
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
            format="text",
        )


def normalize_database_url(url: str) -> str:
    """asyncpg expects PostgreSQL URLs without the SQLAlchemy driver suffix."""

    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@dataclass(slots=True)
class Database:
    settings: Settings
    pool: asyncpg.Pool | None = None

    def __post_init__(self) -> None:
        wait = self.settings.database_pool_wait_seconds
        if not math.isfinite(wait) or not 0 < wait <= 30:
            raise ValueError("DATABASE_POOL_WAIT_SECONDS must be finite and between 0 (exclusive) and 30")

    async def connect(self) -> None:
        if self.pool is not None:
            return
        if not self.settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        self.pool = await asyncpg.create_pool(
            dsn=normalize_database_url(self.settings.database_url),
            min_size=self.settings.database_min_pool_size,
            max_size=self.settings.database_max_pool_size,
            command_timeout=30,
            server_settings={"application_name": "sales-backend"},
            init=_initialize_connection,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("database pool is not initialized")
        return self.pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        pool = self.require_pool()
        start = perf_counter()
        try:
            connection = await pool.acquire(timeout=self.settings.database_pool_wait_seconds)
        except TimeoutError as exc:
            raise DatabaseBusy("Database connection wait budget exhausted") from exc
        finally:
            record_pool_wait(perf_counter() - start)
        try:
            yield connection
        finally:
            # asyncpg shields release from cancellation. Give reset its own
            # bounded budget: a short acquisition policy must not kill healthy
            # connections while rolling back/resetting them. Query and release
            # timeouts are deliberately outside the acquisition error mapping.
            await pool.release(connection, timeout=5)

    @asynccontextmanager
    async def transaction(
        self, actor: ActorContext, *, readonly: bool = False,
        isolation: str = "read_committed", lease_lock_nowait: bool = False,
    ) -> AsyncIterator[asyncpg.Connection]:
        if readonly and lease_lock_nowait:
            raise ValueError("a readonly transaction does not lock the worker lease")
        async with self.connection() as connection:
            async with connection.transaction(readonly=readonly, isolation=isolation):
                await set_request_context(connection, actor)
                lease = current_job_lease.get()
                if lease is not None:
                    # Holding the job row during a write transaction serializes reclaim
                    # against every handler result, not only the final queue update.
                    query = (
                        (
                            "SELECT 1 FROM ops.job WHERE id=$1::uuid AND lease_token=$2::uuid "
                            "AND status='running' AND locked_until>clock_timestamp() "
                            "AND security.workspace_job_enabled(workspace_id,job_type)"
                        )
                        if readonly
                        else (
                            "SELECT 1 FROM ops.job WHERE id=$1::uuid AND lease_token=$2::uuid "
                            "AND status='running' AND locked_until>clock_timestamp() "
                            "AND security.workspace_job_enabled(workspace_id,job_type) FOR UPDATE"
                        )
                    )
                    if lease_lock_nowait:
                        query += " NOWAIT"
                    try:
                        valid = await connection.fetchval(query, lease.job_id, lease.token)
                    except asyncpg.LockNotAvailableError as exc:
                        if not lease_lock_nowait:
                            raise
                        # Only contention at this exact fence is deferrable.
                        # The transaction must roll back before a readonly check.
                        raise JobLeaseLockBusy("worker lease row is locked") from exc
                    if not valid:
                        raise JobLeaseLost("worker lease expired or superseded")
                yield connection


async def set_request_context(connection: asyncpg.Connection, actor: ActorContext) -> None:
    metadata = request_metadata.get()
    await connection.execute(
        """
        SELECT
          set_config('app.workspace_id', $1, true),
          set_config('app.user_ref_id', $2, true),
          set_config('app.role_code', $3, true),
          set_config('app.team_ids', $4, true),
          set_config('app.request_id', $5, true),
          set_config('app.client_ip', $6, true),
          set_config('app.user_agent', $7, true),
          set_config('app.job_id', $8, true),
          set_config('app.authorization_context', '', true),
          set_config('app.authorized_feature', $9, true)
        """,
        actor.workspace_id,
        actor.user_id,
        actor.role.value,
        ",".join(actor.team_ids),
        metadata.request_id,
        metadata.client_ip,
        metadata.user_agent,
        current_job_lease.get().job_id if current_job_lease.get() else "",
        current_feature.get(),
    )

    # Resolve from current database assignments once per transaction. Row policies
    # consume only this server-established context, never frontend capability flags.
    try:
        await connection.execute("SELECT security.authorization_refresh()")
    except asyncpg.PostgresError as exc:
        if exc.sqlstate == 'P7403':
            raise TenantInactive('公司当前已暂停或关闭，请联系平台管理员') from None
        raise


def json_value(value: Any) -> Any:
    """asyncpg may return json/jsonb as text unless a codec is configured."""

    if not isinstance(value, str):
        return value
    import json

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
