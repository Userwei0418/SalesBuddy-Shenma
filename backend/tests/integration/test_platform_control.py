"""Real platform HTTP/SQL under separate database roles; all fixtures roll back."""
from contextlib import asynccontextmanager
from uuid import uuid4

import time
import secrets
import asyncpg
from sales_backend.security.platform_mfa import PlatformMFACipher, authenticator
import pytest
import pytest_asyncio

from sales_backend.auth.passwords import encode_password
from sales_backend.main import app
from sales_backend.services.platform_control import PUBLIC_FUNCTIONS, verify_platform_pool
from tests.integration.test_operations_api import client_for, sign_in

pytestmark = pytest.mark.asyncio
BASE = '/api/v1/platform'
PASSWORD = 'Isolated-platform-test-2026'


class PlatformDatabase:
    def __init__(self, connection, role):
        self.conn, self.role = connection, role

    @asynccontextmanager
    async def connection(self):
        previous = await self.conn.fetchval('SELECT current_user')
        async with self.conn.transaction():
            await self.conn.execute(f'SET LOCAL ROLE "{self.role}"')
            yield self.conn
            await self.conn.execute(f'SET LOCAL ROLE "{previous}"')


@pytest_asyncio.fixture
async def platform(connection, monkeypatch):
    assert (await connection.fetchval('SELECT current_database()')).startswith('salegent_verify_integration_')
    runtime = await connection.fetchval('SELECT current_user')
    await connection.execute('RESET ROLE')
    role = 'platform_test_' + uuid4().hex
    await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    await connection.execute(f'GRANT USAGE ON SCHEMA control TO "{role}"')
    for signature in PUBLIC_FUNCTIONS:
        await connection.execute(f'GRANT EXECUTE ON FUNCTION control.{signature} TO "{role}"')
    account = 'PLATFORM' + uuid4().hex[:10].upper()
    seed = secrets.token_bytes(20)
    cipher = PlatformMFACipher('test', {'test':secrets.token_bytes(32)})
    operator_id = uuid4()
    await connection.execute('INSERT INTO control.operator(id,account_code,display_name,password_hash,mfa_secret) VALUES($1,$2,$3,$4,$5)',
                             operator_id, account, '隔离平台管理', encode_password(PASSWORD), cipher.seal(str(operator_id),seed))
    monkeypatch.setattr(app.state, 'platform_mfa_cipher', cipher, raising=False)
    database = PlatformDatabase(connection, role)
    database.seed, database.account, database.operator_id = seed, account, operator_id
    await verify_platform_pool(database)
    monkeypatch.setattr(app.state, 'platform_database', database, raising=False)
    await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    async with await client_for(connection) as client:
        response = await client.post(BASE+'/auth/login', json={'account_code':account,'password':PASSWORD,'otp':authenticator(seed).generate(time.time()).decode()})
        assert response.status_code == 200, response.text
        assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
        yield client, database


async def provision(client, code=None, key=None):
    body = {'company_code':code or 'tenant-'+uuid4().hex[:10], 'name':'合成 SaaS 公司', 'purpose':'customer',
            'administrator_account':'ADMIN', 'administrator_name':'测试管理员'}
    headers = {'Idempotency-Key':key or str(uuid4())}
    response = await client.post(BASE+'/tenants', json=body, headers=headers)
    assert response.status_code == 201, response.text
    return body, headers, response.json()


async def tenant(client, identifier):
    response = await client.get(BASE+'/tenants')
    assert response.status_code == 200, response.text
    return next(t for t in response.json()['items'] if t['id']==identifier)


async def transition(client, row, state):
    return await client.post(BASE+'/tenants/'+row['id']+'/state', json={
        'version_no':row['version_no'], 'status':state, 'reason':'隔离生命周期验收'})


async def activate(client, body, created):
    response = await client.post(BASE+'/activate', json={'company_code':body['company_code'],
        'activation_token':created['activation_token'], 'new_password':PASSWORD})
    assert response.status_code == 200, response.text


async def test_provision_activation_idempotency_state_machine_and_audit(platform):
    client, _ = platform
    body, headers, created = await provision(client)
    replay = await client.post(BASE+'/tenants', json=body, headers=headers)
    assert replay.status_code == 201 and replay.json()['id']==created['id']
    assert replay.json()['activation_token'] is None
    assert (await client.post(BASE+'/tenants', json={**body,'name':'不同请求'},headers=headers)).status_code==409
    before = await tenant(client, created['id'])
    assert before['status']=='provisioning' and not before['readiness']['activation_complete']
    assert (await transition(client, before, 'active')).status_code==409
    await activate(client, body, created)
    assert (await client.post(BASE+'/activate', json={'company_code':body['company_code'],
        'activation_token':created['activation_token'],'new_password':PASSWORD})).status_code==422
    assert (await transition(client, before, 'active')).status_code==409  # activation bumped version
    row=await tenant(client,created['id'])
    assert all(row['readiness'].values())
    assert (await transition(client,row,'active')).status_code==200
    row=await tenant(client,created['id'])
    assert (await transition(client,row,'suspended')).status_code==200
    row=await tenant(client,created['id'])
    assert (await transition(client,row,'active')).status_code==200
    row=await tenant(client,created['id'])
    assert (await transition(client,row,'closed')).status_code==200
    assert (await transition(client,await tenant(client,created['id']),'active')).status_code==422
    audit=await client.get(BASE+'/audit')
    assert created['activation_token'] not in audit.text and PASSWORD not in audit.text
    assert 'tenant.provision' in audit.text and 'tenant.closed' in audit.text


async def test_platform_role_has_no_business_data_and_company_credentials_are_not_platform(platform, connection):
    client, database=platform
    async with database.connection() as conn:
        for sql in ('SELECT * FROM crm.customer','SELECT * FROM platform.password_credential',
                    'SELECT * FROM control.operator','SELECT * FROM control.session',
                    'SELECT control.actor(\'forged\')'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetch(sql)
    async with await client_for(connection) as company:
        await sign_in(company,'ADMIN001')
        assert (await company.get(BASE+'/tenants')).status_code==401
        assert (await company.post(BASE+'/auth/login',json={'account_code':'ADMIN001','password':'Isolated-Testing-2026'})).status_code==401
    assert (await client.get('/api/v1/console/organization')).status_code==401
    assert (await client.post(BASE+'/auth/logout')).status_code==200
    assert (await client.get(BASE+'/tenants')).status_code==401


async def test_same_account_tenant_selection_and_suspend_revokes_existing_sessions(platform, connection):
    client,_=platform
    companies=[]
    for _ in range(2):
        body,_,created=await provision(client)
        await activate(client,body,created)
        assert (await transition(client,await tenant(client,created['id']),'active')).status_code==200
        companies.append((body,created))
    async with await client_for(connection) as user:
        # Ambiguous accounts never select an arbitrary company.
        assert (await user.post('/api/v1/console/auth/login',json={'account_code':'ADMIN','password':PASSWORD})).status_code==401
        for body,created in companies:
            response=await user.post('/api/v1/console/auth/login',json={'workspace':body['company_code'].upper(),'account_code':'ADMIN','password':PASSWORD})
            assert response.status_code==200,response.text
            assert response.json()['actor']['workspace_id']==created['id']
        body,created=companies[-1]
        user.headers['Authorization']='Bearer '+response.json()['access_token']
        assert (await user.get('/api/v1/console/organization')).status_code==200
        assert (await transition(client,await tenant(client,created['id']),'suspended')).status_code==200
        assert (await user.get('/api/v1/console/organization')).status_code in (401,403)
        assert (await user.post('/api/v1/console/auth/refresh')).status_code==401
        logout=await user.post('/api/v1/console/auth/logout')
        assert logout.status_code==200,logout.text
        assert all('Max-Age=0' in value for value in logout.headers.get_list('set-cookie'))
        assert (await user.post('/api/v1/console/auth/login',json={'workspace':body['company_code'],'account_code':'ADMIN','password':PASSWORD})).status_code==401
        assert (await transition(client,await tenant(client,created['id']),'active')).status_code==200
        assert (await user.get('/api/v1/console/organization')).status_code==401  # old token stays revoked
        assert (await user.post('/api/v1/console/auth/login',json={'workspace':body['company_code'],'account_code':'ADMIN','password':PASSWORD})).status_code==200


async def test_duplicate_code_rolls_back_and_activation_reissue_invalidates_old_code(platform):
    client,_=platform
    body,_,created=await provision(client)
    response=await client.post(BASE+'/tenants',json=body,headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==409
    row=await tenant(client,created['id'])
    new=await client.post(BASE+'/tenants/'+created['id']+'/activation',json={'version_no':row['version_no'],'reason':'原激活码未交付'})
    assert new.status_code==200,new.text
    assert (await client.post(BASE+'/activate',json={'company_code':body['company_code'],
        'activation_token':created['activation_token'],'new_password':PASSWORD})).status_code==422
    await activate(client,body,new.json())
    assert (await client.post(BASE+'/tenants/'+created['id']+'/activation',json={'version_no':row['version_no'],'reason':'已激活不允许重设'})).status_code in (409,422)


async def test_pause_stops_queue_claims_and_invalidates_inflight_worker_receipts(platform, connection):
    from tests.integration.feishu_fixtures import fixture_owner
    client,_=platform
    body,_,created=await provision(client)
    await activate(client,body,created)
    assert (await transition(client,await tenant(client,created['id']),'active')).status_code==200
    ws=created['id']; kind='tenant-test.'+uuid4().hex
    async with fixture_owner(connection):
        job=await connection.fetchval("INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1::uuid,$2,'{}') RETURNING id",ws,kind)
    old=await connection.fetchrow("SELECT * FROM ops.claim_job('test',30,$1::text[],false)",[kind])
    assert old['id']==job and old['attempts']==1
    assert (await transition(client,await tenant(client,ws),'suspended')).status_code==200
    assert not await connection.fetch("SELECT * FROM ops.claim_job('test',30,$1::text[],false)",[kind])
    async with fixture_owner(connection):
        revoked=await connection.fetchrow('SELECT * FROM ops.job WHERE id=$1',job)
        assert revoked['lease_token'] is None and revoked['attempts']==0
    assert (await transition(client,await tenant(client,ws),'active')).status_code==200
    new=await connection.fetchrow("SELECT * FROM ops.claim_job('test',30,$1::text[],false)",[kind])
    assert new['id']==job and new['lease_token']!=old['lease_token'] and new['attempts']==1


async def test_paused_tenant_context_and_feishu_read_claim_write_are_rejected(platform, connection):
    from tests.integration.feishu_fixtures import fixture_owner
    from sales_backend.db import TenantInactive, set_request_context
    from sales_backend.repositories.identity import IdentityRepository
    from sales_backend.repositories.feishu_sync import FeishuRepository, SyncLeaseLost
    client,_=platform
    body,_,created=await provision(client)
    await activate(client,body,created)
    assert (await transition(client,await tenant(client,created['id']),'active')).status_code==200
    ws=created['id']; cid=uuid4(); oid=uuid4()
    record=await IdentityRepository().find_maintenance_administrator(connection,workspace_external_id=body['company_code'],account_code='ADMIN')
    async with fixture_owner(connection):
        await connection.execute("""INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,validated_revision,settings,updated_by)
          VALUES($1,$2::uuid,1,true,1,$3::jsonb,$4::uuid)""",cid,ws,{'workspace_id':ws,'connection_id':str(cid),
          'app_id':'synthetic','base_token':'synthetic','credential_ref':str(uuid4()),'direction':'system_to_base',
          'mappings':{'customer':{'enabled':True,'table_id':'tblSynthetic','id_field_id':'fldId',
            'fields':{'record_status':'fldStatus'}}}},record.context.user_id)
        await connection.execute('INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) VALUES($1,$2::uuid,$3,$4)',cid,ws,bytes(29),'synthetic')
        eid=await connection.fetchval("INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id) VALUES($1,$2::uuid,'customer',$3) RETURNING id",cid,ws,oid)
    module_config=(await client.get(BASE+'/tenants/'+created['id']+'/modules')).json()
    modules=[{'code':m['code'],'enabled':True if m['code']=='feishu' else m['enabled'],'expires_at':m['expires_at']} for m in module_config['modules']]
    assert (await client.post(BASE+'/tenants/'+created['id']+'/modules',json={
        'version_no':module_config['version_no'],'modules':modules,'reason':'测试公司配置齐备后明确开通飞书'})).status_code==200
    runtime=await connection.fetchval('SELECT current_user')
    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    repo=FeishuRepository(); old=dict(await repo.claim(connection)); assert old['id']==eid
    await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    assert (await transition(client,await tenant(client,ws),'suspended')).status_code==200
    with pytest.raises(TenantInactive):
        async with connection.transaction():
            await set_request_context(connection,record.context)
    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    assert not await repo.claim(connection)
    assert not await repo.worker_config(connection,cid)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await repo.source(connection,old)
    with pytest.raises(SyncLeaseLost):
        await repo.remember(connection,old,'table','record','digest')
    assert await connection.fetchval('SELECT count(*) FROM ops.feishu_record_map WHERE connection_id=$1',cid)==0
    await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    assert (await transition(client,await tenant(client,ws),'active')).status_code==200
    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    new=await repo.claim(connection); assert new['lease_token']!=old['lease_token']
    with pytest.raises(SyncLeaseLost):
        await repo.remember(connection,old,'table','late-record','digest')
    await repo.remember(connection,new,'table','new-record','digest')
    assert await connection.fetchval('SELECT record_id FROM ops.feishu_record_map WHERE connection_id=$1',cid)=='new-record'


async def test_cross_origin_and_forged_token_rejected_before_state_change(platform):
    client,_=platform
    before=await client.get(BASE+'/tenants')
    assert (await client.post(BASE+'/tenants',headers={'Origin':'https://attacker.invalid','Idempotency-Key':str(uuid4())},
        json={'company_code':'rejected-origin','name':'拒绝','purpose':'demo','administrator_account':'ADMIN','administrator_name':'测试'})).status_code==403
    assert (await client.get(BASE+'/tenants')).json()==before.json()
    client.cookies.clear()
    client.cookies.set('sales_platform_session','f'*64,path='/api/v1/platform')
    assert (await client.get(BASE+'/tenants')).status_code==401


async def test_nonexistent_tenant_fails_before_any_business_query(connection, sales_actor):
    from sales_backend.db import TenantInactive, set_request_context
    with pytest.raises(TenantInactive):
        async with connection.transaction():
            await set_request_context(connection,sales_actor.model_copy(update={'workspace_id':str(uuid4())}))


async def test_platform_startup_rejects_accidental_business_write_grant(platform, connection):
    client,database=platform
    from tests.integration.feishu_fixtures import fixture_owner
    async with fixture_owner(connection):
        await connection.execute(f'GRANT INSERT ON crm.customer TO "{database.role}"')
    with pytest.raises(RuntimeError,match='directly read or write'):
        await verify_platform_pool(database)
