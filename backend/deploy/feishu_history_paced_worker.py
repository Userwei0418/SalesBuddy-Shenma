"""Optional serial worker entrypoint for slowly processing reconciliation backlog.

Run instead of (never alongside) the normal Feishu worker. Business events and
older same-object events needed by pending business writes retain priority.
The existing worker owns configuration validation, role checks, leases, delivery
guards, LISTEN wakeups, signal handling and cleanup.
"""
import asyncio
import json
import math
import os
from pathlib import Path
from uuid import UUID, uuid4

from sales_backend import feishu_worker as runtime
from sales_backend.repositories.feishu_sync import FeishuRepository


def history_interval():
    value = float(os.environ.get("FEISHU_HISTORY_INTERVAL_SECONDS", "5"))
    if not math.isfinite(value) or not 0 <= value <= 60:
        raise ValueError("FEISHU_HISTORY_INTERVAL_SECONDS must be between 0 and 60")
    return value


class PacedRepository(FeishuRepository):
    def __init__(self):
        self.background_claim = False
        self.priority_scope = None
        filename = os.environ.get("FEISHU_HISTORY_PRIORITY_FILE")
        if filename:
            scope = json.loads(Path(filename).read_text())
            ids = [UUID(value) for value in scope["object_ids"]]
            if not 0 < len(ids) <= 200 or len(set(ids)) != len(ids):
                raise ValueError("History priority scope must contain 1 to 200 unique IDs")
            self.priority_scope = (UUID(scope["workspace_id"]), UUID(scope["connection_id"]), ids)

    async def claim(self, connection, *, prefer_history=False):
        if self.priority_scope is None:
            event = await super().claim(connection, prefer_history=prefer_history)
        else:
            # Identical authorization, eligibility, predecessor, lease and
            # fairness rules to the normal claim. Only historical ordering is
            # refined by the reviewed, company-specific scope. Every eleventh
            # claim remains FIFO so unrelated history cannot be starved.
            event = await connection.fetchrow("""WITH candidate AS (
              SELECT e.id FROM ops.feishu_event e JOIN config.feishu_connection c ON c.id=e.connection_id
              WHERE c.enabled AND c.validated_revision=c.revision AND security.workspace_is_active(e.workspace_id)
              AND security.workspace_module_enabled(e.workspace_id,'feishu')
              AND ((e.status IN ('pending','failed') AND e.available_at<=clock_timestamp())
                OR (e.status='running' AND e.locked_until<clock_timestamp()))
              AND NOT EXISTS(SELECT 1 FROM ops.feishu_event prior WHERE prior.connection_id=e.connection_id
                AND prior.object_kind=e.object_kind AND prior.object_id=e.object_id
                AND prior.sequence_no<e.sequence_no AND prior.status NOT IN ('succeeded','dead_letter'))
              ORDER BY CASE WHEN $2::boolean THEN 0
                WHEN e.queue_origin='business' THEN 0
                WHEN EXISTS(SELECT 1 FROM ops.feishu_event live
                  WHERE live.connection_id=e.connection_id AND live.object_kind=e.object_kind
                    AND live.object_id=e.object_id AND live.queue_origin='business'
                    AND live.status NOT IN ('succeeded','dead_letter')) THEN 0
                WHEN e.queue_origin='reconcile' AND e.historical AND e.workspace_id=$3
                  AND e.connection_id=$4 AND e.object_id=ANY($5::uuid[]) THEN 1
                ELSE 2 END, e.sequence_no FOR UPDATE OF e,c SKIP LOCKED LIMIT 1
            ) UPDATE ops.feishu_event e SET status='running',attempts=attempts+1,
              lease_token=$1::uuid,locked_until=clock_timestamp()+interval '120 seconds'
              FROM candidate WHERE e.id=candidate.id RETURNING e.*""",
                str(uuid4()), prefer_history, *self.priority_scope)
        self.background_claim = False
        # historical=True also occurs on ordinary business events when notices
        # are suppressed; queue_origin is the authoritative work classification.
        if event and event["queue_origin"] == "reconcile" and event["historical"]:
            live_dependency = await connection.fetchval("""
                SELECT EXISTS(SELECT 1 FROM ops.feishu_event
                WHERE connection_id=$1 AND object_kind=$2 AND object_id=$3
                AND queue_origin='business'
                AND status NOT IN ('succeeded','dead_letter'))
                """, event["connection_id"], event["object_kind"], event["object_id"])
            self.background_claim = not live_dependency
        return event


class PacedWorker(runtime.FeishuWorker):
    def __init__(self, database):
        super().__init__(database)
        self.repository = PacedRepository()
        self.history_interval_seconds = history_interval()

    async def run_once(self):
        self.repository.background_claim = False
        self.last_outcome = None
        handled = await super().run_once()
        # Delay only after processing, releasing the object lock and connection.
        # A fresh business event waits at most this short interval, not for an
        # artificial available_at scheduled hours later on its predecessor.
        if handled and self.repository.background_claim:
            # Only a successfully finished, notification-free fingerprint hit
            # can use the local budget (at most 10 events/second). Every other
            # result, including failures and legacy hash reads, keeps pacing.
            delay = 0.1 if self.last_outcome == "source_unchanged" else self.history_interval_seconds
            if delay:
                await asyncio.sleep(delay)
        return handled


def run():
    runtime.FeishuWorker = PacedWorker
    runtime.run()


if __name__ == "__main__":
    run()
