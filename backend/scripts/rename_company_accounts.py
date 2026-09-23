"""Preview/apply an explicit company account-name plan; no passwords are read or reset.

Plan: company, source_company, administrator, accounts [{id, old, new, email}].
The apply requires the exact preview digest and runs in a single transaction.
"""
import argparse
import asyncio
import hashlib
import json
from uuid import uuid4
import asyncpg
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.contracts.operations import AccountCode, AccountContact
from pydantic import TypeAdapter


async def run(connection, plan, apply=False, expected=None):
    identities = IdentityRepository()
    source = await identities.find_maintenance_administrator(
        connection, workspace_external_id=plan['source_company'], account_code=plan['administrator'])
    if not source:
        raise PermissionError('Existing maintenance administrator required')
    await set_request_context(connection, source.context)
    if plan['company'] == plan['source_company']:
        actor = source.context
    else:
        wid = await connection.fetchval("SELECT id FROM platform.workspace WHERE external_workspace_id=$1 AND status='active' AND deleted_at IS NULL", plan['company'])
        delegated = await connection.fetchval('SELECT security.company_management_actor($1::uuid)', wid)
        if not delegated:
            raise PermissionError('Existing company grant required')
        actor = identities._actor(delegated).context
    await set_request_context(connection, actor)
    repo, service = OperationsAccountRepository(), OperationsAccountService()
    await repo.lock_workspace(connection, actor.workspace_id)
    org = await repo.organization(connection)
    accounts = {r['id']:r for r in org['accounts']}
    items = plan['accounts']
    if not items or len({r['id'] for r in items}) != len(items):
        raise ValueError('Empty or duplicate rename plan')
    desired = []
    for item in items:
        row = accounts.get(item['id'])
        if not row or row['platform_managed'] or row['account_code'] != item['old']:
            raise ValueError('Account plan does not match current company')
        new = TypeAdapter(AccountCode).validate_python(item['new']).strip().upper()
        email = AccountContact(email=item['email']).email
        if new == row['account_code'] or (row['email'] and row['email'] != email):
            raise ValueError('No-op rename or existing contact email differs')
        if any(r['id'] != row['id'] and (r['account_code'] == new or r['phone_number'] == new) for r in accounts.values()):
            raise ValueError('Account or phone collision')
        desired.append(new)
    if len(set(desired)) != len(desired):
        raise ValueError('Duplicate target account names')
    snapshot = [{k:r[k] for k in ('id','version_no','account_code','email','phone_number','status','roles','memberships')} for r in org['accounts']]
    digest = hashlib.sha256(json.dumps({'plan':plan,'snapshot':snapshot},sort_keys=True,default=str).encode()).hexdigest()
    result = {'applied':False,'plan_sha256':digest,'renamed_accounts':len(items)}
    if not apply:
        return result
    if expected != digest:
        raise ValueError('Plan/state changed; preview again')
    for item in items:
        row = accounts[item['id']]
        await service.rename_account(connection, actor, row['id'], row['version_no'], item['new'], item['email'])
        if row['status'] == 'active' and row['has_password']:
            identity = await connection.fetchrow('SELECT * FROM security.password_login_identifier($1,$2)',plan['company'],item['new'].lower())
            if not identity or identity['account_code'] != item['new'].upper():
                raise RuntimeError('Renamed login identity could not be resolved')
            if await connection.fetchval('SELECT count(*) FROM security.password_login_identifier($1,$2)',plan['company'],item['old']):
                raise RuntimeError('Old login identity still resolves')
    after = {r['id']:r for r in (await repo.organization(connection))['accounts']}
    for uid,row in accounts.items():
        for key in ('display_name','status','roles','memberships','phone_number','has_password'):
            if row[key] != after[uid][key]:
                raise RuntimeError('Unrelated account state changed: '+key)
    await connection.execute("""INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
        object_type,object_id,object_label,request_id,after_snapshot,changed_fields)
        VALUES($1::uuid,$2::uuid,'administrator','organization.account_name_transition','platform','workspace',$1::uuid,
        'Approved account-name transition',$3::uuid,$4::jsonb,ARRAY['account_code','email'])""",
        actor.workspace_id,actor.user_id,str(uuid4()),result)
    return {**result,'applied':True,'identity_and_memberships_preserved':True,'old_logins_disabled':True}


async def main(args):
    with open(args.plan) as f:
        plan = json.load(f)
    c = await asyncpg.connect(host=args.socket,database=args.database,user=args.db_user)
    await _initialize_connection(c)
    try:
        async with c.transaction(readonly=not args.apply):
            await c.execute('SET LOCAL ROLE sales_runtime')
            if await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user'):
                raise PermissionError('Restricted runtime role required')
            result = await run(c,plan,args.apply,args.expected_plan)
        print(json.dumps(result))
    finally:
        await c.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',required=True)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--expected-plan')
    p.add_argument('--socket',default='/var/run/postgresql')
    p.add_argument('--database',default='shenma_sales')
    p.add_argument('--db-user',default='postgres')
    asyncio.run(main(p.parse_args()))
