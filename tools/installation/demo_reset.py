"""Offline reset of explicitly registered demo business; never an HTTP privilege."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import uuid

from demo_business import seed_business
from demo_reset_catalog import CLEARED, HISTORY_GUARDS, clear_business, load_catalog, quoted, sha, structure, toggle_reset_triggers, validate_structure
from instance_backup import digest, inventory, material_tree, originals, validate_backup
from instance_files import InstanceError

# Unknown/possibly retried external outcomes require operator reconciliation.
# Nothing here sends, retries, cancels or pretends to complete external work.
QUIESCENT={
 'ops.job':("status",('succeeded','cancelled','dead_letter')),
 'ops.outbox_event':('status',('published','dead_letter')),
 'ops.feishu_event':('status',('succeeded','dead_letter')),
 'ops.feishu_delivery':('status',('sent',)),
 'workflow.notification_delivery':('status',('sent','delivered','cancelled')),
 'insight.business_advice':('status',('succeeded','failed','superseded')),
 'insight.customer_risk_assessment':('status',('succeeded','failed','superseded')),
 'insight.sales_competency_review':('status',('succeeded','failed')),
 'ops.crm_import_batch':('status',('applied','cancelled')),
 'agent.run':('status',('succeeded','failed','cancelled')),
 'agent.run_step':('status',('succeeded','failed','skipped')),
 'agent.model_invocation':('status',('succeeded','failed','cancelled')),
 'agent.tool_invocation':('status',('succeeded','failed','cancelled')),
 'agent.inference_operation':('status',('accepted','failed','cancelled')),
 'agent.artifact':('status',('applied','rejected','superseded')),
 'activity.visit_import':('status',('succeeded','failed')),
 'control.storage_allocation':('state',('stored','released')),
 'control.call_attempt':('state',('settled','released')),
 'control.call_event':('state',('used','released')),
}


def mapping_value(value):
    if not isinstance(value,dict) or set(value)!={'administrator','form_version','accounts','teams'}:
        raise InstanceError('Provide the exact reviewed demo account/team/form mapping')
    if (set(value['accounts'])!={'DEMO_SALES_A','DEMO_SALES_B','DEMO_MANAGER','DEMO_FDE'}
        or set(value['teams'])!={'east','west','fde'}): raise InstanceError('Incomplete demo identity mapping')
    result={k:str(uuid.UUID(value[k])) for k in ('administrator','form_version')}
    for k in ('accounts','teams'):
        result[k]={name:str(uuid.UUID(identifier)) for name,identifier in value[k].items()}
        if len(set(result[k].values()))!=len(result[k]): raise InstanceError('Demo personas/teams must map to distinct existing identities')
    return result


async def tenant(conn,workspace,company_code,version):
    row=await conn.fetchrow('''SELECT w.*,s.purpose,s.activated_at FROM platform.workspace w
      LEFT JOIN control.tenant_setup s ON s.workspace_id=w.id WHERE w.id=$1 FOR UPDATE OF w''',workspace)
    if (not row or row['external_workspace_id']!=company_code or row['version_no']!=version
        or row['status']!='active' or row['deleted_at']):
        raise InstanceError('Exact active company UUID/code/current version required')
    if row['purpose'] not in (None,'demo') or (row['purpose']=='demo' and not row['activated_at']):
        raise InstanceError('Internal/customer/unactivated tenant cannot be registered or reset as demo')
    return dict(row)


async def administrator(conn,workspace,user):
    if not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding r
      ON r.workspace_id=u.workspace_id AND r.user_ref_id=u.id WHERE u.workspace_id=$1 AND u.id=$2
      AND u.status='active' AND u.deleted_at IS NULL AND r.role_code='administrator' AND r.data_scope_code='workspace'
      AND r.valid_from<=clock_timestamp() AND r.valid_to>clock_timestamp())''',workspace,user):
        raise InstanceError('Mapping administrator must already be an active company administrator')


async def audit_context(conn,workspace,operation):
    for key,value in {'workspace_id':str(workspace),'user_ref_id':'','role_code':'system','request_id':str(operation),
                      'client_ip':'','user_agent':'salesbuddy-offline-demo-reset-v1'}.items():
        await conn.execute('SELECT set_config($1,$2,true)','app.'+key,value)


async def append_audit(conn,workspace,operation,action,details):
    await conn.execute('''INSERT INTO ops.audit_log(workspace_id,actor_role_code,action_code,module_code,object_type,
      object_id,object_label,request_id,user_agent,after_snapshot,result_code)
      VALUES($1,'system',$2,'installation','workspace',$1,'离线演示业务维护',$3,
      'salesbuddy-offline-demo-reset-v1',$4::jsonb,'success')''',workspace,action,operation,json.dumps(details))


async def register(conn,workspace,company_code,version,admin,reason):
    await tenant(conn,workspace,company_code,version);await administrator(conn,workspace,admin)
    old=await conn.fetchrow('SELECT * FROM control.demo_reset_registration WHERE workspace_id=$1',workspace)
    if old:
        if old['company_code']!=company_code or old['administrator_id']!=admin:
            raise InstanceError('Existing demo-purpose registration differs; it is not overwritten')
        return {'status':'already_registered','workspace_id':str(workspace),'database_modified':False}
    await conn.execute('''INSERT INTO control.demo_reset_registration(workspace_id,company_code,workspace_version,
      purpose,administrator_id,registered_by,reason) VALUES($1,$2,$3,'demo',$4,current_user,$5)''',workspace,company_code,version,admin,reason)
    await append_audit(conn,workspace,uuid.uuid4(),'demo_reset.register',{'company_code':company_code,
      'workspace_version':version,'purpose':'demo','administrator_id':str(admin),'reason':reason})
    return {'status':'registered','workspace_id':str(workspace),'purpose':'demo','accounts_or_configuration_changed':False}


async def check_mapping(conn,row,value,mapping):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/src'))
    from sales_backend.domain.authorization import ObjectScope
    from sales_backend.repositories.authorization import AuthorizationRepository
    ws=row['id'];admin=uuid.UUID(mapping['administrator']);await administrator(conn,ws,admin)
    users={k:uuid.UUID(v) for k,v in mapping['accounts'].items()}
    teams={k:uuid.UUID(v) for k,v in mapping['teams'].items()}
    for team in teams.values():
        if not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM platform.team WHERE workspace_id=$1 AND id=$2
          AND status='active' AND deleted_at IS NULL AND valid_from<=clock_timestamp() AND valid_to>clock_timestamp())''',ws,team):
            raise InstanceError('Mapping requires existing active same-company teams')
    for account in value['accounts']:
        uid=users[account['code']];tid=teams[account['team']]
        if not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.team_membership m
          ON m.workspace_id=u.workspace_id AND m.user_ref_id=u.id WHERE u.workspace_id=$1 AND u.id=$2 AND u.status='active'
          AND u.deleted_at IS NULL AND m.team_id=$3 AND m.valid_from<=clock_timestamp() AND m.valid_to>clock_timestamp()
          AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.workspace_id=u.workspace_id AND r.user_ref_id=u.id
           AND r.role_code=$4 AND r.valid_from<=clock_timestamp() AND r.valid_to>clock_timestamp()))''',ws,uid,tid,account['role']):
            raise InstanceError('Demo persona requires its existing active role and same-company team membership: '+account['code'])
        grants=[dict(r) for r in await conn.fetch('SELECT * FROM security.authorization_grants_for($1,$2)',ws,uid)]
        for grant in grants: grant['team_ids']=[str(x) for x in grant['team_ids']]
        auth=AuthorizationRepository.from_snapshot({'workspace_id':str(ws),'user_id':str(uid),'grants':grants})
        own=ObjectScope(str(ws),str(uid),str(tid),frozenset((str(uid),)))
        required={'sales':('customer.read','opportunity.create','visit.create','task.accept'),
                  'manager':('task.create_customer','task.create_daily','task.assign'),
                  'fde':('opportunity.read',)}[account['role']]
        if not auth.allows('access.mini_program') or any(not auth.allows(p,own) for p in required):
            raise InstanceError('Demo persona lacks effective permissions; no grants were changed: '+account['code'])
        if account['role']=='manager':
            for code in ('DEMO_SALES_A','DEMO_SALES_B'):
                target=ObjectScope(str(ws),str(users[code]),str(teams['east' if code.endswith('A') else 'west']))
                if not auth.allows('task.assign',target): raise InstanceError('Mapped manager cannot assign the demo tasks to both teams')
    form=uuid.UUID(mapping['form_version'])
    if not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM config.form_version v JOIN config.form_definition d
      ON d.id=v.form_definition_id WHERE v.id=$1 AND v.status='active' AND d.workspace_id=$2 AND d.object_type='visit')''',form,ws):
        raise InstanceError('Mapping requires an existing active company visit form; configuration is not replaced')
    return users,teams,form


async def quiescent(conn,workspace):
    for table,(column,terminal) in QUIESCENT.items():
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE workspace_id=$1 AND NOT '+column+'=ANY($2::text[]))',workspace,list(terminal)):
            raise InstanceError('Unfinished external/async work must be reconciled before reset: '+table)


async def row_digest(conn,table,where='',args=()):
    result=hashlib.sha256();count=0
    async for row in conn.cursor('SELECT md5(to_jsonb(t)::text) h FROM '+table+' t '+where+' ORDER BY 1',*args):
        result.update(row['h'].encode());count+=1
    return {'rows':count,'sha256':result.hexdigest()}


async def sequence_counters(conn,sequences):
    from database_inventory import sql_name
    result={}
    for item in sequences:
        name=item['nspname']+'.'+item['relname']
        row=await conn.fetchrow('SELECT last_value,is_called FROM '+sql_name(item['nspname'])+'.'+sql_name(item['relname']))
        result[name]=dict(row)
    return result


async def make_plan(conn,root,data,value,template_sha,workspace,company_code,version,operation,mapping,as_of):
    await conn.execute('SET LOCAL search_path=pg_catalog,public');await conn.execute("SET LOCAL timezone='UTC'")
    row=await tenant(conn,workspace,company_code,version)
    registered=await conn.fetchrow('SELECT * FROM control.demo_reset_registration WHERE workspace_id=$1',workspace)
    if (not registered or registered['company_code']!=company_code or str(registered['administrator_id'])!=mapping['administrator']):
        raise InstanceError('Explicit demo-purpose registration with exact company/admin is required before backup and reset')
    for module in ('customer','opportunity','visit','task','insight'):
        if not await conn.fetchval('SELECT security.workspace_module_enabled($1,$2)',workspace,module):
            raise InstanceError('Demo reset requires enabled business modules: '+module)
    users,teams,form=await check_mapping(conn,row,value,mapping)
    await quiescent(conn,workspace)
    baseline=validate_structure(await structure(conn),load_catalog(root))
    current=await inventory(conn)
    counts={table:await conn.fetchval('SELECT count(*) FROM '+quoted(table)+' WHERE workspace_id=$1',workspace) for table in sorted(CLEARED)}
    quota=await conn.fetchrow('SELECT stored_bytes,reserved_bytes FROM control.tenant_storage_quota WHERE workspace_id=$1',workspace)
    plan={'format':'salesbuddy-demo-reset-plan-v1','instance_id':data['instance_id'],'revision':data['revision'],
      'workspace_id':str(workspace),'company_code':company_code,'company_name':row['name'],'workspace_version':version,'operation_id':str(operation),
      'template_sha256':template_sha,'catalog_sha256':sha(baseline),'mapping':mapping,'as_of':as_of.isoformat(),
      'database_inventory_sha256':sha(current),'clear_counts':counts,'retained_original_bytes':dict(quota),
      'business_history_cleared':sorted(HISTORY_GUARDS),
      'preserved':'all accounts, organization, permissions, configuration, system audit, originals and AI usage',
      'requires_fresh_verified_backup':True,'external_events_generated':0,'new_business_ids':'operation UUID namespace'}
    row['administrator_id']=uuid.UUID(mapping['administrator'])
    return plan,row,users,teams,form,baseline,current


async def apply_plan(conn,root,data,value,template_sha,workspace,company_code,version,operation,mapping,as_of,reason,plan_sha,backup,backup_sha):
    previous=await conn.fetchrow('SELECT * FROM control.demo_reset_receipt WHERE operation_id=$1',operation)
    if previous:
        if (previous['workspace_id']!=workspace or previous['company_code']!=company_code or previous['workspace_version']!=version
          or previous['plan_sha256']!=plan_sha or previous['backup_sha256']!=backup_sha or previous['template_sha256']!=template_sha
          or previous['as_of']!=as_of or previous['reason']!=reason
          or json.loads(previous['summary']).get('mapping_sha256')!=sha(mapping)): raise InstanceError('Reset operation was committed with different inputs')
        return {'status':'already_reset','operation_id':str(operation),'summary':json.loads(previous['summary']),'database_modified':False}
    plan,row,users,teams,form,baseline,before=await make_plan(conn,root,data,value,template_sha,workspace,company_code,version,operation,mapping,as_of)
    if sha(plan)!=plan_sha: raise InstanceError('Reset plan is stale or changed; regenerate and review it before applying')
    manifest,_=validate_backup(root,backup)
    if digest(backup/'BACKUP.json')!=backup_sha or manifest['inventory']!=before:
        raise InstanceError('Backup is not the exact reviewed current database snapshot')
    materials=material_tree(root)
    if materials!={name:value for name,value in manifest['files'].items() if name.startswith('materials/')} :
        raise InstanceError('Material directory differs from verified backup')
    if await originals(conn,root,materials)!=manifest['originals']:
        raise InstanceError('Original material snapshot differs from verified backup')
    # All table locks are taken before destructive statements; concurrent startup
    # can never run through the partial reset. wait_for_offline is an extra check.
    from database_inventory import sql_name
    for table in sorted(before['tables']):
        await conn.execute('LOCK TABLE '+'.'.join(sql_name(x) for x in table.split('.'))+' IN ACCESS EXCLUSIVE MODE')
    from instance_backup import wait_for_offline
    await wait_for_offline(conn)
    counters_before=await sequence_counters(conn,before['sequences'])
    audit_high=await conn.fetchval('SELECT COALESCE(max(id),0) FROM ops.audit_log')
    old_audit=await row_digest(conn,'ops.audit_log','WHERE id<=$1',(audit_high,))
    others={table:await row_digest(conn,quoted(table),'WHERE workspace_id<>$1',(workspace,)) for table in sorted(CLEARED)}
    scope_before=[dict(r) for r in await conn.fetch('SELECT * FROM config.authorization_scope_revision ORDER BY workspace_id')]
    await audit_context(conn,workspace,operation)
    await toggle_reset_triggers(conn,baseline)
    await clear_business(conn,workspace)
    summary=await seed_business(conn,row,value,as_of,users,teams,form,generation=operation)
    await conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
    await toggle_reset_triggers(conn,baseline,restore=True)
    if await structure(conn)!=baseline: raise InstanceError('Reset changed database guards or dependencies')
    await append_audit(conn,workspace,operation,'demo_reset.apply',{'operation_id':str(operation),'plan_sha256':plan_sha,
      'backup_sha256':backup_sha,'reason':reason,'summary':summary,'cleared_rows':plan['clear_counts']})
    after=await inventory(conn)
    allowed=set(CLEARED)|{'ops.audit_log','config.authorization_scope_revision'}
    if before['security']!=after['security'] or before['sequences']!=after['sequences'] or set(before['tables'])!=set(after['tables']):
        raise InstanceError('Reset changed schema/authorization metadata')
    for table,fingerprint in before['tables'].items():
        if table not in allowed and after['tables'][table]!=fingerprint: raise InstanceError('Reset changed protected data: '+table)
    for table,fingerprint in others.items():
        if await row_digest(conn,quoted(table),'WHERE workspace_id<>$1',(workspace,))!=fingerprint:
            raise InstanceError('Reset affected another company')
    if await row_digest(conn,'ops.audit_log','WHERE id<=$1',(audit_high,))!=old_audit:
        raise InstanceError('Historical system audit changed')
    valid_actions={t+'.'+action for t in CLEARED for action in ('insert','update','delete')}|{'demo_reset.apply'}
    new_audit=await conn.fetch('SELECT workspace_id,actor_user_ref_id,actor_role_code,action_code FROM ops.audit_log WHERE id>$1',audit_high)
    if any(r['workspace_id']!=workspace or r['actor_user_ref_id'] is not None or r['actor_role_code']!='system' or r['action_code'] not in valid_actions for r in new_audit):
        raise InstanceError('Unexpected maintenance audit side effect')
    counters_after=await sequence_counters(conn,after['sequences'])
    for name,prior in counters_before.items():
        if name=='ops.audit_log_id_seq':
            expected=prior['last_value']+len(new_audit)-(0 if prior['is_called'] else 1)
            if counters_after[name]!={'last_value':expected,'is_called':True}:
                raise InstanceError('Unexpected maintenance audit sequence advancement')
        elif counters_after[name]!=prior: raise InstanceError('Reset advanced an unrelated sequence')
    scope_after=[dict(r) for r in await conn.fetch('SELECT * FROM config.authorization_scope_revision ORDER BY workspace_id')]
    bump=sum(plan['clear_counts'][t] for t in ('crm.customer_ownership','crm.customer_sales_member','crm.opportunity_participant'))+12
    for r in scope_before:
        if r['workspace_id']==workspace:r['revision']+=bump
    if scope_after!=scope_before: raise InstanceError('Unexpected authorization scope invalidation')
    if material_tree(root)!=materials: raise InstanceError('Original files changed during reset')
    summary={**summary,'mapping_sha256':sha(mapping),'cleared_rows':plan['clear_counts'],'retained_original_bytes':plan['retained_original_bytes'],
      'accounts_and_configuration_unchanged':True,'external_events_generated':0,'historical_system_audit_preserved':True}
    await conn.execute('''INSERT INTO control.demo_reset_receipt(operation_id,workspace_id,company_code,workspace_version,
      plan_sha256,template_sha256,backup_sha256,source_revision,as_of,reason,executor,summary)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,current_user,$11::jsonb)''',operation,workspace,company_code,version,
      plan_sha,template_sha,backup_sha,data['revision'],as_of,reason,json.dumps(summary))
    return {'status':'reset','operation_id':str(operation),'summary':summary,'services_started':False}
