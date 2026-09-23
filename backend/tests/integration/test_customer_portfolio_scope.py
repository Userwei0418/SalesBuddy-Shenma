"""Portfolio projection must preserve the previous customer RLS/owner contract."""

import re
from uuid import UUID, uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_assets import CustomerAssetRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_assets import customer
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_claims_sql import actor, create_customer

pytestmark = pytest.mark.asyncio


async def assert_original_scope(connection, context):
    await set_request_context(connection, context)
    assert not await connection.fetchval(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
    )
    old = await connection.fetch(
        """SELECT c.id,c.workspace_id,c.owner_team_id,
          security.profile_customer_owner(c.id) AS claimant_id
        FROM crm.customer c WHERE c.deleted_at IS NULL
          AND (common.current_role_code()<>'sales'
            OR security.profile_customer_owner(c.id)=common.current_user_ref_id())"""
    )
    new = await connection.fetch("SELECT * FROM security.customer_portfolio_scope()")
    assert len({r["id"] for r in new}) == len(new)
    # Include every projected value, not just aggregate counts or a subset of IDs.
    expected = {str(r["id"]): dict(r) for r in old}
    actual = {str(r["id"]): dict(r) for r in new}
    assert actual == expected
    assert all(str(r["workspace_id"]) == context.workspace_id for r in new)
    return actual


async def new_team(connection, admin):
    await set_request_context(connection, admin)
    return (await OperationsAccountRepository().save_department(
        connection, admin, None,
        dict(code="SCOPE-" + uuid4().hex, name="隔离范围测试组", kind="sales",
             status="active", parent_team_id=None),
    ))["id"]


async def foreign_customer(connection, admin):
    """Only fixture seeding bypasses RLS; every assertion restores the runtime role."""
    role = await connection.fetchval("SELECT current_user")
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role)
    workspace, user = str(uuid4()), str(uuid4())
    await connection.execute("RESET ROLE")
    try:
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1::uuid,$1::text,'隔离其他公司')",
            workspace,
        )
        await connection.execute(
            """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
            VALUES($1::uuid,$2::uuid,$1::text,'隔离其他公司成员')""", user, workspace,
        )
        result = await connection.fetchval(
            """INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id)
            VALUES($1::uuid,'隔离其他公司客户','隔离其他公司客户',$2::uuid) RETURNING id::text""",
            workspace, user,
        )
    finally:
        await connection.execute(f'SET LOCAL ROLE "{role}"')
        await set_request_context(connection, admin)
    return workspace, result


@pytest.mark.parametrize("account", ["XS001", "XS002", "ZJ001", "ZJL001", "OPS001", "ADMIN001", "fde", "fde_lead"])
async def test_portfolio_ids_and_claimants_equal_original_rls_for_each_role(connection, account):
    first = await opportunity(connection, "XS001", 100)
    second = await opportunity(connection, "XS002", 200)
    unclaimed = await create_customer(connection)
    removed = await create_customer(connection)
    _, fde_project, people, _ = await fde_fixture(connection)
    admin = await actor(connection, "ADMIN001")
    await connection.execute("UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1::uuid", removed["id"])
    other_workspace, other_customer = await foreign_customer(connection, admin)
    code = people["first" if account == "fde" else "lead"]["code"] if account.startswith("fde") else account
    context = await actor(connection, code)
    rows = await assert_original_scope(connection, context)
    assert removed["id"] not in rows and other_customer not in rows
    if account in {"XS001", "XS002"}:
        own, peer = (first, second) if account == "XS001" else (second, first)
        assert own["customer_id"] in rows and peer["customer_id"] not in rows
        assert unclaimed["id"] not in rows
        assert all(str(r["claimant_id"]) == context.user_id for r in rows.values())
    elif account.startswith("fde"):
        assert set(rows) == {fde_project["customer_id"]}
        assert rows[fde_project["customer_id"]]["claimant_id"] is None
    else:
        assert {first["customer_id"], second["customer_id"], unclaimed["id"]} <= set(rows)
        assert rows[unclaimed["id"]]["claimant_id"] is None
    # A caller cannot turn its existing identity into another company's scope.
    assert not await assert_original_scope(connection, context.model_copy(update={"workspace_id": other_workspace}))


@pytest.mark.parametrize("account", ["XS001", "ZJ001", "ZJL001", "OPS001", "ADMIN001", "fde_lead"])
async def test_portfolio_matches_original_after_account_disable_and_role_revocation(connection, account):
    await opportunity(connection, "XS001", 100)
    _, _, people, _ = await fde_fixture(connection)
    code = people["lead"]["code"] if account == "fde_lead" else account
    context = await actor(connection, code)
    assert await assert_original_scope(connection, context)
    admin = await actor(connection, "ADMIN001")
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", context.user_id)
    # Account-state validation belongs to identity refresh. Assert that path too;
    # stale SQL contexts must still match the pre-optimization RLS exactly.
    assert await IdentityRepository().find_actor_by_id(
        connection, workspace_id=context.workspace_id, user_id=context.user_id, role=context.role.value,
    ) is None
    await assert_original_scope(connection, context)
    await set_request_context(connection, admin)
    await connection.execute(
        "UPDATE platform.role_binding SET valid_to=clock_timestamp() "
        "WHERE user_ref_id=$1::uuid AND valid_to='infinity'",
        context.user_id,
    )
    assert not await assert_original_scope(connection, context)


async def test_supervisor_scope_tracks_member_appointment_and_retains_own_confirmed_customer(connection):
    project = await opportunity(connection, "XS001", 100)
    supervisor = await actor(connection, "ZJ001")
    owned = await customer(connection, supervisor)
    sales = await actor(connection, "XS001")
    admin = await actor(connection, "ADMIN001")
    outside = await new_team(connection, admin)
    await connection.execute(
        "UPDATE crm.customer SET owner_team_id=$1::uuid WHERE id=ANY($2::uuid[])",
        outside, [UUID(project["customer_id"]), UUID(owned["id"])],
    )
    rows = await assert_original_scope(connection, supervisor)
    assert {project["customer_id"], owned["id"]} <= set(rows)
    await set_request_context(connection, admin)
    await connection.execute(
        "UPDATE platform.team_membership SET valid_to=clock_timestamp() "
        "WHERE user_ref_id=$1::uuid AND valid_to='infinity'",
        sales.user_id,
    )
    rows = await assert_original_scope(connection, supervisor)
    assert project["customer_id"] not in rows and owned["id"] in rows
    await set_request_context(connection, admin)
    await connection.execute(
        "UPDATE platform.team_membership SET valid_to=clock_timestamp() "
        "WHERE user_ref_id=$1::uuid AND valid_to='infinity'",
        supervisor.user_id,
    )
    rows = await assert_original_scope(connection, supervisor)
    assert set(rows) == {owned["id"]}


async def test_sales_history_access_and_pending_claim_do_not_become_current_portfolio(connection):
    project = await opportunity(connection, "XS001", 100)
    first = await actor(connection, "XS001")
    second = await actor(connection, "XS002")
    operator = await actor(connection, "OPS001")
    version = await connection.fetchval(
        "SELECT version_no FROM crm.customer_ownership WHERE customer_id=$1::uuid", project["customer_id"],
    )
    await connection.fetchval(
        "SELECT security.release_customer($1::uuid,$2,'隔离测试历史交接')", project["customer_id"], version,
    )
    assert project["customer_id"] not in await assert_original_scope(connection, first)
    assert await connection.fetchval("SELECT security.has_customer_access($1::uuid)", project["customer_id"])
    await set_request_context(connection, second)
    request = await connection.fetchval("SELECT security.claim_customer($1::uuid)", project["customer_id"])
    assert project["customer_id"] not in await assert_original_scope(connection, second)
    await set_request_context(connection, operator)
    await connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'approved','隔离测试批准交接')", request["request_id"],
    )
    current = await assert_original_scope(connection, second)
    assert str(current[project["customer_id"]]["claimant_id"]) == second.user_id
    assert project["customer_id"] not in await assert_original_scope(connection, first)
    assert await connection.fetchval("SELECT security.has_customer_access($1::uuid)", project["customer_id"])


@pytest.mark.parametrize("change", ["participant", "member_team", "lead_team", "member_disabled"])
async def test_fde_lead_portfolio_follows_current_participation_and_team(connection, change):
    _, project, people, team = await fde_fixture(connection)
    lead = await actor(connection, people["lead"]["code"])
    assert set(await assert_original_scope(connection, lead)) == {project["customer_id"]}
    await actor(connection, "ADMIN001")
    members = [UUID(people["first"]["id"]), UUID(people["second"]["id"])]
    if change == "participant":
        await connection.execute(
            """UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='隔离范围测试撤销'
            WHERE opportunity_id=$1::uuid AND valid_to='infinity'""", project["id"],
        )
    elif change == "member_disabled":
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=ANY($1::uuid[])", members)
    else:
        people_to_remove = members if change == "member_team" else [UUID(lead.user_id)]
        await connection.execute(
            """UPDATE platform.team_membership SET valid_to=clock_timestamp()
            WHERE team_id=$1::uuid AND user_ref_id=ANY($2::uuid[]) AND valid_to='infinity'""", team, people_to_remove,
        )
    assert not await assert_original_scope(connection, lead)


async def test_portfolio_team_filter_uses_only_active_current_claimant_membership_as_fallback(connection):
    project = await opportunity(connection, "XS001", 100)
    peer = await opportunity(connection, "XS002", 200)
    sales = await actor(connection, "XS001")
    manager = await actor(connection, "ZJL001")
    admin = await actor(connection, "ADMIN001")
    other = await new_team(connection, admin)
    repo = CustomerAssetRepository()

    async def summary(team, customer_id=project["customer_id"]):
        await set_request_context(connection, manager)
        return (await repo.read(connection, team_id=UUID(team), customer_id=UUID(customer_id)))["summary"]

    assert (await summary(other))["portfolio_customer_count"] == 0
    await set_request_context(connection, admin)
    membership = await connection.fetchval(
        """INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary)
        VALUES($1::uuid,$2::uuid,$3::uuid,'sales',false) RETURNING id""", admin.workspace_id, other, sales.user_id,
    )
    selected = await summary(other)
    assert selected["portfolio_customer_count"] == 1 and selected["acv_amount"] == 100
    assert selected["customer_count"] == 0  # Existing actual-record pagination stays independent.
    assert (await summary(other, peer["customer_id"]))["portfolio_customer_count"] == 0
    assert (await summary(sales.team_ids[0]))["portfolio_customer_count"] == 1
    await set_request_context(connection, admin)
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", sales.user_id)
    assert (await summary(other))["portfolio_customer_count"] == 0
    assert (await summary(sales.team_ids[0]))["portfolio_customer_count"] == 1
    await set_request_context(connection, admin)
    await connection.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1::uuid", sales.user_id)
    await connection.execute("UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE id=$1", membership)
    assert (await summary(other))["portfolio_customer_count"] == 0
    assert (await summary(sales.team_ids[0]))["portfolio_customer_count"] == 1
