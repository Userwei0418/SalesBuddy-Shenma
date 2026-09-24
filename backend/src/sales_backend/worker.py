from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, replace
from time import perf_counter
from uuid import UUID

import asyncpg

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.job_context import JobLease, JobLeaseLost, current_job_lease
from sales_backend.observability import configure_logging
from sales_backend.repositories.jobs import ClaimedJob, JobRepository
from sales_backend.request_metadata import current_actor
from sales_backend.services.agent_run import AgentRunHandler
from sales_backend.services.battle_map_reviews import BattleMapReviewHandler
from sales_backend.services.competency_reviews import CompetencyReviewHandler
from sales_backend.services.system_events import DatabaseEventSink
from sales_backend.services.worker_maintenance import WorkerMaintenance

logger = logging.getLogger("sales_backend.worker")


class InvalidJobActor(ValueError):
    """A queued identity cannot be trusted to run business work."""


@dataclass(frozen=True, slots=True)
class JobLane:
    name: str
    capacity: int
    job_types: tuple[str, ...]
    exclude_types: bool = False


class Worker:
    def __init__(self, database: Database):
        self.database = database
        self.settings = database.settings
        self.jobs = JobRepository()

    async def run_forever(self) -> None:
        # Each task is an actual execution slot, not a queue of prefetched leases.
        # Import backlog cannot take the slots reserved for interactive AI or reviews.
        # TaskGroup also propagates shutdown/maintenance failure to every heartbeat.
        async with asyncio.TaskGroup() as group:
            group.create_task(WorkerMaintenance(self.database).run_forever(), name="worker:maintenance")
            for lane in self.lanes():
                for slot in range(lane.capacity):
                    group.create_task(self._consume(lane), name=f"worker:{lane.name}:{slot + 1}")

    def lanes(self) -> tuple[JobLane, ...]:
        lanes = (
            JobLane("import", getattr(self.settings, "worker_import_concurrency", 1), ("visit.import",)),
            JobLane("interactive", getattr(self.settings, "worker_interactive_concurrency", 2),
                    ("agent.run", "business.advice")),
            # This complement includes both review types and unknown jobs. An
            # unsupported type still reaches the normal observable dead-letter path.
            JobLane("review", getattr(self.settings, "worker_review_concurrency", 1),
                    ("visit.import", "agent.run", "business.advice"), True),
        )
        if any(not 1 <= lane.capacity <= 8 for lane in lanes) or sum(lane.capacity for lane in lanes) > 16:
            raise ValueError("worker lanes require 1–8 slots each and at most 16 total")
        return lanes

    async def _consume(self, lane: JobLane) -> None:
        while True:
            try:
                handled = await self.run_once(job_types=lane.job_types, exclude_types=lane.exclude_types)
            except (asyncpg.PostgresError, OSError, TimeoutError):
                logger.exception("job queue unavailable lane=%s", lane.name,
                                 extra={"event_type": "worker_queue_unavailable"})
                await asyncio.sleep(max(5, self.settings.worker_poll_seconds))
                continue
            if not handled:
                await asyncio.sleep(self.settings.worker_poll_seconds)

    async def run_once(self, *, job_types: tuple[str, ...] | None = None, exclude_types: bool = False) -> bool:
        async with self.database.connection() as connection:
            job = await self.jobs.claim(
                connection,
                worker_id=self.settings.worker_id,
                lock_seconds=self.settings.worker_lock_seconds,
                job_types=job_types,
                exclude_types=exclude_types,
            )
        if job is None:
            return False
        try:
            actor = self._job_actor(job)
        except InvalidJobActor:
            # Reject only this leased queue item, without inventing a privileged
            # actor from a malformed/legacy payload or cancelling other lanes.
            try:
                await self._reject_invalid_job(job)
            except JobLeaseLost:
                logger.warning("invalid job belongs to an expired attempt", extra={"job_id": job.id})
            return True
        context_token = current_job_lease.set(JobLease(job.id, job.lease_token))
        actor_token = current_actor.set(actor)
        started = perf_counter()
        outcome = "cancelled"
        logger.info("job started id=%s type=%s attempt=%s queue_wait_ms=%.1f", job.id, job.job_type,
                    job.attempts, job.queue_wait_seconds * 1000, extra={"event_type": "worker_job_started"})
        try:
            await self._run_with_heartbeat(job)
            await self._finish_succeeded(job)
            outcome = "succeeded"
        except JobLeaseLost:
            outcome = "lease_lost"
            logger.warning("job lease lost; stale attempt stopped", extra={"job_id": job.id})
        except Exception as exc:
            outcome = "failed"
            logger.exception("job failed", extra={"job_id": job.id, "job_type": job.job_type})
            try:
                await self._finish_failed(job, exc)
            except JobLeaseLost:
                logger.warning("job failure belongs to an expired attempt", extra={"job_id": job.id})
        finally:
            logger.info("job ended id=%s type=%s outcome=%s execution_ms=%.1f", job.id, job.job_type,
                        outcome, (perf_counter() - started) * 1000, extra={"event_type": "worker_job_ended"})
            current_job_lease.reset(context_token)
            current_actor.reset(actor_token)
        return True

    async def _handle_job(self, job: ClaimedJob) -> None:
        if not job.aggregate_id:
            raise ValueError(f"unsupported job type: {job.job_type}")
        actor = self._job_actor(job)
        if job.job_type == "ai.connectivity":
            from sales_backend.services.connectivity import ConnectivityService

            await ConnectivityService(self.database, self.settings).handle(actor, job.aggregate_id)
        elif job.job_type == "weekly_report.generate":
            from sales_backend.services.weekly_reports import WeeklyReportService
            await WeeklyReportService(self.database).handle(actor, job.aggregate_id)
        elif job.job_type == "agent.run":
            await AgentRunHandler(self.database, self.settings).handle(job.aggregate_id, actor)
        elif job.job_type == "visit.import":
            from sales_backend.services.visit_import import VisitImportHandler

            await VisitImportHandler(self.database).handle(job.aggregate_id, actor)
        elif job.job_type == "sales_competency.review":
            await CompetencyReviewHandler(self.database, self.settings).handle(job.aggregate_id, actor)
        elif job.job_type == "battle_map.review":
            await BattleMapReviewHandler(self.database, self.settings).handle(
                job.aggregate_id, actor, trigger=job.payload
            )
        elif job.job_type == "customer.risk.review":
            from sales_backend.services.customer_risk import CustomerRiskReviewHandler

            await CustomerRiskReviewHandler(self.database, self.settings).handle(job.aggregate_id, actor)
        elif job.job_type == "business.advice":
            from sales_backend.services.advice import AdviceHandler
            await AdviceHandler(self.database).handle(job.aggregate_id, actor)
        elif job.job_type == "opportunity.change.review":
            from sales_backend.services.opportunity_changes import OpportunityChangeHandler

            await OpportunityChangeHandler(self.database).handle(job.aggregate_id, actor, job.payload["facts"])
        else:
            raise ValueError(f"unsupported job type: {job.job_type}")

    async def _run_with_heartbeat(self, job: ClaimedJob) -> None:
        async def pulse() -> None:
            while True:
                await asyncio.sleep(max(1, self.settings.worker_lock_seconds / 3))
                try:
                    async with self.database.transaction(self._job_actor(job)) as connection:
                        await self.jobs.heartbeat(connection, job=job, lock_seconds=self.settings.worker_lock_seconds)
                except Exception as exc:
                    raise JobLeaseLost("heartbeat failed; stop before any further result writes") from exc

        handler = asyncio.create_task(self._handle_job(job))
        heartbeat = asyncio.create_task(pulse())
        try:
            done, _ = await asyncio.wait((handler, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
            await handler
        finally:
            for task in (handler, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(handler, heartbeat, return_exceptions=True)

    @staticmethod
    def _job_actor(job: ClaimedJob) -> ActorContext:
        payload = job.payload
        try:
            if not isinstance(payload, dict):
                raise ValueError("expected identity object")
            user_id = payload["user_id"]
            teams = payload.get("team_ids", [])
            if not isinstance(user_id, str) or not isinstance(teams, (list, tuple)):
                raise ValueError("invalid identity fields")
            if not UUID(user_id).int or any(not isinstance(team, str) for team in teams):
                raise ValueError("invalid identity identifiers")
            workspace_id = str(UUID(job.workspace_id))
            if "workspace_id" in payload and str(UUID(payload["workspace_id"])) != workspace_id:
                raise ValueError("identity workspace mismatch")
            return ActorContext(
                workspace_id=workspace_id,
                user_id=str(UUID(user_id)),
                role=RoleCode(payload["role"]),
                data_scope=DataScope(payload["data_scope"]),
                team_ids=tuple(str(UUID(team)) for team in teams),
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            # Payloads may contain private input; the queue and logs get a fixed
            # diagnostic, never the rejected value or a fabricated default role.
            raise InvalidJobActor("queued job identity is invalid") from None

    async def _reject_invalid_job(self, job: ClaimedJob) -> None:
        async with asyncio.timeout(5):
            async with self.database.connection() as connection:
                # This narrow queue function derives workspace/targets from the
                # locked row and closes pending projections without a fake actor.
                outcome = await connection.fetchval(
                    "SELECT ops.reject_invalid_job_actor($1::uuid,$2::uuid)", job.id, job.lease_token,
                )
        if outcome is None:
            raise JobLeaseLost("invalid identity rejection no longer owns the job")
        logger.log(logging.ERROR if outcome == "dead_letter" else logging.INFO,
                   "invalid queued identity closed id=%s type=%s outcome=%s", job.id, job.job_type, outcome,
                   extra={"event_type": "worker_invalid_job_actor"})

    async def _finish_succeeded(self, job: ClaimedJob) -> None:
        # claim_job is SECURITY DEFINER; business completion uses the validated
        # queued actor snapshot, never a fabricated worker/manager identity.
        actor = self._job_actor(job)
        async with self.database.transaction(actor) as connection:
            await self.jobs.succeed(connection, job=job)

    async def _finish_failed(self, job: ClaimedJob, exc: Exception) -> None:
        actor = self._job_actor(job)
        retryable = (isinstance(exc, SenseAudioError) and exc.retryable) or isinstance(
            exc,
            (
                TimeoutError,
                ConnectionError,
                asyncpg.PostgresConnectionError,
                asyncpg.SerializationError,
                asyncpg.DeadlockDetectedError,
            ),
        )
        async with self.database.transaction(actor) as connection:
            if await connection.fetchval("SELECT EXISTS(SELECT 1 FROM ops.job_effect WHERE job_id=$1::uuid)", job.id):
                await self.jobs.succeed(connection, job=job)
                return
            if job.job_type == "agent.run" and job.aggregate_id:
                await connection.execute(
                    """
                    UPDATE agent.run
                       SET status = CASE WHEN $2 THEN 'queued' ELSE 'failed' END,
                           error_code = $3,
                           error_detail = $4,
                           completed_at = CASE WHEN $2 THEN NULL ELSE clock_timestamp() END
                     WHERE id = $1::uuid
                    """,
                    job.aggregate_id,
                    retryable and job.attempts < job.max_attempts,
                    type(exc).__name__[:100],
                    str(exc)[:2000],
                )
            elif job.job_type == "business.advice" and job.aggregate_id:
                await connection.execute(
                    "UPDATE insight.business_advice SET status=$2,error_code=$3 WHERE id=$1::uuid",
                    job.aggregate_id, "queued" if retryable and job.attempts < job.max_attempts else "failed",
                    type(exc).__name__,
                )
            elif job.job_type == "sales_competency.review" and job.aggregate_id:
                await connection.execute(
                    """
                    UPDATE insight.sales_competency_review
                       SET status = CASE WHEN $2 THEN 'queued' ELSE 'failed' END,
                           error_code = $3, error_detail = $4
                     WHERE id = $1::uuid
                    """,
                    job.aggregate_id,
                    retryable and job.attempts < job.max_attempts,
                    type(exc).__name__[:100],
                    str(exc)[:2000],
                )
            if job.job_type == "visit.import" and job.aggregate_id:
                await connection.execute(
                    """UPDATE activity.visit_import SET status=$2,error_message=$3,updated_at=clock_timestamp() WHERE
                    id=$1::uuid""",
                    job.aggregate_id,
                    "queued" if retryable and job.attempts < job.max_attempts else "failed",
                    "语音服务暂不可用，请稍后重试" if isinstance(exc, SenseAudioError) else str(exc)[:200],
                )
            await self.jobs.fail(
                connection,
                job=job,
                error_code=type(exc).__name__,
                error_detail=str(exc),
                retryable=retryable,
            )


async def main() -> None:
    configure_logging()
    settings = get_settings()
    settings.require_auth()
    if not 4 <= settings.worker_database_max_pool_size <= 32:
        raise ValueError("WORKER_DATABASE_MAX_POOL_SIZE must be between 4 and 32")
    # Separate process and explicit budget: changing worker capacity never enlarges
    # the API pool. No connection is held while waiting for a model/audio response.
    database = Database(replace(settings, database_min_pool_size=1,
                                database_max_pool_size=settings.worker_database_max_pool_size))
    worker = Worker(database)
    worker.lanes()  # Fail invalid capacity before opening the pool or claiming jobs.
    await database.connect()
    sink = DatabaseEventSink(database)
    logging.getLogger().addHandler(sink)
    logger.info("Worker service started", extra={"system_event": True, "event_type": "service_start"})
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    stop_requested = False

    def request_stop() -> None:
        nonlocal stop_requested
        stop_requested = True
        task.cancel()

    loop.add_signal_handler(signal.SIGTERM, request_stop)
    try:
        await worker.run_forever()
    except asyncio.CancelledError:
        if not stop_requested:
            raise
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        logger.info("Worker service stopped", extra={"system_event": True, "event_type": "service_stop"})
        await sink.shutdown()
        await database.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
