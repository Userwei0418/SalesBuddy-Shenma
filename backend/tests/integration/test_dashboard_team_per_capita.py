"""Public team cohorts, per-capita denominator and drilldown under real runtime grants."""

from datetime import date
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.dashboard_scope import dashboard_team_groups
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.rankings import department_ranking_groups
from sales_backend.services.dashboard_rankings import dashboard_rankings
from tests.integration.feishu_fixtures import fixture_owner, seed_execute
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_dashboard_departments import add_department, followup
from tests.integration.test_dashboard_selection import two_teams
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio
START, END = date(2026, 9, 13), date(2026, 9, 19)


async def groups(connection, who, metric="followup", team_ids=None, legacy=False):
    return await department_ranking_groups(
        connection, who, metric, START, END, months=None, team_ids=team_ids, legacy=legacy
    )


async def test_zero_members_operations_and_multiple_roles_use_one_business_denominator(connection, monkeypatch):
    monkeypatch.setattr("sales_backend.services.dashboard_rankings.today", lambda: END)
    south, north, _ = await two_teams(connection)
    for _ in range(2):
        await followup(connection, "XS001", south)
    await followup(connection, "XS002", north)
    admin = await actor(connection, "ADMIN001")
    # Operations has an organization membership but no business appointment.
    operations = await actor(connection, "OPS001")
    await actor(connection, "ADMIN001")
    sales = await connection.fetchval("SELECT id FROM platform.user_ref WHERE account_code='XS001'")
    await OperationsAccountRepository().roles_and_team(
        connection, admin, sales, ["sales", "fde"], admin.team_ids[0], company_roles=["administrator"]
    )
    manager = await actor(connection, "ZJL001")
    result = await dashboard_rankings(connection, manager, year=2026, quarters=[3], personal=False)
    rows = {r["name"]: r for r in result["followup"]["rows"]}
    # Two visits / three current sales-business members loses to one visit / one member.
    assert (rows["北区"]["value"], rows["北区"]["rank"]) == (1, 1)
    assert rows["南区"]["value"] == pytest.approx(2 / 3)
    assert rows["南区"]["average"] == 0.67 and rows["南区"]["rank"] == 2
    assert rows["南区"]["member_count"] == 3
    assert len(rows["南区"]["members"]) == 3
    assert {r["followup_count"] for r in rows["南区"]["members"]} == {0, 2}
    assert operations.user_id not in str(rows)
    for row in rows.values():
        assert sum(m["followup_count"] for m in row["members"]) == row["record_count"]
        assert sum(m["current_member"] for m in row["members"]) == row["member_count"]
    assert south["id"] not in str(rows) and north["customer_id"] not in str(rows)


async def test_fde_kind_excluded_before_ranking_product_sales_retained_and_directory_unchanged(connection, monkeypatch):
    monkeypatch.setattr("sales_backend.services.dashboard_rankings.today", lambda: END)
    south, north, _ = await two_teams(connection)
    await followup(connection, "XS001", south)
    await followup(connection, "XS002", north)
    manager = await actor(connection, "ZJL001")
    north_team = await connection.fetchval("SELECT id FROM platform.team WHERE name='北区'")
    await connection.execute(
        "UPDATE platform.team SET name='任意改名的FDE团队',attributes=attributes||'{\"kind\":\"fde\"}' WHERE id=$1",
        north_team,
    )
    await connection.execute(
        "UPDATE platform.team SET name='增长中心',attributes=attributes||'{\"kind\":\"product_sales\"}' "
        "WHERE id=$1::uuid",
        manager.team_ids[0],
    )
    directory = await dashboard_team_groups(connection, manager)
    assert len(directory) == 2
    result = await dashboard_rankings(
        connection, manager, year=2026, quarters=[3], personal=False, team_groups=["team:" + str(north_team)]
    )
    for key in ["opportunity_acv", "followup"]:
        assert [(r["name"], r["rank"]) for r in result[key]["rows"]] == [("增长中心", 1)]
    assert result["opportunity_acv"]["rows"][0]["value"] == 100
    assert len(result["active_opportunities"]["groups"]) == 2
    assert result["selection"]["team_groups"] == ["team:" + str(north_team)]
    personal = await dashboard_rankings(connection, manager, year=2026, quarters=[3], personal=True)
    assert sum(r["value"] for r in personal["region"]["rows"]) == 100
    assert (
        sum(
            r["value"]
            for r in await department_ranking_groups(
                connection, manager, "opportunity_acv", date(2026, 1, 1), END, months=None, team_ids=None, legacy=True
            )
        )
        == 100
    )


async def test_cross_team_roles_do_not_duplicate_events_and_transfer_preserves_recorded_team(connection):
    south, north, _ = await two_teams(connection)
    await followup(connection, "XS001", south)
    admin = await actor(connection, "ADMIN001")
    north_id = str(await connection.fetchval("SELECT id FROM platform.team WHERE name='北区'"))
    sales_id = str(await connection.fetchval("SELECT id FROM platform.user_ref WHERE account_code='XS001'"))
    repo = OperationsAccountRepository()
    await repo.roles_and_team(
        connection,
        admin,
        sales_id,
        [],
        admin.team_ids[0],
        memberships=[
            {"team_id": admin.team_ids[0], "roles": ["sales", "supervisor"]},
            {"team_id": north_id, "roles": ["sales"]},
        ],
    )
    manager = await actor(connection, "ZJL001")
    rows = {r["name"]: r for r in await groups(connection, manager)}
    assert (rows["南区"]["member_count"], rows["北区"]["member_count"]) == (3, 2)
    assert sum(r["record_count"] for r in rows.values()) == 1
    await set_request_context(connection, admin)
    await repo.roles_and_team(connection, admin, sales_id, ["sales"], north_id)
    await set_request_context(connection, manager)
    rows = {r["name"]: r for r in await groups(connection, manager)}
    assert (rows["南区"]["member_count"], rows["南区"]["record_count"]) == (2, 1)
    former = next(m for m in rows["南区"]["members"] if m["user_id"] == sales_id)
    assert former["followup_count"] == 1 and former["current_member"] is False
    assert rows["北区"]["record_count"] == 0


@pytest.mark.parametrize("invalid", ["inactive", "expired_membership", "expired_role"])
async def test_denominator_excludes_invalid_business_members(connection, invalid):
    await two_teams(connection)
    manager = await actor(connection, "ZJL001")
    user = await connection.fetchval("SELECT id FROM platform.user_ref WHERE account_code='XS002'")
    if invalid == "inactive":
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", user)
    elif invalid == "expired_membership":
        await connection.execute(
            "UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1", user
        )
    else:
        await connection.execute(
            "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1", user
        )
    row = next(r for r in await groups(connection, manager) if r["name"] == "北区")
    assert row["member_count"] == 0
    assert row["rank"] is None and row["value"] is None and row["average"] is None


async def test_period_boundary_confirmed_only_ties_and_acl_scope(connection):
    south, north, _ = await two_teams(connection)
    await followup(connection, "XS001", south, on="2026-09-12")
    await followup(connection, "XS001", south, on="2026-09-13")
    await followup(connection, "XS001", south, on="2026-09-19")
    await followup(connection, "XS001", south, on="2026-09-20")
    await followup(connection, "XS001", south, on="2026-09-18")
    await followup(connection, "XS002", north, on="2026-09-19")
    manager = await actor(connection, "ZJL001")
    rows = await groups(connection, manager)
    assert [r["value"] for r in rows] == [1, 1]
    assert [r["rank"] for r in rows] == [1, 1]
    assert sum(r["record_count"] for r in rows) == 4
    # A pure FDE account can receive only this aggregate feature for one team.
    _, reader = await add_department(connection, role="fde")
    admin = await actor(connection, "ADMIN001")
    await grant(
        connection,
        admin,
        reader["id"],
        [dict(permission="dashboard.ranking", effect="allow", scope="teams", team_ids=list(manager.team_ids))],
    )
    # Default FDE templates grant workspace rankings. Configure this isolated template
    # without that grant before testing a deliberately narrow account override.
    await seed_execute(
        connection,
        "DELETE FROM config.permission_role_grant WHERE permission_code='dashboard.ranking' "
        "AND role_id IN (SELECT id FROM config.permission_role WHERE workspace_id=$1::uuid "
        "AND builtin_role_code='fde')",
        admin.workspace_id,
    )
    restricted = await actor(connection, reader["account_code"])
    rows = await groups(connection, restricted)
    assert {r["name"] for r in rows} == {"南区"}
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", south["id"]) == 0
    assert await groups(connection, restricted, team_ids=[str(uuid4())]) == []
    # Even a known team UUID cannot cross tenant scope.
    foreign_workspace, foreign_team = uuid4(), uuid4()
    await seed_execute(
        connection,
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'外部公司')",
        foreign_workspace,
        str(foreign_workspace),
    )
    await seed_execute(
        connection,
        "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'foreign','外部团队')",
        foreign_team,
        foreign_workspace,
    )
    assert await groups(connection, restricted, team_ids=[str(foreign_team)]) == []
    await grant(connection, admin, reader["id"], [dict(permission="dashboard.ranking", effect="deny")], version=1)
    restricted = await actor(connection, reader["account_code"])
    with pytest.raises(PermissionError):
        await groups(connection, restricted)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval(
                "SELECT security.dashboard_team_ranking($1,$2,$3,NULL,NULL,false)", "followup", START, END
            )


async def test_new_aggregate_acl_inherits_exact_owner_and_grant_options_on_repeat(connection):
    migration = Path(__file__).resolve().parents[3] / "database/migrations/V148__dashboard_team_per_capita_rankings.sql"
    source = "security.dashboard_subject_ranking(text,date,date,integer[],uuid)"
    target = "security.dashboard_team_ranking(text,date,date,integer[],uuid[],boolean)"
    async with fixture_owner(connection):
        # Existing restricted owner/grants must survive an in-place replacement;
        # new functions copy the source ACL, never keep deployment default grants.
        await connection.execute(f"REVOKE ALL ON FUNCTION {source} FROM PUBLIC")
        before = await connection.fetchrow("SELECT proowner,proacl FROM pg_proc WHERE oid=$1::regprocedure", source)
        for _ in range(2):
            await connection.execute(migration.read_text())
            assert (
                await connection.fetchrow("SELECT proowner,proacl FROM pg_proc WHERE oid=$1::regprocedure", source)
                == before
            )
            actual = await connection.fetchrow("SELECT proowner,proacl FROM pg_proc WHERE oid=$1::regprocedure", target)
            assert actual == before
