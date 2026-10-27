"""Exact V297 creation guard + API three EXECUTEs + Worker three SELECT columns.

No installation policy replay, business rewrite, old function change or RLS change.
Only the exact reviewed grant is normalized out of the preserved column ACL;
new definitions and effective permissions are independently verified before commit.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import release_opportunity_claim_contract as contract
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require
from release126_task_security import FUNCTION_SQL as BASE_FUNCTION_SQL, ACL_FUNCTION_SQL

FUNCTION_SQL = BASE_FUNCTION_SQL.replace(
    'p.pronargdefaults AS defaults,p.proconfig AS settings,',
    '''p.pronargdefaults AS defaults,p.proconfig AS settings,
 p.procost AS cost,p.prorows AS estimated_rows,p.provariadic::text AS variadic_type,
 p.prosupport::text AS support,p.probin AS library,p.prosqlbody::text AS parsed_body,
 pg_get_expr(p.proargdefaults,0) AS default_expressions,''')
FUNCTION_GRANTS = {
    'security.opportunity_create_customer_reason(uuid)': [],
    'security.opportunity_create_customer_access(uuid)': ['api'],
    'security.opportunity_create_customer_options(text,integer,integer)': ['api'],
    'security.require_opportunity_create_customer(uuid)': ['api'],
    'security.guard_opportunity_create_customer()': [],
}
NEW_TRIGGER = ('crm.opportunity', 'opportunity_create_claim_guard')
WORKER_TABLE = 'workflow.task_execution'
WORKER_COLUMNS = ('task_id', 'workspace_id', 'state')
GRANTS = tuple(f"GRANT EXECUTE ON FUNCTION {sig} TO {','.join(ROLES[r] for r in roles)}"
    for sig, roles in FUNCTION_GRANTS.items() if roles) + (
    'GRANT SELECT(task_id,workspace_id,state) ON workflow.task_execution TO sales_worker_saas',)
TRIGGER_SQL = """SELECT tgrelid::regclass::text AS relation,tgname AS name,
 pg_get_triggerdef(oid) AS definition,tgenabled::text AS enabled
 FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgrelid::regclass::text,tgname"""
COLUMN_ACL_SQL = """SELECT a.attrelid,a.attnum,a.attrelid::regclass::text AS relation,a.attname AS column,
 pg_get_userbyid(x.grantor) AS grantor,
 CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END AS grantee,
 x.privilege_type AS privilege,x.is_grantable AS grantable
 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
 WHERE a.attnum>0 AND NOT a.attisdropped ORDER BY a.attrelid,a.attnum,grantee,grantor,privilege"""


def is_worker_delta(row):
    return (row['relation'] == WORKER_TABLE and row['column'] in WORKER_COLUMNS
            and row['grantor'] == 'postgres' and row['grantee'] == ROLES['worker']
            and row['privilege'] == 'SELECT' and row['grantable'] is False)


async def source_objects(conn):
    return {'worker_task_execution_select': {
        name: await conn.fetchval("SELECT has_column_privilege($1,$2,$3,'SELECT')", ROLES['worker'], WORKER_TABLE, name)
        for name in WORKER_COLUMNS}}


async def snapshot(conn):
    new_oids = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n) for n in FUNCTION_GRANTS}
    rows = {}
    for key, sql in BASE_QUERIES.items():
        if key == 'relations':
            sql = sql.replace("WHERE relkind NOT IN ('i','I') ", '').replace('relforcerowsecurity', 'relforcerowsecurity,reloptions::text')
        if key == 'columns':
            rows[key] = [dict(r) for r in await conn.fetch(COLUMN_ACL_SQL) if not is_worker_delta(r)]
            continue
        values = [dict(r) for r in await conn.fetch(sql)]
        if key == 'functions': values = [r for r in values if r['oid'] not in new_oids]
        rows[key] = values
    rows['constraints'] = [dict(r) for r in await conn.fetch("""SELECT oid,conrelid,conrelid::regclass::text AS relation,
        conname,contype::text,convalidated,md5(pg_get_constraintdef(oid)) AS definition_digest
        FROM pg_constraint ORDER BY oid""")]
    rows['triggers'] = [dict(r) for r in await conn.fetch(TRIGGER_SQL) if (r['relation'],r['name']) != NEW_TRIGGER]
    rows['modified_function_properties'] = {}  # No existing function is permitted to change.
    rows['column_definitions'] = [dict(r) for r in await conn.fetch("""SELECT n.nspname||'.'||c.relname AS relation,
        a.attnum,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,
        a.attidentity::text,a.attgenerated::text,pg_get_expr(d.adbin,d.adrelid) AS default_expression
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attnum>0 AND NOT a.attisdropped AND n.nspname=ANY($1::text[])
        ORDER BY n.nspname,c.relname,a.attnum""", ['activity','agent','common','config','control','crm','insight','knowledge','ops','platform','security','workflow'])]
    # Column ACLs are fully covered separately above, including non-business schemas.
    rows['view_definitions'] = [dict(r) for r in await conn.fetch("""SELECT schemaname||'.'||viewname AS name,
        md5(definition) AS definition_digest FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1""")]
    roles = [r for r in rows['roles'] if r['rolname'] in ROLES.values()]
    require(len(roles)==4 and all(r['rolcanlogin'] and not any(r[k] for k in
        ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')) for r in roles), 'Runtime roles must remain nonprivileged')
    indexes = [dict(r) for r in await conn.fetch("""SELECT indexrelid,indrelid,indisunique,indisprimary,
        indisexclusion,indimmediate,indisvalid,indisready,indislive,indisreplident,
        md5(pg_get_indexdef(indexrelid)) AS definition_digest FROM pg_index ORDER BY indexrelid""")]
    return {'format':'salesbuddy-live-security-v1','fingerprints':{k:digest(v) for k,v in rows.items()},
            'runtime_roles_nonprivileged':True,'index_fingerprint':digest(indexes)}


async def new_objects(conn):
    result = {'format':'salesbuddy-opportunity-claim-new-objects-v1','functions':{}}
    for sig in FUNCTION_GRANTS:
        row = await conn.fetchrow(FUNCTION_SQL,sig)
        require(row is not None, 'V297 helper absent')
        result['functions'][sig] = {'properties':dict(row),'acl':[dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL,sig)]}
    result['trigger'] = [dict(r) for r in await conn.fetch(TRIGGER_SQL) if (r['relation'],r['name'])==NEW_TRIGGER]
    # No catalog OIDs in the independent-restore object contract.
    result['worker_columns'] = [{k:v for k,v in dict(r).items() if k not in ('attrelid','attnum')}
                               for r in await conn.fetch(COLUMN_ACL_SQL) if is_worker_delta(r)]
    return result


def validate_new_acl(objects, *, allow_ungranted=False):
    require(set(objects)=={'format','functions','trigger','worker_columns'}
        and objects['format']=='salesbuddy-opportunity-claim-new-objects-v1'
        and set(objects['functions'])==set(FUNCTION_GRANTS), 'Exact V297 object receipt required')
    for sig, roles in FUNCTION_GRANTS.items():
        value=objects['functions'][sig];p=value['properties']
        language='sql' if sig in ('security.opportunity_create_customer_access(uuid)',
            'security.opportunity_create_customer_options(text,integer,integer)') else 'plpgsql'
        volatility='v' if sig in ('security.require_opportunity_create_customer(uuid)',
            'security.guard_opportunity_create_customer()') else 's'
        require(p['owner']=='postgres' and p['definer'] is True and p['settings']==['search_path=pg_catalog']
            and p['language']==language and p['volatility']==volatility and p['kind']=='f', 'V297 helper properties differ')
        observed={(a['grantee'],a['privilege']) for a in value['acl'] if a['grantee']!=p['owner']}
        wanted={(ROLES[r],'EXECUTE') for r in roles}
        require(observed==wanted or (allow_ungranted and not observed), 'Unexpected V297 helper grants')
        require(all(a['grantor']==p['owner'] and not a['grantable'] for a in value['acl']), 'Unexpected helper grant options')
    expected_trigger = {'relation':NEW_TRIGGER[0],'name':NEW_TRIGGER[1],'enabled':'O',
        'definition':'CREATE TRIGGER opportunity_create_claim_guard BEFORE INSERT ON crm.opportunity FOR EACH ROW EXECUTE FUNCTION security.guard_opportunity_create_customer()'}
    require(objects['trigger']==[expected_trigger], 'Exact enabled INSERT-only creation guard required')
    expected_columns = [{'relation':WORKER_TABLE,'column':name,'grantor':'postgres','grantee':ROLES['worker'],
        'privilege':'SELECT','grantable':False} for name in WORKER_COLUMNS]
    observed=objects['worker_columns']
    require(sorted(observed,key=lambda x:x['column'])==sorted(expected_columns,key=lambda x:x['column'])
            or (allow_ungranted and observed==[]), 'Exactly three nondelegable Worker SELECT columns required')


async def verify_effective_privileges(conn, *, before=False):
    for sig, roles in FUNCTION_GRANTS.items():
        if before: continue
        for role in ROLES.values():
            require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",role,sig)
                    is (role in [ROLES[r] for r in roles]), 'Unexpected inherited helper privilege')
    for name in WORKER_COLUMNS:
        require(await conn.fetchval("SELECT has_column_privilege($1,$2,$3,'SELECT')",ROLES['worker'],WORKER_TABLE,name)
                is (not before), 'Worker required columns do not match the exact release state')
    for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
        require(not await conn.fetchval('SELECT has_table_privilege($1,$2,$3)',ROLES['worker'],WORKER_TABLE,privilege),
                'Worker must not receive whole-table or write rights')
    require(not await conn.fetchval("SELECT has_column_privilege($1,$2,'updated_at','SELECT')",ROLES['worker'],WORKER_TABLE),
            'Worker must not read unrelated task execution columns')


async def business_state(conn):
    require(conn.is_in_transaction(), 'Business preservation snapshot requires a transaction')
    previous = await conn.fetchval('SHOW statement_timeout')
    await conn.execute("SET LOCAL statement_timeout='120s'")
    try: result = await _business_state_digest(conn)
    except BaseException:
        try: await conn.execute("SELECT set_config('statement_timeout',$1,true)",previous)
        except BaseException: pass
        raise
    await conn.execute("SELECT set_config('statement_timeout',$1,true)",previous)
    return result


async def _business_state_digest(conn):
    rows=await conn.fetch("""SELECT n.nspname||'.'||c.relname AS name,
        quote_ident(n.nspname)||'.'||quote_ident(c.relname) AS qualified FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind='r'
        AND (n.nspname||'.'||c.relname) NOT IN ('ops.schema_migration','ops.migration_checksum') ORDER BY 1""",
        ['activity','agent','common','config','control','crm','insight','knowledge','ops','platform','security','workflow'])
    values={}
    for row in rows:
        values[row['name']]=dict(await conn.fetchrow("""SELECT count(*) AS rows,
            encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') AS sha256
            FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS h
                  FROM """+row['qualified']+' t) hashed'))
    return {'row_counts':{k:v['rows'] for k,v in values.items()},'existing_business_sha256':digest(values)}


def load_plan(plan_ref):
    plan = read_ref(plan_ref); expected = read_ref(plan['policy_ref'])
    contract.validate_acl(plan, expected, digest=digest, require=require)
    objects, source = read_ref(plan['new_objects_ref']), read_ref(plan['source_objects_ref'])
    validate_new_acl(objects)
    require(source == {'worker_task_execution_select': {name: False for name in WORKER_COLUMNS}}, 'Exact absent Worker column baseline required')
    contract.validate_restore_evidence(read_ref(plan['restore_evidence_ref']), objects, source, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]; metadata = root/'tools/installation/runtime_permissions.json'
    paths = {str(root/'backend/deploy'/p) for p in contract.GATE_FILES}
    paths |= {str(root/'database/scripts/migrate.py'), str(metadata)}
    paths |= {str(root/'database'/p) for p, _ in contract.SQL.values()}
    require(set(plan.get('source_files', {})) == paths, 'Complete opportunity_claim source closure required')
    for name, checksum in plan['source_files'].items():
        path = Path(name)
        require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                and hashlib.sha256(path.read_bytes()).hexdigest() == checksum, 'Frozen opportunity_claim source changed')
    require(digest(json.loads(metadata.read_bytes())) == contract.INSTALLATION_POLICY_SHA, 'Exact V297 runtime policy required')
    for path, checksum in contract.SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest() == checksum, 'Frozen V297 SQL changed')
    return plan, expected


async def require_no_added_columns(conn):
    require(not any((r['relation'],r['name'])==NEW_TRIGGER for r in await conn.fetch(TRIGGER_SQL)), 'V297 trigger already exists')
    await verify_effective_privileges(conn, before=True)


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), 'Opportunity claim checks must be transactional')
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity == plan['target'], 'Production database identity changed')
    observed = await snapshot(conn)
    require(observed == expected, 'Preserved production catalog/ACL/RLS/role boundary changed')
    if not final:
        for sig in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', sig) is None, 'V297 function already exists')
        require(await source_objects(conn) == read_ref(plan['source_objects_ref']), 'V296 Worker column baseline changed')
        await require_no_added_columns(conn)
    else:
        objects = await new_objects(conn); validate_new_acl(objects, allow_ungranted=allow_ungranted)
        wanted = read_ref(plan['new_objects_ref'])
        if allow_ungranted:
            for sig in FUNCTION_GRANTS:
                objects['functions'][sig]['acl'] = wanted['functions'][sig]['acl']
            objects['worker_columns'] = wanted['worker_columns']
        require(objects == wanted, 'New/replaced definitions differ from the independent restore')
        if not allow_ungranted:
            await verify_effective_privileges(conn)
    return {'security_preserved': True, 'snapshot_sha256': digest(observed), 'schema': 'V297' if final else 'V296'}


async def grant_execute(conn, *, plan_ref):
    require(conn.is_in_transaction(), 'V297 grants require the V296-V297 migration transaction')
    await verify_state(conn, plan_ref=plan_ref, final=True, allow_ungranted=True)
    for sql in GRANTS:
        await conn.execute(sql)
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), 'V297 verification owns a readonly transaction')
    tx = conn.transaction(isolation='repeatable_read', readonly=True); await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='30s'")
        result = await verify_state(conn, plan_ref=plan_ref, final=final)
    finally:
        await tx.rollback()
    return {'status': 'verified_readonly', 'rolled_back': True, 'committed': False, **result}


async def rehearse(conn, *, expected_database, source_backup_sha256):
    """Apply only to an independent production restore, returning metadata only."""
    require(not conn.is_in_transaction(), 'Restore rehearsal owns its transaction')
    identity = dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity == expected_database and identity['database'] != 'sales_saas' and identity['oid'] != 18684
            and await conn.fetchval('SELECT current_user::text') == 'postgres'
            and contract._hex(source_backup_sha256, 64), 'Independent maintenance-owned production restore required')
    require(not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend')"), 'Other restore client present')
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('_opportunity_claim_migrate', root/'database/scripts/migrate.py')
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    migrations = [m.item('BASELINE_V1', root/'database/baseline/V001__current_schema.sql'),
                  m.item('S001', root/'database/seeds/S001__system_configuration.sql'), *m.discover(root/'database')]
    plan = [{'key': v.key, 'file': v.path.relative_to(root/'database').as_posix(), 'sha256': v.checksum} for v in migrations]
    async def ledger(final=False):
        prior = dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        registered = await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
        contract.validate_migrations(plan, prior, registered, 'V297' if final else 'V296', 'V297',
                                     [] if final else contract.PENDING, digest=digest, require=require)
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL lock_timeout='2s'"); await conn.execute("SET LOCAL statement_timeout='60s'")
        await ledger()
        for sig in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', sig) is None, 'Restore already has V297 helpers')
        await require_no_added_columns(conn)
        source = await source_objects(conn)
        before, data = await snapshot(conn), await business_state(conn)
        for migration in migrations:
            if migration.key in contract.PENDING:
                require(await m.apply(conn, migration, root/'database') == 'applied', 'Migration was not fresh')
        after = await snapshot(conn)
        require(after == before, 'Upgrade changed preserved catalogs: ' + ','.join(k for k in before['fingerprints'] if before['fingerprints'][k] != after['fingerprints'][k]))
        validate_new_acl(await new_objects(conn), allow_ungranted=True)
        for sql in GRANTS:
            await conn.execute(sql)
        objects = await new_objects(conn); validate_new_acl(objects); await verify_effective_privileges(conn)
        require(await snapshot(conn) == before and await business_state(conn) == data, 'Upgrade changed existing business/security state')
        await ledger(final=True)
    evidence = {'format': 'salesbuddy-opportunity-claim-restore-rehearsal-v1', 'status': 'passed',
                'evidence_kind': 'independent_production_restore', 'source_database': {'database': 'sales_saas', 'oid': 18684},
                'restored_database': identity, 'source_backup_sha256': source_backup_sha256,
                'restored_from_production_backup': True, 'source_schema': 'V296', 'target_schema': 'V297',
                'source_ledger_sha256': contract.BASE_COMPLETE_LEDGER, 'target_ledger_sha256': contract.TARGET_COMPLETE_LEDGER,
                'migration_plan_sha256': contract.MIGRATION_PLAN_SHA, 'new_objects_sha256': digest(objects),
                'source_objects_sha256': digest(source), 'security_preserved': True,
                'business_state_preserved': True, 'effective_privileges_verified': True}
    return {'evidence': evidence, 'new_objects': objects, 'source_objects': source,
            'security_snapshot_sha256': digest(before), 'business_state_sha256': digest(data)}
