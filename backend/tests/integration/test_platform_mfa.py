import base64
import time
from uuid import uuid4

import pytest
from sales_backend.auth.passwords import encode_password
from sales_backend.security.platform_mfa import authenticator
from tests.integration.test_platform_control import BASE, PASSWORD, provision
# Explicit re-export keeps the shared pytest fixture discoverable in this module.
from tests.integration.test_platform_control import platform as platform
from tests.integration.test_operations_api import client_for
from tests.integration.feishu_fixtures import fixture_owner

pytestmark=pytest.mark.asyncio


async def test_password_only_session_cannot_access_tenants_enrollment_and_replay(platform, connection):
    _, database=platform
    account='NEW'+uuid4().hex[:12].upper()
    async with fixture_owner(connection):
        identifier=await connection.fetchval('INSERT INTO control.operator(account_code,display_name,password_hash) VALUES($1,$1,$2) RETURNING id',account,encode_password(PASSWORD))
    async with await client_for(connection) as client:
        login=await client.post(BASE+'/auth/login',json={'account_code':account,'password':PASSWORD})
        assert login.status_code==200 and not login.json()['operator']['mfa_enrolled']
        assert (await client.get(BASE+'/tenants')).status_code==403
        assert (await client.get(BASE+'/audit')).status_code==403
        prepared=await client.post(BASE+'/auth/mfa/enroll',json={})
        assert prepared.status_code==200,prepared.text
        secret=prepared.json()['secret']; seed=base64.b32decode(secret)
        code=authenticator(seed).generate(time.time()).decode()
        confirm=await client.post(BASE+'/auth/mfa/confirm',json={'otp':code})
        assert confirm.status_code==200,confirm.text
        assert confirm.json()['operator']['mfa_verified']
        assert (await client.get(BASE+'/tenants')).status_code==200
        await provision(client)
        assert (await client.post(BASE+'/auth/mfa/enroll',json={})).status_code==403
        audit=(await client.get(BASE+'/audit')).text
        assert secret not in audit and PASSWORD not in audit
        # The same step cannot create another authenticated session.
        replay=await client.post(BASE+'/auth/login',json={'account_code':account,'password':PASSWORD,'otp':code})
        assert replay.status_code==403,replay.text
        async with fixture_owner(connection):
            stored=await connection.fetchval('SELECT mfa_secret::text FROM control.operator WHERE id=$1',identifier)
            assert secret not in stored


async def test_sensitive_changes_require_recent_password_and_fresh_totp(platform, connection):
    client,database=platform
    async with fixture_owner(connection):
        await connection.execute("UPDATE control.session SET mfa_verified_at=clock_timestamp()-interval '6 minutes' WHERE operator_id=$1",database.operator_id)
    assert (await client.get(BASE+'/tenants')).status_code==200
    body={'company_code':'mfa-'+uuid4().hex[:10],'name':'隔离公司','purpose':'demo','administrator_account':'ADMIN','administrator_name':'测试'}
    headers={'Idempotency-Key':str(uuid4())}
    rejected=await client.post(BASE+'/tenants',json=body,headers=headers)
    assert rejected.status_code==403 and rejected.json()['detail']=='PLATFORM_REAUTH_REQUIRED'
    next_code=authenticator(database.seed).generate(time.time()+30).decode()
    assert (await client.post(BASE+'/auth/verify',json={'password':'incorrect','otp':next_code})).status_code==403
    response=await client.post(BASE+'/auth/verify',json={'password':PASSWORD,'otp':next_code})
    assert response.status_code==200,response.text
    assert (await client.post(BASE+'/tenants',json=body,headers=headers)).status_code==201
    assert (await client.post(BASE+'/auth/verify',json={'password':PASSWORD,'otp':next_code})).status_code==403


async def test_mfa_failure_throttled_without_password_only_fallback(platform):
    client, database=platform
    for _ in range(5):
        failed=await client.post(BASE+'/auth/login',json={'account_code':database.account,'password':PASSWORD,'otp':'abcdef'})
        assert failed.status_code==403
    limited=await client.post(BASE+'/auth/login',json={'account_code':database.account,'password':PASSWORD})
    assert limited.status_code==429


async def test_revoked_and_old_revision_sessions_cannot_use_sensitive_access(platform,connection):
    client,database=platform
    async with fixture_owner(connection):
        await connection.execute('UPDATE control.operator SET mfa_revision=mfa_revision+1 WHERE id=$1',database.operator_id)
    assert (await client.get(BASE+'/tenants')).status_code==401
    assert (await client.get(BASE+'/auth/me')).status_code==401
    assert (await client.post(BASE+'/auth/verify',json={'password':PASSWORD,'otp':'123456'})).status_code==401


async def test_unconfigured_platform_cipher_never_creates_password_only_session(platform,monkeypatch):
    from sales_backend.main import app
    client,database=platform
    monkeypatch.setattr(app.state,'platform_mfa_cipher',None)
    response=await client.post(BASE+'/auth/login',json={'account_code':database.account,'password':PASSWORD,'otp':'123456'})
    assert response.status_code==503
    assert response.json()['detail']=='PLATFORM_MFA_NOT_CONFIGURED'
