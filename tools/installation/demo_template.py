"""Initialize new demos or explicitly reset registered demo business offline.

Reset requires a reviewed plan, existing identity mapping and exact full backup.
Accounts, configuration, original files and historical system audit are preserved.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import traceback
import uuid
from zoneinfo import ZoneInfo

if __name__ == '__main__': sys.dont_write_bytecode = True

import asyncpg

from instance_backup import check_record, wait_for_offline
from instance_database import database_config
from instance_files import InstanceError, atomic_json, locked, no_pending_upgrade, read_private, root_path, source_check
from instance_process import service_lock
from release_bundle import json_bytes
from runtime_roles import validate_policy, verify
from demo_business import identity, seed_business


def template(root):
    raw=(root/'release/tools/installation/demo_template.json').read_bytes()
    value=json.loads(raw)
    if value.get('format')!='salesbuddy-demo-template-v1' or value.get('synthetic') is not True:
        raise InstanceError('Only a reviewed synthetic demo template is supported')
    if value.get('id')!='sales-cycle-v1' or len(value.get('customers',[]))!=4 or len(value.get('accounts',[]))!=4:
        raise InstanceError('Unsupported demo template revision')
    return value,hashlib.sha256(raw).hexdigest()


async def check_tenant(conn, workspace, company_code, value, digest, *, lock=False):
    row=await conn.fetchrow('''SELECT w.id,w.name,w.external_workspace_id,w.status,w.deleted_at,w.default_timezone,
        s.purpose,s.administrator_id,s.activated_at FROM platform.workspace w
        JOIN control.tenant_setup s ON s.workspace_id=w.id WHERE w.id=$1'''+(' FOR UPDATE OF w,s' if lock else ''),workspace)
    if (not row or row['external_workspace_id']!=company_code or row['purpose']!='demo'
            or row['status']!='active' or row['deleted_at'] or not row['activated_at']):
        raise InstanceError('Requires an activated demo tenant and its exact company code; legacy/internal/customer/suspended tenants are refused')
    previous=await conn.fetchrow('SELECT * FROM control.demo_template_receipt WHERE workspace_id=$1',workspace)
    if previous:
        if previous['template_id']!=value['id'] or previous['template_sha256']!=digest:
            raise InstanceError('A different template was already applied; automatic reset is not supported')
        return row,previous
    users=await conn.fetch('SELECT id,status,deleted_at FROM platform.user_ref WHERE workspace_id=$1',workspace)
    if len(users)!=1 or users[0]['id']!=row['administrator_id'] or users[0]['status']!='active' or users[0]['deleted_at']:
        raise InstanceError('New empty demo tenant must contain only its initial active administrator')
    # No soft-delete exemption: retained history must never be silently replaced.
    tables=await conn.fetch('''SELECT n.nspname AS schema,c.relname AS name FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE c.relkind='r' AND a.attname='workspace_id' AND NOT a.attisdropped
        AND n.nspname=ANY(ARRAY['crm','activity','workflow','insight','agent'])''')
    for item in tables:
        identifier='"'+item['schema'].replace('"','""')+'"."'+item['name'].replace('"','""')+'"'
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM '+identifier+' WHERE workspace_id=$1)',workspace):
            raise InstanceError('Demo tenant already contains business data or history; nothing was reset')
    for table in ('platform.team','platform.team_membership','config.feishu_connection',
                  'config.agent_runtime_config','config.model_api_test','config.model_api_release','ops.job'):
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE workspace_id=$1)',workspace):
            raise InstanceError('Demo tenant contains organization, integrations or jobs; nothing was overwritten')
    for module in ('customer','opportunity','visit','task','insight'):
        if not await conn.fetchval('SELECT security.workspace_module_enabled($1,$2)',workspace,module):
            raise InstanceError('Demo template requires enabled business modules: '+module)
    quota=await conn.fetchrow('SELECT account_limit,active_accounts FROM control.tenant_account_quota WHERE workspace_id=$1',workspace)
    if quota['account_limit'] is not None and quota['active_accounts']+len(value['accounts'])>quota['account_limit']:
        raise InstanceError('Demo accounts exceed the tenant account quota; change the quota explicitly in platform management')
    return row,None


def credential_receipt(root, data, workspace, value, digest):
    folder=root/'private/demo-onboarding';folder.mkdir(mode=0o700,exist_ok=True);root_path(folder)
    path=folder/(str(workspace)+'.json')
    if not path.exists():
        receipt={'format':'salesbuddy-demo-onboarding-v1','instance_id':data['instance_id'],'workspace_id':str(workspace),
                 'template_sha256':digest,'accounts':[{'account_code':a['code'],'password':secrets.token_urlsafe(24),
                 'must_change_password':True} for a in value['accounts']]}
        atomic_json(path,receipt,replace=False)
    receipt=read_private(path)
    if (receipt.get('format')!='salesbuddy-demo-onboarding-v1' or receipt.get('instance_id')!=data['instance_id']
        or receipt.get('workspace_id')!=str(workspace) or receipt.get('template_sha256')!=digest
        or [a.get('account_code') for a in receipt.get('accounts',[])]!=[a['code'] for a in value['accounts']]
        or any(not isinstance(a.get('password'),str) or len(a['password'])<24 for a in receipt['accounts'])):
        raise InstanceError('Onboarding receipt does not belong to this template/instance; no credentials were reset')
    return path,receipt,hashlib.sha256(json_bytes(receipt)).hexdigest()


async def seed(conn, row, value, as_of, credentials):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/src'))
    from sales_backend.auth.passwords import encode_password
    ws=row['id']; marker=json.dumps({'demo_template':value['id'],'synthetic':True})
    # Qualification guards resolve company membership from this transaction.
    # Leave actor/user unset: the maintenance receipt records the actual executor.
    await conn.execute("SELECT set_config('app.workspace_id',$1,true)",str(ws))
    teams={t['key']:identity(ws,'team:'+t['key']) for t in value['teams']}
    users={a['code']:identity(ws,'user:'+a['code']) for a in value['accounts']}
    passwords={a['account_code']:a['password'] for a in credentials['accounts']}
    for team in value['teams']:
        await conn.execute('INSERT INTO platform.team(id,workspace_id,code,name,attributes) VALUES($1,$2,$3,$4,$5::jsonb)',
            teams[team['key']],ws,'demo-'+team['key'],team['name'],marker)
    for account in value['accounts']:
        uid=users[account['code']];tid=teams[account['team']]
        await conn.execute('''INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name,attributes)
            VALUES($1,$2,$3,$4,$5,$6::jsonb)''',uid,ws,'demo:'+str(uid),account['code'],account['name'],marker)
        await conn.execute('''INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id)
            VALUES($1,$2,$3,$4,$5)''',ws,uid,account['role'],account['scope'],tid)
        await conn.execute('''INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
            VALUES($1,$2,$3,$4)''',ws,uid,tid,account['role'])
        await conn.execute('''INSERT INTO platform.password_credential(workspace_id,user_ref_id,password_hash,must_change_password)
            VALUES($1,$2,$3,true)''',ws,uid,encode_password(passwords[account['code']]))
    form_definition=await conn.fetchval('''INSERT INTO config.form_definition(workspace_id,form_code,name,object_type)
        VALUES($1,'demo-sales-cycle-v1','合成演示跟进','visit') RETURNING id''',ws)
    form=await conn.fetchval("INSERT INTO config.form_version(form_definition_id,version_no,status) VALUES($1,1,'active') RETURNING id",form_definition)
    summary=await seed_business(conn,row,value,as_of,users,teams,form)
    # No external configuration is copied or enabled. In-app flows remain real.
    await conn.execute("UPDATE control.tenant_module SET enabled=false,source='demo-template-no-external-sync' WHERE workspace_id=$1 AND module_code='feishu'",ws)
    await conn.execute("UPDATE platform.workspace SET attributes=attributes||$2::jsonb,version_no=version_no+1 WHERE id=$1",ws,marker)
    return {**summary,'teams':len(teams),'accounts':len(users),'feishu_enabled':False,'existing_accounts_modified':0}


async def execute(root, command, workspace, company_code, reason, as_of, *, reset=None):
    no_pending_upgrade(root);data,_=source_check(root);value,digest=template(root)
    conn=await asyncpg.connect(database=data['database']['name'],**database_config(data,admin=True,password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD')))
    try:
        if not await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'):
            raise InstanceError('Demo initialization requires the explicit maintenance administrator')
        await check_record(conn,data)
        await verify(conn,data['roles'],validate_policy(json.loads((root/'release/tools/installation/runtime_permissions.json').read_text())))
        await wait_for_offline(conn)
        async with conn.transaction(isolation='serializable'):
            if command in ('register-demo','plan-reset','apply-reset'):
                from demo_reset import apply_plan, make_plan, mapping_value, register, sha
                reset=reset or {}
                if not isinstance(reset.get('version'),int) or reset['version']<1:
                    raise InstanceError('Explicit current --version is required')
                if command!='plan-reset' and (not reason or not 3<=len(reason.strip())<=500):
                    raise InstanceError('Provide a maintenance reason (3-500 characters)')
                if command=='register-demo':
                    if not reset.get('administrator'): raise InstanceError('Explicit --administrator UUID is required')
                    return await register(conn,workspace,company_code,reset['version'],reset['administrator'],reason.strip())
                if not reset.get('mapping') or not reset.get('operation') or not as_of:
                    raise InstanceError('Explicit --mapping, --operation and --as-of are required')
                mapping=mapping_value(read_private(root_path(reset['mapping'].parent)/reset['mapping'].name))
                arguments=(conn,root,data,value,digest,workspace,company_code,reset['version'],reset['operation'],mapping,as_of)
                if command=='plan-reset':
                    plan,*_=await make_plan(*arguments)
                    return {'status':'ready','plan':plan,'plan_sha256':sha(plan),'database_modified':False}
                import re
                if any(not re.fullmatch('[a-f0-9]{64}',reset.get(key) or '') for key in ('plan_sha','backup_sha')) or not reset.get('backup'):
                    raise InstanceError('Apply requires --plan-sha256, --backup and --backup-sha256')
                result=await apply_plan(*arguments,reason.strip(),reset['plan_sha'],root_path(reset['backup']),reset['backup_sha'])
                source_check(root)
                return result
            row,previous=await check_tenant(conn,workspace,company_code,value,digest,lock=True)
            if previous:
                return {'status':'already_initialized','workspace_id':str(workspace),'summary':json.loads(previous['summary']),
                        'credentials_reset':False,'business_rows_changed':False}
            as_of=as_of or datetime.now(ZoneInfo(row['default_timezone'])).date()
            preview={'workspace_id':str(workspace),'company_code':company_code,'company_name':row['name'],
                'template':value['id'],'template_sha256':digest,'as_of':as_of.isoformat(),'new_accounts':4,'customers':4,
                'opportunities':4,'visits':4,'tasks':5,'requires_offline':True,'external_sync':'disabled','reset_supported':False}
            if command=='plan': return {'status':'ready','preview':preview,'database_modified':False}
            if not reason or not 3<=len(reason.strip())<=500: raise InstanceError('Provide an initialization reason (3-500 characters)')
            path,credentials,receipt_digest=credential_receipt(root,data,workspace,value,digest)
            summary=await seed(conn,row,value,as_of,credentials)
            await conn.execute('''INSERT INTO control.demo_template_receipt(workspace_id,template_id,template_sha256,
                source_revision,credential_receipt_sha256,as_of,initialized_by,reason,summary)
                VALUES($1,$2,$3,$4,$5,$6,current_user,$7,$8::jsonb)''',workspace,value['id'],digest,data['revision'],receipt_digest,as_of,reason.strip(),json.dumps(summary))
            await conn.execute('''INSERT INTO ops.audit_log(workspace_id,action_code,module_code,object_type,object_id,
                object_label,after_snapshot,result_code) VALUES($1,'demo_template.initialize','installation','workspace',$1,
                '合成演示模板初始化',$2::jsonb,'success')''',workspace,json.dumps({'template':value['id'],'sha256':digest,
                'revision':data['revision'],'reason':reason.strip(),'executor':'explicit installation maintenance role','summary':summary}))
        return {'status':'initialized','preview':preview,'summary':summary,'onboarding_credentials_file':str(path),
                'first_login_password_change_required':True,'reset_supported':False}
    finally: await conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','apply','register-demo','plan-reset','apply-reset'));parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--workspace',type=uuid.UUID,required=True)
    parser.add_argument('--company-code',required=True,help='Exact company code from platform management')
    parser.add_argument('--reason');parser.add_argument('--as-of',type=date.fromisoformat)
    parser.add_argument('--version',type=int,help='Current workspace version, required for demo registration/reset')
    parser.add_argument('--administrator',type=uuid.UUID,help='Existing company administrator UUID for explicit demo registration')
    parser.add_argument('--mapping',type=Path,help='Private JSON mapping existing accounts, teams and visit form')
    parser.add_argument('--operation',type=uuid.UUID,help='New reset batch UUID, reused only for exact retries')
    parser.add_argument('--plan-sha256');parser.add_argument('--backup',type=Path);parser.add_argument('--backup-sha256')
    args=parser.parse_args();root=root_path(args.root)
    with locked(root),ExitStack() as locks:
        for service in ('api','worker','feishu'): locks.callback(os.close,service_lock(root,service))
        result=asyncio.run(execute(root,args.command,args.workspace,args.company_code,args.reason,args.as_of,reset={
            'version':args.version,'administrator':args.administrator,'mapping':args.mapping,'operation':args.operation,
            'plan_sha':args.plan_sha256,'backup':args.backup,'backup_sha':args.backup_sha256}))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except (InstanceError,ValueError) as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        location=','.join(Path(f.filename).name+':'+str(f.lineno) for f in traceback.extract_tb(exc.__traceback__))
        raise SystemExit('Demo maintenance was not acknowledged ('+type(exc).__name__+'; sqlstate='+str(getattr(exc,'sqlstate',None))+'; '+location+'); transaction/receipt retained for diagnosis; retry the exact operation to resolve its durable outcome') from None
