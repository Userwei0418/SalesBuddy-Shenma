from uuid import uuid4
import pytest
from sales_backend.repositories.passwords import PasswordRepository
from tests.integration.test_operations_api import client_for, sign_in

pytestmark=pytest.mark.asyncio
PASSWORD='Isolated-Phone-2026'
PHONE='13800000001'
NEWPHONE='13900000002'

async def create(admin, phone=PHONE, account=None):
    org=(await admin.get('/api/v1/console/organization')).json()
    body=dict(account_code=account or uuid4().hex[:12]+'@example.com',display_name='隔离手机号账号',
        team_id=org['departments'][0]['id'],roles=['sales'],temporary_password=PASSWORD,phone_number=phone)
    result=await admin.post('/api/v1/console/accounts',json=body,headers={'Idempotency-Key':str(uuid4())})
    return body,result

async def edit(admin, uid, body, version, **changes):
    payload={k:body[k] for k in ['display_name','team_id','roles']}
    payload.update(status='active',version_no=version,**changes)
    return await admin.put('/api/v1/console/accounts/'+uid,json=payload,headers={'Idempotency-Key':str(uuid4())})

async def login(user, account, password=PASSWORD):
    return await user.post('/api/v1/auth/password/login',json={'account_code':account,'password':password})

async def test_phone_lifecycle_same_identity_preserved_password_and_omitted_field(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin)
        body,created=await create(admin, None)
        assert created.status_code==201,created.text
        uid=created.json()['id']
        assert (await login(user,PHONE)).status_code==401
        assert (await edit(admin,uid,body,1,phone_number=PHONE)).status_code==200
        by_email=await login(user,body['account_code']);by_phone=await login(user,PHONE)
        assert by_email.status_code==by_phone.status_code==200
        assert by_email.json()['actor']==by_phone.json()['actor']
        assert by_email.json()['actor']['user_id']==uid
        session=by_phone.json()
        org=(await admin.get('/api/v1/console/organization')).json()
        assert next(r for r in org['accounts'] if r['id']==uid)['phone_number']==PHONE
        assert (await edit(admin,uid,body,2)).status_code==200  # old clients omit phone
        assert (await login(user,PHONE)).status_code==200
        assert (await edit(admin,uid,body,3,phone_number=NEWPHONE)).status_code==200
        assert (await login(user,PHONE)).status_code==401
        assert (await login(user,NEWPHONE)).status_code==200
        assert (await user.post('/api/v1/auth/refresh',json={'refresh_token':session['refresh_token']})).status_code==401
        assert (await edit(admin,uid,body,4,phone_number='')).status_code==200
        assert (await login(user,NEWPHONE)).status_code==401
        assert (await login(user,body['account_code'])).status_code==200
        org=(await admin.get('/api/v1/console/organization')).json()
        assert next(r for r in org['accounts'] if r['id']==uid)['phone_number'] is None

async def test_duplicate_and_legacy_numeric_account_conflicts_are_rejected(connection):
    async with await client_for(connection) as admin:
        await sign_in(admin)
        body,result=await create(admin)
        assert result.status_code==201
        _,duplicate=await create(admin)
        assert duplicate.status_code==409
        _,numeric=await create(admin,None,PHONE)
        assert numeric.status_code==409
        _,legacy=await create(admin,None,NEWPHONE)
        assert legacy.status_code==201
        changed=await edit(admin,result.json()['id'],body,1,phone_number=NEWPHONE)
        assert changed.status_code==409
        changed=await edit(admin,result.json()['id'],body,1,phone_number='12')
        assert changed.status_code==422

async def test_email_phone_share_password_failure_limit(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin)
        body,created=await create(admin)
        assert created.status_code==201
        for name in [body['account_code'],PHONE,body['account_code'],PHONE,PHONE]:
            assert (await login(user,name,'Wrong-Phone-2026')).status_code==401
        assert (await login(user,body['account_code'])).status_code==429
        assert (await login(user,PHONE)).status_code==429
        org=(await admin.get('/api/v1/console/organization')).json()
        member=next(r for r in org['accounts'] if r['id']==created.json()['id'])
        assert member['login_locked'] and member['login_attempts']>=5
        unlocked=await admin.post('/api/v1/console/accounts/'+member['id']+'/unlock-login',
            json={'version_no':member['version_no'],'reason':'隔离测试'},headers={'Idempotency-Key':str(uuid4())})
        assert unlocked.status_code==200,unlocked.text
        assert (await login(user,PHONE)).status_code==200

async def test_removed_phone_cannot_issue_session_after_password_lookup(connection,monkeypatch):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin)
        body,created=await create(admin)
        assert created.status_code==201
        original=PasswordRepository.candidate
        async def revoke_after_lookup(repo,c,workspace,account,role=None):
            result=await original(repo,c,workspace,account,role)
            if account==PHONE:
                removed=await edit(admin,created.json()['id'],body,1,phone_number=None)
                assert removed.status_code==200
            return result
        monkeypatch.setattr(PasswordRepository,'candidate',revoke_after_lookup)
        assert (await login(user,PHONE)).status_code==401
        assert (await login(user,body['account_code'])).status_code==200

async def test_business_member_cannot_edit_phone(connection):
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin)
        body,created=await create(admin)
        signed=await login(user,PHONE)
        user.headers['Authorization']='Bearer '+signed.json()['access_token']
        denied=await edit(user,created.json()['id'],body,1,phone_number=None)
        assert denied.status_code==403


async def test_same_phone_in_two_companies_requires_explicit_company(connection):
    from tests.integration.test_company_tenants import provision_fixture
    _, manifest, wid, _ = await provision_fixture(connection)
    async with await client_for(connection) as admin, await client_for(connection) as user:
        await sign_in(admin,'ADMIN001')
        body,first=await create(admin)
        assert first.status_code==201
        selected=await admin.post('/api/v1/console/companies/select',json={'company_id':wid},headers={'Idempotency-Key':str(uuid4())})
        assert selected.status_code==200
        admin.headers['X-Company-ID']=wid
        _,second=await create(admin)
        assert second.status_code==201,second.text
        assert (await login(user,PHONE)).status_code==401
        for workspace,uid in [('demo-sales-workspace',first.json()['id']), (manifest['target_company']['code'],second.json()['id'])]:
            response=await user.post('/api/v1/auth/password/login',json={'account_code':PHONE,'password':PASSWORD,'workspace':workspace})
            assert response.status_code==200,response.text
            assert response.json()['actor']['user_id']==uid

async def test_session_revocation_function_enforces_permissions(connection):
    import asyncpg
    from tests.integration.test_operations_claims_sql import actor
    admin = await actor(connection, 'ADMIN001')
    sales = await actor(connection, 'XS001')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('SELECT security.revoke_managed_account_sessions($1::uuid)', sales.user_id)
    await actor(connection, 'OPS001')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('SELECT security.revoke_managed_account_sessions($1::uuid)', admin.user_id)
    await actor(connection, 'ADMIN001')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('SELECT security.revoke_managed_account_sessions($1::uuid)', str(uuid4()))

async def test_administrator_self_demotion_with_second_administrator(connection):
    from tests.integration.test_operations_claims_sql import actor
    from sales_backend.services.operations_accounts import OperationsAccountService
    from sales_backend.repositories.operations_accounts import OperationsAccountRepository
    admin=await actor(connection,'ADMIN001')
    org=await OperationsAccountRepository().organization(connection)
    team=org['departments'][0]['id']
    service=OperationsAccountService()
    await service.create(connection,admin,dict(account_code='SECONDADMIN',display_name='第二管理员',team_id=team,roles=['administrator']),PASSWORD)
    result=await service.update(connection,admin,admin.user_id,dict(display_name='原管理员',team_id=team,roles=['sales'],status='active',version_no=1))
    assert result['version_no']==2
