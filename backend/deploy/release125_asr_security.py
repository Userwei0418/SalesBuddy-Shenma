"""V282-V284 additive ASR grants; retain all pre-existing production security.

No installation matrix is applied. New object definitions are frozen from the
independent restore rehearsal and compared again inside the migration transaction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from release125_asr_contract import GATE_FILES, INSTALLATION_POLICY_SHA, SQL, validate_acl
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require

TABLE_GRANTS = {
    'activity.asr_attempt': {'api': ['SELECT', 'UPDATE'], 'worker': ['SELECT', 'UPDATE']},
    'activity.asr_session': {'api': ['SELECT', 'INSERT'], 'worker': ['SELECT', 'INSERT']},
    'config.asr_budget_policy': {'api': ['SELECT', 'INSERT', 'UPDATE']},
    'config.asr_capacity_resolution': {'api': ['SELECT']},
    'config.asr_hotword_current': {'api': ['SELECT', 'INSERT', 'UPDATE']},
    'config.asr_hotword_operation': {'api': ['SELECT', 'INSERT']},
    'config.asr_hotword_version': {'api': ['SELECT', 'INSERT']},
}
FUNCTION_GRANTS = {
    'security.asr_hotword_snapshot()': ['api', 'worker'],
    'security.asr_unknown_attempts()': ['api'],
    'security.close_asr_transport(uuid)': ['api', 'worker'],
    'security.release_asr_capacity(uuid,uuid,text)': ['api'],
    'security.release_asr_capacity(uuid,uuid,text,text)': ['api'],
    'security.reserve_asr_attempt(uuid,uuid,text,text,text,text,text,numeric)': ['api', 'worker'],
}
OLD_FUNCTIONS = {
    'security.authorization_import(text,uuid)': '91ce5ba74c6256750e908d4c684a516f',
    'security.closed_export_originals(text,boolean,integer,integer)': '9c9e49c38785ce486b1698a9a65a3340',
    'security.closed_export_content_chunk(text,uuid,integer)': '1e23cd3524fe70a866d16423d134e252',
}
GRANTS = tuple(
    f"GRANT {','.join(privileges)} ON TABLE {table} TO {ROLES[role]}"
    for table, roles in TABLE_GRANTS.items() for role, privileges in roles.items()
) + tuple(
    f"GRANT EXECUTE ON FUNCTION {signature} TO {','.join(ROLES[role] for role in roles)}"
    for signature, roles in FUNCTION_GRANTS.items()
)
# Only the seven new table relations, their TOAST storage and their indexes are
# omitted from the old catalog projection. All other relations remain frozen.
NEW_RELATIONS = """WITH t AS (SELECT oid,reltoastrelid FROM pg_class
 WHERE oid=ANY(ARRAY[""" + ','.join("COALESCE(to_regclass('" + name + "'),0)" for name in TABLE_GRANTS) + """ ]::oid[])),
 r AS (SELECT oid FROM t UNION SELECT reltoastrelid FROM t WHERE reltoastrelid<>0)
 SELECT oid FROM r UNION SELECT indexrelid FROM pg_index WHERE indrelid IN (SELECT oid FROM r)"""
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
    require(objects.get('format') == 'salesbuddy-asr-new-objects-v1'
            and set(objects.get('tables', {})) == set(TABLE_GRANTS)
            and set(objects.get('functions', {})) == set(FUNCTION_GRANTS) | set(OLD_FUNCTIONS),
            'Complete restored V284 object contract required')
    root = Path(__file__).resolve().parents[2]
    metadata = root/'tools/installation/runtime_permissions.json'
    sources = {str(root/'backend/deploy'/name) for name in GATE_FILES}
    sources.update((str(root/'database/scripts/migrate.py'), str(metadata)))
    sources.update(str(root/'database'/path) for path, _ in SQL.values())
    require(set(plan.get('source_files', {})) == sources, 'Complete ASR security source closure required')
    for name, checksum in plan['source_files'].items():
        source = Path(name)
        require(source.is_file() and not any(p.is_symlink() for p in (source, *source.parents))
                and hashlib.sha256(source.read_bytes()).hexdigest() == checksum, 'ASR security source changed')
    require(digest(json.loads(metadata.read_bytes())) == INSTALLATION_POLICY_SHA, 'Exact V284 installation metadata required')
    for path, checksum in SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest() == checksum,
                'Frozen ASR migration changed')
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
                if r['oid'] in old_functions: r['definition_digest'] = 'reviewed-v283-body-change'
        if key == 'policies': values = [r for r in values if r['schemaname']+'.'+r['tablename'] not in TABLE_GRANTS]
        rows[key] = values
    rows['modified_function_properties'] = {}
    for signature in OLD_FUNCTIONS:
        row = await conn.fetchrow(FUNCTION_SQL, signature)
        require(row is not None, 'Existing V283 security function missing')
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
    result = {'format': 'salesbuddy-asr-new-objects-v1', 'tables': {}, 'functions': {}}
    for table in TABLE_GRANTS:
        row = await conn.fetchrow('''SELECT pg_get_userbyid(relowner) AS owner,
            relkind::text AS kind,relrowsecurity AS rls,relforcerowsecurity AS force_rls
            FROM pg_class WHERE oid=to_regclass($1)''', table)
        require(row is not None, 'New ASR table missing')
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
        require(row is not None, 'New or replaced ASR function missing')
        result['functions'][signature] = {'properties': dict(row), 'acl': [dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL, signature)]}
    result['visit_import_columns'] = [dict(r) for r in await conn.fetch('''SELECT a.attname AS name,
        format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS not_null,
        pg_get_expr(d.adbin,d.adrelid) AS default_expression,a.attacl::text AS acl
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid='activity.visit_import'::regclass AND a.attname IN ('source_kind','asr_session_id') ORDER BY a.attname''')]
    result['visit_import_constraints'] = [dict(r) for r in await conn.fetch('''SELECT conname AS name,
        pg_get_constraintdef(oid) AS definition,convalidated AS valid FROM pg_constraint
        WHERE conrelid='activity.visit_import'::regclass AND conname IN ('visit_import_source_kind_check','visit_import_asr_session_fk') ORDER BY conname''')]
    return result


def validate_new_acl(objects, *, allow_ungranted=False):
    """Reject PUBLIC, extra roles/privileges or grant options before/after grants."""
    owner = 'postgres'
    for table, permissions in TABLE_GRANTS.items():
        value = objects['tables'][table]
        require(all(value[k] is True for k in ('rls','force_rls')) and value['owner'] == owner and value['kind'] == 'r',
                'ASR tables require maintenance ownership and forced tenant RLS')
        observed = {(r['grantee'], r['privilege']) for r in value['acl'] if r['grantee'] != owner}
        wanted = {(ROLES[role], privilege) for role, privileges in permissions.items() for privilege in privileges}
        require(observed == wanted or (allow_ungranted and not observed), 'Unexpected new ASR table grants')
        require(all(r['grantor'] == owner and not r['grantable'] for r in value['acl']), 'Unexpected table grant owner/option')
        require(all(r['acl'] is None for r in value['columns']), 'Unexpected ASR column ACL')
    for signature, roles in FUNCTION_GRANTS.items():
        value = objects['functions'][signature]; props = value['properties']
        require(props['owner'] == owner and props['definer'] is True and props['settings'] == ['search_path=pg_catalog'],
                'ASR function owner or search path changed')
        observed = {(r['grantee'],r['privilege']) for r in value['acl'] if r['grantee'] != owner}
        wanted = {(ROLES[role], 'EXECUTE') for role in roles}
        require(observed == wanted or (allow_ungranted and not observed), 'Unexpected new ASR function grants')
        require(all(r['grantor'] == owner and not r['grantable'] for r in value['acl']), 'Unexpected function grant owner/option')


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), 'ASR security verification must be transactional')
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow('''SELECT current_database() AS database,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid'''))
    require(identity == plan['target'], 'ASR database identity changed')
    observed = await snapshot(conn)
    require(observed == expected, 'Existing roles, ACL, RLS, indexes or function security changed')
    if not final:
        for name in TABLE_GRANTS:
            require(await conn.fetchval('SELECT to_regclass($1)', name) is None, 'ASR table exists before migration')
        for signature in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', signature) is None, 'ASR function exists before migration')
        for signature, checksum in OLD_FUNCTIONS.items():
            require(await conn.fetchval('SELECT md5(prosrc) FROM pg_proc WHERE oid=to_regprocedure($1)', signature) == checksum,
                    'V283 prior security function body changed')
    else:
        objects = await new_objects(conn)
        validate_new_acl(objects, allow_ungranted=allow_ungranted)
        expected_objects = read_ref(plan['new_objects_ref'])
        # Before grants only the fixed six function/seven table ACLs may differ.
        if allow_ungranted:
            for table in TABLE_GRANTS: objects['tables'][table]['acl'] = expected_objects['tables'][table]['acl']
            for signature in FUNCTION_GRANTS: objects['functions'][signature]['acl'] = expected_objects['functions'][signature]['acl']
        require(objects == expected_objects, 'V284 objects differ from the frozen restored definitions')
        if not allow_ungranted:
            for signature, roles in FUNCTION_GRANTS.items():
                for role in ROLES.values():
                    require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
                            is (role in [ROLES[r] for r in roles]), 'Unexpected effective ASR function privilege')
            for table, permissions in TABLE_GRANTS.items():
                for role_key, role in ROLES.items():
                    for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                        require(await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', role, table, privilege)
                                is (privilege in permissions.get(role_key, [])), 'Unexpected effective ASR table privilege')
    return {'security_preserved': True, 'snapshot_sha256': digest(observed), 'schema': 'V284' if final else 'V281'}


async def grant_execute(conn, *, plan_ref):
    """Exact table and function delta, within the caller's sole migration commit."""
    require(conn.is_in_transaction(), 'ASR grants require the migration transaction')
    await verify_state(conn, plan_ref=plan_ref, final=True, allow_ungranted=True)
    for sql in GRANTS: await conn.execute(sql)
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), 'ASR verification needs its own readonly transaction')
    tx = conn.transaction(isolation='repeatable_read', readonly=True); await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='10s'")
        observed = await verify_state(conn, plan_ref=plan_ref, final=final)
    finally:
        await tx.rollback()
    return {'status': 'verified_readonly', 'rolled_back': True, 'committed': False, **observed}
