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
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.visit_flow import prepare_visit_run
from tests.integration.test_visit_entry_platform import TransactionDatabase, existing_actor


async def review_attendance(connection, actor, customer_id, opportunity_id, participants, mutation=None):
    database = TransactionDatabase(connection)
    handler = AgentRunHandler(database, get_settings())
    store = AgentRunStore(database, get_settings())
    customer = await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id)
    fields = canonical_fields({
        "customer_type": "客户", "customer_name": customer["name"], "contact_name": "陈经理",
        "interaction_at": "2026-09-15", "created_date": "2026-09-15",
        "follow_up_record": "双方复核十条样本，八条通过，两条需补充版本。",
        "next_action": "2026年9月18日前销售补齐两条样本并与陈经理复核。",
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
async def test_sales_attendance_appends_without_replacing_or_counting_fde_authorship(connection):
    from tests.integration.test_fde_identity_tasks import fde_fixture
    from tests.integration.test_operations_claims_sql import actor

    sales, source_project, people, _ = await fde_fixture(connection, extra_members=1)
    customer_id = source_project["customer_id"]
    zhou, zhang, ye = (people[key]["id"] for key in ("first", "second", "extra0"))
    lead = await actor(connection, people["lead"]["code"])
    personal_count = await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history()")
    await set_request_context(connection, sales)
    data = OpportunityCreate(name=f"事务回滚-本次参与-{uuid4().hex[:8]}", probability=10,
                             amount=120000, expected_close_date="2026-12-01", sales_channel="direct",
                             fde_member_ids=[zhou]).model_dump()
    opportunity = await save_opportunity(connection, sales, customer_id=customer_id, data=data)
    oid = opportunity["id"]
    database, identity = TransactionDatabase(connection), SimpleNamespace(actor=sales)
    for attendees, expected in [([zhang], {zhou, zhang}), ([], {zhou, zhang}),
                                 ([zhang, ye, zhang], {zhou, zhang, ye})]:
        # Exercise the same commercial form mutation used by the mini program.
        mutation = OpportunityCreate(**{**data, "action": "update", "opportunity_id": oid,
            "fde_member_ids": None, "version_no": await connection.fetchval(
                "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", oid
            )}).model_dump(mode="json")
        _, submitted = await review_attendance(connection, sales, customer_id, oid, attendees, mutation)
        wrong = submitted.model_copy(deep=True)
        wrong.fde_participant_ids = [UUID(zhou)]
        with pytest.raises(HTTPException, match="重新质检"):
            await create_visit(wrong, identity, database, None)
        visit = await create_visit(submitted, identity, database, None)
        replay = await create_visit(submitted, identity, database, None)
        assert replay["replayed"] and replay["id"] == visit["id"]
        assert set(visit["fde_participant_ids"]) == set(attendees)
        assert await effective_ids(connection, oid) == expected
        assert await connection.fetchval(
            "SELECT count(*) FROM activity.visit_participant WHERE visit_id=$1::uuid AND participant_role='fde'",
            visit["id"]
        ) == len(set(attendees))
        assert await connection.fetchval(
            "SELECT recording_role_code_snapshot FROM activity.visit WHERE id=$1::uuid", visit["id"]
        ) == "sales"
    await set_request_context(connection, lead)
    assert await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history()") == personal_count
    await set_request_context(connection, sales)
    # Standalone editing remains an explicit full-roster replacement.
    await save_opportunity(connection, sales, customer_id=customer_id, data={
        **data, "action": "update", "opportunity_id": oid, "fde_member_ids": [zhou],
        "version_no": await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", oid)
    })
    assert await effective_ids(connection, oid) == {zhou}


@pytest.mark.asyncio
async def test_quality_rejects_invalid_attendance_before_enqueuing(connection):
    from tests.integration.test_fde_identity_tasks import fde_fixture

    actor, row, people, _ = await fde_fixture(connection)
    await set_request_context(connection, actor)
    fde = people["first"]["id"]
    body, _ = await review_attendance(connection, actor, row["customer_id"], row["id"], [])
    before = await connection.fetchval("SELECT count(*) FROM agent.run")
    cases = [({"fde_participant_ids": [actor.user_id]}, "FDE"),
             ({"fde_participant_ids": [fde], "opportunity_id": None}, "先关联商机"),
             ({"opportunity_mutation": OpportunityCreate(name="旧草稿", probability=10, amount=100,
               expected_close_date="2026-12-01", fde_member_ids=[])}, "仅选择本次")]
    for patch, message in cases:
        invalid = deepcopy(body).model_copy(update=patch)
        with pytest.raises(ValueError, match=message):
            await prepare_visit_run(connection, actor, invalid, "quality")
    assert await connection.fetchval("SELECT count(*) FROM agent.run") == before
