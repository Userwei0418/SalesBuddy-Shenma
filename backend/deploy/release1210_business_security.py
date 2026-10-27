"""Exact V288 -> V292 catalog preservation, narrow grants and restore evidence.

Only the immutable visibility evidence table may gain derived rows. Existing
customer, opportunity, visit, finance, task and configuration rows are preserved.
No local grants, installation roles or business fixtures are replayed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import release1210_business_contract as contract
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require
from release126_task_security import FUNCTION_SQL, ACL_FUNCTION_SQL, ACL_TABLE_SQL

TABLE = 'security.opportunity_sales_visibility'
FUNCTION_GRANTS = {
    'security.opportunity_collection_edit_scope(uuid)': [],
    'security.opportunity_collection_actual(uuid,integer,integer)': ['api'],
    'security.save_opportunity_collection_actual(uuid,integer,integer,numeric,text,text,text,boolean)': ['api'],
    'security.company_opportunity_follow_up_actor()': ['api', 'worker'],
    'security.company_opportunity_follow_up_reference(uuid)': ['api', 'worker'],
    'security.company_opportunity_follow_up_options(text,uuid,integer,integer,text)': ['api'],
    'security.authorization_visit_target_v290(text,uuid,uuid,uuid,uuid)': [],
    'security.authorization_visit_v290(text,uuid)': [],
    'security.authorization_import_v290(text,uuid)': [],
    'security.authorization_has_v290(text)': [],
    'security.sales_shared_opportunity_actor()': ['api', 'worker'],
    'security.capture_opportunity_sales_visibility()': [],
    'security.sales_shared_opportunity_read(uuid)': [],
    'security.authorization_opportunity_v291(text,uuid)': [],
    'security.authorization_opportunity_direct_v291(text,uuid)': [],
    'security.customer_claim_visit_actor()': [],
    'security.customer_claim_visit_reader()': ['api', 'worker'],
    'security.customer_claim_visit_reference(uuid,uuid)': ['api'],
    'security.customer_claim_visit_read(uuid)': [],
    'security.customer_claim_visit_match(uuid,uuid,uuid)': ['api', 'worker'],
    'security.customer_claim_visit_match_current(uuid,uuid,uuid)': [],
    'security.visit_saved_read_permissions(uuid)': ['api', 'worker'],
    'security.company_opportunity_follow_up_processing(uuid,uuid)': ['api'],
    'security.archived_visit_save_receipt(uuid)': ['api'],
    'security.opportunity_read_context(uuid)': ['api', 'worker'],
    'security.company_opportunity_follow_up_customers(text,integer,integer)': ['api'],
}
OLD_FUNCTIONS = (
    'crm.guard_actual_confirmation()', 'crm.guard_opportunity_actual_basis()',
    'security.authorization_visit_target(text,uuid,uuid,uuid,uuid)',
    'security.authorization_visit(text,uuid)', 'security.authorization_has(text)',
    'security.authorization_import(text,uuid)',
    'security.authorization_opportunity(text,uuid)', 'security.authorization_opportunity_direct(text,uuid)',
    'security.opportunity_access(uuid)',
)
NEW_TRIGGER = ('crm.opportunity', 'zz_opportunity_sales_visibility')
GRANTS = tuple(f"GRANT EXECUTE ON FUNCTION {sig} TO {','.join(ROLES[r] for r in roles)}"
               for sig, roles in FUNCTION_GRANTS.items() if roles)
NEW_RELATIONS = """WITH t AS (SELECT oid,reltoastrelid FROM pg_class WHERE oid=to_regclass('security.opportunity_sales_visibility')),
 r AS (SELECT oid FROM t UNION SELECT reltoastrelid FROM t WHERE reltoastrelid<>0)
 SELECT oid FROM r UNION SELECT indexrelid FROM pg_index WHERE indrelid IN (SELECT oid FROM r)"""
TRIGGER_SQL = """SELECT tgrelid::regclass::text AS relation,tgname AS name,
 pg_get_triggerdef(oid) AS definition,tgenabled::text AS enabled
 FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgrelid::regclass::text,tgname"""


async def source_objects(conn):
    result = {}
    for sig in OLD_FUNCTIONS:
        row = await conn.fetchrow(FUNCTION_SQL, sig)
        require(row is not None, 'Required V288 function is absent')
        result[sig] = {'properties': dict(row), 'acl': [dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL, sig)]}
    return result


async def snapshot(conn):
    excluded = set(await conn.fetchval('SELECT ARRAY(' + NEW_RELATIONS + ')'))
    new_oids = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n) for n in FUNCTION_GRANTS}
    changed = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n) for n in OLD_FUNCTIONS}
    rows = {}
    for key, sql in BASE_QUERIES.items():
        if key == 'relations':
            sql = sql.replace("WHERE relkind NOT IN ('i','I') ", '')
        values = [dict(r) for r in await conn.fetch(sql)]
        if key == 'relations':
            values = [r for r in values if r['oid'] not in excluded]
        if key == 'columns':
            values = [r for r in values if r['attrelid'] not in excluded]
        if key == 'functions':
            values = [r for r in values if r['oid'] not in new_oids]
            for r in values:
                if r['oid'] in changed:
                    r['definition_digest'] = 'reviewed-release1210-body'
        if key == 'policies':
            values = [r for r in values if r['schemaname'] + '.' + r['tablename'] != TABLE]
        rows[key] = values
    rows['constraints'] = [dict(r) for r in await conn.fetch('''SELECT oid,conrelid,conname,contype::text,convalidated,
        md5(pg_get_constraintdef(oid)) AS definition_digest FROM pg_constraint
        WHERE conrelid NOT IN (''' + NEW_RELATIONS + ''') ORDER BY oid''')]
    rows['triggers'] = [dict(r) for r in await conn.fetch(TRIGGER_SQL)
                        if r['relation'] != TABLE and (r['relation'], r['name']) != NEW_TRIGGER]
    rows['modified_function_properties'] = {}
    for sig, value in (await source_objects(conn)).items():
        props = dict(value['properties']); props.pop('body_sha256')
        rows['modified_function_properties'][sig] = props
    roles = [r for r in rows['roles'] if r['rolname'] in ROLES.values()]
    require(len(roles) == 4 and all(r['rolcanlogin'] and not any(r[k] for k in
            ('rolsuper', 'rolcreaterole', 'rolcreatedb', 'rolreplication', 'rolbypassrls')) for r in roles),
            'Production runtime roles must remain nonprivileged')
    indexes = [dict(r) for r in await conn.fetch('''SELECT indexrelid,indrelid,indisunique,indisprimary,
        indisexclusion,indimmediate,indisvalid,indisready,indislive,indisreplident,
        md5(pg_get_indexdef(indexrelid)) AS definition_digest FROM pg_index ORDER BY indexrelid''')
               if r['indexrelid'] not in excluded]
    return {'format': 'salesbuddy-live-security-v1', 'fingerprints': {k: digest(v) for k, v in rows.items()},
            'runtime_roles_nonprivileged': True, 'index_fingerprint': digest(indexes)}


async def new_objects(conn):
    value = dict(await conn.fetchrow('''SELECT pg_get_userbyid(relowner) AS owner,relkind::text AS kind,
        relrowsecurity AS rls,relforcerowsecurity AS force_rls FROM pg_class WHERE oid=to_regclass($1)''', TABLE))
    value['acl'] = [dict(r) for r in await conn.fetch(ACL_TABLE_SQL, TABLE)]
    value['columns'] = [dict(r) for r in await conn.fetch('''SELECT a.attname AS name,
        format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS not_null,
        pg_get_expr(d.adbin,d.adrelid) AS default_expression,a.attidentity::text AS identity,
        a.attgenerated::text AS generated,a.attacl::text AS acl
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid=to_regclass($1) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''', TABLE)]
    for key, sql in {
        'constraints': 'SELECT conname AS name,pg_get_constraintdef(oid) AS definition,convalidated AS valid FROM pg_constraint WHERE conrelid=to_regclass($1) ORDER BY conname',
        'indexes': 'SELECT pg_get_indexdef(indexrelid) AS definition,indisvalid AS valid,indisready AS ready FROM pg_index WHERE indrelid=to_regclass($1) ORDER BY pg_get_indexdef(indexrelid)',
        'policies': "SELECT policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname||'.'||tablename=$1 ORDER BY policyname",
    }.items():
        value[key] = [dict(r) for r in await conn.fetch(sql, TABLE)]
    result = {'format': 'salesbuddy-release1210-new-objects-v1', 'tables': {TABLE: value}, 'functions': {}}
    for sig in (*FUNCTION_GRANTS, *OLD_FUNCTIONS):
        row = await conn.fetchrow(FUNCTION_SQL, sig)
        require(row is not None, 'New/replaced V292 function is absent')
        result['functions'][sig] = {'properties': dict(row), 'acl': [dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL, sig)]}
    result['triggers'] = [dict(r) for r in await conn.fetch(TRIGGER_SQL)
                          if r['relation'] == TABLE or (r['relation'], r['name']) == NEW_TRIGGER]
    return result


def validate_new_acl(objects, *, allow_ungranted=False):
    require(set(objects) == {'format', 'tables', 'functions', 'triggers'}
            and objects['format'] == 'salesbuddy-release1210-new-objects-v1'
            and set(objects['tables']) == {TABLE}
            and set(objects['functions']) == set(FUNCTION_GRANTS) | set(OLD_FUNCTIONS),
            'Complete exact V292 object receipt required')
    value = objects['tables'][TABLE]
    require(value['owner'] == 'postgres' and value['kind'] == 'r' and value['rls'] is True
            and value['force_rls'] is True and not value['policies']
            and not [a for a in value['acl'] if a['grantee'] != 'postgres']
            and all(c['acl'] is None for c in value['columns']), 'Visibility evidence must remain private and forced RLS')
    for sig, roles in FUNCTION_GRANTS.items():
        v = objects['functions'][sig]; p = v['properties']
        require(p['owner'] == 'postgres' and p['definer'] is True and p['settings'] == ['search_path=pg_catalog'],
                'V292 helper owner/security settings differ')
        observed = {(a['grantee'], a['privilege']) for a in v['acl'] if a['grantee'] != p['owner']}
        wanted = {(ROLES[r], 'EXECUTE') for r in roles}
        require(observed == wanted or (allow_ungranted and not observed), 'Unexpected V292 helper grants')
        require(all(a['grantor'] == p['owner'] and not a['grantable'] for a in v['acl']), 'Unexpected helper grant options')
    require(len(objects['triggers']) == 2
            and {(r['relation'], r['name']) for r in objects['triggers']}
            == {NEW_TRIGGER, (TABLE, 'opportunity_sales_visibility_immutable')}, 'Exact immutable evidence triggers required')


async def verify_effective_privileges(conn):
    for sig, roles in FUNCTION_GRANTS.items():
        for role in ROLES.values():
            require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, sig)
                    is (role in [ROLES[r] for r in roles]), 'Unexpected inherited helper privilege')
    for role in ROLES.values():
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            require(await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', role, TABLE, privilege) is False,
                    'Internal evidence table became runtime-accessible')
        require(not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_attribute a
            WHERE a.attrelid=to_regclass($2) AND a.attnum>0 AND NOT a.attisdropped
            AND (has_column_privilege($1,$2,a.attname,'SELECT') OR has_column_privilege($1,$2,a.attname,'INSERT')
             OR has_column_privilege($1,$2,a.attname,'UPDATE') OR has_column_privilege($1,$2,a.attname,'REFERENCES')))''', role, TABLE),
                'Internal evidence column privilege leaked')


async def business_state(conn):
    """Hash existing business rows inside the server; never return their content."""
    require(conn.is_in_transaction(), 'Business preservation snapshot requires a transaction')
    schemas = ['activity', 'agent', 'common', 'config', 'control', 'crm', 'insight', 'knowledge', 'platform', 'security', 'workflow']
    rows = await conn.fetch('''SELECT n.nspname||'.'||c.relname AS name,
        quote_ident(n.nspname)||'.'||quote_ident(c.relname) AS qualified FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind='r'
        AND n.nspname||'.'||c.relname<>$2 ORDER BY 1''', schemas, TABLE)
    values = {}
    for row in rows:
        values[row['name']] = dict(await conn.fetchrow('''SELECT count(*) AS rows,
            encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') AS sha256
            FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS h
                  FROM ''' + row['qualified'] + ' t) hashed'))
    return {'row_counts': {k: v['rows'] for k, v in values.items()}, 'existing_business_sha256': digest(values)}


def load_plan(plan_ref):
    plan = read_ref(plan_ref); expected = read_ref(plan['policy_ref'])
    contract.validate_acl(plan, expected, digest=digest, require=require)
    objects, source = read_ref(plan['new_objects_ref']), read_ref(plan['source_objects_ref'])
    validate_new_acl(objects)
    require(set(source) == set(OLD_FUNCTIONS), 'Complete V288 replaced function snapshot required')
    contract.validate_restore_evidence(read_ref(plan['restore_evidence_ref']), objects, source, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]; metadata = root/'tools/installation/runtime_permissions.json'
    paths = {str(root/'backend/deploy'/p) for p in contract.GATE_FILES}
    paths |= {str(root/'database/scripts/migrate.py'), str(metadata)}
    paths |= {str(root/'database'/p) for p, _ in contract.SQL.values()}
    require(set(plan.get('source_files', {})) == paths, 'Complete release1210 source closure required')
    for name, checksum in plan['source_files'].items():
        path = Path(name)
        require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                and hashlib.sha256(path.read_bytes()).hexdigest() == checksum, 'Frozen release1210 source changed')
    require(digest(json.loads(metadata.read_bytes())) == contract.INSTALLATION_POLICY_SHA, 'Exact V292 runtime policy required')
    for path, checksum in contract.SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest() == checksum, 'Frozen V290-V292 SQL changed')
    return plan, expected


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), 'Release1210 checks must be transactional')
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity == plan['target'], 'Production database identity changed')
    observed = await snapshot(conn)
    require(observed == expected, 'Preserved production catalog/ACL/RLS/role boundary changed')
    if not final:
        require(await conn.fetchval('SELECT to_regclass($1)', TABLE) is None, 'V292 evidence table already exists')
        for sig in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', sig) is None, 'V290-V292 function already exists')
        require(await source_objects(conn) == read_ref(plan['source_objects_ref']), 'V288 function definitions changed')
    else:
        objects = await new_objects(conn); validate_new_acl(objects, allow_ungranted=allow_ungranted)
        wanted = read_ref(plan['new_objects_ref'])
        if allow_ungranted:
            for sig in FUNCTION_GRANTS:
                objects['functions'][sig]['acl'] = wanted['functions'][sig]['acl']
        require(objects == wanted, 'New/replaced definitions differ from the independent restore')
        if not allow_ungranted:
            await verify_effective_privileges(conn)
    return {'security_preserved': True, 'snapshot_sha256': digest(observed), 'schema': 'V292' if final else 'V288'}


async def grant_execute(conn, *, plan_ref):
    require(conn.is_in_transaction(), 'V292 grants require the migration transaction')
    await verify_state(conn, plan_ref=plan_ref, final=True, allow_ungranted=True)
    for sql in GRANTS:
        await conn.execute(sql)
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), 'V292 verification owns a readonly transaction')
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
    spec = importlib.util.spec_from_file_location('_release1210_migrate', root/'database/scripts/migrate.py')
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    migrations = [m.item('BASELINE_V1', root/'database/baseline/V001__current_schema.sql'),
                  m.item('S001', root/'database/seeds/S001__system_configuration.sql'), *m.discover(root/'database')]
    plan = [{'key': v.key, 'file': v.path.relative_to(root/'database').as_posix(), 'sha256': v.checksum} for v in migrations]
    async def ledger(final=False):
        prior = dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        registered = await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
        contract.validate_migrations(plan, prior, registered, 'V292' if final else 'V288', 'V292',
                                     [] if final else contract.PENDING, digest=digest, require=require)
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL lock_timeout='2s'"); await conn.execute("SET LOCAL statement_timeout='60s'")
        await ledger()
        require(await conn.fetchval('SELECT to_regclass($1)', TABLE) is None, 'Restore already has V292 objects')
        for sig in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', sig) is None, 'Restore already has V290-V292 helpers')
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
    evidence = {'format': 'salesbuddy-release1210-restore-rehearsal-v1', 'status': 'passed',
                'evidence_kind': 'independent_production_restore', 'source_database': {'database': 'sales_saas', 'oid': 18684},
                'restored_database': identity, 'source_backup_sha256': source_backup_sha256,
                'restored_from_production_backup': True, 'source_schema': 'V288', 'target_schema': 'V292',
                'source_ledger_sha256': contract.BASE_COMPLETE_LEDGER, 'target_ledger_sha256': contract.TARGET_COMPLETE_LEDGER,
                'migration_plan_sha256': contract.MIGRATION_PLAN_SHA, 'new_objects_sha256': digest(objects),
                'source_objects_sha256': digest(source), 'security_preserved': True,
                'business_state_preserved': True, 'effective_privileges_verified': True}
    return {'evidence': evidence, 'new_objects': objects, 'source_objects': source,
            'security_snapshot_sha256': digest(before), 'business_state_sha256': digest(data)}
