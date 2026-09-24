from datetime import date, timedelta
from decimal import Decimal

import pytest
from tests.integration.feishu_fixtures import seed_execute

from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.marketing import efficiency_rankings
from sales_backend.repositories.rankings import ranking
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_opportunity_lifecycle import setup_customer
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def opportunity(connection, account, amount, status="open", close=date(2026, 9, 12)):
    person = await actor(connection, account)
    _, customer = await setup_customer(connection, person)
    return await save_opportunity(
        connection,
        person,
        customer_id=customer["id"],
        data={
            "name": "隔离排名商机",
            "amount": Decimal(amount),
            "probability": 100 if status == "won" else 10,
            "status": status,
            "closure_confirmed": True,
            "expected_close_date": close,
            "quarterly_forecasts": [
                {"year": 2026, "quarter": 3, "recognized_amount": Decimal(0), "collection_amount": Decimal(0)}
            ],
        },
    )


async def test_sales_peer_ranking_exposes_only_aggregates_and_preserves_detail_permissions(connection):
    first = await opportunity(connection, "XS001", 100)
    second = await opportunity(connection, "XS002", 200)
    await opportunity(connection, "XS002", 9999, "lost")
    await opportunity(connection, "XS002", 9999, "won")
    await actor(connection, "XS001")
    result = await ranking(connection, "opportunity_acv", date(2026, 1, 1), date(2026, 12, 31), months=[7, 8, 9])
    assert result["scope"] == "peer" and result["groups"] == [] and len(result["rows"]) == 2
    own = next(row for row in result["rows"] if row["account_code"] == "XS001")
    assert own["account_code"] == "XS001" and own["rank"] == 2 and own["population"] == 2
    assert own["value"] == 100 and second["id"] not in str(result)
    assert next(row for row in result["rows"] if row["account_code"] == "XS002")["value"] == 200
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", second["id"]) == 0
    await actor(connection, "ZJL001")
    all_rows = await ranking(connection, "opportunity_acv", date(2026, 1, 1), date(2026, 12, 31), months=[7, 8, 9])
    assert len(all_rows["rows"]) == 4  # Includes real zero-opportunity supervisor and manager.
    assert all_rows["groups"][0]["name"] == "南区＋港澳" and all_rows["groups"][0]["value"] == 300
    assert sum(r["value"] for r in all_rows["rows"]) == 300
    personal = await ranking(connection, "opportunity_acv", date(2026, 1, 1), date(2026, 12, 31), personal=True)
    assert len(personal["rows"]) == 1 and personal["rows"][0]["value"] == 0 and personal["groups"] == []
    # Current totals exclude Lost, include Won; no list-page truncation or customer-owner inner join.
    second_actor = await actor(connection, "XS002")
    marketing = await efficiency_rankings(connection, second_actor)
    assert marketing["opportunities"]["all"][0]["value"] == 2
    assert marketing["opportunities"]["all"][0]["rank"] == 1
    first_actor = await actor(connection, "XS001")
    await save_opportunity(
        connection,
        first_actor,
        customer_id=first["customer_id"],
        data={
            "name": first["name"],
            "action": "update",
            "opportunity_id": first["id"],
            "version_no": first["version_no"],
            "amount": Decimal(200),
        },
    )
    tied = await ranking(connection, "opportunity_acv", date(2026, 1, 1), date(2026, 12, 31))
    assert tied["rows"][0]["rank"] == 1


async def test_rank_endpoint_validates_quarters_and_exposes_peer_aggregates(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get(
            "/api/v1/dashboard/rankings", params={"year": 2026, "quarters": [1, 3], "personal": False}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["scope"] == "peer" and data["quarters"] == [1, 3]
        assert data["opportunity_acv"]["months"] == [1, 2, 3, 7, 8, 9]
        assert len(data["opportunity_acv"]["rows"]) == 2
        assert (
            await client.get("/api/v1/dashboard/rankings", params={"year": 2026, "quarters": [0]})
        ).status_code == 422


async def test_followup_counts_distinct_group_customers_and_active_won_with_period_boundary(connection):
    won = await opportunity(connection, "XS001", 100, "won")
    lost = await opportunity(connection, "XS001", 100, "lost")
    now = today()
    owner = await actor(connection, "XS001")

    async def visit(person, customer_id, opportunity_id=None, offset=0):
        return await VisitRepository().create(
            connection,
            person,
            customer_id=customer_id,
            fields={
                "opportunity_id": opportunity_id,
                "interaction_at": (now - timedelta(days=offset)).isoformat(),
                "created_date": now.isoformat(),
                "contact_name": "隔离联系人",
                "follow_up_record": "客户明确试点效果",
                "next_action": "下周一由销售确认反馈",
                "_follow_up_quality_score": 85,
            },
        )

    await visit(owner, won["customer_id"], won["id"])
    await visit(owner, won["customer_id"], won["id"])
    await visit(owner, lost["customer_id"], lost["id"])
    await visit(owner, won["customer_id"], won["id"], offset=7)
    other = await actor(connection, "XS002")
    await visit(other, won["customer_id"])  # Following another customer's record doesn't claim ownership.
    await actor(connection, "ZJL001")
    result = await ranking(connection, "followup", now - timedelta(days=6), now)
    assert sum(r["value"] for r in result["rows"]) == 4
    group = next(g for g in result["groups"] if g["code"] == "south_hkmo")
    assert group["value"] == 4 and group["customer_count"] == 2
    active = await ranking(connection, "active_opportunities", now - timedelta(days=6), now)
    assert sum(r["value"] for r in active["rows"]) == 1  # Repeated won followups count one; Lost never counts active.
    empty = await ranking(connection, "active_opportunities", date(2020, 1, 1), date(2020, 12, 31))
    assert sum(r["value"] for r in empty["rows"]) == 0


async def test_supervisor_ranks_nonprimary_team_members_without_cross_team_facts_or_duplicates(connection):
    from uuid import uuid4

    from sales_backend.repositories.dashboard import DashboardRepository
    from sales_backend.repositories.operations_accounts import OperationsAccountRepository

    now = today()
    south_op = await opportunity(connection, "XS001", 100, close=now)
    peer_op = await opportunity(connection, "XS002", 200, close=now)
    north_op = await opportunity(connection, "XS001", 900, close=now)
    member = await actor(connection, "XS001")
    south_team = member.team_ids[0]
    before_customers = await ranking(connection, "customers", date(now.year, 1, 1), date(now.year, 12, 31))
    previous_customer_count = next(r for r in before_customers["rows"] if r["account_code"] == "XS001")["value"]
    admin = await actor(connection, "ADMIN001")
    north_team = str(uuid4())
    await connection.execute(
        "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1::uuid,$2::uuid,$3,'北区')",
        north_team,
        admin.workspace_id,
        "rank-north-" + uuid4().hex,
    )
    # A current, valid secondary relationship must not disappear when another team becomes primary.
    await connection.execute(
        "UPDATE platform.team_membership SET is_primary=false WHERE user_ref_id=$1::uuid", member.user_id
    )
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,'sales',true)",
        admin.workspace_id,
        north_team,
        member.user_id,
    )
    # Overlapping effective memberships are permitted historically and must never multiply a record.
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary,valid_from) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,'sales',false,clock_timestamp()-interval '1 day')",
        admin.workspace_id,
        south_team,
        member.user_id,
    )
    await seed_execute(
        connection, "UPDATE crm.opportunity SET owner_team_id=$2::uuid WHERE id=$1::uuid", north_op["id"], north_team
    )
    await seed_execute(
        connection, "UPDATE crm.customer SET owner_team_id=$2::uuid WHERE id=$1::uuid", north_op["customer_id"], north_team
    )
    # A different person with only an expired south-team membership remains outside the cohort.
    unrelated = await OperationsAccountRepository().create(
        connection,
        admin,
        {"account_code": "RANKNORTH", "display_name": "仅北区成员", "roles": ["sales"], "team_id": north_team},
    )
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,"
        "is_primary,valid_from,valid_to) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,'sales',false,clock_timestamp()-interval '3 days',"
        "clock_timestamp()-interval '1 day')",
        admin.workspace_id,
        south_team,
        unrelated["id"],
    )

    for op, team in [(south_op, south_team), (north_op, north_team)]:
        who = await actor(connection, "XS001")
        visit = await VisitRepository().create(
            connection,
            who,
            customer_id=op["customer_id"],
            fields={
                "opportunity_id": op["id"],
                "interaction_at": now.isoformat(),
                "created_date": now.isoformat(),
                "contact_name": "多部门排名联系人",
                "follow_up_record": "客户已确认试点反馈",
                "next_action": "销售于下周一发送试点结果",
                "_follow_up_quality_score": 85,
            },
        )
        await actor(connection, "ADMIN001")
        await seed_execute(
            connection, "UPDATE activity.visit SET recorder_team_id=$2::uuid WHERE id=$1::uuid", visit["id"], team
        )

    supervisor = await actor(connection, "ZJ001")
    start, end = date(now.year, 1, 1), date(now.year, 12, 31)
    result = await ranking(connection, "opportunity_acv", start, end)
    by_account = {r["account_code"]: r for r in result["rows"]}
    assert by_account["XS001"]["value"] == 100 and by_account["XS001"]["record_count"] == 1
    assert by_account["XS002"]["value"] == 200 and by_account["XS002"]["rank"] == 1
    assert "RANKNORTH" not in by_account
    assert sum(r["value"] for r in result["rows"]) == 300
    assert next(g for g in result["groups"] if g["code"] == "south_hkmo")["value"] == 300
    assert not any(g["code"] == "north_east" for g in result["groups"])
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", north_op["id"]) == 0
    dashboard = await DashboardRepository().load(connection, supervisor)
    assert {r["id"] for r in dashboard["opportunities"]} == {south_op["id"], peer_op["id"]}
    assert sum(r["amount"] for r in dashboard["opportunities"]) == sum(r["value"] for r in result["rows"])
    for metric in ("followup", "active_opportunities"):
        ranks = await ranking(connection, metric, start, end)
        assert next(r for r in ranks["rows"] if r["account_code"] == "XS001")["value"] == 1
        assert sum(r["value"] for r in ranks["rows"]) == 1
    customer_ranks = await ranking(connection, "customers", start, end)
    assert (
        next(r for r in customer_ranks["rows"] if r["account_code"] == "XS001")["value"] == previous_customer_count - 1
    )
    assert not any(g["code"] == "north_east" for g in customer_ranks["groups"])
    # Peer aggregates cross team boundaries without granting project detail access.
    await actor(connection, "XS002")
    sales_ranks = await ranking(connection, "opportunity_acv", start, end)
    assert {r["account_code"] for r in sales_ranks["rows"]} == {"XS001", "XS002", "RANKNORTH"}
    assert all(r["population"] == 3 for r in sales_ranks["rows"]) and sales_ranks["groups"] == []
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", north_op["id"]) == 0

    # A supervisor covering both teams sees both records once, not one copy per membership.
    await actor(connection, "ADMIN001")
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,'supervisor',false)",
        admin.workspace_id,
        north_team,
        supervisor.user_id,
    )
    await actor(connection, "ZJ001")
    combined = await ranking(connection, "opportunity_acv", start, end)
    same_member = next(r for r in combined["rows"] if r["account_code"] == "XS001")
    assert same_member["value"] == 1000 and same_member["record_count"] == 2
    assert sum(r["value"] for r in combined["rows"]) == 1200
    assert {g["code"]: g["value"] for g in combined["groups"]} == {"south_hkmo": 300, "north_east": 900}
    for metric in ("followup", "active_opportunities"):
        ranks = await ranking(connection, metric, start, end)
        assert next(r for r in ranks["rows"] if r["account_code"] == "XS001")["value"] == 2
        assert sum(r["value"] for r in ranks["rows"]) == 2
