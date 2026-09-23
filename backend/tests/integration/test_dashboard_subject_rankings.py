"""Subject selection never changes the peer cohort or the caller's detail permissions."""

from datetime import date
from uuid import uuid4

import pytest

from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.fde_dashboard import fde_dashboard
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.dashboard_rankings import dashboard_rankings
from tests.integration.test_dashboard_selection import two_teams
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import members, other_project, own_visit
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def ranks(connection, who, *, personal=True, member_id=None, team_groups=()):
    return await dashboard_rankings(
        connection, who, year=2026, quarters=[3], personal=personal, member_id=member_id, team_groups=team_groups
    )


async def test_same_salesperson_same_rank_for_self_supervisor_and_manager(connection):
    first, second, other_id = await two_teams(connection)
    sales = await actor(connection, "XS001")
    own = await ranks(connection, sales)
    assert own["opportunity_acv"]["rows"][0]["user_id"] == other_id
    assert next(r for r in own["opportunity_acv"]["rows"] if r["user_id"] == sales.user_id)["rank"] == 2
    supervisor = await actor(connection, "ZJ001")
    viewed = await ranks(connection, supervisor, member_id=sales.user_id)
    assert viewed["opportunity_acv"] == own["opportunity_acv"]
    assert viewed["followup"] == own["followup"]
    assert viewed["selection"]["member_id"] == sales.user_id
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", second["id"]) == 0
    facts = await DashboardRepository().load(connection, supervisor, personal=True, member_id=sales.user_id)
    assert [r["id"] for r in facts["opportunities"]] == [first["id"]]
    with pytest.raises(PermissionError):
        await ranks(connection, supervisor, member_id=other_id)
    manager = await actor(connection, "ZJL001")
    managed = await ranks(connection, manager, member_id=sales.user_id)
    assert managed["opportunity_acv"] == own["opportunity_acv"]
    assert managed["followup"] == own["followup"]
    own_manager = await ranks(connection, manager)
    assert own_manager["selection"]["cohort_role"] == "manager"
    assert all(r["role"] == "manager" for r in own_manager["opportunity_acv"]["rows"])
    assert first["id"] not in str(managed) and second["id"] not in str(managed)


async def test_team_ranks_are_company_complete_selection_only_changes_summary(connection):
    _, second, _ = await two_teams(connection)
    supervisor = await actor(connection, "ZJ001")
    own = await ranks(connection, supervisor, personal=False)
    assert own["scope"] == "company_teams"
    assert own["selection"]["team_groups"] == ["south_hkmo"]
    assert {r["code"]: (r["value"], r["rank"]) for r in own["opportunity_acv"]["rows"]} == {
        "north_east": (200, 1),
        "south_hkmo": (100, 2),
    }
    assert len(own["followup"]["rows"]) == 2
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", second["id"]) == 0
    manager = await actor(connection, "ZJL001")
    for groups in (["north_east"], ["south_hkmo"], ["north_east", "south_hkmo"]):
        data = await ranks(connection, manager, personal=False, team_groups=groups)
        assert data["opportunity_acv"] == own["opportunity_acv"]
        assert data["selection"]["team_groups"] == groups
        assert len(data["opportunity_acv"]["rows"]) == 2  # Never rank a synthetic combined team.


async def test_rank_endpoint_rejects_unauthorized_subject_and_team_selection(connection):
    _, _, other_id = await two_teams(connection)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "ZJ001")
        base = {"year": 2026, "quarters": [3], "personal": True}
        assert (
            await client.get("/api/v1/dashboard/rankings", params={**base, "member_id": other_id})
        ).status_code == 403
        assert (
            await client.get("/api/v1/dashboard/rankings", params={**base, "team_groups": "north_east"})
        ).status_code == 403
        assert (
            await client.get("/api/v1/dashboard/rankings", params={**base, "team_groups": "bad"})
        ).status_code == 422
        await business_login(client, "XS001")
        assert (
            await client.get("/api/v1/dashboard/rankings", params={**base, "member_id": other_id})
        ).status_code == 403


async def test_fde_selected_person_same_peer_rank_team_ledger_deduplicates_shared_project(connection):
    _, opportunity, people, team_id = await fde_fixture(connection)
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    other_team = await repo.save_department(
        connection,
        admin,
        None,
        {"code": "FDE-OTHER-" + uuid4().hex[:6], "name": "另一个FDE团队", "status": "active", "parent_team_id": None},
    )
    await repo.create(
        connection,
        admin,
        {
            "account_code": "FDX" + uuid4().hex[:8],
            "display_name": "公司另一FDE",
            "roles": ["fde"],
            "team_id": other_team["id"],
        },
    )
    manager = await actor(connection, "ZJL001")
    await connection.execute(
        """INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,
        source_ref,request_id,confirmed_by_user_ref_id) VALUES($1::uuid,$2::uuid,$3::uuid,'recognized',50000,$4,
        '隔离排名确收',$5,$6::uuid)""",
        manager.workspace_id,
        opportunity["customer_id"],
        opportunity["id"],
        date(2026, 9, 1),
        uuid4(),
        manager.user_id,
    )
    first = await actor(connection, people["first"]["code"])
    own = await fde_dashboard(connection, first, scope="self", year=2026, quarters=[3])
    company = own["company_rankings"]
    assert company["scope"] == "all_fde" and len(company["items"]) == 3
    assert sum(row["recognized_amount"] for row in company["items"]) == 100000  # Two participant rows.
    lead = await actor(connection, people["lead"]["code"])
    viewed = await fde_dashboard(connection, lead, scope="team", member_id=first.user_id, year=2026, quarters=[3])
    assert viewed["company_rankings"]["items"] == company["items"]
    assert viewed["company_rankings"]["selection"]["member_id"] == first.user_id
    team = await fde_dashboard(connection, lead, scope="team", year=2026, quarters=[3])
    assert team["company_rankings"]["scope"] == "company_fde_teams"
    assert team["company_rankings"]["selection"]["team_ids"] == [team_id]
    rows = {r["user_id"]: r for r in team["company_rankings"]["items"]}
    assert len(rows) == 2 and rows[team_id]["recognized_amount"] == 50000
    assert rows[other_team["id"]]["recognized_amount"] == 0
    own_lead = await fde_dashboard(connection, lead, scope="self", year=2026, quarters=[3])
    assert own_lead["company_rankings"]["scope"] == "all_fde_leads"
    assert [r["user_id"] for r in own_lead["company_rankings"]["items"]] == [lead.user_id]


@pytest.mark.parametrize("change", ["transfer", "second_team", "departed", "inactive"])
async def test_fde_team_followups_keep_event_team_after_membership_changes(connection, change):
    _, opportunity, people, old_team = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    await own_visit(connection, first, opportunity)
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    new_team = await repo.save_department(
        connection,
        admin,
        None,
        {"code": "FDE-HISTORY-" + uuid4().hex[:6], "name": "调入FDE团队", "status": "active", "parent_team_id": None},
    )
    await repo.create(
        connection,
        admin,
        {
            "account_code": "FDH" + uuid4().hex[:8],
            "display_name": "调入团队成员",
            "roles": ["fde"],
            "team_id": new_team["id"],
        },
    )
    if change == "transfer":
        await repo.roles_and_team(connection, admin, first.user_id, ["fde"], new_team["id"])
    elif change == "second_team":
        await connection.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
            "VALUES($1::uuid,$2::uuid,'fde','self',$3::uuid)",
            admin.workspace_id,
            first.user_id,
            new_team["id"],
        )
        await connection.execute(
            "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role,is_primary) "
            "VALUES($1::uuid,$2::uuid,$3::uuid,'fde',false)",
            admin.workspace_id,
            first.user_id,
            new_team["id"],
        )
    elif change == "departed":
        await connection.execute(
            "UPDATE platform.team_membership SET valid_to=clock_timestamp() "
            "WHERE user_ref_id=$1::uuid AND valid_to='infinity'",
            first.user_id,
        )
    else:
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", first.user_id)
    lead = await actor(connection, people["lead"]["code"])
    data = await fde_dashboard(connection, lead, scope="team", year=2026, quarters=[3])
    rows = {r["user_id"]: r for r in data["company_rankings"]["items"]}
    assert rows[old_team]["followup_count"] == 1
    assert rows[new_team["id"]]["followup_count"] == 0
    assert rows[old_team]["opportunity_count"] == 1
    assert rows[new_team["id"]]["opportunity_count"] == 0
    assert sum(r["followup_count"] for r in rows.values()) == 1
    assert data["summary"]["period_visits"] == 1  # Same event ownership as the existing upper metrics.
    assert data["summary"]["period_opportunities"] == rows[old_team]["opportunity_count"]


async def test_fde_team_period_projects_deduplicate_members_and_respect_dates(connection):
    sales, opportunity, people, team_id = await fde_fixture(connection)
    extra = await other_project(connection, sales, opportunity)
    await members(connection, sales, extra, [people["first"]["id"]])
    first = await actor(connection, people["first"]["code"])
    await own_visit(connection, first, opportunity, on="2026-09-10")
    await own_visit(connection, first, opportunity, on="2026-09-11")
    await own_visit(connection, first, extra, on="2026-08-31")
    second = await actor(connection, people["second"]["code"])
    await own_visit(connection, second, opportunity, on="2026-09-12")
    lead = await actor(connection, people["lead"]["code"])
    for start, end, expected_projects, expected_visits in [
        (date(2026, 9, 1), date(2026, 9, 15), 1, 3),
        (date(2026, 8, 1), date(2026, 9, 15), 2, 4),
        (date(2026, 9, 14), date(2026, 9, 15), 0, 0),
    ]:
        result = await fde_dashboard(
            connection,
            lead,
            scope="team",
            team_id=team_id,
            year=2026,
            date_from=start,
            date_to=end,
        )
        assert result["company_rankings"]["complete"] is True
        row = next(r for r in result["company_rankings"]["items"] if r["user_id"] == team_id)
        assert row["opportunity_count"] == result["summary"]["period_opportunities"] == expected_projects
        assert row["followup_count"] == result["summary"]["period_visits"] == expected_visits
        assert row["role"] == "fde_team"
    # Personal cohorts still count each person's own distinct projects.
    first = await actor(connection, people["first"]["code"])
    personal = await fde_dashboard(
        connection,
        first,
        scope="self",
        year=2026,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 15),
    )
    assert personal["company_rankings"]["scope"] == "all_fde"
    assert sum(r["opportunity_count"] for r in personal["company_rankings"]["items"]) == 2
