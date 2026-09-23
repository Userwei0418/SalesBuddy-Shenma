"""Real PostgreSQL/RLS: immutable event facts and idempotent recipient updates."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.repositories.opportunity_changes import complete_change_review, review_is_pending
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_visit_entry_platform import existing_actor


@pytest.mark.asyncio
async def test_change_snapshot_and_single_card_completion(connection):
    actor = await existing_actor(connection, "XS001", "sales")
    customer = await connection.fetchrow(
        "SELECT id::text FROM crm.customer WHERE deleted_at IS NULL "
        "AND owner_user_ref_id=$1::uuid ORDER BY id LIMIT 1", actor.user_id,
    )
    assert customer, "Existing sales-owned customer required"
    data = dict(name=f"事务验证商机-{uuid4()}", probability=30, amount=Decimal("500000"),
                expected_close_date=date(2026, 12, 1), sales_channel="direct",
                quarterly_forecasts=[dict(year=2026, quarter=4, recognized_amount=Decimal("0"),
                                          collection_amount=Decimal("0"))])
    opportunity = await save_opportunity(connection, actor, customer_id=customer["id"], data=data)
    assert not await connection.fetchval(
        "SELECT count(*) FROM ops.job WHERE job_type='opportunity.change.review' AND aggregate_id=$1::uuid",
        opportunity["event_id"],
    )
    update = {**data, "action": "update", "opportunity_id": opportunity["id"],
              "version_no": opportunity["version_no"], "probability": 50}
    current_visit = {"follow_up_record": "客户确认试点通过，但本季度预算暂缓。", "next_action": "9月18日复核预算"}
    first = await save_opportunity(connection, actor, customer_id=customer["id"], data=update,
                                   visit_context=current_visit)
    event = first["event_id"]
    snapshot = await connection.fetchval(
        "SELECT payload->'facts' FROM ops.job WHERE job_type='opportunity.change.review' AND aggregate_id=$1::uuid",
        event,
    )
    assert Decimal(str(snapshot["change"]["before"]["probability"])) == 30
    assert snapshot["change"]["after"]["probability"] == 50
    assert snapshot["change"]["version"] == first["version_no"]
    assert snapshot["records"]["current_visit"]["follow_up_record"] == current_visit["follow_up_record"]
    assert snapshot["change"]["fallback"]["tone"] == "green"
    assert all(len(snapshot["records"][kind]) <= 20 for kind in ("visits", "tasks", "risks"))
    assert await review_is_pending(connection, event)
    noop = await save_opportunity(connection, actor, customer_id=customer["id"],
                                  data={**update, "version_no": first["version_no"]})
    assert not noop["changed"]
    second = await save_opportunity(connection, actor, customer_id=customer["id"],
                                    data={**update, "version_no": first["version_no"], "probability": 70})
    assert await connection.fetchval(
        "SELECT payload->'facts' FROM ops.job WHERE job_type='opportunity.change.review' AND aggregate_id=$1::uuid",
        event,
    ) == snapshot
    original_count = await connection.fetchval(
        "SELECT count(*) FROM workflow.notification WHERE payload->>'event_id'=$1", event,
    )
    review = {"status": "completed", "color": "yellow", "source": "agent_platform",
              "summary": "阶段推进，但客户确认预算暂缓。", "evidence_refs": ["change", "current_visit"]}
    assert await complete_change_review(connection, event, review) >= original_count > 0
    assert not await review_is_pending(connection, event)
    assert await complete_change_review(connection, event, {**review, "color": "red"}) == 0
    card = await connection.fetchrow(
        "SELECT title,payload FROM workflow.notification WHERE payload->>'event_id'=$1 LIMIT 1", event,
    )
    assert card["title"] == "商机变化需关注" and card["payload"]["change_review"]["color"] == "yellow"
    assert await review_is_pending(connection, second["event_id"])
    assert await connection.fetchval(
        "SELECT count(*) FROM workflow.notification WHERE payload->>'event_id'=$1", event,
    ) == original_count
    from tests.integration.test_fde_identity_tasks import fde_fixture
    _, _, people, _ = await fde_fixture(connection)
    await existing_actor(connection, people["first"]["code"], "fde")
    assert not await review_is_pending(connection, second["event_id"])
    # A different recipient must not change the originator's assessment.
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="outside current scope"):
        async with connection.transaction():
            await complete_change_review(connection, second["event_id"], review)
