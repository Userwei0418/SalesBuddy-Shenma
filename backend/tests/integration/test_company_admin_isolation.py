"""Company management permissions do not create business appointments."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.team_directory import selectable_teams
from sales_backend.services.operations_accounts import OperationsAccountService
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio
PASSWORD = "Isolated-Testing-2026"


async def test_company_only_admin_has_no_team_and_can_be_maintained(connection):
    admin = await actor(connection, "ADMIN001")
    teams = await selectable_teams(connection, admin)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        payload = dict(
            account_code="ISOLATEDADMIN",
            display_name="公司运营",
            roles=["administrator", "operations"],
            company_roles=["administrator", "operations"],
            team_id=None,
            memberships=[],
            organization_team_id=teams[0]["id"],
            temporary_password=PASSWORD,
        )
        created = await client.post("/api/v1/console/accounts", json=payload, headers={"Idempotency-Key": str(uuid4())})
        assert created.status_code == 201, created.text
        uid = created.json()["id"]
        org = (await client.get("/api/v1/console/organization")).json()
        account = next(a for a in org["accounts"] if a["id"] == uid)
        assert account["team_id"] is None and account["memberships"] == []
        assert set(account["company_roles"]) == {"administrator", "operations"}
        update = {k: v for k, v in payload.items() if k not in ("temporary_password", "company_roles", "team_id", "organization_team_id")}
        # Old clients cannot silently strip the independent management permission.
        changed = await client.put(
            "/api/v1/console/accounts/" + uid,
            json={**update, "version_no": 1, "status": "active"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert changed.status_code == 200, changed.text
        org = (await client.get("/api/v1/console/organization")).json()
        assert next(a for a in org["accounts"] if a["id"] == uid)["organization_team_id"] == teams[0]["id"]
        await set_request_context(connection, admin)
        assert teams == await selectable_teams(connection, admin)
        manager = await actor(connection, "ZJL001")
        assert uid not in {m["id"] for m in await DirectoryRepository().members(connection, manager)}
        await set_request_context(connection, admin)
        assert uid not in {m["id"] for m in await DirectoryRepository().task_assignees(connection, admin)}
        assert (
            await connection.fetchval("SELECT count(*) FROM platform.team_membership WHERE user_ref_id=$1::uuid", uid)
            == 0
        )
    async with await client_for(connection) as client:
        session = await client.post(
            "/api/v1/console/auth/login", json={"account_code": "isolatedadmin", "password": PASSWORD}
        )
        assert session.status_code == 200, session.text
        assert session.json()["actor"]["role"] == "administrator"


async def test_dual_role_login_and_refresh_preserve_business_role(connection):
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    org = await repo.organization(connection)
    manager = next(a for a in org["accounts"] if a["account_code"] == "ZJL001")
    from sales_backend.auth.passwords import encode_password

    await connection.execute(
        "SELECT security.set_account_password($1::uuid,$2,false)", manager["id"], encode_password(PASSWORD)
    )
    before = manager["memberships"]
    await OperationsAccountService().update(
        connection,
        admin,
        manager["id"],
        dict(
            version_no=manager["version_no"],
            display_name=manager["display_name"],
            status="active",
            team_id=manager["team_id"],
            roles=["manager", "administrator"],
            company_roles=["administrator"],
            memberships=[{k: m[k] for k in ("team_id", "roles", "acting")} for m in before],
        ),
    )
    assert (await repo.assignments(connection, manager["id"]))[0]["roles"] == ["manager"]
    async with await client_for(connection) as native, await client_for(connection) as console:
        n = await native.post("/api/v1/auth/password/login", json={"account_code": "zjl001", "password": PASSWORD})
        assert n.status_code == 200, n.text
        assert n.json()["actor"]["role"] == "manager"
        assert n.json()["actor"]["team_ids"] == [manager["team_id"]]
        refreshed = await native.post("/api/v1/auth/refresh", json={"refresh_token": n.json()["refresh_token"]})
        assert refreshed.status_code == 200 and refreshed.json()["actor"]["role"] == "manager"
        c = await console.post("/api/v1/console/auth/login", json={"account_code": "ZJL001", "password": PASSWORD})
        assert c.status_code == 200, c.text
        assert c.json()["actor"]["role"] == "administrator"
        console.headers["Authorization"] = "Bearer " + c.json()["access_token"]
        assert (await console.get("/api/v1/console/organization")).status_code == 200
        companies = (await console.get("/api/v1/console/companies")).json()["items"]
        assert [v["id"] for v in companies] == [admin.workspace_id]
        assert (
            await console.get("/api/v1/console/organization", headers={"X-Company-ID": str(uuid4())})
        ).status_code == 403


async def test_sales_without_department_and_operations_privilege_escalation_rejected(connection):
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        invalid = dict(
            account_code="NOAPPOINTMENT",
            display_name="无业务岗位",
            roles=["sales"],
            memberships=[],
            team_id=None,
            company_roles=[],
            temporary_password=PASSWORD,
        )
        assert (
            await client.post("/api/v1/console/accounts", json=invalid, headers={"Idempotency-Key": str(uuid4())})
        ).status_code == 422
        await sign_in(client, "OPS001")
        elevated = {**invalid, "roles": ["administrator"], "company_roles": ["administrator"]}
        assert (
            await client.post("/api/v1/console/accounts", json=elevated, headers={"Idempotency-Key": str(uuid4())})
        ).status_code == 403


async def test_transition_revokes_existing_session_delegation_and_preserves_business(connection):
    import importlib.util
    from pathlib import Path
    from tests.integration.test_company_tenants import provision_fixture

    spec = importlib.util.spec_from_file_location(
        "isolation", Path(__file__).parents[2] / "scripts/isolate_company_administrators.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    admin, manifest, wid, runtime_role = await provision_fixture(connection)
    repo = OperationsAccountRepository()
    async with await client_for(connection) as old_client:
        await sign_in(old_client, "ADMIN001")
        old_client.headers["X-Company-ID"] = wid
        org = (await old_client.get("/api/v1/console/organization")).json()
        business = next(a for a in org["accounts"] if "manager" in a["roles"])
        proxy = next(a for a in org["accounts"] if a["platform_managed"])
        plan = dict(
            source_company="demo-sales-workspace",
            source_account="ADMIN001",
            source_user_id=admin.user_id,
            target_company=manifest["target_company"]["code"],
            business_account=business["account_code"],
            business_user_id=business["id"],
            retired_proxy_id=proxy["id"],
            new_account="NEWCOMPANYADMIN",
            new_name="公司运营",
        )
        await connection.execute("RESET ROLE")
        await connection.execute(
            "INSERT INTO security.password_policy(workspace_id,require_initial_change) VALUES($1::uuid,false),($2::uuid,false) ON CONFLICT(workspace_id) DO UPDATE SET require_initial_change=false",
            admin.workspace_id,
            wid,
        )
        preview = await module.run(connection, plan)
        with pytest.raises(ValueError, match="changed"):
            await module.run(connection, plan, True, "wrong", PASSWORD)
        result = await module.run(connection, plan, True, preview["plan_sha256"], PASSWORD)
        assert result["applied"] and result["business_identity_preserved"]
        assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE action_code='organization.admin_isolation' AND (after_snapshot->>'applied')::boolean") == 2
        await connection.execute(f'SET LOCAL ROLE "{runtime_role}"')
        assert (await old_client.get("/api/v1/console/organization")).status_code == 401
        old_client.headers.pop("X-Company-ID")
        await sign_in(old_client, "ADMIN001")
        assert (await old_client.get("/api/v1/console/organization")).status_code == 200
        assert (await old_client.get("/api/v1/console/organization", headers={"X-Company-ID": wid})).status_code == 403
        for code in ("NEWCOMPANYADMIN", business["account_code"]):
            async with await client_for(connection) as client:
                auth = await client.post(
                    "/api/v1/console/auth/login", json={"account_code": code, "password": PASSWORD}
                )
                assert auth.status_code == 200, auth.text
                assert auth.json()["actor"]["workspace_id"] == wid
                assert auth.json()["actor"]["role"] == "administrator"
                client.headers["Authorization"] = "Bearer " + auth.json()["access_token"]
                assert (await client.get("/api/v1/console/organization")).status_code == 200
                assert (
                    await client.get("/api/v1/console/organization", headers={"X-Company-ID": admin.workspace_id})
                ).status_code == 403
        await connection.execute("RESET ROLE")
        assert (
            await connection.fetchval("SELECT status FROM platform.user_ref WHERE id=$1::uuid", proxy["id"])
            == "inactive"
        )
        assert (
            await connection.fetchval("SELECT count(*) FROM security.company_management_grant WHERE status='active'")
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM platform.team_membership WHERE user_ref_id=$1::uuid",
                result["new_administrator_id"],
            )
            == 0
        )
        # Re-running a used plan cannot silently reset passwords or create duplicates.
        with pytest.raises(PermissionError, match="delegation"):
            await module.run(connection, plan, True, preview["plan_sha256"], PASSWORD)


async def test_organization_affiliation_validated_preserved_and_cleared(connection):
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    org = await repo.organization(connection)
    target = next(a for a in org["accounts"] if a["account_code"] == "OPS001")
    department = next(t for t in org["departments"] if t["status"] == "active")
    payload = dict(version_no=target["version_no"], display_name=target["display_name"], status="active",
                   team_id=target["team_id"], roles=target["roles"], company_roles=target["company_roles"],
                   memberships=[{k: m[k] for k in ("team_id", "roles", "acting")} for m in target["memberships"]])
    service = OperationsAccountService()
    # Unknown/other-company IDs cannot be assigned through affiliation.
    with pytest.raises(ValueError, match="有效部门"):
        await service.validate_organization(connection, {**payload, "organization_team_id": str(uuid4())})
    await connection.execute("UPDATE platform.user_ref SET attributes=attributes || '{\"keep_marker\":true}'::jsonb WHERE id=$1::uuid", target["id"])
    payload["version_no"] = (await repo.member(connection, target["id"]))["version_no"]
    await service.update(connection, admin, target["id"], {**payload, "organization_team_id": department["id"]})
    row = next(a for a in (await repo.organization(connection))["accounts"] if a["id"] == target["id"])
    assert row["organization_team_id"] == department["id"]
    assert row["memberships"] == target["memberships"]
    assert row["company_roles"] == target["company_roles"]
    payload["version_no"] = row["version_no"]
    await service.update(connection, admin, target["id"], {**payload, "organization_team_id": None})
    row = next(a for a in (await repo.organization(connection))["accounts"] if a["id"] == target["id"])
    assert row["organization_team_id"] is None
    assert await connection.fetchval("SELECT (attributes->>'keep_marker')::boolean FROM platform.user_ref WHERE id=$1::uuid", target["id"])
