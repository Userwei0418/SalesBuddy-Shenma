"""Exact V286 -> V288 security delta for isolated demo access and task protocol 3.

Only reviewed definitions/ACL deltas are normalized. Every other role, grant,
RLS expression, index, trigger and business row remains frozen. Rehearsal never
opens a backup and synthetic evidence cannot satisfy production admission.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import release128_task_contract as contract
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require
from release126_task_security import FUNCTION_SQL, ACL_FUNCTION_SQL, ACL_TABLE_SQL

TABLE_GRANTS = {f'security.demo_readonly_{name}': {} for name in
                ('identity','session','text','catalog','admission')}
TEXT_COLUMNS = ['workspace_id','resource','resource_id','source_updated_at','display_text']
FUNCTION_GRANTS = {
 'security.demo_readonly_workspace()': [],
 'security.demo_readonly_guard_identity()': [],
 'security.demo_readonly_guard_employee()': [],
 'security.is_demo_readonly_identity(uuid,uuid)': ['api','worker','feishu'],
 'security.demo_readonly_candidate(text,text,text)': ['api'],
 'security.demo_readonly_session(uuid,uuid,uuid,integer)': ['api'],
 'security.demo_readonly_create_session(uuid,uuid,uuid,integer,text,text)': ['api'],
 'security.demo_readonly_revoke_session(uuid,uuid,uuid)': ['api'],
 'security.demo_readonly_admit(uuid,uuid,uuid,integer)': ['api'],
 'security.demo_readonly_touch_catalog()': [],
 'security.demo_readonly_register(uuid,text,text)': [],
 'security.demo_readonly_update_identity(uuid,text,boolean,boolean)': [],
 'security.demo_readonly_review_text(uuid,text,uuid,text,text)': [],
 'security.demo_readonly_context(uuid)': ['PUBLIC','api','worker','feishu'],
 'security.demo_readonly_read(uuid,uuid,uuid,integer,text,uuid,text,integer,integer)': ['api'],
 'workflow.task_execution_authorized_v3(uuid,text)': [],
 'workflow.task_execution_capabilities_v3(uuid)': [],
 'workflow.task_execution_summary_v3(uuid)': [],
 'workflow.task_execution_initialize_v3(uuid,jsonb)': ['api'],
 'workflow.task_execution_command_v3(uuid,text,jsonb,uuid)': ['api'],
 'workflow.task_execution_adoption_v3(uuid)': ['api'],
 'workflow.task_execution_adopt_v3(uuid,jsonb,uuid)': ['api'],
 'workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid)': [],
}
OLD_FUNCTIONS = {
 'workflow.task_execution_candidate(uuid,uuid,text)': '0ce66cf22546b35aa6f6ad6c8477eca4',
 'workflow.task_execution_candidates(uuid,text,text,integer,integer)': 'c9352343d32b7d008c4fc7f5e4833464',

 'security.password_login_identifier(text,text)': '39f3bb1ca870d3d92f461e43232f3cf3',
 'security.password_login_identifier(text,text,text)': '2d35e5bef49beacdd8b9df7d8b1fc277',
 'security.task_actor_recipient(uuid)': '5d96cbc2ad1618cf9f5cdac950989069',
 'security.authorization_task(text,uuid)': '87463d69fa8c10c4ef6e09dfd25bfdf2',
 'workflow.task_execution_capabilities(uuid)': 'd06cdb5ad8651b2ffecf075e292a31e0',
 'workflow.task_execution_summary(uuid)': '977d4b88ba791c13ddec1a945226a45a',
 'workflow.guard_task_execution_legacy_write()': 'a4a88c3e65e2ebf6e4a9efbddc244619',
 'workflow.require_task_execution_initialization()': '9224d7a04268e0157c4bb77500194383',
 'security.save_task_alignment(uuid,jsonb,integer)': '5e871c0578fd9c96dc0252c6fe22fda2',
 'workflow.task_execution_command(uuid,text,jsonb,uuid)': 'e59e5a492ad1b6d95fc2d40c1f5b6037',
}
OLD_EXTRA_GRANTS = {'workflow.task_execution_progress(jsonb)': ['api']}
COMMAND = 'workflow.task_execution_command(uuid,text,jsonb,uuid)'
ARCHIVE = 'workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid)'
POLICY_TABLES = ['crm.customer','crm.opportunity','activity.visit','workflow.task','workflow.task_execution']
NEW_TRIGGERS = [(name, 'demo_catalog_changed') for name in POLICY_TABLES] + [('platform.user_ref','demo_identity_employee_collision')]
CONSTRAINTS = ['task_execution_mode_check','task_status_check']
GRANTS = tuple(f"GRANT EXECUTE ON FUNCTION {sig} TO {','.join(ROLES.get(r,r) for r in roles)}"
 for sig,roles in {**FUNCTION_GRANTS,**OLD_EXTRA_GRANTS}.items() if roles) + (
 'GRANT SELECT ('+','.join(TEXT_COLUMNS)+') ON security.demo_readonly_text TO '+ROLES['api'],)
NEW_RELATIONS = """WITH t AS (SELECT oid,reltoastrelid FROM pg_class WHERE oid=ANY(ARRAY[""" + ','.join("COALESCE(to_regclass('"+n+"'),0)" for n in TABLE_GRANTS) + """ ]::oid[])),
 r AS (SELECT oid FROM t UNION SELECT reltoastrelid FROM t WHERE reltoastrelid<>0)
 SELECT oid FROM r UNION SELECT indexrelid FROM pg_index WHERE indrelid IN (SELECT oid FROM r)"""
POLICY_SQL = "SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies ORDER BY 1,2,3"
TRIGGER_SQL = "SELECT tgrelid::regclass::text AS relation,tgname AS name,pg_get_triggerdef(oid) AS definition,tgenabled::text AS enabled FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgrelid::regclass::text,tgname"


def unwrap_policy(value):
    # Exact parser for the V287 wrapper emitted by pg_get_expr. The full final
    # policy is frozen separately, so even the approved branch cannot drift.
    match = re.fullmatch(r"\s*CASE\s+WHEN \(NULLIF\(current_setting\('app.demo_readonly_context'::text, true\), ''::text\) IS NOT NULL\) THEN (false|security.demo_readonly_context\(workspace_id\))\s+ELSE (.*)\s+END", value, re.S)
    result=match.group(2).strip() if match else value
    # pg_get_expr indents an unchanged nested CASE one level deeper. Normalize
    # whitespace only outside SQL string literals; preserve every literal byte.
    return re.sub(r"'(?:[^']|'')*'|\s+",lambda m: m.group(0) if m.group(0).startswith("'") else ' ',result).strip()


async def snapshot(conn):
    excluded = set(await conn.fetchval('SELECT ARRAY('+NEW_RELATIONS+')'))
    new_oids = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n) for n in FUNCTION_GRANTS}
    changed = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n):n for n in OLD_FUNCTIONS}
    extra = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n):n for n in OLD_EXTRA_GRANTS}
    rows = {}
    for key,sql in BASE_QUERIES.items():
        if key == 'relations': sql=sql.replace("WHERE relkind NOT IN ('i','I') ",'')
        values = [dict(r) for r in await conn.fetch(sql)]
        if key == 'relations': values=[r for r in values if r['oid'] not in excluded]
        if key == 'columns': values=[r for r in values if r['attrelid'] not in excluded]
        if key == 'functions':
            values=[r for r in values if r['oid'] not in new_oids]
            for r in values:
                signature=changed.get(r['oid']) or extra.get(r['oid'])
                if signature:
                    # ACL ordering is irrelevant; all grantor and grant options
                    # are still included. Only the explicitly additive API
                    # progress grant is removed from this preserved snapshot.
                    acl=[dict(a) for a in await conn.fetch(ACL_FUNCTION_SQL,signature)]
                    if signature in extra.values(): acl=[a for a in acl if a['grantee'] != ROLES['api']]
                    r['proacl']=acl
                    if signature in OLD_FUNCTIONS: r['definition_digest']='reviewed-release128-body'
                    if signature == COMMAND: r['oid']=COMMAND
            values=sorted(values,key=lambda r:str(r['oid']))
        if key == 'policies':
            values=[]
            for raw in await conn.fetch(POLICY_SQL):
                r=dict(raw); name=r['schemaname']+'.'+r['tablename']
                if name in TABLE_GRANTS or (name in POLICY_TABLES and r['policyname']=='demo_fixed_company_read'): continue
                if name in POLICY_TABLES and r['cmd'] in ('ALL','SELECT') and r['qual'] is not None:
                    r['qual']=unwrap_policy(r['qual'])
                values.append(r)
        rows[key]=values
    rows['constraints']=[dict(r) for r in await conn.fetch('''SELECT oid,conrelid,conname,contype::text,convalidated,
        md5(pg_get_constraintdef(oid)) AS definition_digest FROM pg_constraint
        WHERE conrelid NOT IN ('''+NEW_RELATIONS+''') AND NOT(conrelid='workflow.task'::regclass AND conname=ANY($1::text[])) ORDER BY oid''',CONSTRAINTS)]
    rows['triggers']=[dict(r) for r in await conn.fetch(TRIGGER_SQL)
        if r['relation'] not in TABLE_GRANTS and (r['relation'],r['name']) not in NEW_TRIGGERS]
    rows['modified_function_properties']={}
    for sig in (*OLD_FUNCTIONS,*OLD_EXTRA_GRANTS):
        v=dict(await conn.fetchrow(FUNCTION_SQL,sig)); v.pop('body_sha256')
        rows['modified_function_properties'][sig]=v
    roles=[r for r in rows['roles'] if r['rolname'] in ROLES.values()]
    require(len(roles)==4 and all(r['rolcanlogin'] and not any(r[k] for k in
     ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')) for r in roles),'Unsafe runtime roles')
    indexes=[dict(r) for r in await conn.fetch('''SELECT indexrelid,indrelid,indisunique,indisprimary,indisexclusion,indimmediate,
     indisvalid,indisready,indislive,indisreplident,md5(pg_get_indexdef(indexrelid)) definition_digest FROM pg_index ORDER BY indexrelid''') if r['indexrelid'] not in excluded]
    return {'format':'salesbuddy-live-security-v1','fingerprints':{k:digest(v) for k,v in rows.items()},
            'runtime_roles_nonprivileged':True,'index_fingerprint':digest(indexes)}


async def new_objects(conn):
    result={'format':'salesbuddy-release128-new-objects-v1','tables':{},'functions':{}}
    for table in TABLE_GRANTS:
        value=dict(await conn.fetchrow('''SELECT pg_get_userbyid(relowner) owner,relkind::text kind,
         relrowsecurity rls,relforcerowsecurity force_rls FROM pg_class WHERE oid=to_regclass($1)''',table))
        value['acl']=[dict(r) for r in await conn.fetch(ACL_TABLE_SQL,table)]
        value['columns']=[dict(r) for r in await conn.fetch('''SELECT a.attname name,format_type(a.atttypid,a.atttypmod) type,
         a.attnotnull not_null,pg_get_expr(d.adbin,d.adrelid) default_expression,a.attidentity::text identity,
         a.attgenerated::text generated,a.attacl::text acl FROM pg_attribute a
         LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
         WHERE a.attrelid=to_regclass($1) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''',table)]
        value['column_acl']=[dict(r) for r in await conn.fetch('''SELECT a.attname name,pg_get_userbyid(x.grantor) grantor,
         CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END grantee,x.privilege_type privilege,x.is_grantable grantable
         FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x WHERE a.attrelid=to_regclass($1) ORDER BY 1,2,3,4''',table)]
        for key,sql in {
          'constraints':'SELECT conname name,pg_get_constraintdef(oid) definition,convalidated valid FROM pg_constraint WHERE conrelid=to_regclass($1) ORDER BY conname',
          'indexes':'SELECT pg_get_indexdef(indexrelid) definition,indisvalid valid,indisready ready FROM pg_index WHERE indrelid=to_regclass($1) ORDER BY pg_get_indexdef(indexrelid)',
        }.items(): value[key]=[dict(r) for r in await conn.fetch(sql,table)]
        value['policies']=[dict(r) for r in await conn.fetch(POLICY_SQL) if r['schemaname']+'.'+r['tablename']==table]
        value['triggers']=[dict(r) for r in await conn.fetch(TRIGGER_SQL) if r['relation']==table]
        result['tables'][table]=value
    for sig in (*FUNCTION_GRANTS,*OLD_FUNCTIONS,*OLD_EXTRA_GRANTS):
        result['functions'][sig]={'properties':dict(await conn.fetchrow(FUNCTION_SQL,sig)),
                                 'acl':[dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL,sig)]}
    result['task_constraints']=[dict(r) for r in await conn.fetch("SELECT conname name,pg_get_constraintdef(oid) definition,convalidated valid FROM pg_constraint WHERE conrelid='workflow.task'::regclass AND conname=ANY($1::text[]) ORDER BY conname",CONSTRAINTS)]
    result['policies']=[dict(r) for r in await conn.fetch(POLICY_SQL) if r['schemaname']+'.'+r['tablename'] in POLICY_TABLES]
    result['triggers']=[dict(r) for r in await conn.fetch(TRIGGER_SQL) if (r['relation'],r['name']) in NEW_TRIGGERS]
    return result


def validate_new_acl(objects, *, allow_ungranted=False):
    require(set(objects)=={'format','tables','functions','task_constraints','policies','triggers'}
     and objects['format']=='salesbuddy-release128-new-objects-v1'
     and set(objects['tables'])==set(TABLE_GRANTS)
     and set(objects['functions'])==set(FUNCTION_GRANTS)|set(OLD_FUNCTIONS)|set(OLD_EXTRA_GRANTS),'Incomplete release128 object contract')
    for table,v in objects['tables'].items():
        require(v['owner']=='postgres' and v['rls'] is True and v['force_rls'] is False and v['kind']=='r','Private demo tables must retain maintenance ownership/RLS')
        require(not [a for a in v['acl'] if a['grantee']!='postgres'],'No runtime whole-table access to private identity/session/text data')
        require(all(a['grantor']=='postgres' and not a['grantable'] for a in v['acl']),'Unexpected private table grantor/options')
        columns=v['column_acl']; wanted=set(TEXT_COLUMNS) if table=='security.demo_readonly_text' else set()
        observed={a['name'] for a in columns}
        require(observed==wanted or (allow_ungranted and not observed),'Unexpected readonly text columns')
        require(len(columns)==len(observed) and all(a['grantee']==ROLES['api'] and a['privilege']=='SELECT' and a['grantor']=='postgres' and not a['grantable'] for a in columns),'Unexpected readonly column grants')
    for sig,roles in FUNCTION_GRANTS.items():
        v=objects['functions'][sig]; p=v['properties']
        archived=sig==ARCHIVE
        require(p['owner']==(objects['functions'][COMMAND]['properties']['owner'] if archived else 'postgres'),'Function owner differs')
        require(p['definer'] is (not sig.startswith('security.demo_readonly_read(')) and p['settings']==(['search_path=pg_catalog','statement_timeout=5s'] if sig.startswith('security.demo_readonly_read(') else ['search_path=pg_catalog']),'Function security settings differ')
        observed={(a['grantee'],a['privilege']) for a in v['acl'] if a['grantee']!=p['owner']}
        wanted={(ROLES.get(role,role),'EXECUTE') for role in roles}
        require(observed==wanted or (allow_ungranted and observed<=wanted),'Unexpected runtime/private helper grants')
        require(all(a['grantor']==p['owner'] and not a['grantable'] for a in v['acl']),'Unexpected function grant options')
    require(objects['functions'][ARCHIVE]['properties']['body_sha256']==contract.ARCHIVED_COMMAND_SHA,'Archived V2 implementation changed')
    require(len(objects['task_constraints'])==2 and all(r['valid'] for r in objects['task_constraints']),'Exact validated task constraints required')
    require(len(objects['triggers'])==len(NEW_TRIGGERS),'Exact demo invalidation/collision triggers required')


async def verify_effective_privileges(conn):
    for sig,roles in FUNCTION_GRANTS.items():
        for role in ROLES.values():
            require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",role,sig)
                is ('PUBLIC' in roles or role in [ROLES.get(r,r) for r in roles]),'Unexpected effective function grant')
    require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",ROLES['api'],'workflow.task_execution_progress(jsonb)') is True,'Safe progress projection unavailable')
    for role in ROLES.values():
        for table in TABLE_GRANTS:
            for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                require(await conn.fetchval('SELECT has_table_privilege($1,$2,$3)',role,table,privilege) is False,'Private whole-table privilege leaked')
            columns=await conn.fetch("SELECT attname FROM pg_attribute WHERE attrelid=to_regclass($1) AND attnum>0 AND NOT attisdropped",table)
            for col in columns:
                for privilege in ('SELECT','INSERT','UPDATE','REFERENCES'):
                    wanted=table=='security.demo_readonly_text' and role==ROLES['api'] and col['attname'] in TEXT_COLUMNS and privilege=='SELECT'
                    require(await conn.fetchval('SELECT has_column_privilege($1,$2,$3,$4)',role,table,col['attname'],privilege) is wanted,'Private column privilege leaked')


async def business_state(conn):
    from release126_task_security import business_state as previous
    result=await previous(conn)
    result['row_counts']={k:v for k,v in result['row_counts'].items() if k not in TABLE_GRANTS}
    # Old helper hashes only task/ASR/config rows, so new security tables do not affect it.
    return result


async def verify_no_seed(conn):
    for name in TABLE_GRANTS:
        require(await conn.fetchval('SELECT count(*) FROM '+name)==0,'Release must not provision identity, password, session, text or test data')


def load_plan(plan_ref):
    plan=read_ref(plan_ref); expected=read_ref(plan['policy_ref'])
    contract.validate_acl(plan,expected,digest=digest,require=require)
    objects=read_ref(plan['new_objects_ref']); validate_new_acl(objects)
    contract.validate_restore_evidence(read_ref(plan['restore_evidence_ref']),objects,digest=digest,require=require)
    root=Path(__file__).resolve().parents[2]; metadata=root/'tools/installation/runtime_permissions.json'
    paths={str(root/'backend/deploy'/p) for p in contract.GATE_FILES}|{str(root/'database/scripts/migrate.py'),str(metadata)}|{str(root/'database'/p) for p,_ in contract.SQL.values()}
    require(set(plan.get('source_files',{}))==paths,'Complete release128 source closure required')
    for name,checksum in plan['source_files'].items():
        path=Path(name)
        require(path.is_file() and not any(p.is_symlink() for p in (path,*path.parents)) and hashlib.sha256(path.read_bytes()).hexdigest()==checksum,'Frozen release128 source changed')
    require(digest(json.loads(metadata.read_bytes()))==contract.INSTALLATION_POLICY_SHA,'Exact V288 installation policy required')
    for path,checksum in contract.SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest()==checksum,'Frozen V287/V288 SQL changed')
    return plan,expected


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(),'Release128 checks must be transactional')
    plan,expected=load_plan(plan_ref)
    identity=dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity==plan['target'],'Database identity changed')
    observed=await snapshot(conn); require(observed==expected,'Preserved catalog/ACL/RLS/role boundary changed')
    if not final:
        for table in TABLE_GRANTS: require(await conn.fetchval('SELECT to_regclass($1)',table) is None,'V287 table already exists')
        for sig in FUNCTION_GRANTS: require(await conn.fetchval('SELECT to_regprocedure($1)',sig) is None,'V287/V288 function already exists')
        for sig,checksum in OLD_FUNCTIONS.items():
            require(await conn.fetchval('SELECT md5(prosrc) FROM pg_proc WHERE oid=to_regprocedure($1)',sig)==checksum,'V286 function differs')
    else:
        objects=await new_objects(conn); validate_new_acl(objects,allow_ungranted=allow_ungranted)
        wanted=read_ref(plan['new_objects_ref'])
        if allow_ungranted:
            for sig in (*FUNCTION_GRANTS,*OLD_EXTRA_GRANTS): objects['functions'][sig]['acl']=wanted['functions'][sig]['acl']
            for table in TABLE_GRANTS:
                objects['tables'][table]['column_acl']=wanted['tables'][table]['column_acl']
                for a,b in zip(objects['tables'][table]['columns'],wanted['tables'][table]['columns']): a['acl']=b['acl']
        require(objects==wanted,'New/replaced definitions differ from independent restore receipt')
        if not allow_ungranted: await verify_effective_privileges(conn)
    return {'security_preserved':True,'snapshot_sha256':digest(observed),'schema':'V288' if final else 'V286'}


async def grant_execute(conn, *, plan_ref):
    require(conn.is_in_transaction(),'Release128 grants require migration transaction')
    await verify_state(conn,plan_ref=plan_ref,final=True,allow_ungranted=True)
    await verify_no_seed(conn)
    for sql in GRANTS: await conn.execute(sql)
    return await verify_state(conn,plan_ref=plan_ref,final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(),'Release128 verification owns readonly transaction')
    tx=conn.transaction(isolation='repeatable_read',readonly=True); await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='10s'")
        result=await verify_state(conn,plan_ref=plan_ref,final=final)
    finally: await tx.rollback()
    return {'status':'verified_readonly','rolled_back':True,'committed':False,**result}


async def rehearse(conn, *, expected_database, source_backup_sha256=None, synthetic=False):
    require(not conn.is_in_transaction(),'Restore rehearsal owns transaction')
    identity=dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity==expected_database and identity['database']!='sales_saas' and identity['oid']!=18684
        and await conn.fetchval('SELECT current_user::text')=='postgres','Independent maintenance-owned restore required')
    require((synthetic and source_backup_sha256 is None) or (not synthetic and contract._hex(source_backup_sha256,64)),'Explicit evidence provenance required')
    require(not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend')"),'Other restore client present')
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('_release128_migrate',root/'database/scripts/migrate.py')
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
    migrations=[m.item('BASELINE_V1',root/'database/baseline/V001__current_schema.sql'),m.item('S001',root/'database/seeds/S001__system_configuration.sql'),*m.discover(root/'database')]
    plan=[{'key':v.key,'file':v.path.relative_to(root/'database').as_posix(),'sha256':v.checksum} for v in migrations]
    async def ledger(final=False):
        prior=dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        registered=await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
        contract.validate_migrations(plan,prior,registered,'V288' if final else 'V286','V288',[] if final else contract.PENDING,digest=digest,require=require)
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL lock_timeout='2s'"); await conn.execute("SET LOCAL statement_timeout='30s'")
        await ledger()
        for sig,checksum in OLD_FUNCTIONS.items():
            require(await conn.fetchval('SELECT md5(prosrc) FROM pg_proc WHERE oid=to_regprocedure($1)',sig)==checksum,'V286 body checksum differs')
        before,data=await snapshot(conn),await business_state(conn)
        for migration in migrations:
            if migration.key in contract.PENDING: require(await m.apply(conn,migration,root/'database')=='applied','Migration was not fresh')
        await verify_no_seed(conn)
        after=await snapshot(conn)
        require(after==before,'Upgrade changed existing security boundary: '+','.join(k for k in before['fingerprints'] if before['fingerprints'][k]!=after['fingerprints'][k]))
        validate_new_acl(await new_objects(conn),allow_ungranted=True)
        for sql in GRANTS: await conn.execute(sql)
        objects=await new_objects(conn); validate_new_acl(objects); await verify_effective_privileges(conn)
        require(await snapshot(conn)==before and await business_state(conn)==data,'Upgrade changed existing business/security state')
        await ledger(final=True)
    evidence={'format':'salesbuddy-release128-restore-rehearsal-v1','status':'passed',
     'evidence_kind':'synthetic' if synthetic else 'independent_production_restore',
     'source_database':{'database':'sales_saas','oid':18684},'restored_database':identity,
     'source_backup_sha256':source_backup_sha256,'restored_from_production_backup':not synthetic,
     'source_schema':'V286','target_schema':'V288','source_ledger_sha256':contract.BASE_COMPLETE_LEDGER,
     'target_ledger_sha256':contract.TARGET_COMPLETE_LEDGER,'migration_plan_sha256':contract.MIGRATION_PLAN_SHA,
     'new_objects_sha256':digest(objects),'security_preserved':True,'business_state_preserved':True,'effective_privileges_verified':True}
    return {'evidence':evidence,'new_objects':objects,'security_snapshot_sha256':digest(before),'business_state_sha256':digest(data)}
