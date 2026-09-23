from pathlib import Path
import sys
from uuid import uuid4
import pytest
from tests.integration.test_optional_phone_login import create,edit,login,PHONE
from tests.integration.test_operations_api import client_for,sign_in

sys.path.insert(0,str(Path(__file__).parents[2]/'scripts'))
from rename_company_accounts import run
pytestmark=pytest.mark.asyncio


async def test_account_name_and_phone_share_identity_but_contact_email_is_not_a_login(connection):
    async with await client_for(connection) as admin,await client_for(connection) as user:
        await sign_in(admin)
        name='Member_'+uuid4().hex[:8]
        body,created=await create(admin,account=name)
        uid=created.json()['id']
        email=name+'@example.com'
        assert (await edit(admin,uid,body,1,email=email)).status_code==200
        by_name=await login(user,name.lower());by_phone=await login(user,PHONE)
        assert by_name.status_code==by_phone.status_code==200
        assert by_name.json()['actor']==by_phone.json()['actor']
        assert (await login(user,email)).status_code==401
        assert (await edit(admin,uid,body,2)).status_code==200
        org=(await admin.get('/api/v1/console/organization')).json()
        assert next(r for r in org['accounts'] if r['id']==uid)['email']==email
        assert (await edit(admin,uid,body,3,email='')).status_code==200
        org=(await admin.get('/api/v1/console/organization')).json()
        assert next(r for r in org['accounts'] if r['id']==uid)['email'] is None
        assert (await login(user,name)).status_code==200


async def test_previewed_rename_preserves_password_phone_roles_and_revokes_old_sessions(connection):
    async with await client_for(connection) as admin,await client_for(connection) as user:
        await sign_in(admin,'ADMIN001')
        body,created=await create(admin)
        assert created.status_code==201
        uid=created.json()['id'];old=body['account_code'].upper();new='MEMBER_'+uuid4().hex[:8].upper()
        signed=await login(user,old);session=signed.json()
        plan=dict(company='demo-sales-workspace',source_company='demo-sales-workspace',administrator='ADMIN001',accounts=[dict(id=uid,old=old,new=new,email=body['account_code'])])
        preview=await run(connection,plan)
        assert not preview['applied']
        with pytest.raises(ValueError,match='changed'):
            await run(connection,plan,True,'stale')
        result=await run(connection,plan,True,preview['plan_sha256'])
        assert result['applied'] and result['identity_and_memberships_preserved']
        assert (await login(user,old)).status_code==401
        renamed=await login(user,new.lower());phone=await login(user,PHONE)
        assert renamed.status_code==phone.status_code==200
        assert renamed.json()['actor']['user_id']==phone.json()['actor']['user_id']==uid
        assert renamed.json()['actor']['role']==session['actor']['role']
        assert (await user.post('/api/v1/auth/refresh',json={'refresh_token':session['refresh_token']})).status_code==401
        assert (await login(user,body['account_code'])).status_code==401


async def test_rename_plan_rejects_collisions_and_keeps_accounts_unchanged(connection):
    async with await client_for(connection) as admin,await client_for(connection) as user:
        await sign_in(admin,'ADMIN001')
        body,created=await create(admin)
        plan=dict(company='demo-sales-workspace',source_company='demo-sales-workspace',administrator='ADMIN001',accounts=[dict(id=created.json()['id'],old=body['account_code'].upper(),new='XS001',email=body['account_code'])])
        with pytest.raises(ValueError,match='collision'):
            await run(connection,plan)
        assert (await login(user,body['account_code'])).status_code==200
