"""Exact V298 private-score policy addition; no GRANT or business rewrites.

Keep every existing ACL, policy, function, role, index and row unchanged. Only
these two policy identities are separated from the preserved catalog, and their
complete definitions must equal the independent production-restore evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import release_battlemap_report_contract as contract
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require

NEW_POLICIES = ('self_quadrant_insert', 'self_quadrant_update')
GRANTS = ()  # The common atomic runner calls grant_execute; this adapter only verifies.
WORKER_TABLE = 'workflow.task_execution'
WORKER_COLUMNS = ('task_id', 'workspace_id', 'state')
SCORE_TABLE = 'insight.quadrant_score'
PRIVILEGES = ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
POLICY_PREDICATE = ('((workspace_id = common.current_workspace_id()) AND '
                    '(subject_user_ref_id = common.current_user_ref_id()) AND '
                    'security.has_customer_access(customer_id))')
POLICY_SQL = """SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check
 FROM pg_policies WHERE schemaname='insight' AND tablename='quadrant_score'
 ORDER BY policyname"""
TRIGGER_SQL = """SELECT tgrelid::regclass::text AS relation,tgname AS name,
 pg_get_triggerdef(oid) AS definition,tgenabled::text AS enabled
 FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgrelid::regclass::text,tgname"""


def is_new_policy(row):
    return (row['schemaname'] == 'insight' and row['tablename'] == 'quadrant_score'
            and row['policyname'] in NEW_POLICIES)


async def source_objects(conn):
    score_privileges = {}
    for role in ROLES.values():
        score_privileges[role] = {privilege: await conn.fetchval(
            'SELECT has_table_privilege($1,$2,$3)', role, SCORE_TABLE, privilege)
            for privilege in PRIVILEGES}
    return {
        'worker_task_execution_select': {name: await conn.fetchval(
            "SELECT has_column_privilege($1,$2,$3,'SELECT')", ROLES['worker'], WORKER_TABLE, name)
            for name in WORKER_COLUMNS},
        'quadrant_score_privileges': score_privileges,
    }


async def new_objects(conn):
    return {'format':'salesbuddy-battlemap-report-new-objects-v1',
            'policies':[dict(r) for r in await conn.fetch(POLICY_SQL) if is_new_policy(r)]}


def validate_new_acl(objects, *, allow_ungranted=False):
    # allow_ungranted is retained only for the common runner's interface. It
    # never permits partial definitions or grants; this migration adds neither.
    expected = [{'schemaname':'insight','tablename':'quadrant_score','policyname':name,
        'permissive':'PERMISSIVE','roles':['public'],'cmd':command,
        'qual':None if command=='INSERT' else POLICY_PREDICATE,'with_check':POLICY_PREDICATE}
        for name,command in zip(NEW_POLICIES,('INSERT','UPDATE'))]
    require(objects == {'format':'salesbuddy-battlemap-report-new-objects-v1','policies':expected},
            'Exactly the reviewed self-only INSERT/UPDATE score policies are required')


async def verify_effective_privileges(conn, *, before=False):
    for name in WORKER_COLUMNS:
        require(await conn.fetchval("SELECT has_column_privilege($1,$2,$3,'SELECT')",
                ROLES['worker'],WORKER_TABLE,name) is True, 'Retain existing Worker report columns')
    for privilege in PRIVILEGES:
        require(not await conn.fetchval('SELECT has_table_privilege($1,$2,$3)',
                ROLES['worker'],WORKER_TABLE,privilege), 'Worker task table grants must remain narrow')
    require(not await conn.fetchval("SELECT has_column_privilege($1,$2,'updated_at','SELECT')",
            ROLES['worker'],WORKER_TABLE), 'Worker unrelated task columns remain denied')
    for privilege in ('SELECT','INSERT','UPDATE'):
        require(await conn.fetchval('SELECT has_table_privilege($1,$2,$3)',
                ROLES['worker'],SCORE_TABLE,privilege) is True, 'Existing scoring Worker grant missing')
    for privilege in ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
        require(not await conn.fetchval('SELECT has_table_privilege($1,$2,$3)',
                ROLES['api'],SCORE_TABLE,privilege), 'API must not acquire score writes')


async def snapshot(conn):
    rows = {}
    for key, sql in BASE_QUERIES.items():
        if key == 'relations':
            sql = sql.replace("WHERE relkind NOT IN ('i','I') ", '').replace('relforcerowsecurity', 'relforcerowsecurity,reloptions::text')
        values = [dict(r) for r in await conn.fetch(sql)]
        if key == 'policies': values = [r for r in values if not is_new_policy(r)]
        rows[key] = values
    rows['constraints'] = [dict(r) for r in await conn.fetch("""SELECT oid,conrelid,conrelid::regclass::text AS relation,
        conname,contype::text,convalidated,md5(pg_get_constraintdef(oid)) AS definition_digest
        FROM pg_constraint ORDER BY oid""")]
    rows['triggers'] = [dict(r) for r in await conn.fetch(TRIGGER_SQL)]
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
    require(set(source) == {'worker_task_execution_select','quadrant_score_privileges'}
            and source['worker_task_execution_select'] == dict.fromkeys(WORKER_COLUMNS,True)
            and set(source['quadrant_score_privileges']) == set(ROLES.values())
            and all(set(v)==set(PRIVILEGES) and all(isinstance(b,bool) for b in v.values())
                    for v in source['quadrant_score_privileges'].values()), 'Exact existing privilege baseline required')
    contract.validate_restore_evidence(read_ref(plan['restore_evidence_ref']), objects, source, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]; metadata = root/'tools/installation/runtime_permissions.json'
    paths = {str(root/'backend/deploy'/p) for p in contract.GATE_FILES}
    paths |= {str(root/'database/scripts/migrate.py'), str(metadata)}
    paths |= {str(root/'database'/p) for p, _ in contract.SQL.values()}
    require(set(plan.get('source_files', {})) == paths, 'Complete battlemap_report source closure required')
    for name, checksum in plan['source_files'].items():
        path = Path(name)
        require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                and hashlib.sha256(path.read_bytes()).hexdigest() == checksum, 'Frozen battlemap_report source changed')
    require(digest(json.loads(metadata.read_bytes())) == contract.INSTALLATION_POLICY_SHA, 'Exact V298 runtime policy required')
    for path, checksum in contract.SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest() == checksum, 'Frozen V298 SQL changed')
    return plan, expected


async def require_no_added_columns(conn):
    """Common harness hook: require the exact pre-migration state, without DDL."""
    require(not (await new_objects(conn))['policies'], 'V298 policy already exists')
    await verify_effective_privileges(conn, before=True)


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), 'Battlemap report checks must be transactional')
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity == plan['target'], 'Production database identity changed')
    observed = await snapshot(conn)
    require(observed == expected, 'Preserved production catalog/ACL/RLS/role boundary changed')
    require(await source_objects(conn) == read_ref(plan['source_objects_ref']), 'Existing effective privilege baseline changed')
    if not final:
        await require_no_added_columns(conn)
    else:
        objects = await new_objects(conn); validate_new_acl(objects)
        require(objects == read_ref(plan['new_objects_ref']), 'Policies differ from the independent restore')
        await verify_effective_privileges(conn)
    return {'security_preserved':True,'snapshot_sha256':digest(observed),'schema':'V298' if final else 'V297',
            'runtime_grants_changed':False}


async def grant_execute(conn, *, plan_ref):
    """Verification-only common hook. V298 must never add runtime grants."""
    require(conn.is_in_transaction(), 'V298 verification requires the atomic migration transaction')
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), 'V298 verification owns a readonly transaction')
    tx = conn.transaction(isolation='repeatable_read', readonly=True); await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='30s'")
        result = await verify_state(conn, plan_ref=plan_ref, final=final)
    finally:
        await tx.rollback()
    return {'status':'verified_readonly','rolled_back':True,'committed':False,**result}


async def rehearse(conn, *, expected_database, source_backup_sha256):
    """Apply only to an independent production restore, returning metadata only."""
    require(not conn.is_in_transaction(), 'Restore rehearsal owns its transaction')
    identity = dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity == expected_database and identity['database'] != 'sales_saas' and identity['oid'] != 18684
            and await conn.fetchval('SELECT current_user::text') == 'postgres'
            and contract._hex(source_backup_sha256,64), 'Independent maintenance-owned production restore required')
    require(not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend')"), 'Other restore client present')
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('_battlemap_report_migrate',root/'database/scripts/migrate.py')
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
    migrations = [m.item('BASELINE_V1',root/'database/baseline/V001__current_schema.sql'),
                  m.item('S001',root/'database/seeds/S001__system_configuration.sql'),*m.discover(root/'database')]
    plan = [{'key':v.key,'file':v.path.relative_to(root/'database').as_posix(),'sha256':v.checksum} for v in migrations]
    async def ledger(final=False):
        prior = dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        registered = await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
        contract.validate_migrations(plan,prior,registered,'V298' if final else 'V297','V298',
            [] if final else contract.PENDING,digest=digest,require=require)
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL lock_timeout='2s'"); await conn.execute("SET LOCAL statement_timeout='30s'")
        await ledger(); await require_no_added_columns(conn)
        source = await source_objects(conn)
        before,data = await snapshot(conn),await business_state(conn)
        for migration in migrations:
            if migration.key in contract.PENDING:
                require(await m.apply(conn,migration,root/'database') == 'applied', 'Migration was not fresh')
        after = await snapshot(conn)
        require(after == before, 'Upgrade changed preserved catalogs: '+','.join(
            k for k in before['fingerprints'] if before['fingerprints'][k] != after['fingerprints'][k]))
        objects = await new_objects(conn); validate_new_acl(objects); await verify_effective_privileges(conn)
        require(await source_objects(conn)==source and await snapshot(conn)==before
                and await business_state(conn)==data, 'Upgrade changed existing privileges/business/security state')
        await ledger(final=True)
    evidence = {'format':'salesbuddy-battlemap-report-restore-rehearsal-v1','status':'passed',
        'evidence_kind':'independent_production_restore','source_database':{'database':'sales_saas','oid':18684},
        'restored_database':identity,'source_backup_sha256':source_backup_sha256,
        'restored_from_production_backup':True,'source_schema':'V297','target_schema':'V298',
        'source_ledger_sha256':contract.BASE_COMPLETE_LEDGER,'target_ledger_sha256':contract.TARGET_COMPLETE_LEDGER,
        'migration_plan_sha256':contract.MIGRATION_PLAN_SHA,'new_objects_sha256':digest(objects),
        'source_objects_sha256':digest(source),'security_preserved':True,'business_state_preserved':True,
        'effective_privileges_verified':True}
    return {'evidence':evidence,'new_objects':objects,'source_objects':source,
            'security_snapshot_sha256':digest(before),'business_state_sha256':digest(data)}
