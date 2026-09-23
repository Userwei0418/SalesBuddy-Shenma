"""Split visit lifecycle against real PostgreSQL/RLS; model output is an explicit fixture.

Uses existing business accounts and rolls back through the integration fixture.
Actual provider success is verified separately by the release smoke check.
"""

from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from sales_backend.config import get_settings
from sales_backend.contracts.visit_flow import QualityRequest, StructureRequest, canonical_fields, validate_stage_result
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.handler import AgentRunHandler
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.visit_archive import archive_visit
from sales_backend.services.visit_flow import prepare_visit_run


class TransactionDatabase:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def transaction(self, actor, readonly=False):
        async with self.connection.transaction():
            await set_request_context(self.connection, actor)
            yield self.connection


async def existing_actor(connection, account, role):
    row = await connection.fetchrow(
        "SELECT * FROM security.resolve_account_actor($1,$2,$3)", "demo-sales-workspace", account, role
    )
    if not row:
        pytest.skip(f"Existing business account {account} unavailable")
    actor = IdentityRepository()._actor(row).context
    await set_request_context(connection, actor)
    return actor


@pytest.mark.asyncio
@pytest.mark.parametrize("account,role", [("XS001", "sales"), ("FDE001", "fde")])
@pytest.mark.parametrize("case", ["archive_replay", "low_score", "next_rejected", "changed_content", "structure_only"])
async def test_split_visit_requires_own_unchanged_passing_quality(connection, account, role, case):
    # Every branch must execute in a fresh CI database. Do not depend on a
    # previously imported customer, existing FDE account or live model run.
    from tests.integration.test_fde_identity_tasks import fde_fixture
    sales, opportunity, people, _ = await fde_fixture(connection)
    actor = sales if role == "sales" else await existing_actor(connection, people["first"]["code"], role)
    await set_request_context(connection, actor)
    customer_id, opportunity_id = opportunity["customer_id"], opportunity["id"]
    customer = await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id)
    database = TransactionDatabase(connection)
    settings = get_settings()
    handler, store = AgentRunHandler(database, settings), AgentRunStore(database, settings)
    fields = canonical_fields(
        {
            "customer_name": customer["name"],
            "customer_type": "客户",
            "follow_up_record": "陈经理确认10条样本中8条通过，2条待补版本。",
            "next_action": "2026年9月18日前本人补齐样本并与陈经理复核",
            "interaction_at": "2026-09-15",
            "created_date": "2026-09-15",
            "contact_name": "陈经理",
        }
    )
    summary = "样本复核"
    first = await prepare_visit_run(
        connection,
        actor,
        StructureRequest(
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            text=fields["follow_up_record"] + fields["next_action"],
        ),
        "structure",
    )
    run = await handler._load_and_start(first["run_id"], actor)
    facts = await handler.facts_loader.load(run)
    fields["created_date"] = facts["server_fields"]["created_date"]
    result = validate_stage_result({"fields": fields, "summary": summary}, facts)
    assert "quality_review" not in result
    await store.persist_result(run, result, facts)
    review_id = first["run_id"]
    if case != "structure_only":
        second = await prepare_visit_run(
            connection,
            actor,
            QualityRequest(
                customer_id=customer_id,
                opportunity_id=opportunity_id,
                source_run_id=first["run_id"],
                fields=fields,
                summary=summary,
            ),
            "quality",
        )
        run = await handler._load_and_start(second["run_id"], actor)
        facts = await handler.facts_loader.load(run)
        assert facts["fields"] == fields  # Date strings cannot be normalized as timestamps.
        result = validate_stage_result(
            {
                "fields": fields,
                "summary": summary,
                "quality_review": {
                    "follow_up_score": 60 if case == "low_score" else 95,
                    "suggestions": [],
                    "next_action": {
                        "passed": case != "next_rejected",
                        "time_found": True,
                        "goal_or_plan_found": True,
                        "suggestions": [],
                    },
                },
            },
            facts,
        )
        await store.persist_result(run, result, facts)
        review_id = second["run_id"]
    submitted = {**deepcopy(fields), "opportunity_id": opportunity_id, "_quality_review_run_id": review_id}
    if case == "changed_content":
        submitted["contact_name"] = "改了联系人"
    task_count = await connection.fetchval("SELECT count(*) FROM workflow.task")
    if case != "archive_replay":
        with pytest.raises(ValueError):
            async with connection.transaction():
                await archive_visit(connection, actor, customer_id=customer_id, fields=submitted)
        return
    visit = await archive_visit(connection, actor, customer_id=customer_id, fields=submitted)
    replay = await archive_visit(connection, actor, customer_id=customer_id, fields=submitted)
    assert visit["id"] == replay["id"] and replay["replayed"]
    assert (
        await connection.fetchval("SELECT follow_up_task_mode FROM activity.visit WHERE id=$1::uuid", visit["id"])
        == "human_advice"
    )
    assert await connection.fetchval("SELECT count(*) FROM workflow.task") == task_count
