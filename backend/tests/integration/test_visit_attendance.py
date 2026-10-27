"""Original business accounts; every temporary write rolls back, no provider calls."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from sales_backend.api.business import create_visit
from sales_backend.config import get_settings
from sales_backend.contracts.models import OpportunityCreate, VisitCreate
from sales_backend.contracts.visit_flow import QualityRequest, StructureRequest, canonical_fields, validate_stage_result
from sales_backend.db import set_request_context
from sales_backend.repositories.collaboration import effective_ids
from sales_backend.services.agent_run.handler import AgentRunHandler
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.visit_flow import prepare_visit_run
from tests.integration.test_visit_entry_platform import TransactionDatabase


async def review_attendance(
    connection, actor, customer_id, opportunity_id, participants, mutation=None, *, fields_override=None,
):
    database = TransactionDatabase(connection)
    handler = AgentRunHandler(database, get_settings())
    store = AgentRunStore(database, get_settings())
    customer = await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id)
    fields = canonical_fields({
        "customer_type": "客户", "customer_name": customer["name"], "contact_name": "陈经理",
        "interaction_at": "2026-09-15", "created_date": "2026-09-15",
        "follow_up_record": "双方复核十条样本，八条通过，两条需补充版本。",
        "next_action": "2026年9月18日前销售补齐两条样本并与陈经理复核。",
        **(fields_override or {}),
    })
    source = await prepare_visit_run(connection, actor, StructureRequest(
        customer_id=customer_id, opportunity_id=opportunity_id, text=fields["follow_up_record"]
    ), "structure")
    run = await handler._load_and_start(source["run_id"], actor)
    facts = await handler.facts_loader.load(run)
    fields["created_date"] = facts["server_fields"]["created_date"]
    await store.persist_result(run, validate_stage_result({"fields": fields, "summary": "样本复核"}, facts), facts)
    body = QualityRequest(customer_id=customer_id, opportunity_id=opportunity_id, source_run_id=source["run_id"],
                          fields=fields, summary="样本复核", fde_participant_ids=participants,
                          opportunity_mutation=mutation)
    quality = await prepare_visit_run(connection, actor, body, "quality")
    run = await handler._load_and_start(quality["run_id"], actor)
    facts = await handler.facts_loader.load(run)
    result = validate_stage_result({"fields": fields, "summary": body.summary, "quality_review": {
        "follow_up_score": 92, "suggestions": [], "next_action": {
            "passed": True, "time_found": True, "goal_or_plan_found": True, "suggestions": []
        }
    }}, facts)
    await store.persist_result(run, result, facts)
    submitted = VisitCreate(customer_id=customer_id, fde_participant_ids=participants, fields={
        **fields, "opportunity_id": opportunity_id, "_quality_review_run_id": quality["run_id"],
        "_opportunity_mutation": mutation,
    })
    return body, submitted


@pytest.mark.asyncio
@pytest.mark.parametrize("with_opportunity", [False, True])
async def test_sales_attendance_is_visit_only_and_does_not_count_as_fde_authorship(connection, with_opportunity):
    from tests.integration.test_fde_identity_tasks import fde_fixture
    from tests.integration.test_operations_claims_sql import actor

    sales, source_project, people, _ = await fde_fixture(connection)
    customer_id = source_project["customer_id"]
    zhou, zhang, lead_id = (people[key]["id"] for key in ("first", "second", "lead"))
    lead = await actor(connection, people["lead"]["code"])
    personal_count = await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history()")
    project_access_before = await connection.fetchval("SELECT id FROM crm.opportunity WHERE id=$1::uuid", source_project["id"])
    await set_request_context(connection, sales)
    oid = source_project["id"] if with_opportunity else None
    roster = await effective_ids(connection, source_project["id"])
    version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", source_project["id"])
    opportunity_count = await connection.fetchval("SELECT count(*) FROM crm.opportunity")
    database, identity = TransactionDatabase(connection), SimpleNamespace(actor=sales)
    for attendees in ([zhang], [], [zhang, lead_id, zhang]):
        _, submitted = await review_attendance(connection, sales, customer_id, oid, attendees)
        wrong = submitted.model_copy(deep=True)
        wrong.fde_participant_ids = [UUID(zhou)]
        with pytest.raises(HTTPException, match="重新质检"):
            await create_visit(wrong, identity, database, None)
        visit = await create_visit(submitted, identity, database, None)
        replay = await create_visit(submitted, identity, database, None)
        assert replay["replayed"] and replay["id"] == visit["id"]
        assert visit["opportunity_id"] == oid
        assert set(visit["fde_participant_ids"]) == set(attendees)
        assert await effective_ids(connection, source_project["id"]) == roster
        assert await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", source_project["id"]) == version
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity") == opportunity_count
        assert await connection.fetchval(
            "SELECT count(*) FROM activity.visit_participant WHERE visit_id=$1::uuid AND participant_role='fde'",
            visit["id"]
        ) == len(set(attendees))
        assert await connection.fetchval(
            "SELECT recording_role_code_snapshot FROM activity.visit WHERE id=$1::uuid", visit["id"]
        ) == "sales"
    await set_request_context(connection, lead)
    assert await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history()") == personal_count
    if not with_opportunity:
        # Attendance allows this event without changing the existing project access.
        assert await connection.fetchval("SELECT id FROM activity.visit WHERE id=$1::uuid", visit["id"])
        assert await connection.fetchval("SELECT id FROM crm.opportunity WHERE id=$1::uuid", source_project["id"]) == project_access_before


@pytest.mark.asyncio
async def test_quality_rejects_invalid_attendance_before_enqueuing(connection):
    from tests.integration.test_fde_identity_tasks import fde_fixture

    actor, row, people, _ = await fde_fixture(connection)
    await set_request_context(connection, actor)
    body, _ = await review_attendance(connection, actor, row["customer_id"], None, [])
    before = await connection.fetchval("SELECT count(*) FROM agent.run")
    cases = [({"fde_participant_ids": [actor.user_id]}, "FDE"),
             ({"fde_participant_ids": [str(uuid4())]}, "FDE"),
             ({"opportunity_mutation": OpportunityCreate(name="旧草稿", probability=10, amount=100,
               expected_close_date="2026-12-01", fde_member_ids=[])}, "仅选择本次")]
    for patch, message in cases:
        invalid = deepcopy(body).model_copy(update=patch)
        with pytest.raises(ValueError, match=message):
            await prepare_visit_run(connection, actor, invalid, "quality")
    assert await connection.fetchval("SELECT count(*) FROM agent.run") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("already_archived", [False, True])
async def test_customer_attendance_rechecks_permission_before_quality_archive_and_replay(connection, already_archived):
    from sales_backend.api.visit_flow import enqueue
    from tests.integration.test_authorization_opportunities import grant
    from tests.integration.test_fde_identity_tasks import fde_fixture
    from tests.integration.test_operations_claims_sql import actor

    sales, row, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    body, submitted = await review_attendance(connection, sales, row["customer_id"], None, [people["first"]["id"]])
    database, identity = TransactionDatabase(connection), SimpleNamespace(actor=sales)
    key = str(uuid4())
    if already_archived:
        await create_visit(submitted, identity, database, key)
    admin = await actor(connection, "ADMIN001")
    await grant(connection, admin, sales.user_id, [dict(permission="visit.attendance_manage", effect="deny")])
    await set_request_context(connection, sales)
    before = await connection.fetchval("SELECT count(*) FROM activity.visit")
    for operation in (lambda: enqueue(body, "quality", identity, database, None),
                      lambda: create_visit(submitted, identity, database, key)):
        with pytest.raises(HTTPException) as error:
            await operation()
        assert error.value.status_code == 403
    assert await connection.fetchval("SELECT count(*) FROM activity.visit") == before
