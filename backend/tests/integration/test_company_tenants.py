"""Two-company HTTP/RLS lifecycle, using real DB roles and rollback-only fixtures."""

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

scripts = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("company_provision", scripts / "provision_company.py")
provisioning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provisioning)
pytestmark = pytest.mark.asyncio


async def provision_fixture(connection):
    admin = await actor(connection, "ADMIN001")
    role = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    # The fixture has a second operations identity: deactivate it to match the
    # intended seed of business personnel plus one explicitly selected admin.
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE account_code='OPS001'")
    await set_request_context(connection, admin)
    org = await OperationsAccountRepository().organization(connection)
    people = [u for u in org["accounts"] if u["status"] == "active" and u["id"] != admin.user_id]
    manifest = {
        "source_company": "demo-sales-workspace",
        "administrator_account": "ADMIN001",
        "target_company": {"code": "formal-" + uuid4().hex[:8], "name": "隔离正式公司"},
        "accounts": {
            u["account_code"]: {"name": u["display_name"], "account": "pinyin" + str(i)} for i, u in enumerate(people)
        },
    }
    before = await connection.fetchval("SELECT count(*) FROM crm.customer")
    plan = await provisioning.provision(connection, manifest)
    with pytest.raises(ValueError, match="changed"):
        await provisioning.provision(
            connection, manifest, apply=True, expected="old", password="OnlyLettersInitial", allow_letters=True
        )
    result = await provisioning.provision(
        connection,
        manifest,
        apply=True,
        expected=plan["plan_sha256"],
        password="OnlyLettersInitial",
        allow_letters=True,
    )
    wid = result["company_id"]
    assert before == await connection.fetchval("SELECT count(*) FROM crm.customer")
    assert not await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid", wid)
    with pytest.raises(ValueError, match="exists"):
        await provisioning.provision(connection, manifest)
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    await set_request_context(connection, admin)
    return admin, manifest, wid, role


async def test_company_selection_scopes_admin_reads_writes_and_audit(connection):
    admin, manifest, wid, _ = await provision_fixture(connection)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        companies = (await client.get("/api/v1/console/companies")).json()["items"]
        assert {c["id"] for c in companies} == {admin.workspace_id, wid}
        selected = await client.post(
            "/api/v1/console/companies/select", json={"company_id": wid}, headers={"Idempotency-Key": str(uuid4())}
        )
        assert selected.status_code == 200, selected.text
        assert (
            await client.get("/api/v1/console/companies", headers={"X-Company-ID": str(uuid4())})
        ).status_code == 200  # unscoped directory
        client.headers["X-Company-ID"] = wid
        org = (await client.get("/api/v1/console/organization")).json()
        assert len(org["accounts"]) == len(manifest["accounts"]) + 1
        assert all(a["account_code"] == "OPSADMIN" or a["account_code"].startswith("PINYIN") for a in org["accounts"])
        managed = next(a for a in org["accounts"] if a["platform_managed"])
        assert not managed["has_password"]
        reset = await client.post(
            "/api/v1/console/accounts/" + managed["id"] + "/reset-password",
            json={"version_no": 1, "temporary_password": "Denied-Password-2026"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert reset.status_code == 403, reset.text
        for path in ["/customers", "/opportunities", "/summary"]:
            response = await client.get("/api/v1/console" + path)
            assert response.status_code == 200, response.text
            if path in ["/customers", "/opportunities"]:
                assert response.json()["total"] == 0
        # Forged source department may not create a user in the selected company.
        await set_request_context(connection, admin)
        source_team = str(await connection.fetchval("SELECT id FROM platform.team LIMIT 1"))
        denied = await client.post(
            "/api/v1/console/accounts",
            json={
                "account_code": "CROSS" + uuid4().hex[:8],
                "display_name": "拒绝跨公司部门",
                "team_id": source_team,
                "roles": ["sales"],
                "temporary_password": "Testing-Only-2026",
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert denied.status_code == 422 and denied.json()["detail"] == "请选择有效部门", denied.text
        # Refresh/authentication stay bound to the original administrator account.
        refreshed = await client.post("/api/v1/console/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["actor"]["workspace_id"] == admin.workspace_id
        client.headers["Authorization"] = "Bearer " + refreshed.json()["access_token"]
        assert (await client.get("/api/v1/console/organization")).status_code == 200
        await set_request_context(connection, admin)
        await set_request_context(
            connection,
            IdentityRepository()
            ._actor(await connection.fetchval("SELECT security.company_management_actor($1::uuid)", wid))
            .context,
        )
        audit = await connection.fetchval(
            "SELECT count(*) FROM ops.audit_log WHERE after_snapshot->>'authenticated_user_id'=$1", admin.user_id
        )
        assert audit > 0
        # Revocation immediately removes delegation while original login survives.
        await connection.execute("RESET ROLE")
        await connection.execute(
            "UPDATE security.company_management_grant SET status='revoked' WHERE target_workspace_id=$1::uuid", wid
        )
        import os

        await connection.execute(f'SET LOCAL ROLE "{os.environ["SALES_TEST_ROLE"]}"')
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        client.headers.pop("X-Company-ID")
        assert (await client.get("/api/v1/console/organization")).status_code == 200
        assert (await client.post("/api/v1/console/auth/logout")).status_code == 200
        assert (await client.get("/api/v1/console/organization")).status_code == 401


async def test_pinyin_login_native_first_change_ambiguity_and_company_denial(connection):
    admin, manifest, wid, role = await provision_fixture(connection)
    entry = manifest["accounts"]["XS001"]
    async with await client_for(connection) as client:
        result = await client.post(
            "/api/v1/auth/password/login", json={"account_code": entry["account"], "password": "OnlyLettersInitial"}
        )
        assert result.status_code == 200, result.text
        session = result.json()
        assert session["actor"]["workspace_id"] == wid and session["must_change_password"]
        client.headers["Authorization"] = "Bearer " + session["access_token"]
        assert (await client.get("/api/v1/dashboard")).status_code == 403
        changed = await client.post(
            "/api/v1/auth/password", json={"old_password": "OnlyLettersInitial", "new_password": "Changed-Initial-2026"}
        )
        assert changed.status_code == 200, changed.text
        assert (await client.get("/api/v1/dashboard")).status_code == 200
        assert (await client.get("/api/v1/console/companies")).status_code == 403
        assert (await client.get("/api/v1/dashboard", headers={"X-Company-ID": admin.workspace_id})).status_code == 403
        assert (await client.get("/api/v1/dashboard", headers={"X-Company-ID": str(uuid4())})).status_code == 403
        assert (await client.get("/api/v1/dashboard", headers={"X-Company-ID": "bad"})).status_code == 400
        # Stopping a company invalidates ordinary access and refresh.
        await connection.execute("RESET ROLE")
        await connection.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1::uuid", wid)
        await connection.execute(f'SET LOCAL ROLE "{role}"')
        assert (await client.get("/api/v1/dashboard")).status_code == 401
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
        ).status_code == 401
        assert (
            await client.post(
                "/api/v1/auth/password/login",
                json={"account_code": entry["account"], "password": "Changed-Initial-2026"},
            )
        ).status_code == 401


async def test_runtime_cannot_grant_itself_cross_company_access(connection):
    admin, _, wid, _ = await provision_fixture(connection)
    await set_request_context(connection, admin)
    assert not await connection.fetchval("SELECT count(*) FROM security.company_management_grant")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO security.company_management_grant SELECT $1::uuid,$2::uuid,$3::uuid,$2::uuid,'active',clock_timestamp()",
                admin.workspace_id,
                admin.user_id,
                wid,
            )


async def test_company_rls_scopes_every_workspace_business_table(connection):
    admin, _, wid, role = await provision_fixture(connection)
    await connection.execute("RESET ROLE")
    target_user = await connection.fetchval(
        "SELECT target_user_id FROM security.company_management_grant WHERE target_workspace_id=$1::uuid", wid
    )
    target_customer = await connection.fetchval(
        """INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id)
       VALUES($1::uuid,'隔离正式客户','隔离正式客户',$2) RETURNING id""",
        wid,
        target_user,
    )
    source_customer = await connection.fetchval(
        "SELECT id FROM crm.customer WHERE workspace_id=$1::uuid LIMIT 1", admin.workspace_id
    )
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        source_data = (await client.get("/api/v1/console/customers")).json()
        assert str(target_customer) not in str(source_data)
        assert (await client.get("/api/v1/console/customers/" + str(target_customer))).status_code == 404
        client.headers["X-Company-ID"] = wid
        target_data = (await client.get("/api/v1/console/customers")).json()
        assert target_data["total"] == 1 and str(target_customer) in str(target_data)
        if source_customer:
            assert (await client.get("/api/v1/console/customers/" + str(source_customer))).status_code == 404
        await set_request_context(connection, admin)
        delegated = await connection.fetchval("SELECT security.company_management_actor($1::uuid)", wid)
        await set_request_context(connection, IdentityRepository()._actor(delegated).context)
        tables = await connection.fetch("""SELECT c.table_schema,c.table_name FROM information_schema.columns c
          JOIN information_schema.tables t USING(table_schema,table_name)
          WHERE c.column_name='workspace_id' AND t.table_type='BASE TABLE'
          AND c.table_schema IN ('crm','activity','workflow','insight','agent','ops','platform')""")
        assert len(tables) > 40
        for row in tables:
            # Names are catalog-derived and quoted; values remain parameters.
            count = await connection.fetchval(
                f'SELECT count(*) FROM "{row["table_schema"]}"."{row["table_name"]}" WHERE workspace_id<>$1::uuid', wid
            )
            assert count == 0, (row["table_schema"], row["table_name"], count)


async def test_pinyin_throttling_matches_company_admin_unlock_and_duplicate_is_ambiguous(connection):
    import hashlib
    from sales_backend.auth.passwords import encode_password

    admin, manifest, wid, role = await provision_fixture(connection)
    code = manifest["accounts"]["XS001"]["account"]
    async with await client_for(connection) as client:
        for _ in range(2):
            result = await client.post(
                "/api/v1/auth/password/login", json={"account_code": code, "password": "Wrong-Password-2026"}
            )
            assert result.status_code == 401
        await connection.execute("RESET ROLE")
        expected = hashlib.sha256(f"account:{manifest['target_company']['code']}:{code.upper()}".encode()).hexdigest()
        assert (
            await connection.fetchval("SELECT attempts FROM security.login_throttle WHERE key_hash=$1", expected) == 2
        )
        duplicate_id = uuid4()
        await connection.execute(
            """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
          VALUES($1,$2::uuid,$3,$4,'重名账号')""",
            duplicate_id,
            admin.workspace_id,
            str(duplicate_id),
            code.upper(),
        )
        await connection.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1::uuid,$2,'manager','workspace')",
            admin.workspace_id,
            duplicate_id,
        )
        await connection.execute(
            "INSERT INTO platform.password_credential(workspace_id,user_ref_id,password_hash,must_change_password) VALUES($1::uuid,$2,$3,false)",
            admin.workspace_id,
            duplicate_id,
            encode_password("Distinct-Only-2026"),
        )
        await connection.execute(f'SET LOCAL ROLE "{role}"')
        ambiguous = await client.post(
            "/api/v1/auth/password/login", json={"account_code": code, "password": "OnlyLettersInitial"}
        )
        assert ambiguous.status_code == 401
        explicit = await client.post(
            "/api/v1/auth/password/login",
            json={
                "account_code": code,
                "password": "OnlyLettersInitial",
                "workspace": manifest["target_company"]["code"],
            },
        )
        assert explicit.status_code == 200 and explicit.json()["actor"]["workspace_id"] == wid
        wrong = await client.post(
            "/api/v1/auth/password/login",
            json={"account_code": code, "password": "OnlyLettersInitial", "workspace": "demo-sales-workspace"},
        )
        assert wrong.status_code == 401


async def test_company_clone_preserves_primary_acting_and_product_titles(connection):
    from tests.integration.test_department_appointments import departments
    from sales_backend.services.operations_accounts import OperationsAccountService

    admin = await actor(connection, "ADMIN001")
    east, north, product = await departments(connection, admin)
    repo = OperationsAccountRepository()
    lead = await actor(connection, "ZJ001")
    await set_request_context(connection, admin)
    await repo.roles_and_team(
        connection,
        admin,
        lead.user_id,
        ["supervisor"],
        east,
        [dict(team_id=east, roles=["supervisor"]), dict(team_id=north, roles=["supervisor"], acting=True)],
    )
    await OperationsAccountService().create(
        connection,
        admin,
        dict(account_code="PRODUCTSEED", display_name="产品主管", team_id=product, roles=["supervisor"]),
        "Testing-Seed-2026",
    )
    source = await repo.organization(connection)
    admin, manifest, wid, _ = await provision_fixture(connection)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        client.headers["X-Company-ID"] = wid
        target = (await client.get("/api/v1/console/organization")).json()

        def normalized(person, org):
            teams = {t["id"]: t["code"] for t in org["departments"]}
            return (
                sorted(person["roles"]),
                teams[person["team_id"]],
                sorted(
                    (teams[m["team_id"]], sorted(m["roles"]), m["acting"], m["is_primary"])
                    for m in person["memberships"]
                ),
            )

        for code, mapping in manifest["accounts"].items():
            before = next(p for p in source["accounts"] if p["account_code"] == code)
            after = next(p for p in target["accounts"] if p["account_code"] == mapping["account"].upper())
            assert before["id"] != after["id"]
            assert normalized(before, source) == normalized(after, target)
        client.headers.pop("Authorization")
        client.headers.pop("X-Company-ID")
        login = await client.post(
            "/api/v1/auth/password/login",
            json=dict(account_code=manifest["accounts"]["PRODUCTSEED"]["account"], password="OnlyLettersInitial"),
        )
        assert login.status_code == 200 and login.json()["actor"]["role_name"] == "产品销售主管", login.text


async def test_company_rename_is_scoped_versioned_idempotent_and_audited(connection):
    admin, _, wid, _ = await provision_fixture(connection)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        body = {"name": "正式公司验收名称", "version_no": 1}
        key = str(uuid4())
        assert (
            await client.put("/api/v1/console/companies/" + wid, json=body, headers={"Idempotency-Key": key})
        ).status_code == 403
        client.headers["X-Company-ID"] = wid
        changed = await client.put("/api/v1/console/companies/" + wid, json=body, headers={"Idempotency-Key": key})
        assert changed.status_code == 200, changed.text
        replay = await client.put("/api/v1/console/companies/" + wid, json=body, headers={"Idempotency-Key": key})
        assert replay.status_code == 200 and replay.json() == changed.json()
        stale = await client.put(
            "/api/v1/console/companies/" + wid, json=body, headers={"Idempotency-Key": str(uuid4())}
        )
        assert stale.status_code == 409, stale.text
        await set_request_context(connection, admin)
        target = (
            IdentityRepository()
            ._actor(await connection.fetchval("SELECT security.company_management_actor($1::uuid)", wid))
            .context
        )
        await set_request_context(connection, target)
        audit = await connection.fetch(
            "SELECT before_snapshot,after_snapshot FROM ops.audit_log WHERE action_code='company.rename'"
        )
        assert len(audit) == 1 and audit[0]["after_snapshot"]["name"] == body["name"]
        assert audit[0]["before_snapshot"]["name"] == "隔离正式公司"
        companies = (await client.get("/api/v1/console/companies")).json()["items"]
        assert next(c for c in companies if c["id"] == admin.workspace_id)["name"] == "演示公司"
