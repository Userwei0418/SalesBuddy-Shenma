"""Historical primary-team overlap must not multiply workbench risks or tasks."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from tests.integration.feishu_fixtures import seed_execute

from sales_backend.repositories.workbench import WorkbenchRepository
from sales_backend.services.tasks import TaskService
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def risk_fixture(connection, *, owned=True):
    op = await opportunity(connection, "XS001", 100)
    owner = await actor(connection, "XS001")
    admin = await actor(connection, "ADMIN001")
    risk_id = uuid4()
    await seed_execute(
        connection, """INSERT INTO insight.risk(id,workspace_id,customer_id,opportunity_id,risk_type_code,
           title,severity_code,status,owner_user_ref_id,owner_team_id)
        VALUES($1,$2::uuid,$3::uuid,$4::uuid,'followup','重叠团队风险投影回归','high','new',$5::uuid,$6::uuid)""",
        risk_id,
        admin.workspace_id,
        op["customer_id"],
        op["id"],
        owner.user_id if owned else None,
        owner.team_ids[0],
    )
    return str(risk_id), owner


async def team(connection, workspace_id, name):
    return await connection.fetchval(
        "INSERT INTO platform.team(workspace_id,code,name) VALUES($1::uuid,$2,$3) RETURNING id",
        workspace_id,
        "risk-projection-" + uuid4().hex,
        name,
    )


async def membership(connection, owner, team_id, start, *, primary=True, end=None, membership_id=None):
    await connection.execute(
        """INSERT INTO platform.team_membership(id,workspace_id,user_ref_id,team_id,
          membership_role,is_primary,valid_from,valid_to)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,'sales',$5,$6,COALESCE($7,'infinity'::timestamptz))""",
        membership_id or uuid4(),
        owner.workspace_id,
        owner.user_id,
        team_id,
        primary,
        start,
        end,
    )


async def assert_risk_projection(connection, risk_id, account, expected_team):
    context = await actor(connection, account)
    result = await WorkbenchRepository().load(connection, context)
    ids = [row["id"] for row in result["risks"]]
    assert len(ids) == len(set(ids)), "Team display joins must not duplicate risk facts"
    selected = [row for row in result["risks"] if row["id"] == risk_id]
    assert len(selected) == 1
    assert selected[0]["team"] == expected_team
    independent_count = await connection.fetchval(
        "SELECT count(*) FROM insight.risk WHERE deleted_at IS NULL "
        "AND status IN ('new','pending','in_progress','escalated')"
    )
    assert result["summary"]["risks"] == independent_count == len(ids)
    return result


async def overlapping_memberships(connection, owner, overlap):
    now = datetime.now(UTC)
    original_team = owner.team_ids[0]
    original_name = await connection.fetchval("SELECT name FROM platform.team WHERE id=$1::uuid", original_team)
    if overlap == "same_team":
        await membership(connection, owner, original_team, now - timedelta(days=2))
        await membership(connection, owner, original_team, now - timedelta(days=1))
        expected_team = original_name
    elif overlap == "different_teams":
        other_team = await team(connection, owner.workspace_id, "最新主团队")
        await membership(connection, owner, original_team, now - timedelta(days=2))
        await membership(connection, owner, other_team, now - timedelta(days=1))
        expected_team = "最新主团队"
    else:
        other_team = await team(connection, owner.workspace_id, "同时间较小ID团队")
        first_id, second_id = sorted([uuid4(), uuid4()])
        start = now - timedelta(days=1)
        # Insert in reverse ID order: ties are resolved by ID, not physical/insertion order.
        await membership(connection, owner, original_team, start, membership_id=second_id)
        await membership(connection, owner, other_team, start, membership_id=first_id)
        expected_team = "同时间较小ID团队"
    return expected_team


@pytest.mark.parametrize("account", ["XS001", "ZJ001", "ZJL001"])
@pytest.mark.parametrize("overlap", ["same_team", "different_teams", "same_start"])
async def test_workbench_overlapping_primary_memberships_keep_one_risk_and_deterministic_team(
    connection, account, overlap
):
    risk_id, owner = await risk_fixture(connection)
    expected_team = await overlapping_memberships(connection, owner, overlap)
    await assert_risk_projection(connection, risk_id, account, expected_team)


@pytest.mark.parametrize("case", ["secondary_only", "expired_primary", "future_primary", "no_owner"])
async def test_workbench_ignores_ineligible_team_labels_and_keeps_ownerless_risks(connection, case):
    risk_id, owner = await risk_fixture(connection, owned=case != "no_owner")
    now = datetime.now(UTC)
    original_team = owner.team_ids[0]
    original_name = await connection.fetchval("SELECT name FROM platform.team WHERE id=$1::uuid", original_team)
    other_team = await team(connection, owner.workspace_id, "不应显示的团队")
    expected_team = original_name
    if case == "secondary_only":
        await connection.execute(
            "UPDATE platform.team_membership SET is_primary=false WHERE user_ref_id=$1::uuid", owner.user_id
        )
        await membership(connection, owner, other_team, now - timedelta(days=1), primary=False)
        expected_team = None
    elif case == "expired_primary":
        await membership(connection, owner, other_team, now - timedelta(days=2), end=now - timedelta(days=1))
    elif case == "future_primary":
        await membership(connection, owner, other_team, now + timedelta(days=1))
    else:
        expected_team = None
    result = await assert_risk_projection(connection, risk_id, "ZJL001", expected_team)
    if case == "no_owner":
        assert next(row for row in result["risks"] if row["id"] == risk_id)["owner"] is None


async def create_linked_task(connection, risk_id, *, due_at, assigned=True):
    creator = await actor(connection, "ZJ001")
    risk = await connection.fetchrow(
        "SELECT customer_id::text,opportunity_id::text FROM insight.risk WHERE id=$1::uuid", risk_id
    )
    return await TaskService().create(
        connection,
        actor=creator,
        description="重叠团队任务投影回归",
        due_at=due_at,
        priority_code="medium",
        assignee_account_code="XS001" if assigned else None,
        target_position=None if assigned else "supervisor",
        customer_id=risk["customer_id"],
        opportunity_id=risk["opportunity_id"],
    )


@pytest.mark.parametrize("account", ["XS001", "ZJ001", "ZJL001"])
@pytest.mark.parametrize("overlap", ["same_team", "different_teams", "same_start"])
async def test_workbench_task_primary_memberships_do_not_duplicate_items_or_change_summary(
    connection, account, overlap
):
    risk_id, owner = await risk_fixture(connection)
    task = await create_linked_task(connection, risk_id, due_at=datetime.now(UTC) + timedelta(days=2))
    await actor(connection, "ADMIN001")
    expected_team = await overlapping_memberships(connection, owner, overlap)
    result = await assert_risk_projection(connection, risk_id, account, expected_team)
    ids = [row["id"] for row in result["tasks"]]
    assert len(ids) == len(set(ids))
    selected = [row for row in result["tasks"] if row["id"] == task["id"]]
    assert len(selected) == 1
    assert selected[0]["team"] == expected_team
    assert selected[0]["owner"] == "XS001"
    independent = await connection.fetch(
        "SELECT id::text FROM workflow.task WHERE deleted_at IS NULL "
        "AND status IN ('pending_confirm','pending_execution','in_progress','deferred') "
        "ORDER BY due_at,created_at DESC"
    )
    assert ids == [row["id"] for row in independent]
    # Task display joins must not change the separate complete risk summary.
    assert result["summary"]["risks"] == len(result["risks"])


async def test_workbench_unclaimed_position_task_retains_placeholder_without_team_fanout(connection):
    risk_id, owner = await risk_fixture(connection)
    task = await create_linked_task(
        connection, risk_id, due_at=datetime.now(UTC) + timedelta(days=2), assigned=False
    )
    assert task["assignees"] == []
    await actor(connection, "ADMIN001")
    expected_team = await overlapping_memberships(connection, owner, "different_teams")
    result = await assert_risk_projection(connection, risk_id, "ZJL001", expected_team)
    selected = [row for row in result["tasks"] if row["id"] == task["id"]]
    assert len(selected) == 1
    assert selected[0]["owner"] == "待领取"
    assert selected[0]["team"] is None


async def test_workbench_task_order_and_active_statuses_survive_overlapping_teams(connection):
    risk_id, owner = await risk_fixture(connection)
    now = datetime.now(UTC)
    statuses = ["pending_confirm", "pending_execution", "in_progress", "deferred", "completed", "cancelled"]
    tasks = [
        await create_linked_task(connection, risk_id, due_at=now + timedelta(days=8 - index))
        for index in range(len(statuses))
    ]
    await actor(connection, "ZJL001")
    from tests.integration.feishu_fixtures import seed_execute
    for task, status in zip(tasks, statuses, strict=True):
        # This fixture deliberately seeds historical workflow states, not user transitions.
        await seed_execute(connection,
            "UPDATE workflow.task SET status=$2,completed_at=CASE WHEN $2='completed' "
            "THEN clock_timestamp() ELSE NULL END WHERE id=$1::uuid",
            task["id"], status,
        )
    await actor(connection, "ADMIN001")
    expected_team = await overlapping_memberships(connection, owner, "different_teams")
    result = await assert_risk_projection(connection, risk_id, "ZJL001", expected_team)
    task_ids = {task["id"] for task in tasks}
    displayed = [row for row in result["tasks"] if row["id"] in task_ids]
    assert [row["id"] for row in displayed] == [row["id"] for row in reversed(tasks[:4])]
    assert len({row["id"] for row in result["tasks"]}) == len(result["tasks"])
    assert {row["team"] for row in displayed} == {expected_team}
