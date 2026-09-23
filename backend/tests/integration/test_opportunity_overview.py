from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from sales_backend.repositories.customer_assets import CustomerAssetRepository
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.opportunity_overview import opportunity_overview
from sales_backend.repositories.visits import VisitRepository
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_assets import fact
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def test_overview_native_periods_followup_dedup_and_owner_scope(connection):
    owner = await actor(connection, "XS001")
    before = await opportunity_overview(connection, owner, year=2026, quarters=[1, 3])
    opened = await opportunity(connection, "XS001", 100, close=date(2026, 1, 1))
    won = await opportunity(connection, "XS001", 100, "won", close=date(2026, 4, 1))
    lost = await opportunity(connection, "XS001", 100, "lost", close=date(2026, 1, 1))
    await connection.execute(
        """UPDATE crm.opportunity SET created_at='2026-03-31T16:00:00Z',
        closed_at=CASE WHEN status='won' THEN '2026-09-01T00:00:00+08'::timestamptz ELSE closed_at END
        WHERE id=ANY($1::uuid[])""",
        [opened["id"], won["id"], lost["id"]],
    )
    for op, when in [(opened, "2026-04-01"), (won, "2026-01-02"), (won, "2026-09-02"), (lost, "2026-09-02")]:
        await VisitRepository().create(
            connection,
            owner,
            customer_id=op["customer_id"],
            fields={
                "opportunity_id": op["id"],
                "interaction_at": when,
                "created_date": "2026-09-12",
                "contact_name": "隔离联系人",
                "follow_up_record": "客户已确认试点反馈",
                "next_action": "2026年9月15日由销售发送方案",
                "_follow_up_quality_score": 85,
            },
        )
    after = await opportunity_overview(connection, owner, year=2026, quarters=[1, 3])
    delta = {k: after["metrics"][k] - before["metrics"][k] for k in ("won", "total", "active", "newCount")}
    assert delta == {"won": 1, "total": 3, "active": 1, "newCount": 0}
    # A different salesperson never contributes to self overview.
    await opportunity(connection, "XS002", 99999, close=date(2026, 1, 1))
    owner = await actor(connection, "XS001")
    assert (await opportunity_overview(connection, owner, year=2026, quarters=[1, 3]))["metrics"] == after["metrics"]
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get("/api/v1/opportunities/overview", params={"year": 2026, "quarters": [1, 3]})
        assert response.status_code == 200, response.text
        assert response.json()["metrics"] == after["metrics"]
        assert (
            await client.get("/api/v1/opportunities/overview", params={"year": 2026, "quarters": [5]})
        ).status_code == 422


async def test_history_creation_dates_are_business_facts_not_import_timestamps(connection):
    template = await opportunity(connection, "XS001", 100, close=date(2032, 1, 1))
    await connection.execute("UPDATE crm.opportunity SET created_at='2032-01-01' WHERE id=$1::uuid", template["id"])
    owner = await actor(connection, "XS001")
    before = await opportunity_overview(connection, owner, year=2026, quarters=[3])
    await actor(connection, "OPS001")
    await connection.execute("SELECT set_config('app.feishu_historical_import','on',true)")
    specs = [
        ({"source_fields": {"商机创建时间": "2026-07-01T00:00:00+08:00"}}, "lost", "2029-09-22"),
        ({"raw_fields": {"商机创建时间": "2026/03/31"}}, "open", "2029-09-22"),
        ({}, "open", "2029-09-22"),
        ({"source_fields": {"商机创建时间": "2026-02-30"}}, "open", "2029-09-22"),
        ({"source_fields": {"商机创建时间": "2025-12-31"}}, "open", "2029-09-22"),
        (None, "open", "2026-09-01"),
    ]
    ids = []
    for extra, status, timestamp in specs:
        meta = {"import_type": "crm_history", **extra} if extra is not None else {}
        ids.append(await connection.fetchval(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
              created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code,
              created_at,import_meta)
            SELECT workspace_id,customer_id,$2,owner_user_ref_id,owner_team_id,
              created_by_user_ref_id,stage_code,$3,amount,probability,expected_close_date,source_code,
              $4::text::timestamptz,$5::jsonb FROM crm.opportunity WHERE id=$1::uuid RETURNING id""",
            template["id"], "隔离日期商机-" + uuid4().hex, status, timestamp, meta,
        ))
    await connection.execute("SELECT set_config('app.feishu_historical_import','off',true)")
    owner = await actor(connection, "XS001")
    q3 = await opportunity_overview(connection, owner, year=2026, quarters=[3])
    year = await opportunity_overview(connection, owner, year=2026, quarters=[1, 2, 3, 4])
    all_time = await opportunity_overview(connection, owner, year=2026, quarters=[])
    assert q3["definition_version"] == "opportunity_overview_v3"
    assert q3["metrics"]["total"] == before["metrics"]["total"] + 6
    assert year["metrics"]["total"] == all_time["metrics"]["total"] == q3["metrics"]["total"]
    assert q3["metrics"]["newCount"] == before["metrics"]["newCount"] + 2
    assert year["metrics"]["newCount"] == 3
    assert all_time["metrics"]["newCount"] == 5  # Includes the ordinary template and 2025 history.
    assert all(result["metrics"]["missingCreatedDates"] == 2 for result in (q3, year, all_time))
    assert all(result["metrics"][key] <= result["metrics"]["total"]
               for result in (q3, year, all_time) for key in ("active", "won", "newCount"))
    page = await OpportunityRepository().page(connection, owner, limit=20, include_closed=True)
    assert 2025 in page["facets"]["years"] and 2026 in page["facets"]["years"]
    assert 2029 not in page["facets"]["years"]  # Import time is not a business creation year.
    assert await connection.fetchval(
        "SELECT count(*) FROM crm.opportunity WHERE id=ANY($1::uuid[]) AND extract(year FROM created_at)=2029", ids
    ) == 5  # Reading must not rewrite the audit timestamp.
    await opportunity(connection, "XS002", 999, close=date(2026, 9, 1))
    owner = await actor(connection, "XS001")
    assert (await opportunity_overview(connection, owner, year=2026, quarters=[3]))["metrics"] == q3["metrics"]
    await connection.execute("SELECT set_config('app.workspace_id',$1,true)", str(uuid4()))
    hidden = await opportunity_overview(connection, owner, year=2026, quarters=[])
    assert all(hidden["metrics"][key] == 0 for key in ("total", "newCount", "missingCreatedDates", "active", "won"))


async def test_full_pagination_beyond_300_and_linked_actuals_only(connection):
    op = await opportunity(connection, "XS001", 100)
    owner = await actor(connection, "XS001")
    await connection.execute(
        """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
          created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code)
        SELECT workspace_id,customer_id,'分页商机-'||n,owner_user_ref_id,owner_team_id,
          created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code
        FROM crm.opportunity CROSS JOIN generate_series(1,305) n WHERE id=$1::uuid""",
        op["id"],
    )
    manager = await actor(connection, "ZJL001")
    assets = CustomerAssetRepository()
    customer = {"id": op["customer_id"]}
    await assets.create(
        connection, manager, fact(customer, amount=Decimal("9000"))
    )  # Customer-level remains unallocated.
    await assets.create(connection, manager, fact(customer, opportunity_id=UUID(op["id"]), amount=Decimal("12.34")))
    await assets.create(
        connection, manager, fact(customer, opportunity_id=UUID(op["id"]), kind="collection", amount=Decimal("0"))
    )
    voided = await assets.create(
        connection, manager, fact(customer, opportunity_id=UUID(op["id"]), amount=Decimal("77"))
    )
    await assets.void(connection, manager, UUID(voided["id"]), "修正")
    owner = await actor(connection, "XS001")
    filters = dict(
        customer_id=op["customer_id"],
        owner_name=None,
        probability=None,
        stage_code=None,
        close_from=None,
        close_to=None,
        include_closed=True,
    )
    repo = OpportunityRepository()
    first = await repo.page(connection, owner, limit=300, **filters)
    second = await repo.page(connection, owner, limit=300, offset=first["next_offset"], **filters)
    assert len(first["items"]) == 300 and first["has_more"]
    assert len(second["items"]) == 6 and not second["has_more"] and second["next_offset"] is None
    rows = first["items"] + second["items"]
    assert len({r["id"] for r in rows}) == 306
    own = next(r for r in rows if r["id"] == op["id"])
    assert own["actuals"] == {"recognized_amount": Decimal("12.34"), "collection_amount": Decimal(0)}
    assert own["owner_id"] == str(owner.user_id)
    assert all(r["actuals"]["recognized_amount"] is None for r in rows if r["id"] != op["id"])
