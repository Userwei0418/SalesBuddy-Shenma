from tests.integration.provision import create_owned_customer, create_partner
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.services.opportunities import save_opportunity
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.opportunity_mutations import forecasts

pytestmark = pytest.mark.asyncio


async def setup_customer(connection, actor):
    repo = CustomerMutationRepository()
    team = await connection.fetchval("SELECT name FROM platform.team WHERE id=$1::uuid", actor.team_ids[0])
    customer = await create_owned_customer(
        connection,
        actor,
        data=dict(
            name=f"商机闭环-{uuid4()}",
            industry="",
            partner_name="",
            customer_type="潜在客户",
            level_code="Tier-2",
            source="销售线索",
            target_team=team,
            contact_name="林悦",
            contact_title="经理",
            contact_role="决策者",
        ),
    )
    return repo, customer


async def test_lifecycle_forecasts_differences_notifications(connection, sales_actor):
    repo, c = await setup_customer(connection, sales_actor)
    partner = await create_partner(connection, sales_actor, '示例伙伴')
    data = dict(
        name="客服知识助手",
        probability=30,
        amount=Decimal("500000"),
        expected_close_date=date(2026, 12, 1),
        partner_name="示例伙伴",
        partner_id=str(partner['id']), sales_channel='partner',
        quarterly_forecasts=[
            dict(year=2026, quarter=3, recognized_amount=Decimal("0"), collection_amount=Decimal("0")),
            dict(year=2026, quarter=4, recognized_amount=Decimal("300000"), collection_amount=Decimal("200000")),
        ],
    )
    o = await save_opportunity(connection, sales_actor, customer_id=c["id"], data=data)
    assert o["changed"] and o["status"] == "open"
    count = await connection.fetchval(
        "SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid AND template_code='business_changed'",
        o["id"],
    )
    unchanged = await save_opportunity(
        connection,
        sales_actor,
        customer_id=c["id"],
        data={**data, "action": "update", "opportunity_id": o["id"], "version_no": o["version_no"]},
    )
    assert not unchanged["changed"] and unchanged["version_no"] == o["version_no"]
    assert count == await connection.fetchval(
        "SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid AND template_code='business_changed'",
        o["id"],
    )
    update = {
        **data,
        "action": "update",
        "opportunity_id": o["id"],
        "version_no": o["version_no"],
        "probability": 50,
        "quarterly_forecasts": [
            dict(year=2026, quarter=4, recognized_amount=Decimal("350000"), collection_amount=Decimal("250000"))
        ],
    }
    o = await save_opportunity(connection, sales_actor, customer_id=c["id"], data=update)
    q = await forecasts(connection, o["id"])
    assert q[0]["recognized_amount"] == 0 and q[0]["collection_amount"] == 0
    assert q[1]["recognized_amount"] == 350000
    card = await connection.fetchrow(
        "SELECT payload FROM workflow.notification WHERE dedupe_key LIKE $1", f"business_changed:{o['event_id']}:%"
    )
    assert card["payload"]["tone"] == "green"
    win = {**update, "version_no": o["version_no"], "status": "won", "probability": 100}
    with pytest.raises(ValueError, match="确认"):
        await save_opportunity(connection, sales_actor, customer_id=c["id"], data=win)
    o = await save_opportunity(connection, sales_actor, customer_id=c["id"], data={**win, "closure_confirmed": True})
    assert o["status"] == "won"
    closed = await connection.fetchval("SELECT closed_at FROM crm.opportunity WHERE id=$1::uuid", o["id"])
    assert closed
    reopen = {**update, "version_no": o["version_no"], "probability": 50}
    with pytest.raises(ValueError, match="重新打开"):
        await save_opportunity(connection, sales_actor, customer_id=c["id"], data=reopen)
    o = await save_opportunity(connection, sales_actor, customer_id=c["id"], data={**reopen, "reopen_confirmed": True})
    assert not await connection.fetchval("SELECT closed_at FROM crm.opportunity WHERE id=$1::uuid", o["id"])
    o = await save_opportunity(
        connection,
        sales_actor,
        customer_id=c["id"],
        data={
            **update,
            "version_no": o["version_no"],
            "status": "lost",
            "probability": None,
            "closure_confirmed": True,
        },
    )
    assert o["status"] == "lost" and o["probability"] == 50
    with pytest.raises(FileExistsError):
        await save_opportunity(connection, sales_actor, customer_id=c["id"], data=data)
    with pytest.raises(ValueError, match="一条预测"):
        await save_opportunity(
            connection,
            sales_actor,
            customer_id=c["id"],
            data={**data, "name": "另一商机", "quarterly_forecasts": [data["quarterly_forecasts"][0]] * 2},
        )


async def test_customer_noop_and_meaningful_changes(connection, sales_actor):
    repo, c = await setup_customer(connection, sales_actor)
    version = await connection.fetchval("SELECT version_no FROM crm.customer WHERE id=$1::uuid", c["id"])
    result = await repo.update(
        connection, sales_actor, customer_id=c["id"], data={"name": c["name"]}, expected_version=version
    )
    assert not result["changed"]
    await repo.update(
        connection, sales_actor, customer_id=c["id"], data={"customer_type": "商机客户"}, expected_version=version
    )
    card = await connection.fetchrow(
        "SELECT payload FROM workflow.notification WHERE object_id=$1::uuid AND template_code='business_changed'",
        c["id"],
    )
    assert card["payload"]["changes"][0]["after"] == "商机客户"
