"""Actual manager departments retain metric, selection and ordinary-role boundaries."""

from datetime import date
from uuid import uuid4

import pytest

from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.dashboard_scope import dashboard_members, dashboard_team_groups, resolve_selection
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.rankings import department_ranking_groups
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.dashboard_rankings import dashboard_rankings
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_dashboard_selection import two_teams
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def add_department(connection, *, role="fde", name="客户成功组"):
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    department = await repo.save_department(
        connection, admin, None,
        {"code": "DASH-" + uuid4().hex[:8], "name": name, "status": "active", "parent_team_id": None},
    )
    member = await repo.create(
        connection, admin,
        {"account_code": "DB" + uuid4().hex[:10], "display_name": name + "成员",
         "roles": [role], "team_id": department["id"]},
    )
    return department, member


async def followup(connection, account, project, on="2026-09-18"):
    owner = await actor(connection, account)
    return await VisitRepository().create(
        connection, owner, customer_id=project["customer_id"],
        fields={"opportunity_id": project["id"], "interaction_at": on, "created_date": on,
                "contact_name": "部门验收联系人", "follow_up_record": "客户已确认当前试点反馈",
                "next_action": "销售下周一同步试点结果", "_follow_up_quality_score": 85},
    )


async def test_department_ranks_and_active_counts_match_selected_facts(connection, monkeypatch):
    monkeypatch.setattr("sales_backend.services.dashboard_rankings.today", lambda: date(2026, 9, 19))
    first, second, _ = await two_teams(connection)
    won = await opportunity(connection, "XS002", 500, "won")
    lost = await opportunity(connection, "XS002", 9999, "lost")
    for account, project in [("XS001", first), ("XS002", second), ("XS002", second),
                             ("XS002", won), ("XS002", lost)]:
        await followup(connection, account, project)
    fde, _ = await add_department(connection)
    manager = await actor(connection, "ZJL001")
    options = await dashboard_team_groups(connection, manager)
    codes = {row["name"]: row["code"] for row in options}
    assert set(codes) == {"南区", "北区", "客户成功组"}
    full = await dashboard_rankings(connection, manager, year=2026, quarters=[3], personal=False)
    acv = {row["code"]: row for row in full["opportunity_acv"]["rows"]}
    assert {key: row["value"] for key, row in acv.items()} == {
        codes["北区"]: 200, codes["南区"]: 100, "team:" + fde["id"]: 0,
    }
    assert acv[codes["北区"]]["rank"] == 1 and acv[codes["南区"]]["rank"] == 2
    assert {row["code"]: row["value"] for row in full["followup"]["rows"]} == {
        codes["北区"]: 4, codes["南区"]: 1, codes["客户成功组"]: 0,
    }
    assert {row["code"]: row["value"] for row in full["active_opportunities"]["groups"]} == {
        codes["北区"]: 2, codes["南区"]: 1, codes["客户成功组"]: 0,
    }  # Repeated followups count once; Won remains active, Lost does not.
    assert first["id"] not in str(full) and second["id"] not in str(full)
    for selected in ([codes["北区"]], [codes["北区"], codes["客户成功组"]], list(codes.values())):
        result = await dashboard_rankings(
            connection, manager, year=2026, quarters=[3], personal=False, team_groups=selected,
        )
        assert set(result["selection"]["team_groups"]) == set(selected)
        assert result["opportunity_acv"] == full["opportunity_acv"]
        assert result["active_opportunities"] == full["active_opportunities"]
        facts = await DashboardRepository().load(connection, manager, team_groups=selected)
        assert sum(row["amount"] for row in facts["opportunities"]) == sum(acv[code]["value"] for code in selected)
    # Each client generation is supported, but mixed grouping schemes cannot silently diverge.
    with pytest.raises(ValueError, match="不能混用"):
        await resolve_selection(connection, manager, team_groups=["south_hkmo", codes["北区"]])


@pytest.mark.parametrize(
    "invalid", ["inactive_team", "expired_team", "inactive_user", "expired_membership", "expired_role"],
)
async def test_department_selector_uses_team_validity_not_member_presence(connection, invalid):
    department, member = await add_department(connection)
    if invalid == "inactive_team":
        await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", department["id"])
    elif invalid == "expired_team":
        await connection.execute(
            "UPDATE platform.team SET valid_to=clock_timestamp() WHERE id=$1::uuid", department["id"],
        )
    elif invalid == "inactive_user":
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", member["id"])
    elif invalid == "expired_membership":
        await connection.execute(
            "UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid", member["id"],
        )
    else:
        await connection.execute(
            "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid", member["id"],
        )
    manager = await actor(connection, "ZJL001")
    present = department["id"] in {row["team_id"] for row in await dashboard_team_groups(connection, manager)}
    assert present == (invalid not in {"inactive_team", "expired_team"})
    if present:
        selected = await resolve_selection(connection, manager, team_groups=["team:" + department["id"]])
        assert selected.team_ids == (department["id"],)
    else:
        with pytest.raises(ValueError, match="有效的团队"):
            await resolve_selection(connection, manager, team_groups=["team:" + department["id"]])



async def test_manager_member_roles_enable_fde_selection_without_expanding_supervisor_directory(connection):
    admin = await actor(connection, "ADMIN001")
    fde = await OperationsAccountRepository().create(
        connection, admin,
        {"account_code": "DB" + uuid4().hex[:10], "display_name": "同部门FDE",
         "roles": ["fde"], "team_id": admin.team_ids[0]},
    )
    manager = await actor(connection, "ZJL001")
    members = {row["id"]: row for row in await dashboard_members(connection, manager)}
    assert members[fde["id"]]["role"] == "fde"
    assert members[manager.user_id]["role"] == "manager"
    assert (await resolve_selection(connection, manager, personal=True, member_id=fde["id"])).member_id == fde["id"]
    supervisor = await actor(connection, "ZJ001")
    assert fde["id"] not in {row["id"] for row in await dashboard_members(connection, supervisor)}
    with pytest.raises(PermissionError, match="不在可查看范围"):
        await resolve_selection(connection, supervisor, personal=True, member_id=fde["id"])
    with pytest.raises(PermissionError):
        await department_ranking_groups(
            connection, supervisor, "followup", date(2026, 9, 1), date(2026, 9, 19),
            months=None, team_ids=supervisor.team_ids,
        )
