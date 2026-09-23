from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.domain.tasks import TaskConflict
from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.tasks import TaskService
from tests.integration.provision import create_owned_customer

pytestmark = pytest.mark.asyncio


async def customer(connection, actor):
    return await create_owned_customer(
        connection,
        actor,
        data=dict(
            name=f"实绩测试-{uuid4()}",
            industry="",
            partner_name="",
            customer_type="潜在客户",
            level_code="Tier-2",
            source="销售线索",
            target_team=await connection.fetchval(
                "SELECT name FROM platform.team WHERE id=$1::uuid", actor.team_ids[0]
            ),
            contact_name="演示联系人",
            contact_title="经理",
            contact_role="决策者",
        ),
    )


def fact(c, **overrides):
    data = dict(
        customer_id=UUID(c["id"]),
        opportunity_id=None,
        kind="recognized",
        amount=Decimal("120000"),
        occurred_on=today(),
        source_ref=str(uuid4()),
        note="",
        request_id=uuid4(),
        confirmed=True,
    )
    return {**data, **overrides}


async def test_actual_periods_idempotency_source_and_void(connection, actor_factory):
    sales = await actor_factory(RoleCode.SALES)
    c = await customer(connection, sales)
    repo = CustomerAssetRepository()
    data = fact(c)
    with pytest.raises(PermissionError):
        await repo.create(connection, sales, data)
    manager = await actor_factory(RoleCode.MANAGER)
    first = await repo.create(connection, manager, data)
    replay = await repo.create(connection, manager, data)
    assert replay["id"] == first["id"] and replay["replayed"]
    with pytest.raises(FileExistsError):
        await repo.create(connection, manager, {**data, "amount": Decimal(10)})
    await repo.create(connection, manager, fact(c, amount=Decimal("80000"), occurred_on=date(today().year - 1, 12, 31)))
    await repo.create(connection, manager, fact(c, kind="collection", amount=Decimal(0)))
    annual = await repo.read(connection, customer_id=UUID(c["id"]), period="year", limit=1)
    assert annual["summary"]["recognized_amount"] == 120000
    assert annual["summary"]["collection_amount"] == 0 and annual["summary"]["collection_count"] == 1
    assert annual["has_more"] and len(annual["items"]) == 1
    cumulative = await repo.read(connection, customer_id=UUID(c["id"]), period="all")
    assert cumulative["summary"]["recognized_amount"] == 200000
    assert cumulative["summary"]["unlinked_count"] == 3
    await repo.void(connection, manager, UUID(first["id"]), "录入金额更正")
    await repo.void(connection, manager, UUID(first["id"]), "重试")
    annual = await repo.read(connection, customer_id=UUID(c["id"]), period="year")
    assert annual["summary"]["recognized_amount"] is None
    assert (
        await connection.fetchval("SELECT void_reason FROM crm.customer_actual WHERE id=$1::uuid", first["id"])
        == "录入金额更正"
    )
    sales = await actor_factory(RoleCode.SALES)
    assert (await repo.read(connection, customer_id=UUID(c["id"]), period="all"))["summary"][
        "recognized_amount"
    ] == 80000
    with pytest.raises(PermissionError):
        await repo.void(connection, sales, UUID(first["id"]), "不允许")

    manager = await actor_factory(RoleCode.MANAGER)
    corrected = await repo.create(connection, manager, {**data, "request_id": uuid4(), "amount": Decimal("110000")})
    assert corrected["id"] != first["id"]
    assert (await repo.read(connection, customer_id=UUID(c["id"]), period="year"))["summary"][
        "recognized_amount"
    ] == 110000
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM crm.customer_actual WHERE customer_id=$1 AND source_ref=$2",
            UUID(c["id"]),
            data["source_ref"],
        )
        == 2
    )


async def test_actual_customer_scope_and_link_validation(connection, actor_factory):
    sales = await actor_factory(RoleCode.SALES)
    c = await customer(connection, sales)
    other = await customer(connection, sales)
    opp = await save_opportunity(
        connection,
        sales,
        customer_id=c["id"],
        data=dict(
            name="项目一", probability=30, amount=Decimal("9000000"), expected_close_date=today() + timedelta(days=30),
            quarterly_forecasts=[dict(year=2026,quarter=4,recognized_amount=Decimal('0'),collection_amount=Decimal('0'))],
        ),
    )
    manager = await actor_factory(RoleCode.MANAGER)
    repo = CustomerAssetRepository()
    with pytest.raises(ValueError, match="不属于"):
        await repo.create(connection, manager, fact(other, opportunity_id=UUID(opp["id"])))
    await repo.create(connection, manager, fact(c, opportunity_id=UUID(opp["id"])))
    # Forecast/ACV never becomes actuals, no double-count from opportunity joins.
    result = await repo.read(connection, customer_id=UUID(c["id"]), opportunity_id=UUID(opp["id"]))
    assert result["summary"]["recognized_amount"] == 120000 and result["summary"]["collection_amount"] is None
    assert (await repo.read(connection, owner_id=uuid4()))["summary"]["entry_count"] == 0
    rows = await CustomerRepository().list(connection, query=None, level=None, unassigned=None, limit=None)
    row = next(x for x in rows if x["id"] == c["id"])
    assert len(row["plan_close_dates"]) == 1
    sales = await actor_factory(RoleCode.SALES)
    from datetime import UTC, datetime

    task = await TaskService().create(
        connection,
        actor=sales,
        description="关联商机的演示任务",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="normal",
        customer_id=c["id"],
        opportunity_id=opp["id"],
    )
    assert (
        await connection.fetchval("SELECT opportunity_id::text FROM workflow.task WHERE id=$1::uuid", task["id"])
        == opp["id"]
    )
    with pytest.raises(TaskConflict, match="不属于"):
        await TaskService().create(
            connection,
            actor=sales,
            description="拒绝串客户的商机",
            assignee_account_code="XS001",
            due_at=datetime.now(UTC) + timedelta(days=3),
            priority_code="normal",
            customer_id=other["id"],
            opportunity_id=opp["id"],
        )
