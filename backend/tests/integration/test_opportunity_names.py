from tests.integration.provision import create_owned_customer
from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.services.opportunities import save_opportunity
from sales_backend.repositories.opportunities import opportunity_name_available

pytestmark = pytest.mark.asyncio


async def test_names_are_unique_per_customer_for_create_update_and_closed(connection, sales_actor):
    team = await connection.fetchval("SELECT name FROM platform.team WHERE id=$1::uuid", sales_actor.team_ids[0])

    async def customer():
        return await create_owned_customer(
            connection,
            sales_actor,
            data={
                "name": f"名称边界-{uuid4()}",
                "industry": "",
                "partner_name": "",
                "customer_type": "潜在客户",
                "level_code": "Tier-2",
                "source": "销售线索",
                "target_team": team,
                "contact_name": "陈经理",
                "contact_title": "经理",
                "contact_role": "决策者",
            },
        )

    a, b = await customer(), await customer()
    data = dict(name="客服 AI 助手", probability=30, amount=10000, expected_close_date=date(2026, 12, 1),
        quarterly_forecasts=[dict(year=2026,quarter=4,recognized_amount=0,collection_amount=0)])
    first = await save_opportunity(connection, sales_actor, customer_id=a["id"], data=data)
    assert not await opportunity_name_available(connection, a["id"], " 客服\tai助手 ")
    assert await opportunity_name_available(connection, a["id"], data["name"], first["id"])
    await save_opportunity(connection, sales_actor, customer_id=b["id"], data=data)
    with pytest.raises(FileExistsError):
        await save_opportunity(connection, sales_actor, customer_id=a["id"], data={**data, "name": "客服ai助手"})
    second = await save_opportunity(connection, sales_actor, customer_id=a["id"], data={**data, "name": "财务助手"})
    version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", second["id"])
    with pytest.raises(FileExistsError):
        await save_opportunity(
            connection,
            sales_actor,
            customer_id=a["id"],
            data={
                **data,
                "action": "update",
                "opportunity_id": second["id"],
                "version_no": version,
            },
        )
    await save_opportunity(
        connection,
        sales_actor,
        customer_id=a["id"],
        data={
            **data,
            "name": "财务助手",
            "action": "update",
            "opportunity_id": second["id"],
            "version_no": version,
        },
    )
    await connection.execute("UPDATE crm.opportunity SET status='lost' WHERE id=$1::uuid", first["id"])
    assert not await opportunity_name_available(connection, a["id"], "客服Ai助手")
    # The database must also reject a writer that bypasses repository validation.
    with pytest.raises(asyncpg.UniqueViolationError):
        async with connection.transaction():
            await connection.execute("UPDATE crm.opportunity SET name=$2 WHERE id=$1::uuid", second["id"], "客服AI助手")
