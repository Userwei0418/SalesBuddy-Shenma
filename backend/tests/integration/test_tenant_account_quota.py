"""Account identity accounting and platform controls on disposable PostgreSQL."""
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.feishu_fixtures import seed_execute, seed_fetchval, fixture_owner
from tests.integration.test_platform_control import platform, provision, activate, tenant, transition, PASSWORD  # noqa: F401
from tests.integration.test_operations_api import client_for

pytestmark = pytest.mark.asyncio


async def quota(client, workspace):
    response = await client.get(f'/api/v1/platform/tenants/{workspace}/account-quota')
    assert response.status_code == 200, response.text
    return response.json()


async def save(client, workspace, limit):
    row = await quota(client, workspace)
    response = await client.post(f'/api/v1/platform/tenants/{workspace}/account-quota', json={
        'version_no': row['version_no'], 'account_limit': limit, 'reason': '合成租户额度验收'})
    assert response.status_code == 200, response.text
    return response.json()


async def account(connection, workspace, *, status='active'):
    uid = uuid4()
    await seed_execute(connection, """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,status)
        VALUES($1,$2::uuid,$3,'合成额度用户',$4)""", uid, workspace, str(uid), status)
    return uid


async def test_initial_admin_counts_once_and_defaults_are_unlimited(platform, connection):  # noqa: F811
    client, _ = platform
    _, _, created = await provision(client)
    ws = created['id']
    initial = await quota(client, ws)
    assert initial['active_accounts'] == 1 and initial['account_limit'] is None and initial['remaining'] is None
    uid = await account(connection, ws)
    for role in ('operations', 'manager'):
        await seed_execute(connection, """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
            VALUES($1::uuid,$2,$3,'workspace')""", ws, uid, role)
    assert (await quota(client, ws))['active_accounts'] == 2
    assert (await save(client, ws, 1))['over_limit'] is True
    # Changes unrelated to count are allowed even while the tenant is over limit.
    await seed_execute(connection, "UPDATE platform.user_ref SET display_name='更名不占新名额' WHERE id=$1", uid)
    with pytest.raises(asyncpg.PostgresError) as error:
        await account(connection, ws)
    assert error.value.sqlstate == 'P7429'
    assert (await quota(client, ws))['active_accounts'] == 2


async def test_disable_delete_restore_and_rollback_accounting(platform, connection):  # noqa: F811
    client, _ = platform
    _, _, created = await provision(client)
    ws = created['id']
    await save(client, ws, 2)
    uid = await account(connection, ws)
    inactive = await account(connection, ws, status='inactive')
    with pytest.raises(asyncpg.PostgresError) as error:
        await seed_execute(connection, "UPDATE platform.user_ref SET status='active' WHERE id=$1", inactive)
    assert error.value.sqlstate == 'P7429'
    await seed_execute(connection, "UPDATE platform.user_ref SET status='inactive' WHERE id=$1", uid)
    assert (await quota(client, ws))['active_accounts'] == 1
    await seed_execute(connection, "UPDATE platform.user_ref SET status='active' WHERE id=$1", inactive)
    await seed_execute(connection, 'UPDATE platform.user_ref SET deleted_at=clock_timestamp() WHERE id=$1', inactive)
    assert (await quota(client, ws))['active_accounts'] == 1
    await seed_execute(connection, 'UPDATE platform.user_ref SET deleted_at=NULL WHERE id=$1', inactive)
    assert (await quota(client, ws))['active_accounts'] == 2
    await seed_execute(connection, 'DELETE FROM platform.user_ref WHERE id=$1', inactive)
    assert (await quota(client, ws))['active_accounts'] == 1
    with pytest.raises(RuntimeError, match='force rollback'):
        async with connection.transaction():
            await account(connection, ws)
            assert (await quota(client, ws))['active_accounts'] == 2
            raise RuntimeError('force rollback')
    assert (await quota(client, ws))['active_accounts'] == 1
    await save(client, ws, 0)
    assert (await quota(client, ws))['remaining'] == 0
    await account(connection, ws, status='inactive')
    await save(client, ws, None)
    await account(connection, ws)


async def test_company_http_rejects_over_quota_and_idempotent_replay_is_free(platform, connection):  # noqa: F811
    client, _ = platform
    body, _, created = await provision(client)
    ws = created['id']
    await activate(client, body, created)
    assert (await transition(client, await tenant(client, ws), 'active')).status_code == 200
    await save(client, ws, 2)
    async with await client_for(connection) as company:
        login = await company.post('/api/v1/console/auth/login', json={
            'workspace': body['company_code'], 'account_code': 'ADMIN', 'password': PASSWORD})
        assert login.status_code == 200, login.text
        company.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        data = dict(account_code='QUOTA001', display_name='额度测试', roles=['operations'],
                    company_roles=['operations'], temporary_password='Isolated-Quota-2026')
        path = '/api/v1/console/accounts'
        headers = {'Idempotency-Key': str(uuid4())}
        response = await company.post(path, json=data, headers=headers)
        assert response.status_code == 201, response.text
        assert (await company.post(path, json=data, headers=headers)).json() == response.json()
        failed_key = {'Idempotency-Key': str(uuid4())}
        rejected = await company.post(path, json={**data, 'account_code': 'QUOTA002'}, headers=failed_key)
        assert rejected.status_code == 409 and '额度' in rejected.json()['detail']
        assert not await seed_fetchval(connection, "SELECT EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=$1::uuid AND account_code='QUOTA002')", ws)
        # Reducing the limit never revokes existing accounts. Updating a member
        # and freeing a seat still works while usage exceeds the limit.
        await save(client, ws, 1)
        assert (await company.get('/api/v1/console/organization')).status_code == 200
        changed = await company.put(path+'/'+response.json()['id'], json=dict(
            display_name='额度测试', version_no=response.json()['version_no'], status='inactive',
            roles=['operations'], company_roles=['operations']), headers={'Idempotency-Key': str(uuid4())})
        assert changed.status_code == 200, changed.text
        await save(client, ws, 2)
        retried = await company.post(path, json={**data, 'account_code': 'QUOTA002'}, headers=failed_key)
        assert retried.status_code == 201, retried.text
        assert (await company.get(f'/api/v1/platform/tenants/{ws}/account-quota')).status_code == 401
    assert (await quota(client, ws))['active_accounts'] == 2


async def test_quota_cas_sensitive_verification_audit_and_validation(platform, connection):  # noqa: F811
    client, db = platform
    _, _, created = await provision(client)
    ws = created['id']; initial = await quota(client, ws)
    path = f'/api/v1/platform/tenants/{ws}/account-quota'
    payload = dict(version_no=initial['version_no'], account_limit=3, reason='合成额度变更')
    assert (await client.post(path, json=payload)).status_code == 200
    assert (await client.post(path, json=payload)).status_code == 409
    for invalid in (-1, True, 1.5, 9007199254740992):
        assert (await client.post(path, json={**payload, 'account_limit': invalid})).status_code == 422
    assert (await client.post(path, json={k: v for k, v in payload.items() if k != 'account_limit'})).status_code == 422
    assert (await client.post(path, json={**payload, 'version_no': initial['version_no']+1, 'reason': ' '})).status_code == 422
    rows = (await client.get('/api/v1/platform/audit')).json()['items']
    row = next(r for r in rows if r['action'] == 'tenant.account_quota')
    assert row['before_state']['account_limit'] is None and row['after_state']['account_limit'] == 3
    async with fixture_owner(connection):
        await connection.execute("UPDATE control.session SET mfa_verified_at=clock_timestamp()-interval '6 minutes' WHERE operator_id=$1", db.operator_id)
    assert (await client.post(path, json={**payload, 'version_no': initial['version_no']+1})).status_code == 403


async def test_tenants_are_independent_and_counter_cannot_be_tampered_with(platform, connection):  # noqa: F811
    client, _ = platform
    _, _, first = await provision(client)
    _, _, second = await provision(client)
    await save(client, first['id'], 0)
    await account(connection, second['id'])
    assert (await quota(client, second['id']))['active_accounts'] == 2
    for statement in ('SELECT * FROM control.tenant_account_quota',
                      'UPDATE control.tenant_account_quota SET active_accounts=0',
                      "SELECT control.account_quota('forged',gen_random_uuid())",
                      "SELECT control.save_account_quota('forged',gen_random_uuid(),1,NULL,'forged')"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(statement)
    # Even an owner fixture must not move a user between companies or bulk-reset
    # the source table without an explicit counter reconciliation.
    uid = await account(connection, second['id'], status='inactive')
    with pytest.raises(asyncpg.PostgresError) as error:
        await seed_execute(connection, 'UPDATE platform.user_ref SET workspace_id=$2::uuid WHERE id=$1', uid, first['id'])
    assert error.value.sqlstate == 'P7422'
    with pytest.raises(asyncpg.PostgresError) as error:
        await seed_execute(connection, 'TRUNCATE platform.user_ref CASCADE')
    assert error.value.sqlstate == 'P7422'
