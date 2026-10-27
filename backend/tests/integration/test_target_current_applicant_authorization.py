"""Legacy target requests must recheck the applicant just like current batches."""

from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.targets import save_target_batch
from tests.integration.feishu_fixtures import assert_restricted, seed_execute
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_target_batches import body

pytestmark = pytest.mark.asyncio


async def pending_request(connection, applicant, kind="legacy"):
    await assert_restricted(connection)
    admin = await actor(connection, "ADMIN001")
    saved = await save_target_batch(connection, admin, body(
        [{"kind": "collection", "amount": "1000"}], scope="person", user_id=UUID(applicant.user_id),
    ))
    target = saved["items"][0]
    if kind == "batch":
        await set_request_context(connection, applicant)
        pending = await save_target_batch(connection, applicant, body(
            [{"kind": "collection", "amount": "800", "version_no": target["version_no"]}],
        ))
        await set_request_context(connection, admin)
        return admin, target, pending["request"]["id"]
    request = str(uuid4())
    # New submissions use batches. Seed only the legacy pending row whose
    # compatibility decision API is still exposed, under an isolated owner.
    await seed_execute(connection,
        "INSERT INTO crm.sales_target_change_request(id,workspace_id,target_id,applicant_user_ref_id,"
        "base_version,previous_amount,proposed_amount) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,1000,800)",
        request, applicant.workspace_id, target["id"], applicant.user_id, target["version_no"],
    )
    return admin, target, request


@pytest.mark.parametrize("change", ["deny_submit", "roles_expired", "inactive"])
async def test_legacy_approval_rejects_lost_current_applicant_qualification(connection, sales_actor, change):
    admin, target, request = await pending_request(connection, sales_actor)
    if change == "deny_submit":
        await grant(connection, admin, sales_actor.user_id, [dict(permission="target.submit", effect="deny")])
    elif change == "roles_expired":
        await connection.execute(
            "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid "
            "AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to", sales_actor.user_id,
        )
    else:
        await connection.execute(
            "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", sales_actor.user_id,
        )
    await set_request_context(connection, admin)
    before_target = await connection.fetchrow("SELECT * FROM crm.sales_target WHERE id=$1::uuid", target["id"])
    before_request = await connection.fetchrow(
        "SELECT * FROM crm.sales_target_change_request WHERE id=$1::uuid", request,
    )
    with pytest.raises((asyncpg.RaiseError, asyncpg.InsufficientPrivilegeError)):
        async with connection.transaction():
            await connection.fetchval("SELECT security.review_target_change($1::uuid,'approved','确认')", request)
    assert await connection.fetchrow("SELECT * FROM crm.sales_target WHERE id=$1::uuid", target["id"]) == before_target
    assert await connection.fetchrow(
        "SELECT * FROM crm.sales_target_change_request WHERE id=$1::uuid", request,
    ) == before_request
    if change != "inactive":
        # Closing an obsolete request remains available when its target is visible.
        rejected = await connection.fetchval(
            "SELECT security.review_target_change($1::uuid,'rejected','资格已变化')", request,
        )
        assert rejected["status"] == "rejected"


@pytest.mark.parametrize("kind", ["legacy", "batch"])
async def test_current_qualified_role_change_preserves_request_history(connection, sales_actor, kind):
    admin, target, request = await pending_request(connection, sales_actor, kind)
    await OperationsAccountRepository().roles_and_team(
        connection, admin, sales_actor.user_id, ["supervisor"], sales_actor.team_ids[0],
    )
    current = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=admin.workspace_id, user_id=sales_actor.user_id, role="supervisor",
    )
    assert current
    await set_request_context(connection, current.context)
    assert await connection.fetchval(
        "SELECT security.authorization_subject('target.submit','person',$1::uuid,NULL)", sales_actor.user_id,
    )
    await set_request_context(connection, admin)
    function = "review_target_change" if kind == "legacy" else "review_target_batch"
    approved = await connection.fetchval(
        f"SELECT security.{function}($1::uuid,'approved','当前资格有效')", request,
    )
    effective = approved["target"] if kind == "legacy" else approved["items"][0]
    assert approved["status"] == "approved" and effective["amount"] == 800
    assert effective["version_no"] == target["version_no"] + 1
    assert approved["request"]["applicant_user_ref_id"] == sales_actor.user_id
    historical = approved["request"] if kind == "legacy" else approved["request"]["items"][0]
    assert historical["previous_amount"] == 1000 and historical["base_version"] == target["version_no"]
    with pytest.raises(asyncpg.RaiseError, match="申请已处理"):
        async with connection.transaction():
            await connection.fetchval(f"SELECT security.{function}($1::uuid,'approved','重复请求')", request)
