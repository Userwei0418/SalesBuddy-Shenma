"""Target history is scoped business data, not a grant to technical audit rows."""
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_console_business_form_options import ENTRY, NO_DIRECTORY, target_body
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_sales_opportunity_permissions import PASSWORD, account_fixture

pytestmark = pytest.mark.asyncio
SQL = "SELECT * FROM security.target_history_rows($1::uuid,$2,$3)"


def permission(code, scope="workspace", teams=None):
    return dict(permission=code, effect="allow", scope=scope, team_ids=teams or [])


def path(target):
    return f"/api/v1/console/targets/{target['id']}/history"


async def current(connection, admin, user):
    identity = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=admin.workspace_id, user_id=user["id"], role="sales")
    await set_request_context(connection, identity.context)
    await assert_restricted(connection)


async def save(client, body):
    result = await client.post("/api/v1/console/targets/batch", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "effective"
    return result.json()["items"][0]


async def denied_sql(connection, target):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetch(SQL, target["id"], 50, 0)


async def test_self_history_without_audit_access_is_complete_bounded_and_paginated(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    await grant(connection, admin, user["id"], [ENTRY, *NO_DIRECTORY,
        permission("target.manage", "self")])
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        payload = target_body(user_id=user["id"])
        first = await save(client, payload)
        payload["items"][0].update(amount="2345.67", version_no=first["version_no"])
        await save(client, payload)
        response = await client.get(path(first))
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 2
        assert [r["action_code"] for r in items] == ["crm.sales_target.update", "crm.sales_target.insert"]
        assert all(set(r) == {"id", "occurred_at", "actor_name", "action_code", "before_snapshot", "after_snapshot"} for r in items)
        assert all(r["after_snapshot"]["id"] == first["id"] for r in items)
        assert items[0]["before_snapshot"]["amount"] == 1234.56
        assert items[0]["after_snapshot"]["amount"] == 2345.67
        for offset in (0, 1, 2):
            page = await client.get(path(first), params={"limit": 1, "offset": offset})
            assert page.status_code == 200 and page.json()["items"] == items[offset:offset+1]
        assert (await client.get("/api/v1/console/audit")).status_code == 403
        await current(connection, admin, user)
        assert not await connection.fetchval("SELECT security.authorization_has('audit.read') OR security.authorization_has('audit.export')")
        assert await connection.fetchval("SELECT count(*) FROM ops.audit_log") == 0
        rows = await connection.fetch(SQL, first["id"], 50, 0)
        assert [r["id"] for r in rows] == [r["id"] for r in items]
        for limit, offset in [(0, 0), (101, 0), (None, 0), (1, -1), (1, None)]:
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with connection.transaction():
                    await connection.fetch(SQL, first["id"], limit, offset)


async def test_audit_workspace_does_not_expand_history_and_history_does_not_expand_read(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    other, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as owner, await client_for(connection) as client:
        await sign_in(owner, "ADMIN001")
        own = await save(owner, target_body(user_id=user["id"]))
        foreign = await save(owner, target_body(user_id=other["id"]))
        broad_read = [ENTRY, permission("audit.read"), permission("target.read"), permission("target.history", "self")]
        await grant(connection, admin, user["id"], broad_read)
        await sign_in(client, user["account_code"], PASSWORD)
        assert (await client.get(path(own))).status_code == 200
        response = await client.get(path(foreign))
        assert response.status_code == 403, response.text
        await current(connection, admin, user)
        assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_id=$1::uuid", foreign["id"]) == 1
        await denied_sql(connection, foreign)
        # Reverse the scopes: a broad history grant cannot bypass target.read.
        await grant(connection, admin, user["id"], [ENTRY, permission("audit.read"),
            permission("target.history"), permission("target.read", "self")], version=1)
        assert (await client.get(path(foreign))).status_code == 404
        await current(connection, admin, user)
        await denied_sql(connection, foreign)
        await grant(connection, admin, user["id"], [*[p for p in broad_read if p["permission"] != "target.history"],
            dict(permission="target.history", effect="deny")], version=2)
        assert (await client.get(path(own))).status_code == 403
        await current(connection, admin, user)
        await denied_sql(connection, own)


async def test_team_history_follows_current_memberships_and_deny_without_audit(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    colleague, teams, _ = await account_fixture(connection, ["sales"])
    outsider, _, _ = await account_fixture(connection, ["sales"])
    grants = [ENTRY, permission("target.read"), permission("target.history", "teams", [teams["sales"]])]
    await grant(connection, admin, user["id"], grants)
    async with await client_for(connection) as owner, await client_for(connection) as client:
        await sign_in(owner, "ADMIN001")
        member = await save(owner, target_body(user_id=colleague["id"]))
        team = await save(owner, target_body(team_id=teams["sales"]))
        unrelated = await save(owner, target_body(user_id=outsider["id"]))
        await sign_in(client, user["account_code"], PASSWORD)
        for row in (member, team):
            result = await client.get(path(row))
            assert result.status_code == 200 and len(result.json()["items"]) == 1, result.text
        assert (await client.get(path(unrelated))).status_code == 403
        await set_request_context(connection, admin)
        await connection.execute("UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid AND team_id=$2::uuid", colleague["id"], teams["sales"])
        assert (await client.get(path(member))).status_code == 403
        assert (await client.get(path(team))).status_code == 200
        await grant(connection, admin, user["id"], [ENTRY, permission("target.read"),
            dict(permission="target.history", effect="deny")], version=1)
        assert (await client.get(path(team))).status_code == 403
        await current(connection, admin, user)
        await denied_sql(connection, team)


async def test_history_rejects_cross_tenant_unknown_and_fde_without_its_own_permission(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    async with fixture_owner(connection):
        ws, uid, tid = uuid4(), uuid4(), uuid4()
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'目标历史外公司')", ws, str(ws))
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'外公司账号')", uid, ws, str(uid))
        await connection.execute("INSERT INTO crm.sales_target(id,workspace_id,target_year,scope_type,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,2032,'department','collection',9988,$3,'year','2032-01-01','2032-12-31')", tid, ws, uid)
    grants = [ENTRY, permission("target.read"), permission("target.history"), permission("audit.read")]
    await grant(connection, admin, user["id"], grants)
    async with await client_for(connection) as owner, await client_for(connection) as client:
        await sign_in(owner, "ADMIN001")
        fde = await save(owner, target_body(department="fde"))
        await sign_in(client, user["account_code"], PASSWORD)
        for row in (fde, {"id": str(tid)}, {"id": str(uuid4())}):
            assert (await client.get(path(row))).status_code == 404
            await current(connection, admin, user)
            await denied_sql(connection, row)
        await grant(connection, admin, user["id"], [*grants, permission("target.fde_department")], version=1)
        result = await client.get(path(fde))
        assert result.status_code == 200 and len(result.json()["items"]) == 1, result.text
        await grant(connection, admin, user["id"], grants, version=2)
        assert (await client.get(path(fde))).status_code == 404
    await current(connection, admin, user)
    # Broad fixture grants are reconciled just as deployed runtime grants are.
    assert await connection.fetchval("SELECT has_function_privilege(current_user,'security.target_history_rows(uuid,integer,integer)','EXECUTE')")
    assert not await connection.fetchval("SELECT has_function_privilege('salegent_feishu_worker','security.target_history_rows(uuid,integer,integer)','EXECUTE')")
    assert not await connection.fetchval("SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants_before_target_history()','EXECUTE')")
    assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM pg_proc p,aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE p.oid='security.target_history_rows(uuid,integer,integer)'::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')")
