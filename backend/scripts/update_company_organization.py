"""Apply an explicitly reviewed organization plan atomically using account services.

Plan contains company, administrator (in that company), optional source_company, renames [{old,name}], accounts [{old,email}], and
new_accounts [{name,email,department,role}]. Password is stdin only.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from uuid import uuid4
import asyncpg
from sales_backend.contracts.operations import AccountCreate, AccountUpdate, DepartmentSave
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.request_metadata import RequestMetadata, request_metadata

async def run(connection, plan, apply=False, expected=None, password=''):
    identities = IdentityRepository()
    source = await identities.find_maintenance_administrator(connection,
        workspace_external_id=plan.get('source_company', plan['company']), account_code=plan['administrator'])
    if not source:
        raise ValueError('Existing maintenance administrator required')
    await set_request_context(connection, source.context)
    wid = await connection.fetchval('SELECT id FROM platform.workspace WHERE external_workspace_id=$1 AND status=\'active\' AND deleted_at IS NULL', plan['company'])
    if str(wid) == source.context.workspace_id:
        actor = source.context
    else:
        delegated = await connection.fetchval('SELECT security.company_management_actor($1::uuid)', wid)
        if not delegated:
            raise PermissionError('Company grant required')
        actor = identities._actor(delegated).context
    await set_request_context(connection, actor)
    repo, service = OperationsAccountRepository(), OperationsAccountService()
    await repo.lock_workspace(connection, actor.workspace_id)
    org = await repo.organization(connection)
    accounts = {r['account_code']: r for r in org['accounts']}
    teams = {t['name']: t for t in org['departments']}
    for item in plan['renames']:
        assert item['old'] in teams and item['name'] not in teams, 'Department plan mismatch'
    all_emails = [i['email'].upper() for i in plan['accounts'] + plan['new_accounts']]
    assert len(all_emails) == len(set(all_emails)) and not (set(all_emails) & set(accounts)), 'Duplicate account'
    for item in plan['accounts']:
        assert item['old'] in accounts and not accounts[item['old']]['platform_managed'], 'Account plan mismatch'
        assert accounts[item['old']]['display_name'] == item['name'], 'Person mismatch'
    versions = {'accounts': [(r['id'],r['version_no'],r['account_code']) for r in org['accounts']],
                'departments': [(r['id'],r['version_no'],r['name']) for r in org['departments']], 'plan': plan}
    digest = hashlib.sha256(json.dumps(versions,sort_keys=True).encode()).hexdigest()
    result = dict(applied=False, plan_sha256=digest, renamed_departments=len(plan['renames']),
                  renamed_accounts=len(plan['accounts']), created_accounts=len(plan['new_accounts']))
    if not apply:
        return result
    if expected != digest:
        raise ValueError('Plan/state changed; preview again')
    hashes = await connection.fetch('SELECT user_ref_id::text,password_hash FROM platform.password_credential WHERE workspace_id=$1::uuid', actor.workspace_id)
    for item in plan['renames']:
        t = teams[item['old']]
        body = DepartmentSave(**{**t,'name':item['name']}).model_dump()
        await repo.save_department(connection, actor, t['id'], body)
    for item in plan['accounts']:
        r = accounts[item['old']]
        body = AccountUpdate(**{**r, 'account_code':item['email']}).model_dump()
        await service.update(connection, actor, r['id'], body)
        assert not await connection.fetchval('SELECT security.password_candidate($1,$2,NULL)', plan['company'],item['old'])
        assert await connection.fetchval('SELECT id::text FROM platform.user_ref WHERE account_code=$1',item['email'].upper()) == r['id']
    for item in plan['new_accounts']:
        t = teams[item['department']]
        body = AccountCreate(account_code=item['email'],display_name=item['name'],team_id=t['id'],roles=[item['role']],temporary_password=password)
        await service.create(connection,actor,body.model_dump(exclude={'temporary_password'}),password)
    for row in hashes:
        assert await connection.fetchval('SELECT password_hash FROM platform.password_credential WHERE user_ref_id=$1::uuid',row['user_ref_id']) == row['password_hash'], 'Existing password changed'
    await connection.execute("""INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
        object_type,object_id,object_label,request_id,after_snapshot,changed_fields)
        VALUES($1::uuid,$2::uuid,'administrator','organization.email_transition','platform','workspace',$1::uuid,
        '按审核名单调整部门与邮箱账号',$3::uuid,$4::jsonb,ARRAY['account_code','department_name','new_accounts'])""",
        actor.workspace_id,actor.user_id,str(uuid4()),result)
    return {**result,'applied':True,'existing_passwords_preserved':True,'old_logins_disabled':True}

async def main(args):
    request_metadata.set(RequestMetadata(request_id=str(uuid4()),user_agent='authorized-organization-maintenance'))
    plan=json.load(open(args.plan))
    c=await asyncpg.connect(host='/tmp',database='sales_saas',user='postgres')
    await _initialize_connection(c)
    try:
        async with c.transaction():
            result=await run(c,plan,args.apply,args.expected_plan,sys.stdin.readline().rstrip('\r\n') if args.apply else '')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        await c.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',required=True); p.add_argument('--apply',action='store_true');p.add_argument('--expected-plan')
    asyncio.run(main(p.parse_args()))
