"""Sales appointments, native login and writes under real non-bypass PostgreSQL RLS."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.api.business import create_visit
from sales_backend.auth.passwords import encode_password
from sales_backend.contracts.models import OpportunityCreate
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor, create_customer
from tests.integration.test_visit_attendance import review_attendance
from tests.integration.test_visit_entry_platform import TransactionDatabase

pytestmark = pytest.mark.asyncio
PASSWORD = "Isolated-Sales-Permissions-2026"


async def account_fixture(connection, roles, *, kind="product_sales", company_role=None):
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    teams = []
    for role in roles:
        team = await repo.save_department(connection, admin, None, dict(
            code=uuid4().hex, name="权限测试-" + role, kind="fde" if role.startswith("fde") else kind,
            status="active", parent_team_id=None,
        ))
        teams.append(team["id"])
    created = await OperationsAccountService().create(connection, admin, dict(
        account_code="PERM" + uuid4().hex[:12], display_name="隔离权限账号",
        roles=[*roles, *([company_role] if company_role else [])],
        team_id=teams[0] if teams else None,
        memberships=[dict(team_id=team, roles=[role]) for role, team in zip(roles, teams)],
        company_roles=[company_role] if company_role else [],
    ), PASSWORD)
    # Test-only credential; bypass the initial-change prompt, not authorization.
    await connection.execute("SELECT security.set_account_password($1::uuid,$2,false)",
                             created["id"], encode_password(PASSWORD))
    return created, dict(zip(roles, teams)), admin


def opportunity_payload():
    return OpportunityCreate(name="权限回归-" + uuid4().hex, amount=1000, probability=10,
                             expected_close_date="2026-12-01", sales_channel="direct").model_dump(mode="json")


async def native_login(client, account, role=None):
    payload = dict(account_code=account["account_code"], password=PASSWORD)
    if role:
        payload["role"] = role
    response = await client.post("/api/v1/auth/password/login", json=payload)
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return response.json()


@pytest.mark.parametrize("role,kind", [
    ("sales", "sales"), ("sales", "product_sales"), ("supervisor", "sales"),
    ("supervisor", "product_sales"), ("manager", "general"),
])
async def test_sales_sequence_manual_agent_and_visit_creation(connection, role, kind):
    customer = await create_customer(connection)
    account, teams, admin = await account_fixture(connection, [role], kind=kind)
    async with await client_for(connection) as client:
        auth = await native_login(client, account)
        assert auth["actor"]["role"] == role
        assert auth["actor"]["capabilities"]["opportunity.edit"]
        if role == "supervisor" and kind == "product_sales":
            assert auth["actor"]["role_name"] == "产品销售主管"
        payload = opportunity_payload()
        # A non-console client cannot use the owner field to create for someone else.
        response = await client.post(f"/api/v1/customers/{customer['id']}/opportunities",
                                     json={**payload, "owner_user_ref_id": admin.user_id})
        assert response.status_code == 201, response.text
        oid = response.json()["id"]
        row = await connection.fetchrow(
            "SELECT owner_user_ref_id::text,owner_team_id::text,created_by_user_ref_id::text "
            "FROM crm.opportunity WHERE id=$1::uuid", oid,
        )
        assert tuple(row) == (account["id"], teams[role], account["id"])
        updated = await client.post(f"/api/v1/customers/{customer['id']}/opportunities", json={
            **payload, "action": "update", "opportunity_id": oid, "amount": 2000,
            "version_no": response.json()["version_no"],
        })
        assert updated.status_code == 201, updated.text
        missing = await client.post(f"/api/v1/customers/{uuid4()}/opportunities", json=opportunity_payload())
        assert missing.status_code == 404
        before = await connection.fetchval("SELECT count(*) FROM crm.opportunity")
        conversation = await client.post("/api/v1/conversations", json={
            "mode": "opportunity_draft", "customer_id": customer["id"],
        })
        assert conversation.status_code == 201, conversation.text
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity") == before

    identity = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=admin.workspace_id, user_id=account["id"], role=role,
    )
    current = identity.context
    await set_request_context(connection, current)
    mutation = opportunity_payload()
    _, submitted = await review_attendance(connection, current, customer["id"], None, [], mutation)
    visit = await create_visit(submitted, SimpleNamespace(actor=current), TransactionDatabase(connection), None)
    assert visit["opportunity_id"] and visit["opportunity_id"] != oid
    replay = await create_visit(submitted, SimpleNamespace(actor=current), TransactionDatabase(connection), None)
    assert replay["id"] == visit["id"] and replay["replayed"]
    row = await connection.fetchrow(
        "SELECT owner_user_ref_id::text,owner_team_id::text FROM crm.opportunity WHERE id=$1::uuid",
        visit["opportunity_id"],
    )
    assert tuple(row) == (account["id"], teams[role])


@pytest.mark.parametrize("roles,company_role,expected", [
    (["fde", "sales"], None, "sales"),
    (["fde_lead", "sales"], None, "sales"),
    (["fde", "sales"], "administrator", "sales"),
    (["fde", "sales"], "operations", "sales"),
    (["fde", "supervisor", "sales"], None, "supervisor"),
    (["fde", "manager", "sales"], "administrator", "manager"),
    (["fde"], None, "fde"), (["fde_lead"], None, "fde_lead"),
    ([], "operations", "operations"), ([], "administrator", "administrator"),
])
async def test_default_role_uses_same_person_sales_appointment_and_refresh_retains_it(
    connection, roles, company_role, expected,
):
    customer = await create_customer(connection)
    account, teams, _ = await account_fixture(connection, roles, company_role=company_role)
    async with await client_for(connection) as native, await client_for(connection) as console:
        auth = await native_login(native, account)
        assert auth["actor"]["user_id"] == account["id"]
        assert auth["actor"]["role"] == expected
        if expected in {"sales", "supervisor", "fde", "fde_lead"}:
            assert auth["actor"]["team_ids"] == [teams[expected]]
        if expected in {"sales", "supervisor"}:
            response = await native.post(f"/api/v1/customers/{customer['id']}/opportunities",
                                         json=opportunity_payload())
            assert response.status_code == 201, response.text
            row = await connection.fetchrow(
                "SELECT owner_user_ref_id::text,owner_team_id::text FROM crm.opportunity WHERE id=$1::uuid",
                response.json()["id"],
            )
            assert tuple(row) == (account["id"], teams[expected])
        refreshed = await native.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]})
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["actor"]["role"] == expected
        if "fde" in roles:
            explicit = await native_login(native, account, "fde")
            assert explicit["actor"]["role"] == "fde"
            assert explicit["actor"]["capabilities"]["opportunity.edit"] == bool(
                set(roles) & {"sales", "supervisor", "manager"} or company_role == "administrator")
        if company_role:
            web = await console.post("/api/v1/console/auth/login", json=dict(
                account_code=account["account_code"], password=PASSWORD,
            ))
            assert web.status_code == 200 and web.json()["actor"]["role"] == company_role


@pytest.mark.parametrize("role", ["fde", "fde_lead"])
async def test_fde_cannot_create_manually_or_enter_opportunity_agent(connection, role):
    customer = await create_customer(connection)
    account, _, _ = await account_fixture(connection, [role])
    async with await client_for(connection) as client:
        await native_login(client, account)
        response = await client.post(f"/api/v1/customers/{customer['id']}/opportunities", json=opportunity_payload())
        assert response.status_code == 403
        conversation = await client.post("/api/v1/conversations", json={
            "mode": "opportunity_draft", "customer_id": customer["id"],
        })
        assert conversation.status_code == 403


@pytest.mark.parametrize("invalid", ["expired_binding", "expired_membership", "inactive_team"])
async def test_default_login_does_not_revive_invalid_sales_appointment(connection, invalid):
    account, teams, _ = await account_fixture(connection, ["fde", "sales"])
    if invalid == "inactive_team":
        await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", teams["sales"])
    else:
        table, column = ("role_binding", "role_code") if invalid == "expired_binding" else ("team_membership", "membership_role")
        await connection.execute(
            f"UPDATE platform.{table} SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid AND {column}='sales'",
            account["id"],
        )
    async with await client_for(connection) as client:
        auth = await native_login(client, account)
        assert auth["actor"]["role"] == "fde"
        assert not auth["actor"]["capabilities"]["opportunity.edit"]


@pytest.mark.parametrize("code", ["OPS001", "ADMIN001"])
async def test_console_creation_obeys_explicit_default_permissions(connection, code):
    customer = await create_customer(connection)
    current = await actor(connection, code)
    data = OpportunityCreate.model_validate(opportunity_payload()).model_dump()
    if code == "OPS001":
        with pytest.raises(PermissionError):
            await save_opportunity(connection, current, customer_id=customer["id"], data=data)
        return
    sales = await actor(connection, "XS001")
    await set_request_context(connection, current)
    result = await save_opportunity(connection, current, customer_id=customer["id"], data={
        **data, "owner_user_ref_id": sales.user_id,
    })
    row = await connection.fetchrow(
        "SELECT owner_user_ref_id::text,owner_team_id::text,created_by_user_ref_id::text "
        "FROM crm.opportunity WHERE id=$1::uuid", result["id"],
    )
    assert tuple(row) == (sales.user_id, sales.team_ids[0], current.user_id)


@pytest.mark.parametrize("role", ["sales", "supervisor", "manager"])
async def test_creation_keeps_tenant_and_existing_opportunity_scope(connection, role):
    customer = await create_customer(connection)
    other_sales = await actor(connection, "XS001")
    existing = await save_opportunity(connection, other_sales, customer_id=customer["id"],
                                     data=OpportunityCreate.model_validate(opportunity_payload()).model_dump())
    account, _, _ = await account_fixture(connection, [role])
    # Construct only in the disposable database, then restore the non-bypass role.
    await connection.execute("RESET ROLE")
    wid, cid, uid = str(uuid4()), str(uuid4()), str(uuid4())
    await connection.execute(
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1::uuid,$1,'隔离他公司')", wid,
    )
    await connection.execute(
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
        "VALUES($1::uuid,$2::uuid,'OTHER','他公司成员','OTHER')", uid, wid,
    )
    await connection.execute(
        "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id) "
        "VALUES($1::uuid,$2::uuid,'他公司','他公司',$3::uuid)", cid, wid, uid,
    )
    runtime_role = os.environ["SALES_TEST_ROLE"]
    assert runtime_role.startswith("salegent_verify_role_") and runtime_role.removeprefix("salegent_verify_role_").isalnum()
    await connection.execute(f'SET LOCAL ROLE "{runtime_role}"')
    async with await client_for(connection) as client:
        await native_login(client, account)
        foreign = await client.post(f"/api/v1/customers/{cid}/opportunities", json=opportunity_payload())
        assert foreign.status_code == 404
        response = await client.post(f"/api/v1/customers/{customer['id']}/opportunities", json={
            **opportunity_payload(), "action": "update", "opportunity_id": existing["id"],
            "version_no": existing["version_no"],
        })
        # Managers retain company-wide scope; personal/team roles cannot update the other team's opportunity.
        assert response.status_code == (201 if role == "manager" else 403), response.text
