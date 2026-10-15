"""A task follows its current owner; appointment history is not an access grant."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.tasks import TaskForbidden, TaskNotFound
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.feishu_fixtures import assert_restricted, seed_execute
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_authorization_store import resolve
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_operations_api import client_for
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login

pytestmark = pytest.mark.asyncio


async def assign(connection, creator, account):
    await set_request_context(connection, creator)
    return await TaskService().create(
        connection, actor=creator, description="当前负责人任职变化隔离回归",
        assignee_account_code=account, association_kind="daily", priority_code="normal",
        due_at=datetime.now(UTC) + timedelta(days=1),
    )


async def change_role(connection, admin, user, team, new_role):
    await set_request_context(connection, admin)
    await OperationsAccountRepository().roles_and_team(
        connection, admin, user["id"], [new_role], team,
    )
    return await resolve(connection, admin, user, new_role)


@pytest.mark.parametrize("before_role,after_role", [("sales", "supervisor"), ("fde", "fde_lead")])
@pytest.mark.parametrize("accepted", [False, True])
async def test_current_owner_role_change_retains_task_and_history(connection, before_role, after_role, accepted):
    user, teams, admin = await account_fixture(connection, [before_role])
    task = await assign(connection, admin, user["account_code"])
    owner = await resolve(connection, admin, user, before_role)
    service = TaskService()
    if accepted:
        task = await service.apply_event(connection, actor=owner, task_id=task["id"],
                                        event_type="accept", note=None, expected_version=task["version_no"])
    snapshot = await TaskRepository().detail(connection, task_id=task["id"])
    owner = await change_role(connection, admin, user, teams[before_role], after_role)
    # The exact-role legacy condition is false; current ownership remains valid.
    assert not await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM platform.role_binding WHERE user_ref_id=$1::uuid "
        "AND role_code=$2 AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to)",
        user["id"], before_role,
    )
    current = await TaskRepository().detail(connection, task_id=task["id"])
    assert not current["handover_required"] and current["requires_action"]
    for key in ("assignees", "status", "version_no", "accepted_at", "events"):
        assert current[key] == snapshot[key]
    assert current["assignees"][0]["user_id"] == user["id"]
    assert current["assignees"][0]["role"] == before_role
    if not accepted:
        current = await service.apply_event(connection, actor=owner, task_id=task["id"],
                                           event_type="accept", note=None, expected_version=current["version_no"])
    completed = await service.complete(connection, actor=owner, task_id=task["id"],
                                       note="正常交付，沿用原验收流程", expected_version=current["version_no"])
    assert completed["status"] == "pending_review"


@pytest.mark.parametrize("invalidation", ["inactive", "deleted", "roles_expired", "fde_appointment_expired"])
async def test_real_qualification_loss_still_requires_handover(connection, invalidation):
    role = "fde" if invalidation == "fde_appointment_expired" else "sales"
    user, _, admin = await account_fixture(connection, [role])
    task = await assign(connection, admin, user["account_code"])
    owner = await resolve(connection, admin, user, role)
    await set_request_context(connection, admin)
    if invalidation == "inactive":
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", user["id"])
    elif invalidation == "deleted":
        await connection.execute("UPDATE platform.user_ref SET deleted_at=clock_timestamp() WHERE id=$1::uuid", user["id"])
    else:
        table = "team_membership" if invalidation == "fde_appointment_expired" else "role_binding"
        await connection.execute(
            f"UPDATE platform.{table} SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid "
            "AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to", user["id"],
        )
    detail = await TaskRepository().detail(connection, task_id=task["id"])
    assert detail["handover_required"] and detail["can_coordinate"]
    await set_request_context(connection, owner)
    with pytest.raises((TaskForbidden, TaskNotFound, PermissionError, asyncpg.InsufficientPrivilegeError)):
        await TaskService().apply_event(connection, actor=owner, task_id=task["id"],
                                       event_type="accept", note=None, expected_version=task["version_no"])


async def test_role_recovery_does_not_bypass_personal_permission_denies(connection):
    user, teams, admin = await account_fixture(connection, ["sales"])
    task = await assign(connection, admin, user["account_code"])
    owner = await change_role(connection, admin, user, teams["sales"], "supervisor")
    await grant(connection, admin, user["id"], [dict(permission="task.accept", effect="deny")])
    await set_request_context(connection, owner)
    assert not (await TaskRepository().detail(connection, task_id=task["id"]))["handover_required"]
    with pytest.raises((TaskForbidden, PermissionError)):
        await TaskService().apply_event(connection, actor=owner, task_id=task["id"], event_type="accept", note=None)
    await grant(connection, admin, user["id"], [dict(permission="task.complete", effect="deny")], version=1)
    await set_request_context(connection, owner)
    accepted = await TaskService().apply_event(connection, actor=owner, task_id=task["id"], event_type="accept", note=None)
    with pytest.raises((TaskForbidden, PermissionError)):
        await TaskService().complete(connection, actor=owner, task_id=task["id"],
                                     note="明确禁止完成", expected_version=accepted["version_no"])


async def test_unclaimed_supervisor_candidate_cannot_claim_after_becoming_sales(connection):
    creator = await actor(connection, "XS001")
    task = await TaskService().create(
        connection, actor=creator, description="主管岗位候选人仍须匹配岗位", target_position="supervisor",
        priority_code="normal", due_at=datetime.now(UTC) + timedelta(days=1),
    )
    candidate = await actor(connection, "ZJ001")
    assert await TaskTargetRepository().current_candidate(connection, candidate, task["id"])
    admin = await actor(connection, "ADMIN001")
    changed = await change_role(connection, admin, {"id": candidate.user_id}, candidate.team_ids[0], "sales")
    assert not await TaskTargetRepository().current_candidate(connection, changed, task["id"])
    with pytest.raises((TaskForbidden, TaskNotFound, PermissionError)):
        await TaskService().apply_event(connection, actor=changed, task_id=task["id"], event_type="accept", note=None)


async def test_latest_transfer_owner_recovers_not_original_owner(connection):
    original, _, admin = await account_fixture(connection, ["sales"])
    target, teams, _ = await account_fixture(connection, ["sales"])
    task = await assign(connection, admin, original["account_code"])
    transferred = await TaskService().apply_event(
        connection, actor=admin, task_id=task["id"], event_type="reassign", note="明确转交给新负责人",
        expected_version=task["version_no"], assignee_account_code=target["account_code"],
    )
    owner = await change_role(connection, admin, target, teams["sales"], "supervisor")
    current = await TaskRepository().detail(connection, task_id=task["id"])
    assert [a["user_id"] for a in current["assignees"] if a["responsibility"] == "owner"] == [target["id"]]
    assert not current["handover_required"]
    assert current["version_no"] == transferred["version_no"]
    # Qualification cannot accidentally revive the removed original recipient.
    previous_owner = await resolve(connection, admin, original, "sales")
    with pytest.raises((TaskForbidden, TaskNotFound, PermissionError)):
        await TaskService().apply_event(connection, actor=previous_owner,
                                       task_id=task["id"], event_type="accept", note=None)
    await set_request_context(connection, owner)
    accepted = await TaskService().apply_event(connection, actor=owner, task_id=task["id"], event_type="accept", note=None)
    assert accepted["status"] == "pending_execution"


async def test_eligibility_stays_within_tenant_context(connection):
    user, teams, admin = await account_fixture(connection, ["sales"])
    task = await assign(connection, admin, user["account_code"])
    await change_role(connection, admin, user, teams["sales"], "supervisor")
    await connection.execute("SELECT set_config('app.workspace_id',$1,true)", str(uuid4()))
    assert not await connection.fetchval(
        "SELECT security.task_recipient_eligible($1::uuid,$2::uuid,'sales',$3::uuid)",
        task["id"], user["id"], teams["sales"],
    )


async def test_invalid_fde_role_does_not_block_remaining_sales_identity(connection):
    user, teams, admin = await account_fixture(connection, ["sales", "fde"])
    task = await assign(connection, admin, user["account_code"])
    assert task["assignees"][0]["role"] == "fde"
    owner = await change_role(connection, admin, user, teams["sales"], "sales")
    detail = await TaskRepository().detail(connection, task_id=task["id"])
    assert not detail["handover_required"]
    assert not await connection.fetchval(
        "SELECT security.fde_task_eligible($1::uuid,$2::uuid,'fde',$3::uuid)",
        task["id"], user["id"], teams["fde"],
    )
    result = await TaskService().apply_event(connection, actor=owner, task_id=task["id"], event_type="accept", note=None)
    assert result["status"] == "pending_execution"


@pytest.mark.parametrize("company_status", ["suspended", "closed"])
async def test_suspended_or_closed_company_does_not_gain_eligible_recipients(connection, company_status):
    user, teams, admin = await account_fixture(connection, ["sales"])
    task = await assign(connection, admin, user["account_code"])
    await change_role(connection, admin, user, teams["sales"], "supervisor")
    await seed_execute(connection, "UPDATE platform.workspace SET status=$2 WHERE id=$1::uuid",
                       admin.workspace_id, company_status)
    await assert_restricted(connection)
    assert not await connection.fetchval(
        "SELECT security.task_recipient_eligible($1::uuid,$2::uuid,'sales',$3::uuid)",
        task["id"], user["id"], teams["sales"],
    )


async def test_http_detail_and_accept_use_current_identity_after_role_change(connection):
    user, teams, admin = await account_fixture(connection, ["sales"])
    task = await assign(connection, admin, user["account_code"])
    await change_role(connection, admin, user, teams["sales"], "supervisor")
    async with await client_for(connection) as client:
        auth = await native_login(client, user, "supervisor")
        assert auth["actor"]["role"] == "supervisor"
        path = f"/api/v1/tasks/{task['id']}"
        detail = await client.get(path)
        assert detail.status_code == 200, detail.text
        assert detail.json()["handover_required"] is False
        assert detail.json()["action_permissions"]["task.accept"] is True
        result = await client.post(path + "/events", json={
            "event_type": "accept", "version_no": detail.json()["version_no"],
        })
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "pending_execution"
        assert result.json()["assignees"][0]["user_id"] == user["id"]
