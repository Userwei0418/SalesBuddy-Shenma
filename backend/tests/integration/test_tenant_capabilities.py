from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave
from sales_backend.db import set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_platform_control import provision, activate, tenant, transition, PASSWORD, BASE
# Explicit re-export keeps the shared pytest fixture discoverable in this module.
from tests.integration.test_platform_control import platform as platform
from tests.integration.test_authorization_store import resolve
from tests.integration.test_sales_opportunity_permissions import account_fixture

pytestmark=pytest.mark.asyncio
repo=AuthorizationRepository()

async def settings(client,identifier):
 response=await client.get(BASE+'/tenants/'+identifier+'/modules')
 assert response.status_code==200,response.text
 return response.json()

async def change(client,current,code,enabled,expires=None):
 modules=[{'code':m['code'],'enabled':enabled if m['code']==code else m['enabled'],
   'expires_at':expires if m['code']==code else m['expires_at']} for m in current['modules']]
 return await client.post(BASE+'/tenants/'+current['workspace_id']+'/modules',json={
  'version_no':current['version_no'],'modules':modules,'reason':'隔离模块验收'})

async def test_new_tenant_defaults_limits_cas_and_existing_session_revalidation(platform,connection):
 control,_=platform
 body,_,created=await provision(control)
 current=await settings(control,created['id'])
 assert not next(m for m in current['modules'] if m['code']=='ai')['enabled']
 assert (await change(control,current,'core',False)).status_code==422
 await activate(control,body,created)
 assert (await transition(control,await tenant(control,created['id']),'active')).status_code==200
 async with await client_for(connection) as company:
  response=await company.post('/api/v1/console/auth/login',json={'workspace':body['company_code'],'account_code':'ADMIN','password':PASSWORD})
  assert response.status_code==200,response.text
  company.headers['Authorization']='Bearer '+response.json()['access_token']
  assert (await company.get('/api/v1/console/permissions/me')).json()['permissions']['customer.read']
  current=await settings(control,created['id'])
  disabled=await change(control,current,'customer',False)
  assert disabled.status_code==200,disabled.text
  assert (await change(control,current,'customer',True)).status_code==409
  assert not (await company.get('/api/v1/console/permissions/me')).json()['permissions']['customer.read']
  assert (await company.get('/api/v1/console/customers')).status_code==403
  assert (await company.post(BASE+'/tenants/'+created['id']+'/modules',json={'version_no':2,'modules':[],'reason':'公司越权'})).status_code in (401,422)
  # Past expiry disables access independently of the configured enabled flag.
  current=await settings(control,created['id'])
  expired=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
  assert (await change(control,current,'customer',True,expired)).status_code==200
  assert not (await company.get('/api/v1/console/permissions/me')).json()['permissions']['customer.read']
  current=await settings(control,created['id'])
  assert (await change(control,current,'customer',True)).status_code==200
  assert (await company.get('/api/v1/console/permissions/me')).json()['permissions']['customer.read']
 async with await client_for(connection) as other:
  await sign_in(other,'ADMIN001')
  assert (await other.get('/api/v1/console/permissions/me')).json()['permissions']['customer.read']
 audit=await control.get(BASE+'/audit')
 assert 'tenant.modules' in audit.text

async def test_expiring_overrides_and_role_assignments_leave_native_identity_unchanged(connection):
 user,teams,admin=await account_fixture(connection,['fde'])
 future=datetime.now(timezone.utc)+timedelta(hours=1)
 past=datetime.now(timezone.utc)-timedelta(hours=1)
 role=await repo.save_role(connection,None,RoleSave(name='限时商机创建',reason='隔离验收',expires_at=future,
  permissions=[dict(permission='opportunity.create')]).model_dump(mode='json'))
 await repo.save_account(connection,user['id'],AccountAuthorizationSave(version_no=0,reason='授权有效',
  roles=[dict(role_id=role['id'],scope='self',expires_at=future)]).model_dump(mode='json'))
 await resolve(connection,admin,user,'fde')
 assert (await repo.effective(connection)).allows('opportunity.create')
 before=(await repo.snapshot(connection))['permission_version']
 await set_request_context(connection,admin)
 await repo.save_account(connection,user['id'],AccountAuthorizationSave(version_no=1,reason='已到期范围',
  roles=[dict(role_id=role['id'],scope='self',expires_at=past)]).model_dump(mode='json'))
 preview=await repo.account(connection,user['id'])
 assert preview['roles'][0]['expires_at']
 await resolve(connection,admin,user,'fde')
 assert not (await repo.effective(connection)).allows('opportunity.create')
 assert before!=(await repo.snapshot(connection))['permission_version']
 await set_request_context(connection,admin)
 await repo.save_account(connection,user['id'],AccountAuthorizationSave(version_no=2,reason='单独临时权限',
  overrides=[dict(permission='opportunity.create',scope='self',effect='allow',expires_at=past)]).model_dump(mode='json'))
 await resolve(connection,admin,user,'fde')
 assert not (await repo.effective(connection)).allows('opportunity.create')
 assert (await repo.effective(connection)).allows('profile.fde_read')

async def test_role_expiry_and_builtin_administrator_safeguard(connection):
 user,_,admin=await account_fixture(connection,['fde'])
 role=await repo.save_role(connection,None,RoleSave(name='已结束的授权',reason='期限验收',
  expires_at=datetime.now(timezone.utc)-timedelta(hours=1),permissions=[dict(permission='opportunity.create')]).model_dump(mode='json'))
 await repo.save_account(connection,user['id'],AccountAuthorizationSave(version_no=0,reason='过期角色无效',
  roles=[dict(role_id=role['id'],scope='self')]).model_dump(mode='json'))
 await resolve(connection,admin,user,'fde')
 assert not (await repo.effective(connection)).allows('opportunity.create')
 await set_request_context(connection,admin)
 native=next(r for r in await repo.roles(connection) if r['builtin_role_code']=='administrator')
 import asyncpg
 with pytest.raises(asyncpg.InvalidParameterValueError,match='不能设置到期'):
  async with connection.transaction():
   await repo.save_role(connection,native['id'],RoleSave(name=native['name'],reason='不能切断管理员',version_no=native['version_no'],
    expires_at=datetime.now(timezone.utc)+timedelta(hours=1),permissions=native['permissions']).model_dump(mode='json'))

async def test_closed_module_blocks_job_claim_and_late_result_lease(platform,connection,monkeypatch):
 from dataclasses import replace
 from sales_backend.main import app
 # Module renewal now requires static setup; no network requests are made.
 monkeypatch.setattr(app.state,'settings',replace(app.state.settings,
  senseaudio_base_url='https://synthetic-provider.invalid',
  senseaudio_api_key='synthetic-setup-key',llm_model='synthetic'))
 control,_=platform
 current_ws=str(await connection.fetchval("SELECT id FROM platform.workspace WHERE external_workspace_id='demo-sales-workspace'"))
 runtime=await connection.fetchval('SELECT current_user')
 await connection.execute('RESET ROLE')
 job=uuid4()
 await connection.execute("INSERT INTO ops.job(id,workspace_id,job_type,payload) VALUES($1,$2,'agent.run','{}')",job,UUID(current_ws))
 await connection.execute(f'SET LOCAL ROLE "{runtime}"')
 current=await settings(control,current_ws)
 assert (await change(control,current,'ai',False)).status_code==200
 assert not await connection.fetchval("SELECT id FROM ops.claim_job('module-test',30,ARRAY['agent.run'],false)")
 current=await settings(control,current_ws)
 assert (await change(control,current,'ai',True)).status_code==200
 claimed=await connection.fetchrow("SELECT * FROM ops.claim_job('module-test',30,ARRAY['agent.run'],false)")
 assert claimed['id']==job
 current=await settings(control,current_ws)
 assert (await change(control,current,'ai',False)).status_code==200
 await connection.execute('RESET ROLE')
 stopped=await connection.fetchrow('SELECT status,lease_token FROM ops.job WHERE id=$1',job)
 assert stopped['status']=='failed' and stopped['lease_token'] is None
 await connection.execute(f'SET LOCAL ROLE "{runtime}"')
