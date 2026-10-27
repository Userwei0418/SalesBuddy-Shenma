"""V285-V286 exact task grants; retain all pre-existing production security.

No installation matrix is applied. New object definitions are frozen from the
independent restore rehearsal and compared again inside the migration transaction.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import release126_task_contract as contract
from release126_task_contract import GATE_FILES, INSTALLATION_POLICY_SHA, SQL, validate_acl, validate_restore_evidence
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require

TABLE_GRANTS = {'ops.task_coordination_scan_cursor': {}}
FUNCTION_GRANTS = {
    'workflow.task_execution_validate_definition_v2(jsonb,jsonb)': [],
    'workflow.task_execution_initialize_v2(uuid,jsonb,boolean)': ['api'],
    'workflow.task_execution_adopt_v2(uuid,jsonb,uuid)': [],
    'workflow.task_execution_coordination_status(uuid)': [],
    'workflow.task_execution_coordination_recipients(uuid)': [],
    'workflow.task_execution_enqueue_coordination_checks(integer)': ['worker'],
}
OLD_FUNCTIONS = {
    'security.task_actor_recipient(uuid)': '838fcb001e39ec58c0a24525760e00d4',
    'security.authorization_task(text,uuid)': '623b9f56dcb857a270aeb591b8f815bb',
    'workflow.task_execution_capabilities(uuid)': 'f1ccf0d7df79b62d6cc42a3c9bcfe387',
    'workflow.task_execution_summary(uuid)': '01ae47f4c45c2bb968a7f76fcb429f52',
    'workflow.task_execution_candidate(uuid,uuid,text)': '212c39d8786a24c98de881fd2979a635',
    'workflow.task_execution_command(uuid,text,jsonb,uuid)': '173dedc4543b64cbdefe85b841724683',
    'workflow.guard_task_execution_legacy_write()': 'de6017b410bc5d82ad82e38eac471cf8',
    'workflow.require_task_execution_initialization()': '432f9af262804d4329ef872258351362',
    'workflow.close_task_execution_on_cancel()': '2df5d1a569fdb626d29d109f38d81c0c',
}
TASK_INDEX = 'workflow.task_execution_v2_scan'
TASK_CONSTRAINT = 'task_execution_mode_check'
GRANTS = tuple(
    f"GRANT {','.join(privileges)} ON TABLE {table} TO {ROLES[role]}"
    for table, roles in TABLE_GRANTS.items() for role, privileges in roles.items()
) + tuple(
    f"GRANT EXECUTE ON FUNCTION {signature} TO {','.join(ROLES[role] for role in roles)}"
    for signature, roles in FUNCTION_GRANTS.items() if roles
)
# Only the private cursor table/storage and exact new task index are excluded.
# Their final definitions are frozen in the independent restore receipt.
NEW_RELATIONS = """WITH t AS (SELECT oid,reltoastrelid FROM pg_class
 WHERE oid=ANY(ARRAY[""" + ','.join("COALESCE(to_regclass('" + name + "'),0)" for name in TABLE_GRANTS) + """ ]::oid[])),
 r AS (SELECT oid FROM t UNION SELECT reltoastrelid FROM t WHERE reltoastrelid<>0)
 SELECT oid FROM r UNION SELECT indexrelid FROM pg_index WHERE indrelid IN (SELECT oid FROM r)
 UNION SELECT COALESCE(to_regclass('workflow.task_execution_v2_scan'),0)"""
FUNCTION_SQL = """SELECT pg_get_userbyid(p.proowner) AS owner,l.lanname AS language,
 p.prokind::text AS kind,p.prosecdef AS definer,p.proleakproof AS leakproof,
 p.proisstrict AS strict,p.proretset AS returns_set,p.provolatile::text AS volatility,
 p.proparallel::text AS parallel,pg_get_function_result(p.oid) AS result,
 p.proargtypes::text AS argument_types,p.proargnames AS argument_names,
 p.proallargtypes::text AS all_argument_types,p.proargmodes::text AS argument_modes,
 p.pronargdefaults AS defaults,p.proconfig AS settings,
 encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') AS body_sha256
 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure($1)"""
ACL_FUNCTION_SQL = """SELECT pg_get_userbyid(a.grantor) AS grantor,
 CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
 a.privilege_type AS privilege,a.is_grantable AS grantable
 FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE p.oid=to_regprocedure($1) ORDER BY grantee,grantor,privilege"""
ACL_TABLE_SQL = ACL_FUNCTION_SQL.replace('pg_proc p', 'pg_class p').replace("p.proacl,acldefault('f',p.proowner)", "p.relacl,acldefault('r',p.relowner)").replace('to_regprocedure', 'to_regclass')


def load_plan(plan_ref):
    plan = read_ref(plan_ref)
    expected = read_ref(plan['policy_ref'])
    validate_acl(plan, expected, digest=digest, require=require)
    objects = read_ref(plan['new_objects_ref'])
    require(objects.get('format') == 'salesbuddy-task-new-objects-v1'
            and set(objects) == {'format','tables','functions','task_constraint','task_index'}
            and set(objects.get('tables', {})) == set(TABLE_GRANTS)
            and set(objects.get('functions', {})) == set(FUNCTION_GRANTS) | set(OLD_FUNCTIONS),
            'Complete restored V286 object contract required')
    validate_new_acl(objects)
    validate_restore_evidence(read_ref(plan['restore_evidence_ref']), objects, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]
    metadata = root/'tools/installation/runtime_permissions.json'
    sources = {str(root/'backend/deploy'/name) for name in GATE_FILES}
    sources.update((str(root/'database/scripts/migrate.py'), str(metadata)))
    sources.update(str(root/'database'/path) for path, _ in SQL.values())
    require(set(plan.get('source_files', {})) == sources, 'Complete Task security source closure required')
    for name, checksum in plan['source_files'].items():
        source = Path(name)
        require(source.is_file() and not any(p.is_symlink() for p in (source, *source.parents))
                and hashlib.sha256(source.read_bytes()).hexdigest() == checksum, 'Task security source changed')
    require(digest(json.loads(metadata.read_bytes())) == INSTALLATION_POLICY_SHA, 'Exact V286 installation metadata required')
    for path, checksum in SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest() == checksum,
                'Frozen Task migration changed')
    return plan, expected


async def snapshot(conn):
    excluded_relations = set(await conn.fetchval('SELECT ARRAY(' + NEW_RELATIONS + ')'))
    new_functions = {await conn.fetchval('SELECT to_regprocedure($1)::oid', name) for name in FUNCTION_GRANTS}
    old_functions = {await conn.fetchval('SELECT to_regprocedure($1)::oid', name) for name in OLD_FUNCTIONS}
    rows = {}
    for key, sql in BASE_QUERIES.items():
        if key == 'relations':
            sql = sql.replace("WHERE relkind NOT IN ('i','I') ", '')
        values = [dict(r) for r in await conn.fetch(sql)]
        if key == 'relations': values = [r for r in values if r['oid'] not in excluded_relations]
        if key == 'columns': values = [r for r in values if r['attrelid'] not in excluded_relations]
        if key == 'functions':
            values = [r for r in values if r['oid'] not in new_functions]
            for r in values:
                if r['oid'] in old_functions: r['definition_digest'] = 'reviewed-v285-body-change'
        if key == 'policies': values = [r for r in values if r['schemaname']+'.'+r['tablename'] not in TABLE_GRANTS]
        rows[key] = values
    rows['constraints'] = [dict(r) for r in await conn.fetch('''SELECT oid,conrelid,conname,
        contype::text,convalidated,md5(pg_get_constraintdef(oid)) AS definition_digest
        FROM pg_constraint WHERE conrelid NOT IN (SELECT oid FROM (''' + NEW_RELATIONS + ''') excluded WHERE oid<>0)
        AND NOT(conrelid='workflow.task'::regclass AND conname='task_execution_mode_check') ORDER BY oid''')]
    rows['triggers'] = [dict(r) for r in await conn.fetch('''SELECT oid,tgrelid,tgname,tgenabled::text,
        md5(pg_get_triggerdef(oid)) AS definition_digest FROM pg_trigger
        WHERE NOT tgisinternal AND tgrelid NOT IN (''' + NEW_RELATIONS + ''') ORDER BY oid''')]
    rows['modified_function_properties'] = {}
    for signature in OLD_FUNCTIONS:
        row = await conn.fetchrow(FUNCTION_SQL, signature)
        require(row is not None, 'Existing V285 security function missing')
        value = dict(row); value.pop('body_sha256')
        rows['modified_function_properties'][signature] = value
    roles = [row for row in rows['roles'] if row['rolname'] in ROLES.values()]
    require(len(roles) == 4 and all(row['rolcanlogin'] and not any(row[k] for k in
        ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')) for row in roles),
        'Existing runtime roles are absent or privileged')
    indexes = [dict(r) for r in await conn.fetch('''SELECT indexrelid,indrelid,indisunique,indisprimary,
        indisexclusion,indimmediate,indisvalid,indisready,indislive,indisreplident,
        md5(pg_get_indexdef(indexrelid)) AS definition_digest FROM pg_index ORDER BY indexrelid''')
        if r['indexrelid'] not in excluded_relations]
    return {'format': 'salesbuddy-live-security-v1', 'fingerprints': {k:digest(v) for k,v in rows.items()},
            'runtime_roles_nonprivileged': True, 'index_fingerprint': digest(indexes)}


async def new_objects(conn):
    """Nonsecret, OID-independent structure receipt from the isolated restore."""
    result = {'format': 'salesbuddy-task-new-objects-v1', 'tables': {}, 'functions': {}}
    for table in TABLE_GRANTS:
        row = await conn.fetchrow('''SELECT pg_get_userbyid(relowner) AS owner,
            relkind::text AS kind,relrowsecurity AS rls,relforcerowsecurity AS force_rls
            FROM pg_class WHERE oid=to_regclass($1)''', table)
        require(row is not None, 'New Task table missing')
        value = dict(row)
        value['acl'] = [dict(r) for r in await conn.fetch(ACL_TABLE_SQL, table)]
        value['columns'] = [dict(r) for r in await conn.fetch('''SELECT a.attname AS name,
            format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS not_null,
            pg_get_expr(d.adbin,d.adrelid) AS default_expression,a.attidentity::text AS identity,
            a.attgenerated::text AS generated,a.attacl::text AS acl
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass($1) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''', table)]
        for key, sql in {
            'constraints': 'SELECT conname AS name,pg_get_constraintdef(oid) AS definition,convalidated AS valid FROM pg_constraint WHERE conrelid=to_regclass($1) ORDER BY conname',
            'indexes': 'SELECT pg_get_indexdef(indexrelid) AS definition,indisvalid AS valid,indisready AS ready FROM pg_index WHERE indrelid=to_regclass($1) ORDER BY pg_get_indexdef(indexrelid)',
            'triggers': 'SELECT tgname AS name,pg_get_triggerdef(oid) AS definition,tgenabled::text AS enabled FROM pg_trigger WHERE tgrelid=to_regclass($1) AND NOT tgisinternal ORDER BY tgname',
            'policies': "SELECT policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname||'.'||tablename=$1 ORDER BY policyname",
        }.items(): value[key] = [dict(r) for r in await conn.fetch(sql, table)]
        result['tables'][table] = value
    for signature in (*FUNCTION_GRANTS, *OLD_FUNCTIONS):
        row = await conn.fetchrow(FUNCTION_SQL, signature)
        require(row is not None, 'New or replaced Task function missing')
        result['functions'][signature] = {'properties': dict(row), 'acl': [dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL, signature)]}
    result['task_constraint'] = [dict(r) for r in await conn.fetch("""SELECT conname AS name,
        pg_get_constraintdef(oid) AS definition,convalidated AS valid FROM pg_constraint
        WHERE conrelid='workflow.task'::regclass AND conname='task_execution_mode_check'""")]
    result['task_index'] = [dict(r) for r in await conn.fetch("""SELECT pg_get_indexdef(indexrelid) AS definition,
        indisvalid AS valid,indisready AS ready,indisunique AS unique,pg_get_userbyid(c.relowner) AS owner,
        pg_get_userbyid(t.relowner) AS table_owner,c.relacl::text AS acl
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
        WHERE i.indexrelid=to_regclass($1) AND t.oid='workflow.task'::regclass""", TASK_INDEX)]
    return result


def validate_new_acl(objects, *, allow_ungranted=False):
    """Reject PUBLIC, extra roles/privileges or grant options before/after grants."""
    owner = 'postgres'
    for table, permissions in TABLE_GRANTS.items():
        value = objects['tables'][table]
        require(all(value[k] is False for k in ('rls','force_rls')) and value['owner'] == owner and value['kind'] == 'r',
                'Private task cursor requires maintenance ownership; no tenant/runtime table access')
        observed = {(r['grantee'], r['privilege']) for r in value['acl'] if r['grantee'] != owner}
        wanted = {(ROLES[role], privilege) for role, privileges in permissions.items() for privilege in privileges}
        require(observed == wanted or (allow_ungranted and not observed), 'Unexpected new Task table grants')
        require(all(r['grantor'] == owner and not r['grantable'] for r in value['acl']), 'Unexpected table grant owner/option')
        require(all(r['acl'] is None for r in value['columns']), 'Unexpected Task column ACL')
        require([r['name'] for r in value['columns']] == ['singleton','last_task_id','updated_at']
                and not value['policies'] and not value['triggers'], 'Unexpected private cursor structure')
    for signature, roles in FUNCTION_GRANTS.items():
        value = objects['functions'][signature]; props = value['properties']
        require(props['owner'] == owner and props['definer'] is (signature != 'workflow.task_execution_validate_definition_v2(jsonb,jsonb)')
                and props['settings'] == (['search_path=pg_catalog', 'app.workspace_id='] if signature in
                    ('workflow.task_execution_coordination_status(uuid)', 'workflow.task_execution_coordination_recipients(uuid)') else ['search_path=pg_catalog']),
                'Task function owner or search path changed')
        observed = {(r['grantee'],r['privilege']) for r in value['acl'] if r['grantee'] != owner}
        wanted = {(ROLES[role], 'EXECUTE') for role in roles}
        require(observed == wanted or (allow_ungranted and not observed), 'Unexpected new Task function grants')
        require(all(r['grantor'] == owner and not r['grantable'] for r in value['acl']), 'Unexpected function grant owner/option')
    require(len(objects['task_constraint']) == 1 and objects['task_constraint'][0]['name'] == TASK_CONSTRAINT
            and objects['task_constraint'][0]['valid'] is True, 'Exact validated task mode constraint required')
    require(len(objects['task_index']) == 1 and bool(objects['task_index'][0].get('table_owner'))
            and objects['task_index'][0]['owner'] == objects['task_index'][0]['table_owner']
            and objects['task_index'][0]['acl'] is None and objects['task_index'][0]['valid'] is True
            and objects['task_index'][0]['ready'] is True and objects['task_index'][0]['unique'] is False,
            'Exact valid nonunique task scan index required')


async def verify_effective_privileges(conn):
    """Check inheritance as well as direct ACLs; never authorize private helpers."""
    for signature, roles in FUNCTION_GRANTS.items():
        for role in ROLES.values():
            require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
                    is (role in [ROLES[r] for r in roles]), 'Unexpected effective task function privilege')
    for role in ROLES.values():
        for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
            require(await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', role,
                    'ops.task_coordination_scan_cursor', privilege) is False, 'Private cursor became runtime-accessible')


async def business_state(conn):
    """Only server-side hashes/counts leave the DB; no business payload is read out.

    Existing business row counts are frozen. Task/history, ASR and DB configuration
    additionally get an order-independent full-row hash, including accepted_at.
    No notifications/commands/compensation are executed to collect this receipt.
    """
    require(conn.is_in_transaction(), 'Business preservation snapshot requires a transaction')
    schemas = ['activity','agent','common','config','control','crm','insight','knowledge','platform','security','workflow']
    rows = await conn.fetch("""SELECT n.nspname AS schema,c.relname AS name,
        quote_ident(n.nspname)||'.'||quote_ident(c.relname) AS qualified
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind='r' ORDER BY 1,2""", schemas)
    counts, states = {}, {}
    for row in rows:
        name = row['schema']+'.'+row['name']; qualified = row['qualified']
        if (row['schema'] == 'config' or row['schema'] == 'workflow' and row['name'].startswith('task')
                or row['schema'] == 'activity' and (row['name'].startswith('asr_') or row['name'] == 'visit_import')):
            value = dict(await conn.fetchrow("""SELECT count(*) AS rows,
                encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') AS sha256
                FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS h
                      FROM """ + qualified + ' t) hashed'))
            counts[name] = value['rows']; states[name] = value['sha256']
        else:
            counts[name] = await conn.fetchval('SELECT count(*) FROM '+qualified)
    return {'row_counts': counts, 'task_asr_configuration_sha256': digest(states)}


async def rehearse(conn, *, expected_database, source_backup_sha256):
    """Upgrade an explicitly named isolated restore, never the production DB.

    Caller restores a fresh production backup and closes application access first.
    Returns structure/attestation only. The helper never opens or restores a dump.
    """
    require(not conn.is_in_transaction(), 'Restore rehearsal owns its transaction')
    identity = dict(await conn.fetchrow("""SELECT current_database() AS database,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid"""))
    require(identity == expected_database and identity['database'] != 'sales_saas' and identity['oid'] != 18684
            and await conn.fetchval('SELECT current_user::text') == 'postgres'
            and contract._hex(source_backup_sha256, 64), 'Explicit isolated restore identity and backup digest required')
    require(not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
        WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend')"""),
        'Other restore database client present')
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('_task126_restore_migrate', root/'database/scripts/migrate.py')
    migrator = importlib.util.module_from_spec(spec); sys.modules[spec.name] = migrator; spec.loader.exec_module(migrator)
    migrations = [migrator.item('BASELINE_V1', root/'database/baseline/V001__current_schema.sql'),
                  migrator.item('S001', root/'database/seeds/S001__system_configuration.sql'),
                  *migrator.discover(root/'database')]
    plan = [{'key':m.key,'file':m.path.relative_to(root/'database').as_posix(),'sha256':m.checksum} for m in migrations]
    async def verify_ledger(final=False):
        prior = dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        registered = await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
        contract.validate_migrations(plan, prior, registered, 'V286' if final else 'V284', 'V286',
            [] if final else contract.PENDING, digest=digest, require=require)
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL lock_timeout='2s'")
        await conn.execute("SET LOCAL statement_timeout='30s'")
        await verify_ledger()
        for signature, checksum in OLD_FUNCTIONS.items():
            require(await conn.fetchval('SELECT md5(prosrc) FROM pg_proc WHERE oid=to_regprocedure($1)', signature) == checksum,
                    'Restored V284 task function body differs')
        before, data = await snapshot(conn), await business_state(conn)
        for migration in migrations:
            if migration.key in contract.PENDING:
                require(await migrator.apply(conn, migration, root/'database') == 'applied', 'Restore migration not freshly applied')
        require(await snapshot(conn) == before, 'Restore changed old security/catalogs')
        validate_new_acl(await new_objects(conn), allow_ungranted=True)
        for sql in GRANTS:
            await conn.execute(sql)
        objects = await new_objects(conn); validate_new_acl(objects)
        await verify_effective_privileges(conn)
        require(await snapshot(conn) == before and await business_state(conn) == data, 'Restore changed business/security state')
        await verify_ledger(final=True)
    evidence = {'format':'salesbuddy-task-restore-rehearsal-v1','status':'passed',
        'source_database':{'database':'sales_saas','oid':18684},'restored_database':identity,
        'source_backup_sha256':source_backup_sha256,'restored_from_production_backup':True,
        'source_schema':'V284','target_schema':'V286','source_ledger_sha256':contract.BASE_COMPLETE_LEDGER,
        'target_ledger_sha256':contract.TARGET_COMPLETE_LEDGER,'migration_plan_sha256':contract.MIGRATION_PLAN_SHA,
        'new_objects_sha256':digest(objects),'security_preserved':True,'business_state_preserved':True,
        'effective_privileges_verified':True}
    return {'evidence':evidence,'new_objects':objects,'security_snapshot_sha256':digest(before),
            'business_state_sha256':digest(data)}


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), 'Task security verification must be transactional')
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow('''SELECT current_database() AS database,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid'''))
    require(identity == plan['target'], 'Task database identity changed')
    observed = await snapshot(conn)
    require(observed == expected, 'Existing roles, ACL, RLS, indexes or function security changed')
    if not final:
        require(await conn.fetchval('SELECT to_regclass($1)', TASK_INDEX) is None, 'Task scan index exists before migration')
        for name in TABLE_GRANTS:
            require(await conn.fetchval('SELECT to_regclass($1)', name) is None, 'Task table exists before migration')
        for signature in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', signature) is None, 'Task function exists before migration')
        for signature, checksum in OLD_FUNCTIONS.items():
            require(await conn.fetchval('SELECT md5(prosrc) FROM pg_proc WHERE oid=to_regprocedure($1)', signature) == checksum,
                    'V285 prior security function body changed')
    else:
        objects = await new_objects(conn)
        validate_new_acl(objects, allow_ungranted=allow_ungranted)
        expected_objects = read_ref(plan['new_objects_ref'])
        # Before grants only the two approved entrypoint ACLs may differ.
        if allow_ungranted:
            for table in TABLE_GRANTS: objects['tables'][table]['acl'] = expected_objects['tables'][table]['acl']
            for signature in FUNCTION_GRANTS: objects['functions'][signature]['acl'] = expected_objects['functions'][signature]['acl']
        require(objects == expected_objects, 'V286 objects differ from the frozen restored definitions')
        if not allow_ungranted:
            await verify_effective_privileges(conn)
    return {'security_preserved': True, 'snapshot_sha256': digest(observed), 'schema': 'V286' if final else 'V284'}


async def grant_execute(conn, *, plan_ref):
    """Exact table and function delta, within the caller's sole migration commit."""
    require(conn.is_in_transaction(), 'Task grants require the migration transaction')
    await verify_state(conn, plan_ref=plan_ref, final=True, allow_ungranted=True)
    for sql in GRANTS: await conn.execute(sql)
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), 'Task verification needs its own readonly transaction')
    tx = conn.transaction(isolation='repeatable_read', readonly=True); await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='10s'")
        observed = await verify_state(conn, plan_ref=plan_ref, final=final)
    finally:
        await tx.rollback()
    return {'status': 'verified_readonly', 'rolled_back': True, 'committed': False, **observed}
