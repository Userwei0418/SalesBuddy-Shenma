"""Durable, content-free inference receipts, including unsuccessful routes.

Provider accounting and business persistence remain separate authorities. A
receipt says which output passed validation, never that business writes happened.
"""

import asyncio
import hashlib
import json
import logging
from time import monotonic
from uuid import UUID, uuid4

from sales_backend.db import set_request_context
from sales_backend.job_context import current_job_lease
from sales_backend.observability import current_request_id
from sales_backend.services.agent_business_rules import business_policy_metadata

logger = logging.getLogger(__name__)
SHAPE_FIELDS = frozenset(
    {
        "title",
        "summary",
        "detail",
        "action_plan",
        "rows",
        "metrics",
        "sections",
        "fields",
        "quality_review",
        "follow_up_score",
        "next_action",
        "passed",
        "risks",
        "ordered_items",
        "potential_score",
        "relationship_score",
        "evidence",
        "action",
        "opportunity_id",
        "color", "evidence_refs",
        "missing_fields",
        "dimensions", "score", "assessment", "coaching_action", "strengths", "improvements", "visit_id",
    }
)


def result_shape(value, depth=0):
    """Keep only known field names and value types; never retain user/model text."""
    if depth > 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {key: result_shape(item, depth + 1) for key, item in value.items() if key in SHAPE_FIELDS}
    if isinstance(value, list):
        return {"type": "list", "count": len(value), "sample_shapes": [result_shape(x, depth + 1) for x in value[:3]]}
    return type(value).__name__


class InferenceAudit:
    def __init__(self, database, actor, operation_id, *, mode, run_id, facts, config):
        self.database, self.actor, self.operation_id = database, actor, operation_id
        self.mode, self.run_id, self.config = mode, run_id, config
        if facts.get("company_policy"):
            policy = facts["company_policy"]
            self.config = {**config, "company_policy": {k: policy.get(k) for k in ("id", "code", "version", "source")}}
        if facts.get("agent_business_policy"):
            self.config = {
                **self.config, "agent_business_policy": business_policy_metadata(facts["agent_business_policy"]),
            }
        encoded = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str).encode()
        self.input_summary = {
            "facts_bytes": len(encoded),
            "facts_sha256": hashlib.sha256(encoded).hexdigest(),
            "data_as_of": facts.get("data_as_of"),
            "record_counts": {key: len(value) for key, value in facts.items() if isinstance(value, list)},
        }
        self.events = []
        self.started = monotonic()

    def event(self, phase, provider, **metadata):
        self.events.append(
            {"phase": phase, "provider": provider, "elapsed_ms": round((monotonic() - self.started) * 1000), **metadata}
        )

    async def start(self):
        lease = current_job_lease.get()
        try:
            request_id = str(UUID(current_request_id()))
        except ValueError:
            request_id = None
        await self._write(
            """INSERT INTO agent.inference_operation
               (id,workspace_id,actor_user_ref_id,actor_role_code,capability,run_id,job_id,
                request_id,scope_snapshot,configuration,input_summary)
               VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6::uuid,$7::uuid,$8::uuid,$9::jsonb,$10::jsonb,$11::jsonb)""",
            self.operation_id,
            self.actor.workspace_id,
            self.actor.user_id,
            self.actor.role.value,
            self.mode,
            self.run_id,
            lease.job_id if lease else None,
            request_id,
            {"data_scope": self.actor.data_scope.value, "team_ids": list(self.actor.team_ids)},
            self.config,
            self.input_summary,
        )

    async def finish(self, status, *, trace, error_code=None):
        await self._write(
            """UPDATE agent.inference_operation SET status=$2,trace=$3::jsonb,events=$4::jsonb,
               error_code=$5,completed_at=clock_timestamp() WHERE id=$1::uuid AND status='running'""",
            self.operation_id,
            status,
            trace,
            self.events,
            error_code,
        )

    async def _write(self, sql, *args):
        if self.database is None:  # Pure routing tests use an explicit in-memory service.
            return
        try:
            # Accounting is independent of a business transaction/job lease and
            # bounded so an unavailable audit store cannot hang the user request.
            async with asyncio.timeout(1):
                async with self.database.connection() as connection:
                    async with connection.transaction():
                        await set_request_context(connection, self.actor)
                        await connection.execute(sql, *args)
        except Exception:
            logger.exception(
                "inference audit receipt unavailable operation=%s",
                self.operation_id,
                extra={"system_event": True, "event_type": "inference_audit_gap", "actor": self.actor},
            )


async def audit_original(*, database, actor, mode, facts, model, invoke, validate, run_id=None, execution_policy=None):
    """Audit the unchanged original API path, without enabling platform routing."""
    from sales_backend.services.model_calls import DatabaseModelObserver

    policy = (execution_policy or {}).get("definition") or {}
    budget = policy.get("total_seconds") if policy.get("override_budget") else None
    operation_id = str(uuid4())
    audit = InferenceAudit(
        database,
        actor,
        operation_id,
        mode=mode,
        run_id=run_id,
        facts=facts,
        config={"platform_enabled": False, "direct_model": model, "routing_reason": "platform_not_selected",
                "execution_policy": execution_policy or {},
                **({"platform_seconds": 0, "total_seconds": budget} if budget is not None else {})},
    )
    await audit.start()
    status, error = "failed", None
    trace = {"operation_id": operation_id, "provider": None, "fallback_reason": None}
    try:
        audit.event("route_started", "senseaudio")
        async with asyncio.timeout(budget):
            result = await invoke(
                DatabaseModelObserver(database, actor, mode, operation_id=operation_id, run_id=run_id)
            )
        audit.event("output_received", "senseaudio", shape=result_shape(result))
        result = validate(result)
        audit.event("contract_accepted", "senseaudio")
        status = "accepted"
        trace.update(provider="senseaudio", model_ref=model, elapsed_ms=round((monotonic() - audit.started) * 1000))
        return result, trace
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        error = type(exc).__name__
        from sales_backend.domain.model_contract import contract_failure_details

        audit.event("route_failed", "senseaudio", error_code=error, **contract_failure_details(exc))
        raise
    finally:
        await audit.finish(status, trace=trace, error_code=error)
