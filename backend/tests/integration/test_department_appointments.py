"""Real RLS coverage for per-department appointments, without persisting fixtures."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def departments(connection, admin):
    repo = OperationsAccountRepository()
    result = []
    for name, kind in [("测试东区", "sales"), ("测试北区", "sales"), ("产品测试", "product_sales")]:
        row = await repo.save_department(
            connection, admin, None, dict(code=uuid4().hex, name=name, kind=kind, parent_team_id=None, status="active")
        )
        result.append(row["id"])
    return result


async def test_department_roles_control_login_scope_and_survive_legacy_edit(connection):
    admin = await actor(connection, "ADMIN001")
    east, north, product = await departments(connection, admin)
    service = OperationsAccountService()
    repo = service.repository
    data = dict(
        account_code="ORG" + uuid4().hex[:10],
        display_name="跨部门任职",
        team_id=east,
        roles=["sales", "supervisor"],
        memberships=[dict(team_id=east, roles=["sales"]), dict(team_id=north, roles=["supervisor"], acting=True)],
    )
    created = await service.create(connection, admin, data, "Isolated-Org-2026")
    uid = created["id"]
    identity = IdentityRepository()
    lead = await identity.find_actor_by_id(connection, workspace_id=admin.workspace_id, user_id=uid, role="supervisor")
    assert lead.context.team_ids == (north,)
    await set_request_context(connection, lead.context)
    assert await connection.fetchval("SELECT security.supervises_team($1::uuid)", north)
    assert not await connection.fetchval("SELECT security.supervises_team($1::uuid)", east)
    salesman = await identity.find_actor_by_id(connection, workspace_id=admin.workspace_id, user_id=uid, role="sales")
    assert salesman.context.team_ids == (east,)
    await set_request_context(connection, admin)
    raw = await connection.fetchrow(
        "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,'supervisor')", created["account_code"]
    )
    assert raw["team_ids"] == [north]
    org = await repo.organization(connection)
    member = next(a for a in org["accounts"] if a["id"] == uid)
    assert len(member["memberships"]) == 2
    assert sum(m["is_primary"] for m in member["memberships"]) == 1
    assert next(m for m in member["memberships"] if m["team_id"] == north)["acting"]
    legacy = {k: v for k, v in data.items() if k != "memberships"}
    await service.update(
        connection, admin, uid, {**legacy, "display_name": "只改姓名", "status": "active", "version_no": 1}
    )
    assert len(await repo.assignments(connection, uid)) == 2
    with pytest.raises(ValueError, match="兼任多个部门"):
        await service.update(
            connection, admin, uid, {**legacy, "roles": ["sales"], "status": "active", "version_no": 2}
        )
    updated = {
        **data,
        "roles": ["supervisor"],
        "memberships": [dict(team_id=product, roles=["supervisor"])],
        "team_id": product,
        "status": "active",
        "version_no": 2,
    }
    await service.update(connection, admin, uid, updated)
    titled = await identity.find_actor_by_id(
        connection, workspace_id=admin.workspace_id, user_id=uid, role="supervisor"
    )
    assert titled.role_title == "产品销售主管"
    assert titled.context.team_ids == (product,)
    await set_request_context(connection, titled.context)
    assert not await connection.fetchval("SELECT security.supervises_team($1::uuid)", north)


async def test_multi_department_same_supervisor_is_one_person_in_rankings(connection):
    admin = await actor(connection, "ADMIN001")
    east, north, _ = await departments(connection, admin)
    lead = await actor(connection, "ZJ001")
    before = await connection.fetchval(
        "SELECT security.dashboard_subject_ranking('opportunity_acv','2026-01-01','2026-12-31',NULL,$1::uuid)",
        lead.user_id,
    )
    await set_request_context(connection, admin)
    await OperationsAccountRepository().roles_and_team(
        connection,
        admin,
        lead.user_id,
        ["supervisor"],
        east,
        [dict(team_id=east, roles=["supervisor"]), dict(team_id=north, roles=["supervisor"], acting=True)],
    )
    resolved = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=admin.workspace_id, user_id=lead.user_id, role="supervisor"
    )
    assert resolved.context.team_ids == (east, north)
    assert resolved.team_names == ("测试东区", "测试北区")
    login = await connection.fetchrow(
        "SELECT * FROM security.resolve_account_actor('demo-sales-workspace','ZJ001','supervisor')"
    )
    assert login["team_ids"] == [east, north]
    assert login["team_names"] == ["测试东区", "测试北区"]
    await set_request_context(connection, resolved.context)
    after = await connection.fetchval(
        "SELECT security.dashboard_subject_ranking('opportunity_acv','2026-01-01','2026-12-31',NULL,$1::uuid)",
        lead.user_id,
    )
    assert len(after["rows"]) == len(before["rows"])
    assert len([r for r in after["rows"] if r["user_id"] == lead.user_id]) == 1
    assert sum(r["value"] for r in after["rows"]) == sum(r["value"] for r in before["rows"])


async def test_invalid_department_and_inconsistent_appointments_rejected(connection):
    admin = await actor(connection, "ADMIN001")
    service = OperationsAccountService()
    with pytest.raises(ValueError, match="有效部门"):
        await service.validate_organization(connection, dict(team_id=str(uuid4()), roles=["sales"]))
    primary = admin.team_ids[0]
    with pytest.raises(ValueError, match="部门不能重复"):
        await service.validate_organization(
            connection,
            dict(
                team_id=primary,
                roles=["sales"],
                memberships=[dict(team_id=primary, roles=["sales"]), dict(team_id=primary, roles=["sales"])],
            ),
        )
    with pytest.raises(ValueError, match="岗位一致"):
        await service.validate_organization(
            connection, dict(team_id=primary, roles=["manager"], memberships=[dict(team_id=primary, roles=["sales"])])
        )


async def test_other_workspace_department_cannot_be_assigned(connection):
    import os

    admin = await actor(connection, "ADMIN001")
    await connection.execute("RESET ROLE")
    other = str(uuid4())
    team = str(uuid4())
    await connection.execute(
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1::uuid,$2,'隔离他租户')", other, other
    )
    await connection.execute(
        "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1::uuid,$2::uuid,'OTHER','隔离他部门')",
        team,
        other,
    )
    role = os.environ["SALES_TEST_ROLE"]
    assert role.startswith("salegent_verify_role_") and role.removeprefix("salegent_verify_role_").isalnum()
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    await set_request_context(connection, admin)
    with pytest.raises(ValueError, match="有效部门"):
        await OperationsAccountService().validate_organization(connection, dict(team_id=team, roles=["supervisor"]))
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM security.account_team_scope($1::uuid,$2::uuid,'supervisor')", other, admin.user_id
        )
        == 0
    )


async def test_product_supervisor_title_is_returned_by_real_password_login(connection):
    from tests.integration.test_operations_api import client_for, sign_in

    async with await client_for(connection) as admin, await client_for(connection) as native:
        await sign_in(admin)
        department = await admin.post(
            "/api/v1/console/departments",
            headers={"Idempotency-Key": str(uuid4())},
            json=dict(
                code="PRODUCT" + uuid4().hex[:10],
                name="产品销售测试",
                kind="product_sales",
                status="active",
                parent_team_id=None,
            ),
        )
        assert department.status_code == 201, department.text
        team = department.json()["id"]
        code = "PRODUCT" + uuid4().hex[:10]
        created = await admin.post(
            "/api/v1/console/accounts",
            headers={"Idempotency-Key": str(uuid4())},
            json=dict(
                account_code=code,
                display_name="测试产品主管",
                team_id=team,
                roles=["supervisor"],
                temporary_password="Product-Test-2026",
            ),
        )
        assert created.status_code == 201, created.text
        native.headers.pop("Origin", None)
        login = await native.post(
            "/api/v1/auth/password/login", json=dict(account_code=code, password="Product-Test-2026", role="supervisor")
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["actor"]["role_name"] == "产品销售主管"
        assert body["actor"]["team_names"] == ["产品销售测试"]
        assert body["actor"]["team_ids"] == [team]
        assert body["must_change_password"] is True
