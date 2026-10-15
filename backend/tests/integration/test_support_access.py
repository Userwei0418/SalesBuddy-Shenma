"""Real HTTP/SQL authorization with a restricted platform runtime and isolated data."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.test_platform_control import platform as platform_fixture, BASE
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.feishu_fixtures import fixture_owner

platform = platform_fixture
pytestmark = pytest.mark.asyncio
COMPANY = '/api/v1/console/support-access'
SUPPORT = BASE + '/support-access'


async def approve(company, database, scopes=None):
    body = {'id': str(uuid4()), 'operator_id': str(database.operator_id),
            'scopes': scopes or ['customers'], 'expires_at': (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            'reason': '隔离数据排障审批'}
    result = await company.post(COMPANY, json=body)
    assert result.status_code == 201, result.text
    return body


async def test_default_closed_approval_read_revoke_and_persisted_denials(platform, connection):
    support, database = platform
    assert (await support.get(SUPPORT)).json() == {'items': []}
    async with await client_for(connection) as company:
        await sign_in(company, 'ADMIN001')
        directory = await company.get(COMPANY+'/directory', params={'account': database.account})
        assert directory.status_code == 200, directory.text
        assert directory.json()['operator']['id'] == str(database.operator_id)
        assert 'password' not in directory.text and 'mfa_secret' not in directory.text
        assert (await company.get(COMPANY)).json() == {'items': []}
        body = await approve(company, database)
        assert (await company.post(COMPANY, json=body)).json()['replayed']
        assert (await company.post(COMPANY, json={**body,'reason':'不同请求'})).status_code == 409
        assignment = (await support.get(SUPPORT)).json()['items'][0]
        assert assignment['id']==body['id'] and assignment['active']
        records = await support.get(SUPPORT+'/'+body['id']+'/records/customers')
        assert records.status_code == 200, records.text
        assert records.json()['items'] and records.headers['cache-control']=='no-store'
        assert 'email' not in records.text and 'phone' not in records.text
        # Separate session domains, no ordinary-console identity or business writes.
        assert (await support.get('/api/v1/console/customers')).status_code == 401
        assert (await company.get(SUPPORT)).status_code == 401
        assert (await support.post(SUPPORT+'/'+body['id']+'/records/customers',json={})).status_code == 405
        assert (await support.get(SUPPORT+'/'+body['id']+'/records/tasks')).status_code == 403
        revoke = await company.post(COMPANY+'/'+body['id']+'/revoke',json={'reason':'排障完成'})
        assert revoke.status_code == 200, revoke.text
        assert (await support.get(SUPPORT+'/'+body['id']+'/records/customers')).status_code == 403
        events = (await company.get(COMPANY+'/audit')).json()['items']
        assert [e['action'] for e in events]==['denied','revoked','denied','read','approved']
        assert events[-2]['row_count']==len(records.json()['items'])
        assert all('items' not in e for e in events)


@pytest.mark.parametrize('kind',['expires','approver','permission','module','operator','session','paused'])
async def test_authority_is_checked_again_on_each_read(platform, connection, kind):
    support, database = platform
    async with await client_for(connection) as company:
        await sign_in(company, 'ADMIN001')
        body = await approve(company, database)
        gid = body['id']
        assert (await support.get(SUPPORT+'/'+gid+'/records/customers')).status_code==200
        async with fixture_owner(connection):
            grant = await connection.fetchrow('SELECT * FROM control.support_grant WHERE id=$1::uuid',gid)
            if kind=='expires':
                await connection.execute("UPDATE control.support_grant SET approved_at=clock_timestamp()-interval '2 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE id=$1::uuid",gid)
            elif kind=='approver':
                await connection.execute("UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1 AND role_code='administrator'",grant['approved_by'])
            elif kind=='permission':
                await connection.execute("DELETE FROM config.permission_role_grant WHERE workspace_id=$1 AND permission_code='customer.read'",grant['workspace_id'])
            elif kind=='module':
                await connection.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code IN (SELECT module_code FROM control.permission_module WHERE permission_code='customer.read')",grant['workspace_id'])
            elif kind=='operator':
                await connection.execute('UPDATE control.operator SET enabled=false WHERE id=$1',database.operator_id)
            elif kind=='session':
                await connection.execute('UPDATE control.session SET revoked_at=clock_timestamp() WHERE operator_id=$1',database.operator_id)
            else:
                await connection.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1",grant['workspace_id'])
                # Resume cannot resurrect an approval invalidated by suspension.
                await connection.execute("UPDATE platform.workspace SET status='active' WHERE id=$1",grant['workspace_id'])
        assert (await support.get(SUPPORT+'/'+gid+'/records/customers')).status_code in (401,403)


async def test_non_admin_cannot_approve_even_with_an_action_grant(platform, connection):
    _, database = platform
    async with await client_for(connection) as company:
        await sign_in(company, 'OPS001')
        assert (await company.get(COMPANY)).status_code==403
        async with fixture_owner(connection):
            await connection.execute("""INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
              SELECT workspace_id,id,'support.manage','workspace' FROM config.permission_role WHERE builtin_role_code='operations'""")
        # A configurable action permission alone cannot make a company approver.
        assert (await company.get(COMPANY+'/directory',params={'account':database.account})).status_code==403


async def test_limits_unknown_scope_forged_grant_and_other_operator(platform, connection):
    support, database = platform
    async with await client_for(connection) as company:
        await sign_in(company,'ADMIN001')
        body = await approve(company,database,['organization','customers','opportunities','visits','tasks'])
        for scope in body['scopes']:
            result=await support.get(SUPPORT+'/'+body['id']+'/records/'+scope)
            assert result.status_code==200,result.text
        for update in [{'scopes':['passwords']},{'scopes':['customers','customers']},
                       {'expires_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()},
                       {'expires_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()},
                       {'reason':'   '},{'workspace_id':str(uuid4())}]:
            assert (await company.post(COMPANY,json={**body,'id':str(uuid4()),**update})).status_code==422
        assert (await support.get(SUPPORT+'/'+str(uuid4())+'/records/customers')).status_code==403
        assert (await support.get(SUPPORT+'/'+body['id']+'/records/passwords')).status_code==422
        # A different platform operator's approved grant cannot be guessed.
        async with fixture_owner(connection):
            other=await connection.fetchval("INSERT INTO control.operator(account_code,display_name,password_hash) VALUES($1,'other','unused') RETURNING id",'OTHER'+uuid4().hex[:10].upper())
            await connection.execute('UPDATE control.support_grant SET operator_id=$1 WHERE id=$2::uuid',other,body['id'])
        assert (await support.get(SUPPORT+'/'+body['id']+'/records/customers')).status_code==403
        assert not (await support.get(SUPPORT)).json()['items']
        async with database.connection() as conn:
            for query in ('SELECT * FROM crm.customer','SELECT * FROM control.support_grant',
                          "SELECT security.approve_support(NULL,NULL,NULL,NULL,NULL)"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction(): await conn.execute(query)


async def test_each_company_controls_only_its_own_grants_and_records(platform, connection):
    from tests.integration.test_platform_control import provision, activate, transition, tenant, PASSWORD
    support,database=platform
    data,_,created=await provision(support)
    await activate(support,data,created)
    assert (await transition(support,await tenant(support,created['id']),'active')).status_code==200
    async with await client_for(connection) as first, await client_for(connection) as second:
        await sign_in(first,'ADMIN001')
        grant_a=await approve(first,database)
        result=await second.post('/api/v1/console/auth/login',json={'workspace':data['company_code'],'account_code':'ADMIN','password':PASSWORD})
        assert result.status_code==200,result.text
        second.headers['Authorization']='Bearer '+result.json()['access_token']
        grant_b=await approve(second,database)
        a=(await first.get(COMPANY)).json()['items'];b=(await second.get(COMPANY)).json()['items']
        assert [g['id'] for g in a]==[grant_a['id']] and [g['id'] for g in b]==[grant_b['id']]
        assert (await second.post(COMPANY+'/'+grant_a['id']+'/revoke',json={'reason':'foreign'})).status_code==404
        assert (await second.get(COMPANY+'/audit')).json()['items'][0]['grant_id']==grant_b['id']
        # The second company is empty, while the original isolated company has customers.
        assert (await support.get(SUPPORT+'/'+grant_b['id']+'/records/customers')).json()['items']==[]
        assert (await support.get(SUPPORT+'/'+grant_a['id']+'/records/customers')).json()['items']
        assert len((await support.get(SUPPORT)).json()['items'])==2


async def test_all_unexpired_grants_remain_visible_ahead_of_history_and_are_bounded(platform, connection):
    support,database=platform
    async with await client_for(connection) as company:
        await sign_in(company,'ADMIN001')
        body=await approve(company,database)
        async with fixture_owner(connection):
            g=await connection.fetchrow('SELECT * FROM control.support_grant WHERE id=$1::uuid',body['id'])
            # More than one page of revoked history must not bury a live approval.
            await connection.execute("""INSERT INTO control.support_grant(id,workspace_id,operator_id,approved_by,scopes,reason,expires_at,revoked_at)
             SELECT gen_random_uuid(),$1,$2,$3,ARRAY['customers'],'history',clock_timestamp()+interval '1 hour',clock_timestamp()
             FROM generate_series(1,105)""",g['workspace_id'],g['operator_id'],g['approved_by'])
        assert (await company.get(COMPANY)).json()['items'][0]['id']==body['id']
        assert (await support.get(SUPPORT)).json()['items'][0]['id']==body['id']
        async with fixture_owner(connection):
            await connection.execute("""INSERT INTO control.support_grant(id,workspace_id,operator_id,approved_by,scopes,reason,expires_at)
             SELECT gen_random_uuid(),$1,$2,$3,ARRAY['customers'],'limit fixture',clock_timestamp()+interval '1 hour'
             FROM generate_series(1,19)""",g['workspace_id'],g['operator_id'],g['approved_by'])
        result=await company.post(COMPANY,json={**body,'id':str(uuid4())})
        assert result.status_code==422 and '二十' in result.text
        assert sum(g['active'] for g in (await company.get(COMPANY)).json()['items'])==20
