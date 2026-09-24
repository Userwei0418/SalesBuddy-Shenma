"""Real HTTP configuration with RLS, auditing, stale writes and immediate revocation."""

from uuid import uuid4

import pytest

from sales_backend.contracts.authorization import AccountAuthorizationSave
from sales_backend.db import set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login

pytestmark = pytest.mark.asyncio
BASE = "/api/v1/console/permissions"


async def test_templates_and_account_changes_roundtrip_idempotency_and_conflicts(connection):
    user, teams, _ = await account_fixture(connection, ["fde"])
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        catalog = await client.get(BASE + "/catalog")
        assert catalog.status_code == 200
        assert any(p["code"] == "opportunity.create" for p in catalog.json()["permissions"])
        assert len((await client.get(BASE + "/roles")).json()["roles"]) == 7
        key = {"Idempotency-Key": str(uuid4())}
        body = dict(name="指定团队商机录入", reason="创建功能模板", permissions=[dict(permission="opportunity.create")])
        role = await client.post(BASE + "/roles", json=body, headers=key)
        assert role.status_code == 201, role.text
        replay = await client.post(BASE + "/roles", json=body, headers=key)
        assert replay.json() == role.json()
        role_id = role.json()["id"]
        body = dict(version_no=0, reason="授权一个团队", roles=[dict(role_id=role_id, scope="teams", team_ids=[teams["fde"]])])
        path = BASE + "/accounts/" + user["id"]
        saved = await client.put(path, json=body)
        assert saved.status_code == 200, saved.text
        conflict = await client.put(path, json=body)
        assert conflict.status_code == 409, conflict.text
        preview = await client.get(path)
        assert preview.status_code == 200 and preview.json()["permissions"]["opportunity.create"]
        assert preview.json()["roles"] == body["roles"]
        assert len((await client.get(BASE + "/audit")).json()["entries"]) == 2
        assert (await client.get(BASE + "/accounts/" + str(uuid4()))).status_code == 404


async def test_explicit_permission_grants_work_without_changing_role_and_revoke_same_session(connection):
    user, _, admin = await account_fixture(connection, ["fde"])
    repository = AuthorizationRepository()
    async with await client_for(connection) as client:
        await native_login(client, user)
        assert (await client.get(BASE + "/roles")).status_code == 403
        await set_request_context(connection, admin)
        await repository.save_account(connection, user["id"], AccountAuthorizationSave(version_no=0, reason="仅授权查看",
            overrides=[dict(permission=p, effect="allow", scope="workspace") for p in ("access.console", "authorization.read")]
        ).model_dump(mode="json"))
        assert (await client.get(BASE + "/roles")).status_code == 200
        assert (await client.get(BASE + "/audit")).status_code == 403
        assert (await client.post(BASE + "/roles", json=dict(name="不能写", reason="测试"))).status_code == 403
        mine = (await client.get("/api/v1/auth/permissions")).json()
        assert mine["permissions"]["authorization.read"]
        await set_request_context(connection, admin)
        await repository.save_account(connection, user["id"], AccountAuthorizationSave(version_no=1, reason="撤回访问"
        ).model_dump(mode="json"))
        assert (await client.get(BASE + "/roles")).status_code == 403
        after = (await client.get("/api/v1/auth/permissions")).json()
        assert after["permission_version"] != mine["permission_version"]
        assert not after["permissions"]["authorization.read"]


async def test_account_denial_wins_over_administrator_and_cannot_be_replayed(connection):
    user, _, admin = await account_fixture(connection, [], company_role="administrator")
    async with await client_for(connection) as client:
        from tests.integration.test_sales_opportunity_permissions import PASSWORD
        await sign_in(client, user["account_code"], PASSWORD)
        payload = dict(name="一次授权", reason="测试重放权限")
        key = {"Idempotency-Key": str(uuid4())}
        assert (await client.post(BASE + "/roles", json=payload, headers=key)).status_code == 201
        await set_request_context(connection, admin)
        await AuthorizationRepository().save_account(connection, user["id"], AccountAuthorizationSave(
            version_no=0, reason="收回配置写权限", overrides=[dict(permission="authorization.roles_manage", effect="deny")]
        ).model_dump(mode="json"))
        assert (await client.post(BASE + "/roles", json=payload, headers=key)).status_code == 403


async def test_entry_permission_revocation_applies_to_existing_native_session(connection):
    user, _, admin = await account_fixture(connection, ["fde"])
    repository = AuthorizationRepository()
    async with await client_for(connection) as client:
        await native_login(client, user)
        assert (await client.get('/api/v1/assistant/home')).status_code == 200
        await set_request_context(connection, admin)
        await repository.save_account(connection, user['id'], AccountAuthorizationSave(
            version_no=0, reason='撤回小程序入口', overrides=[
                dict(permission='access.mini_program', effect='deny'),
                dict(permission='access.console', effect='allow', scope='workspace'),
            ]).model_dump(mode='json'))
        # Forged headers cannot select the web channel. Identity remains inspectable.
        assert (await client.get('/api/v1/assistant/home', headers={'X-Client-Channel': 'web'})).status_code == 403
        me = await client.get('/api/v1/auth/me')
        assert me.status_code == 200 and not me.json()['permissions']['access.mini_program']
        # A genuine console session can use shared APIs with its own entry grant.
        from tests.integration.test_sales_opportunity_permissions import PASSWORD
        await sign_in(client, user['account_code'], PASSWORD)
        assert (await client.get('/api/v1/tasks')).status_code == 200
        await set_request_context(connection, admin)
        await repository.save_account(connection, user['id'], AccountAuthorizationSave(
            version_no=1, reason='撤回后台入口', overrides=[
                dict(permission='access.mini_program', effect='deny'),
                dict(permission='access.console', effect='deny'),
            ]).model_dump(mode='json'))
        assert (await client.get('/api/v1/tasks')).status_code == 403
