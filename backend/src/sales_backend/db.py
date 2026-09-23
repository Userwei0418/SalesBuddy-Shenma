from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from time import perf_counter

import asyncpg

from sales_backend.config import Settings
from sales_backend.domain.agent import ActorContext
from sales_backend.job_context import JobLeaseLost, current_job_lease
from sales_backend.request_metadata import request_metadata
from sales_backend.performance import record_pool_wait, record_query


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
        start = perf_counter()
        async with self.require_pool().acquire() as connection:
            record_pool_wait(perf_counter() - start)
            yield connection

    @asynccontextmanager
    async def transaction(self, actor: ActorContext, *, readonly: bool = False) -> AsyncIterator[asyncpg.Connection]:
        async with self.connection() as connection:
            async with connection.transaction(readonly=readonly):
                await set_request_context(connection, actor)
                lease = current_job_lease.get()
                if lease is not None:
                    # Holding the job row during a write transaction serializes reclaim
                    # against every handler result, not only the final queue update.
                    query = (
                        (
                            "SELECT 1 FROM ops.job WHERE id=$1::uuid AND lease_token=$2::uuid "
                            "AND status='running' AND locked_until>clock_timestamp()"
                        )
                        if readonly
                        else (
                            "SELECT 1 FROM ops.job WHERE id=$1::uuid AND lease_token=$2::uuid "
                            "AND status='running' AND locked_until>clock_timestamp() FOR UPDATE"
                        )
                    )
                    valid = await connection.fetchval(
                        query,
                        lease.job_id,
                        lease.token,
                    )
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
          set_config('app.job_id', $8, true)
        """,
        actor.workspace_id,
        actor.user_id,
        actor.role.value,
        ",".join(actor.team_ids),
        metadata.request_id,
        metadata.client_ip,
        metadata.user_agent,
        current_job_lease.get().job_id if current_job_lease.get() else "",
    )


def json_value(value: Any) -> Any:
    """asyncpg may return json/jsonb as text unless a codec is configured."""

    if not isinstance(value, str):
        return value
    import json

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
