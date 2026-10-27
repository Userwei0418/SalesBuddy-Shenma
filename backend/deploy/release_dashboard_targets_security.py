"""Exact V293 -> V296 recognized actuals, coverage and targets, preserving existing catalog and business rows.

Only six existing function bodies, one view, two nullable columns, one trigger and five helpers may change.
Only the two new API entry points receive EXECUTE. No installation matrix is replayed. Old-code recovery on V296 retains V296
semantics; a schema rollback requires a separately reviewed ledger/body plan.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import release_dashboard_targets_contract as contract
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES, ROLES, read_ref, require
from release126_task_security import FUNCTION_SQL as BASE_FUNCTION_SQL, ACL_FUNCTION_SQL

# Body normalization must not permit a change to defaults, costs or support
# functions on the six existing signatures.
FUNCTION_SQL = BASE_FUNCTION_SQL.replace(
    'p.pronargdefaults AS defaults,p.proconfig AS settings,',
    '''p.pronargdefaults AS defaults,p.proconfig AS settings,
 p.procost AS cost,p.prorows AS estimated_rows,p.provariadic::text AS variadic_type,
 p.prosupport::text AS support,p.probin AS library,p.prosqlbody::text AS parsed_body,
 pg_get_expr(p.proargdefaults,0) AS default_expressions,''',
)

FUNCTION_GRANTS = {
    'security.recognized_period_attribution(uuid,integer,integer)': [],
    'crm.guard_actual_attribution()': [],
    'security.opportunity_recognized_edit_scope(uuid)': [],
    'security.opportunity_recognized_actual(uuid,integer,integer)': ['api'],
    'security.save_opportunity_recognized_actual(uuid,integer,integer,jsonb,text,boolean)': ['api'],
}
OLD_FUNCTIONS = (
    'crm.guard_actual_confirmation()', 'crm.guard_opportunity_actual_basis()',
    'security.transfer_preserve_attribution(uuid,uuid,text,text,integer,integer,text,jsonb,jsonb)',
    'security.dashboard_team_ranking(text,date,date,integer[],uuid[],boolean)',
    'security.dashboard_subject_ranking(text,date,date,integer[],uuid)',
    'security.save_target_batch(text,uuid,uuid,text,date,date,jsonb,text,text)',
)
NEW_COLUMNS = {'crm.opportunity_actual_basis': 'attribution_snapshot', 'crm.customer_actual': 'attribution_snapshot'}
NEW_CONSTRAINTS = {'crm.opportunity_actual_basis': 'opportunity_actual_basis_attribution_snapshot_check',
                   'crm.customer_actual': 'customer_actual_attribution_snapshot_check'}
NEW_TRIGGER = ('crm.customer_actual', 'actual_attribution_guard')
VIEW = 'crm.effective_actual'
COLUMN_SQL = """SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,
 a.attnotnull AS not_null,a.attidentity::text AS identity,a.attgenerated::text AS generated,
 a.attacl::text AS acl,pg_get_expr(d.adbin,d.adrelid) AS default_expression
 FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid=to_regclass($1) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""
GRANTS = tuple(f"GRANT EXECUTE ON FUNCTION {sig} TO {','.join(ROLES[r] for r in roles)}"
               for sig, roles in FUNCTION_GRANTS.items() if roles)

TRIGGER_SQL = """SELECT tgrelid::regclass::text AS relation,tgname AS name,
 pg_get_triggerdef(oid) AS definition,tgenabled::text AS enabled
 FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgrelid::regclass::text,tgname"""

# pg_dump restore can add the redundant default column label to these UNION
# constant projections. Do not normalize whitespace, arbitrary aliases or SQL.
_VIEW_REDUNDANT_ALIAS = re.compile(
    r"(?m)^[ \t]*(?P<literal>'(?:unknown|exclusive|explicit_empty_population)'::text)"
    r"(?P<alias> AS text),(?=\r?$)"
)
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


def normalize_effective_actual_definition(definition):
    """Remove only reviewed redundant aliases, outside SQL literals/comments."""
    require(isinstance(definition, str), 'Effective actual view definition required')
    candidates = {m.start('literal'): m for m in _VIEW_REDUNDANT_ALIAS.finditer(definition)}
    pieces, start, i, length = [], 0, 0, len(definition)
    while i < length:
        match = candidates.get(i)
        if match is not None:
            pieces.append(definition[start:match.start('alias')])
            start = match.end('alias')
            i = match.end()
            continue
        if definition.startswith('--', i):
            end = definition.find('\n', i + 2)
            i = length if end < 0 else end + 1
        elif definition.startswith('/*', i):
            depth, i = 1, i + 2
            while i < length and depth:
                if definition.startswith('/*', i): depth, i = depth + 1, i + 2
                elif definition.startswith('*/', i): depth, i = depth - 1, i + 2
                else: i += 1
        elif definition[i] in ("'", '"'):
            quote = definition[i]
            escaped = quote == "'" and i > 0 and definition[i - 1] in 'eE' and (
                i < 2 or not (definition[i - 2].isalnum() or definition[i - 2] == '_'))
            i += 1
            while i < length:
                if escaped and definition[i] == '\\': i += 2
                elif definition[i] == quote:
                    i += 1
                    if i < length and definition[i] == quote: i += 1
                    else: break
                else: i += 1
        elif definition[i] == '$' and (quoted := _DOLLAR_QUOTE.match(definition, i)):
            end = definition.find(quoted.group(), quoted.end())
            i = length if end < 0 else end + len(quoted.group())
        else:
            i += 1
    pieces.append(definition[start:])
    return ''.join(pieces)


async def effective_actual_view(conn):
    return {'definition': normalize_effective_actual_definition(
                await conn.fetchval('SELECT pg_get_viewdef($1::regclass,true)', VIEW)),
            'columns': [dict(row) for row in await conn.fetch(COLUMN_SQL, VIEW)]}


async def source_objects(conn):
    result = {}
    for sig in OLD_FUNCTIONS:
        row = await conn.fetchrow(FUNCTION_SQL, sig)
        require(row is not None, 'Required V293 function is absent')
        result[sig] = {'properties': dict(row), 'acl': [dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL, sig)]}
    result[VIEW] = await effective_actual_view(conn)
    return result


async def snapshot(conn):
    new_oids = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n) for n in FUNCTION_GRANTS}
    changed = {await conn.fetchval('SELECT to_regprocedure($1)::oid', n) for n in OLD_FUNCTIONS}
    rows = {}
    for key, sql in BASE_QUERIES.items():
        if key == 'relations':
            sql = sql.replace("WHERE relkind NOT IN ('i','I') ", '').replace(
                'relforcerowsecurity', 'relforcerowsecurity,reloptions::text')
        values = [dict(r) for r in await conn.fetch(sql)]
        if key == 'functions':
            values = [r for r in values if r['oid'] not in new_oids]
            for r in values:
                if r['oid'] in changed: r['definition_digest'] = 'reviewed-dashboard-targets-body'
        rows[key] = values
    constraints = [dict(r) for r in await conn.fetch("""SELECT oid,conrelid,conrelid::regclass::text AS relation,
        conname,contype::text,convalidated,md5(pg_get_constraintdef(oid)) AS definition_digest
        FROM pg_constraint ORDER BY oid""")]
    rows['constraints'] = [r for r in constraints if NEW_CONSTRAINTS.get(r['relation']) != r['conname']]
    rows['triggers'] = [dict(r) for r in await conn.fetch(TRIGGER_SQL)
                        if (r['relation'],r['name']) != NEW_TRIGGER]
    rows['modified_function_properties'] = {}
    for sig, value in (await source_objects(conn)).items():
        if sig==VIEW: continue
        props = dict(value['properties']); props.pop('body_sha256')
        rows['modified_function_properties'][sig] = props
    columns = [dict(r) for r in await conn.fetch("""SELECT n.nspname||'.'||c.relname AS relation,
        a.attnum,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,
        a.attidentity::text,a.attgenerated::text,a.attacl::text,pg_get_expr(d.adbin,d.adrelid) AS default_expression
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attnum>0 AND NOT a.attisdropped AND n.nspname=ANY($1::text[])
        ORDER BY n.nspname,c.relname,a.attnum""", ['activity','agent','common','config','control','crm','insight','knowledge','ops','platform','security','workflow'])]
    rows['column_definitions'] = [r for r in columns if NEW_COLUMNS.get(r['relation']) != r['attname']
                                  and not (r['relation']==VIEW and r['attname']=='attribution_status')]
    views = [dict(r) for r in await conn.fetch("""SELECT schemaname||'.'||viewname AS name,
        md5(definition) AS definition_digest FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema')
        ORDER BY 1""")]
    for view in views:
        if view['name']==VIEW: view['definition_digest']='reviewed-effective-actual-view'
    rows['view_definitions']=views
    roles = [r for r in rows['roles'] if r['rolname'] in ROLES.values()]
    require(len(roles)==4 and all(r['rolcanlogin'] and not any(r[k] for k in
        ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')) for r in roles),
        'Production runtime roles must remain nonprivileged')
    indexes = [dict(r) for r in await conn.fetch("""SELECT indexrelid,indrelid,indisunique,indisprimary,
        indisexclusion,indimmediate,indisvalid,indisready,indislive,indisreplident,
        md5(pg_get_indexdef(indexrelid)) AS definition_digest FROM pg_index ORDER BY indexrelid""")]
    return {'format':'salesbuddy-live-security-v1','fingerprints':{k:digest(v) for k,v in rows.items()},
            'runtime_roles_nonprivileged':True,'index_fingerprint':digest(indexes)}


async def new_objects(conn):
    result = {'format':'salesbuddy-dashboard-targets-new-objects-v1','functions':{},'columns':{},'constraints':{}}
    for sig in (*FUNCTION_GRANTS,*OLD_FUNCTIONS):
        row=await conn.fetchrow(FUNCTION_SQL,sig)
        require(row is not None,'New/replaced V296 function is absent')
        result['functions'][sig]={'properties':dict(row),'acl':[dict(r) for r in await conn.fetch(ACL_FUNCTION_SQL,sig)]}
    for table,name in NEW_COLUMNS.items():
        matches=[dict(r) for r in await conn.fetch(COLUMN_SQL,table) if r['name']==name]
        require(len(matches)==1,'V294 nullable attribution column missing')
        result['columns'][table]=matches[0]
        result['constraints'][table]=[dict(r) for r in await conn.fetch("""SELECT conname AS name,
            contype::text AS type,convalidated AS validated,pg_get_constraintdef(oid) AS definition
            FROM pg_constraint WHERE conrelid=to_regclass($1) AND conname=$2""",table,NEW_CONSTRAINTS[table])]
    result['trigger']=[dict(r) for r in await conn.fetch(TRIGGER_SQL) if (r['relation'],r['name'])==NEW_TRIGGER]
    result['view'] = await effective_actual_view(conn)
    return result


def validate_new_acl(objects, *, allow_ungranted=False):
    require(set(objects)=={'format','functions','columns','constraints','trigger','view'}
            and objects['format']=='salesbuddy-dashboard-targets-new-objects-v1'
            and set(objects['functions'])==set(FUNCTION_GRANTS)|set(OLD_FUNCTIONS),
            'Complete exact V294-V296 object receipt required')
    for sig,roles in FUNCTION_GRANTS.items():
        value=objects['functions'][sig];p=value['properties']
        language='sql' if sig=='security.opportunity_recognized_edit_scope(uuid)' else 'plpgsql'
        volatility='v' if sig in ('crm.guard_actual_attribution()',
            'security.save_opportunity_recognized_actual(uuid,integer,integer,jsonb,text,boolean)') else 's'
        require(p['owner']=='postgres' and p['definer'] is True and p['settings']==['search_path=pg_catalog']
                and p['language']==language and p['volatility']==volatility and p['kind']=='f',
                'V294 helper owner/security settings differ')
        observed={(a['grantee'],a['privilege']) for a in value['acl'] if a['grantee']!=p['owner']}
        wanted={(ROLES[r],'EXECUTE') for r in roles}
        require(observed==wanted or (allow_ungranted and not observed),'Unexpected V294 helper grants')
        require(all(a['grantor']==p['owner'] and not a['grantable'] for a in value['acl']),'Unexpected helper grant options')
    require(set(objects['columns'])==set(NEW_COLUMNS) and set(objects['constraints'])==set(NEW_CONSTRAINTS),
            'Only the two reviewed nullable columns are admitted')
    for table,value in objects['columns'].items():
        require(value=={'name':'attribution_snapshot','type':'jsonb','not_null':False,'identity':'','generated':'',
                        'acl':None,'default_expression':None},'Attribution column must remain nullable without a default or grants')
        constraints=objects['constraints'][table]
        require(len(constraints)==1 and constraints[0]['name']==NEW_CONSTRAINTS[table]
                and constraints[0]['type']=='c' and constraints[0]['validated'] is True,'Exact attribution constraint required')
    require(len(objects['trigger'])==1 and (objects['trigger'][0]['relation'],objects['trigger'][0]['name'])==NEW_TRIGGER,
            'Exactly one reviewed attribution trigger required')
    require(objects['view']['columns'][-1]['name']=='attribution_status','Effective actual attribution projection required')


async def verify_effective_privileges(conn):
    for sig, roles in FUNCTION_GRANTS.items():
        for role in ROLES.values():
            require(await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, sig)
                    is (role in [ROLES[r] for r in roles]), 'Unexpected inherited helper privilege')


async def business_state(conn):
    """Hash existing business rows inside the server; never return their content."""
    require(conn.is_in_transaction(), 'Business preservation snapshot requires a transaction')
    # Full-row hashing can outlast the DDL timeout on the live database. Give
    # only these read-only scans more time; preserve the caller's DDL/ACL limit.
    previous_timeout = await conn.fetchval('SHOW statement_timeout')
    await conn.execute("SET LOCAL statement_timeout='120s'")
    try:
        result = await _business_state_digest(conn)
    except BaseException:
        try:
            await conn.execute("SELECT set_config('statement_timeout',$1,true)", previous_timeout)
        except BaseException:
            pass  # An aborted transaction is rolled back by the owning caller.
        raise
    await conn.execute("SELECT set_config('statement_timeout',$1,true)", previous_timeout)
    return result


async def _business_state_digest(conn):
    schemas = ['activity', 'agent', 'common', 'config', 'control', 'crm', 'insight', 'knowledge', 'ops', 'platform', 'security', 'workflow']
    rows = await conn.fetch('''SELECT n.nspname||'.'||c.relname AS name,
        quote_ident(n.nspname)||'.'||quote_ident(c.relname) AS qualified FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind='r'
        AND (n.nspname||'.'||c.relname) NOT IN ('ops.schema_migration','ops.migration_checksum')
        ORDER BY 1''', schemas)
    values = {}
    for row in rows:
        value = "to_jsonb(t)-'attribution_snapshot'" if row['name'] in NEW_COLUMNS else 'to_jsonb(t)'
        if row['name'] in NEW_COLUMNS:
            present=await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=$1::regclass AND attname=$2 AND NOT attisdropped)',row['name'],'attribution_snapshot')
            if present: require(not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM '+row['qualified']+' WHERE attribution_snapshot IS NOT NULL)'), 'Migration backfilled prior attribution')
        values[row['name']] = dict(await conn.fetchrow('''SELECT count(*) AS rows,
            encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') AS sha256
            FROM (SELECT encode(sha256(convert_to(('''+value+''')::text,'UTF8')),'hex') AS h
                  FROM ''' + row['qualified'] + ' t) hashed'))
    return {'row_counts': {k: v['rows'] for k, v in values.items()}, 'existing_business_sha256': digest(values)}


def load_plan(plan_ref):
    plan = read_ref(plan_ref); expected = read_ref(plan['policy_ref'])
    contract.validate_acl(plan, expected, digest=digest, require=require)
    objects, source = read_ref(plan['new_objects_ref']), read_ref(plan['source_objects_ref'])
    validate_new_acl(objects)
    require(set(source) == set(OLD_FUNCTIONS)|{VIEW}, 'Complete V293 replaced function snapshot required')
    contract.validate_restore_evidence(read_ref(plan['restore_evidence_ref']), objects, source, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]; metadata = root/'tools/installation/runtime_permissions.json'
    paths = {str(root/'backend/deploy'/p) for p in contract.GATE_FILES}
    paths |= {str(root/'database/scripts/migrate.py'), str(metadata)}
    paths |= {str(root/'database'/p) for p, _ in contract.SQL.values()}
    require(set(plan.get('source_files', {})) == paths, 'Complete dashboard_targets source closure required')
    for name, checksum in plan['source_files'].items():
        path = Path(name)
        require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))
                and hashlib.sha256(path.read_bytes()).hexdigest() == checksum, 'Frozen dashboard_targets source changed')
    require(digest(json.loads(metadata.read_bytes())) == contract.INSTALLATION_POLICY_SHA, 'Exact V296 runtime policy required')
    for path, checksum in contract.SQL.values():
        require(hashlib.sha256((root/'database'/path).read_bytes()).hexdigest() == checksum, 'Frozen V296 SQL changed')
    return plan, expected


async def require_no_added_columns(conn):
    for table,name in NEW_COLUMNS.items():
        require(not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass($1) AND attname=$2 AND NOT attisdropped)',table,name),
                'V294 attribution column already exists')
    require(not any((r['relation'],r['name'])==NEW_TRIGGER for r in await conn.fetch(TRIGGER_SQL)),
            'V294 attribution trigger already exists')


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), 'Dashboard targets checks must be transactional')
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow("SELECT current_database() database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) oid"))
    require(identity == plan['target'], 'Production database identity changed')
    observed = await snapshot(conn)
    require(observed == expected, 'Preserved production catalog/ACL/RLS/role boundary changed')
    if not final:
        for sig in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', sig) is None, 'V296 function already exists')
        require(await source_objects(conn) == read_ref(plan['source_objects_ref']), 'V293 function/view definitions changed')
        await require_no_added_columns(conn)
    else:
        objects = await new_objects(conn); validate_new_acl(objects, allow_ungranted=allow_ungranted)
        wanted = read_ref(plan['new_objects_ref'])
        if allow_ungranted:
            for sig in FUNCTION_GRANTS:
                objects['functions'][sig]['acl'] = wanted['functions'][sig]['acl']
        require(objects == wanted, 'New/replaced definitions differ from the independent restore')
        if not allow_ungranted:
            await verify_effective_privileges(conn)
    return {'security_preserved': True, 'snapshot_sha256': digest(observed), 'schema': 'V296' if final else 'V293'}


async def grant_execute(conn, *, plan_ref):
    require(conn.is_in_transaction(), 'V294 grants require the V293-V296 migration transaction')
    await verify_state(conn, plan_ref=plan_ref, final=True, allow_ungranted=True)
    for sql in GRANTS:
        await conn.execute(sql)
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), 'V296 verification owns a readonly transaction')
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
    spec = importlib.util.spec_from_file_location('_dashboard_targets_migrate', root/'database/scripts/migrate.py')
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    migrations = [m.item('BASELINE_V1', root/'database/baseline/V001__current_schema.sql'),
                  m.item('S001', root/'database/seeds/S001__system_configuration.sql'), *m.discover(root/'database')]
    plan = [{'key': v.key, 'file': v.path.relative_to(root/'database').as_posix(), 'sha256': v.checksum} for v in migrations]
    async def ledger(final=False):
        prior = dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        registered = await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
        contract.validate_migrations(plan, prior, registered, 'V296' if final else 'V293', 'V296',
                                     [] if final else contract.PENDING, digest=digest, require=require)
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL lock_timeout='2s'"); await conn.execute("SET LOCAL statement_timeout='60s'")
        await ledger()
        for sig in FUNCTION_GRANTS:
            require(await conn.fetchval('SELECT to_regprocedure($1)', sig) is None, 'Restore already has V296 helpers')
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
    evidence = {'format': 'salesbuddy-dashboard-targets-restore-rehearsal-v1', 'status': 'passed',
                'evidence_kind': 'independent_production_restore', 'source_database': {'database': 'sales_saas', 'oid': 18684},
                'restored_database': identity, 'source_backup_sha256': source_backup_sha256,
                'restored_from_production_backup': True, 'source_schema': 'V293', 'target_schema': 'V296',
                'source_ledger_sha256': contract.BASE_COMPLETE_LEDGER, 'target_ledger_sha256': contract.TARGET_COMPLETE_LEDGER,
                'migration_plan_sha256': contract.MIGRATION_PLAN_SHA, 'new_objects_sha256': digest(objects),
                'source_objects_sha256': digest(source), 'security_preserved': True,
                'business_state_preserved': True, 'effective_privileges_verified': True}
    return {'evidence': evidence, 'new_objects': objects, 'source_objects': source,
            'security_snapshot_sha256': digest(before), 'business_state_sha256': digest(data)}
