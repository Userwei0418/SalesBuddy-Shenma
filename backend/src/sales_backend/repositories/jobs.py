from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.job_context import JobLeaseLost, current_job_lease


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    workspace_id: str
    job_type: str
    aggregate_id: str | None
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_token: str = ""
    queue_wait_seconds: float = 0


class JobRepository:
    async def claim(
        self, connection: asyncpg.Connection, *, worker_id: str, lock_seconds: int,
        job_types: tuple[str, ...] | None = None, exclude_types: bool = False,
    ) -> ClaimedJob | None:
        # Filtering must happen before the database acquires a lease. Claiming all
        # types and then queuing them in Python consumes attempts without capacity.
        row = await connection.fetchrow(
            "SELECT * FROM ops.claim_job($1, $2, $3::text[], $4)",
            worker_id, lock_seconds, list(job_types) if job_types is not None else None, exclude_types,
        )
        if not row:
            return None
        return ClaimedJob(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            job_type=row["job_type"],
            aggregate_id=str(row["aggregate_id"]) if row["aggregate_id"] else None,
            payload=row["payload"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            lease_token=str(row["lease_token"]),
            # Per-attempt eligible queue delay; scheduled/backoff time is excluded.
            queue_wait_seconds=max(0, (row["updated_at"] - row["available_at"]).total_seconds()),
        )

    async def succeed(self, connection: asyncpg.Connection, *, job: ClaimedJob) -> None:
        status = await connection.execute(
            """
            UPDATE ops.job SET status = 'succeeded', completed_at = clock_timestamp(),
              locked_by = NULL, locked_until = NULL, last_error_code = NULL,
              last_error_detail = NULL, lease_token = NULL
            WHERE id = $1::uuid AND lease_token=$2::uuid AND status='running'
              AND locked_until>clock_timestamp()
            """,
            job.id,
            job.lease_token,
        )
        if status != "UPDATE 1":
            raise JobLeaseLost("cannot acknowledge an expired or superseded job")

    async def heartbeat(self, connection: asyncpg.Connection, *, job: ClaimedJob, lock_seconds: int) -> None:
        status = await connection.execute(
            """UPDATE ops.job SET locked_until=clock_timestamp()+make_interval(secs=>$3),
               updated_at=clock_timestamp() WHERE id=$1::uuid AND lease_token=$2::uuid
               AND status='running' AND locked_until>clock_timestamp()""",
            job.id,
            job.lease_token,
            lock_seconds,
        )
        if status != "UPDATE 1":
            raise JobLeaseLost("cannot renew an expired or superseded job")

    async def fail(
        self,
        connection: asyncpg.Connection,
        *,
        job: ClaimedJob,
        error_code: str,
        error_detail: str,
        retryable: bool,
    ) -> None:
        terminal = not retryable or job.attempts >= job.max_attempts
        status = await connection.execute(
            """
            UPDATE ops.job
               SET status = $2,
                   available_at = CASE WHEN $2 = 'failed'
                     THEN clock_timestamp() + make_interval(secs => LEAST(300, power(2, attempts)::int))
                     ELSE available_at END,
                   completed_at = CASE WHEN $2 = 'dead_letter' THEN clock_timestamp() ELSE NULL END,
                   locked_by = NULL,
                   locked_until = NULL,
                   lease_token = NULL,
                   last_error_code = $3,
                   last_error_detail = $4
             WHERE id = $1::uuid AND lease_token=$5::uuid AND status='running'
               AND locked_until>clock_timestamp()
            """,
            job.id,
            "dead_letter" if terminal else "failed",
            error_code[:100],
            error_detail[:2000],
            job.lease_token,
        )
        if status != "UPDATE 1":
            raise JobLeaseLost("cannot fail an expired or superseded job")


async def record_job_effect(connection: asyncpg.Connection, workspace_id: str) -> None:
    """Call at the end of the same transaction that persists the complete business result."""
    lease = current_job_lease.get()
    if lease is not None:
        await connection.execute(
            """INSERT INTO ops.job_effect(job_id,workspace_id,lease_token)
               VALUES($1::uuid,$2::uuid,$3::uuid) ON CONFLICT(job_id) DO NOTHING""",
            lease.job_id,
            workspace_id,
            lease.token,
        )


async def enqueue_battle_map_review(
    connection: asyncpg.Connection,
    actor: ActorContext,
    *,
    customer_id: str,
    trigger_type: str,
    trigger_id: str,
) -> None:
    # Cross-customer follow-up is allowed, but sharing its broader analysis remains
    # unconfirmed. Preserve the existing analysis scope without granting membership.
    if not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer_id):
        return
    await connection.execute(
        """
        INSERT INTO ops.job (
          workspace_id, job_type, aggregate_type, aggregate_id, payload,
          priority, correlation_id
        ) VALUES ($1::uuid, 'battle_map.review', 'customer', $2::uuid, $3::jsonb, 75, $4::uuid)
        """,
        actor.workspace_id,
        customer_id,
        {
            "workspace_id": actor.workspace_id,
            "user_id": actor.user_id,
            "role": actor.role.value,
            "data_scope": actor.data_scope.value,
            "team_ids": list(actor.team_ids),
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
        },
        trigger_id,
    )
