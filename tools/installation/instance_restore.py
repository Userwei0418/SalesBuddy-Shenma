"""Restore a trusted detached backup into a new, stopped instance.

No existing database or role is adopted. Schema and grants come from the locked
product artifact, never from restored SQL definitions. This is an explicit local
recovery operation, not a scheduler, production deployment or offsite SLA.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import traceback
import uuid

import asyncpg

from database_inventory import sql_name, table_inventory, verify_sequence_positions
from instance import prepare, run_private, runtime_env
from instance_backup import (MAX_MANIFEST_BYTES, copy_file, digest, inventory, material_tree,
                             originals, pg_command, validate_contents, verify_keys, owned_file)
from instance_database import database_config
from instance_files import (InstanceError, atomic_json, clean_env, locked, metadata_digest,
                            read_private, root_path, source_check, instance)
from release_bundle import json_bytes, read_verified
from runtime_roles import identifier, provision_new, validate_policy, verify

FORMAT = 'salesbuddy-instance-recovery-v1'
INSTALLATION_DDL = '''CREATE TABLE public.salesbuddy_installation(
 singleton boolean PRIMARY KEY CHECK(singleton), instance_id uuid NOT NULL,
 metadata_digest text NOT NULL, operator_id uuid NOT NULL,
 installed_at timestamptz NOT NULL DEFAULT clock_timestamp());
 REVOKE ALL ON public.salesbuddy_installation FROM PUBLIC'''


def trusted_backup(directory, expected):
    directory = root_path(directory)
    if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise InstanceError('Provide the trusted BACKUP.json SHA-256')
    # Read with ownership/mode/size protection before hashing the file.
    read_private(directory/'BACKUP.json', max_bytes=MAX_MANIFEST_BYTES)
    if digest(directory/'BACKUP.json') != expected:
        raise InstanceError('Backup manifest does not match the trusted digest')
    manifest, saved = validate_contents(directory)
    if digest(directory/'BACKUP.json') != expected:
        raise InstanceError('Backup manifest changed during verification')
    if not all(key in manifest for key in ('inventory', 'originals', 'keys_checked')):
        raise InstanceError('Backup lacks complete restore evidence')
    for role in [*saved['roles'].values(), saved['database']['administrator']]: identifier(role)
    if set(saved['roles']) != {'api','worker','platform','feishu'} or len(set(saved['roles'].values())) != 4:
        raise InstanceError('Backup runtime identities are invalid')
    files, package = read_verified(directory/'artifact.zip', saved['artifact_sha256'])
    if package['revision'] != saved['revision'] or package['expected_schema'] != saved['expected_schema']:
        raise InstanceError('Backup metadata does not match its locked artifact')
    for name, expected_hash in saved['private_hashes'].items():
        if name not in {'bootstrap.json','api.json','worker.json','feishu.json','provider-keys.json','platform-mfa-keys.json'}:
            raise InstanceError('Unknown private configuration member')
        if hashlib.sha256(json_bytes(read_private(directory/'private'/name))).hexdigest() != expected_hash:
            raise InstanceError('Backup runtime configuration hash mismatch')
    policy = validate_policy(json.loads(files['tools/installation/runtime_permissions.json']))
    return manifest, saved, policy


def mapped_security(value, source, target):
    """Map role identifiers only in documented ownership/ACL/policy fields."""
    mapping = {source['database']['administrator']: target['database']['administrator']}
    mapping.update({source['roles'][s]: target['roles'][s] for s in source['roles']})
    result = json.loads(json.dumps(value))
    for kind, rows in result.items():
        for row in rows:
            if 'owner' in row: row['owner'] = mapping.get(row['owner'], row['owner'])
            if 'roles' in row: row['roles'] = sorted(mapping.get(r, r) for r in row['roles'])
            if 'acl' in row:
                mapped = []
                for acl in row['acl']:
                    # Our generated identifiers never require PostgreSQL quoting;
                    # unknown quoted ACLs remain untouched and must compare equal.
                    match = re.fullmatch(r'([a-z0-9_]*)(=[A-Za-z*]*/)([a-z0-9_]+)', acl)
                    mapped.append(mapping.get(match[1], match[1])+match[2]+mapping.get(match[3], match[3]) if match else acl)
                row['acl'] = sorted(mapped)
    return result


def material_destination(source_root, value, root):
    path = Path(value)
    base = Path(source_root)/'materials'
    if not path.is_absolute() or '..' in path.parts or base not in path.parents:
        raise InstanceError('Restored original path escapes its source materials directory')
    return root/'materials'/path.relative_to(base)


def begin(args):
    directory = root_path(args.backup)
    manifest, saved, _ = trusted_backup(directory, args.backup_sha256)
    root = root_path(args.root, existing=False)
    if (root == Path(saved['root']) or Path(saved['root']) in root.parents
            or root in directory.parents or directory == root or directory in root.parents):
        raise InstanceError('Recovery requires a separate new instance root')
    if args.database == saved['database']['name']:
        raise InstanceError('Select a new database name; source database replacement is not supported')
    target = argparse.Namespace(root=root, archive=directory/'artifact.zip', sha256=saved['artifact_sha256'],
        database=args.database, db_host=args.db_host, db_port=args.db_port, db_user=args.db_user,
        db_sslmode=args.db_sslmode, api_port=saved['api_port'], operator=saved['operator']['account'],
        operator_name=saved['operator']['name'], trust_loopback_proxy=saved['trust_loopback_proxy'])
    target.recovery_pending = True
    prepare(target)
    data, _ = source_check(root,recovery=True)
    state = {'format':FORMAT, 'instance_id':data['instance_id'], 'backup':str(directory),
             'backup_sha256':args.backup_sha256, 'stage':'prepared', 'services_started':False,
             'original_metadata':data}
    atomic_json(root/'run/recovery.json', state, replace=False)
    return root


def expected_metadata(root,state,directory):
    data = json.loads(json.dumps(state['original_metadata']))
    updates = {}
    # Fresh database passwords/token signing remain fresh. Business account
    # hashes and the platform MFA/provider encryption keys are preserved.
    for name in ('provider-keys.json','platform-mfa-keys.json'):
        value = read_private(directory/'private'/name)
        updates[name] = value
        data['private_hashes'][name] = hashlib.sha256(json_bytes(value)).hexdigest()
    passwords = {}
    for service in ('api','worker','feishu'):
        name = service+'.json'; value = read_private(root/'private'/name)
        if hashlib.sha256(json_bytes(value)).hexdigest() != data['private_hashes'][name]:
            raise InstanceError('Fresh service configuration changed during recovery')
        for role,password in value['database_passwords'].items():
            if role in passwords and passwords[role] != password:
                raise InstanceError('Recovery service credentials disagree')
            passwords[role] = password
    if set(passwords) != set(data['roles']): raise InstanceError('Recovery service credentials are incomplete')
    bootstrap = {'database_passwords':passwords,
                 'operator_password':read_private(directory/'private/bootstrap.json')['operator_password']}
    updates['bootstrap.json'] = bootstrap
    data['private_hashes']['bootstrap.json'] = hashlib.sha256(json_bytes(bootstrap)).hexdigest()
    return data,updates


def recovery_source_check(root,state):
    marker = root/'release/RECOVERY_PENDING'
    if marker.exists(): return source_check(root,recovery=True)
    if state.get('stage') not in ('committing','database_verified') or not state.get('post_inventory_sha256'):
        raise InstanceError('Recovery guard disappeared before verification')
    return source_check(root)


def prepare_files(root, state, directory, saved):
    data,updates = expected_metadata(root,state,directory)
    current = instance(root)
    if current not in (state['original_metadata'],data):
        raise InstanceError('Partial recovery metadata changed outside the recovery journal')
    # A crash between individual config replacements is resumable, but an
    # unrelated edit is never silently overwritten.
    for name, value in updates.items():
        actual = hashlib.sha256(json_bytes(read_private(root/'private'/name))).hexdigest()
        if actual not in {state['original_metadata']['private_hashes'][name],data['private_hashes'][name]}:
            raise InstanceError('Partial recovery private configuration was changed')
        atomic_json(root/'private'/name,value)
    atomic_json(root/'INSTANCE.json', data)
    for name, expected in read_private(directory/'BACKUP.json', max_bytes=MAX_MANIFEST_BYTES)['files'].items():
        if not name.startswith('materials/'): continue
        destination = root/name
        if destination.resolve() != destination or destination.is_symlink():
            raise InstanceError('Partial recovery material path is redirected')
        if destination.exists():
            if digest(owned_file(destination)) != expected:
                raise InstanceError('Partial recovery material differs from the backup')
        else: copy_file(directory/name, destination)
    recovery_source_check(root,state)
    uv = shutil.which('uv', path=clean_env().get('PATH'))
    if not uv: raise InstanceError('Install uv before restoring the fixed runtime')
    run_private([uv, '--no-config', 'sync', '--locked', '--no-dev', '--no-editable', '--python','3.12',
                 '--project',str(root/'release/backend')], root, 'recovery-dependencies.log', env=runtime_env(root))
    state['stage'] = 'files_ready'; atomic_json(root/'run/recovery.json', state)
    return data


async def remove_partial(admin, data):
    database = data['database']['name']; marker = 'salesbuddy-instance-v1:'+data['instance_id']
    found = await admin.fetchrow("SELECT shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=$1",database)
    if found:
        if found['marker'] != marker:
            raise InstanceError('Existing database is not marked as this recovery; refusing adoption or removal')
        if await admin.fetchval('SELECT count(*) FROM pg_stat_activity WHERE datname=$1', database):
            raise InstanceError('Recovery database has active connections; no connection will be terminated')
        await admin.execute('DROP DATABASE '+identifier(database))
    for role in data['roles'].values():
        row = await admin.fetchrow("SELECT shobj_description(oid,'pg_authid') AS marker FROM pg_roles WHERE rolname=$1",role)
        if row:
            if row['marker'] != marker: raise InstanceError('Runtime role collision; refusing to alter an unowned role')
            await admin.execute('DROP ROLE '+identifier(role))


async def source_session_bindings(conn):
    """Fixed session tables; old product schemas predate closed-company export."""
    bindings = [('platform.auth_session','id',"status<>'revoked' OR revoked_at IS NULL"),
                ('control.session','token_hash','revoked_at IS NULL')]
    if await conn.fetchval("SELECT to_regclass('security.closed_export_session') IS NOT NULL"):
        bindings.append(('security.closed_export_session','id','revoked_at IS NULL'))
    return bindings


async def revoke_source_sessions(conn):
    """Revoke every source capability, including expired rows; retain history."""
    for table, _, pending in await source_session_bindings(conn):
        status = "status='revoked'," if table == 'platform.auth_session' else ''
        await conn.execute('UPDATE '+table+' SET '+status+
            'revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE '+pending)


async def verify_source_sessions_revoked(conn):
    """Also guard a committed recovery made by an older tool before resuming."""
    for table, _, pending in await source_session_bindings(conn):
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE '+pending+')'):
            raise InstanceError('Restored source sessions were not revoked ('+table+'); '
                'retain the recovery database and restore the trusted backup into a new directory')


async def bound_rows(conn):
    result = {}
    bindings = [('activity.visit_import','id'),('control.storage_allocation','import_id'),
                ('public.salesbuddy_installation','singleton')]
    bindings.extend((table,key) for table,key,_ in await source_session_bindings(conn))
    for table, key in bindings:
        result[table] = {str(row['key']):json.loads(row['body']) for row in await conn.fetch(
            'SELECT '+key+'::text AS key,to_jsonb(t)::text AS body FROM '+table+' t')}
    return result


async def audit_rows(conn):
    return {str(row['id']):json.loads(row['body']) for row in await conn.fetch(
        'SELECT id,to_jsonb(t)::text AS body FROM ops.audit_log t')}


async def expected_recovery_audit(conn, before, after, changed, request_id, transaction_id):
    result = {}
    for key in changed:
        old, new = before[key], after[key]
        if old == new: continue
        redacted = [json.loads(await conn.fetchval('SELECT security.audit_redact($1::jsonb)::text',
                    json.dumps(value))) for value in (old,new)]
        result[key] = {'workspace_id':new['workspace_id'],'actor_user_ref_id':None,
            'actor_role_code':'system','action_code':'activity.visit_import.update',
            'module_code':'activity','object_type':'visit_import','object_id':key,
            'object_label':new.get('name') or new.get('display_name') or new.get('title') or 'visit_import',
            'request_id':request_id,'client_ip':None,'user_agent':'SalesBuddy instance recovery',
            'before_snapshot':redacted[0],'after_snapshot':redacted[1],
            'changed_fields':sorted(name for name in old.keys()|new.keys()
                if old.get(name)!=new.get(name) and name not in {'updated_at','created_at','version_no'}),
            'result_code':'success','sensitivity':'internal','actor_name_snapshot':None,
            'actor_team_snapshot':None,'execution_kind':'system','transaction_id':transaction_id}
    return result


def verify_recovery_audit(before, after, expected, started_at, ended_at):
    """Preserve every historical row; permit only each exact new path receipt."""
    if any(after.get(key) != row for key,row in before.items()):
        raise InstanceError('Recovery changed or removed historical audit evidence')
    additions = {key:row for key,row in after.items() if key not in before}
    if len(additions) != len(expected): raise InstanceError('Recovery appended unexpected audit evidence')
    seen = set()
    for key,row in additions.items():
        try:
            occurred = datetime.fromisoformat(row['occurred_at'])
            valid = (type(row['id']) is int and str(row['id']) == key
                and row['id'] > max((int(value) for value in before),default=0)
                and occurred.tzinfo is not None and started_at <= occurred <= ended_at)
        except (KeyError,TypeError,ValueError):
            valid = False
        object_id = row.get('object_id')
        body = {name:value for name,value in row.items() if name not in {'id','occurred_at'}}
        if not valid or object_id in seen or object_id not in expected or body != expected[object_id]:
            raise InstanceError('Recovery appended unapproved audit evidence')
        seen.add(object_id)


def verify_bound_rows(before, after, changed, data, allocation_changes=None):
    """Only the exact identity/path fields changed; other row content survives."""
    if before.keys() != after.keys(): raise InstanceError('Recovery table bindings differ')
    for table, rows in before.items():
        if rows.keys() != after[table].keys(): raise InstanceError('Recovery added or lost a binding row')
        for key, original in rows.items():
            expected = dict(original); actual = dict(after[table][key])
            if table == 'public.salesbuddy_installation':
                expected.update(instance_id=str(uuid.UUID(data['instance_id'])),metadata_digest=metadata_digest(data))
            elif table == 'platform.auth_session' and (original['status'] != 'revoked' or original['revoked_at'] is None):
                expected.update(status='revoked',revoked_at=original['revoked_at'] or actual.get('revoked_at'))
                if not expected['revoked_at']: raise InstanceError('Restored business session was not revoked')
            elif table in ('control.session','security.closed_export_session') and original['revoked_at'] is None:
                expected['revoked_at'] = actual.get('revoked_at')
                if not expected['revoked_at']: raise InstanceError('Restored source session was not revoked')
            elif table in ('activity.visit_import','control.storage_allocation') and (
                    key in changed or key in (allocation_changes or {})):
                if key in changed:
                    expected['storage_key'] = changed[key]
                    if table == 'activity.visit_import': expected['file_path'] = changed[key]
                if table == 'control.storage_allocation': expected.update((allocation_changes or {}).get(key,{}))
                # Existing update triggers maintain the modified timestamp.
                expected['updated_at'] = actual.get('updated_at')
                if 'updated_at' not in original: expected.pop('updated_at',None)
            if actual != expected: raise InstanceError('Recovery changed an unapproved binding field')


async def build_database(root, directory, manifest, saved, policy, data, state, pg_bin):
    config = database_config(data, admin=True, password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'))
    admin = await asyncpg.connect(database='postgres', **config)
    conn = None
    database = data['database']['name']; marker = 'salesbuddy-instance-v1:'+data['instance_id']
    try:
        if not await admin.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'):
            raise InstanceError('Recovery requires the explicitly selected maintenance administrator')
        if not 160000 <= int(await admin.fetchval('SHOW server_version_num')) < 170000:
            raise InstanceError('Recovery requires PostgreSQL 16')
        await admin.execute('SELECT pg_advisory_lock(hashtextextended($1,0))','salesbuddy-install:'+database)
        found = await admin.fetchrow("SELECT shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=$1", database)
        if found and found['marker'] == marker and state.get('post_inventory_sha256'):
            conn = await asyncpg.connect(database=database, **config)
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                record = await conn.fetchrow('SELECT instance_id,metadata_digest FROM public.salesbuddy_installation')
                actual = await inventory(conn)
                if (record and str(record['instance_id']).replace('-','') == data['instance_id']
                        and record['metadata_digest'] == metadata_digest(data)
                        and hashlib.sha256(json_bytes(actual)).hexdigest() == state['post_inventory_sha256']):
                    await verify_source_sessions_revoked(conn)
                    await verify(conn, data['roles'], policy)
                    await verify_sequence_positions(conn)
                    if await verify_keys(conn, root) != manifest['keys_checked']: raise InstanceError('Recovered keys differ')
                    if await originals(conn, root, material_tree(root)) != manifest['originals']: raise InstanceError('Recovered originals differ')
                    return
                if record and str(record['instance_id']).replace('-','') == data['instance_id']:
                    raise InstanceError('Committed recovery has changed; refusing to discard restored data')
            await conn.close(); conn = None
        if found:
            if found['marker'] != marker: raise InstanceError('Database already exists; recovery never adopts or replaces it')
            await remove_partial(admin, data)
        elif state['stage'] not in ('files_ready','prepared'):
            # A prior cleanup may have committed DROP DATABASE before dropping
            # its marked roles. Only those exact owned roles may be removed.
            await remove_partial(admin,data)
        # Collision is checked before creating the DB, even if a guessed name
        # happens to belong to another database on this cluster.
        if await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))',list(data['roles'].values())):
            raise InstanceError('A recovery runtime identity already exists')
        state['stage'] = 'creating_database'; atomic_json(root/'run/recovery.json', state)
        await admin.execute('CREATE DATABASE '+identifier(database)+' TEMPLATE template0')
        literal = await admin.fetchval('SELECT quote_literal($1::text)', marker)
        await admin.execute('COMMENT ON DATABASE '+identifier(database)+' IS '+literal)
        conn = await asyncpg.connect(database=database, **config)
        sys.path[:0] = [str(root/'release/database/scripts'), str(root/'release/backend/src')]
        from migrate import migrate
        await migrate(conn, root/'release/database')
        bootstrap = read_private(root/'private/bootstrap.json')
        async with conn.transaction():
            await provision_new(conn, database=database, roles=data['roles'], policy=policy, passwords=bootstrap['database_passwords'])
            for role in data['roles'].values():
                await conn.execute('COMMENT ON ROLE '+identifier(role)+' IS '+literal)
            await conn.execute(INSTALLATION_DDL)
            await conn.execute("SET LOCAL session_replication_role='replica'")
            tables = await table_inventory(conn)
            if set(tables) != set(manifest['inventory']['tables']):
                raise InstanceError('Backup tables differ from the fixed product schema')
            await conn.execute('TRUNCATE '+','.join('.'.join(sql_name(p) for p in name.split('.')) for name in sorted(tables)))
        state['stage'] = 'restoring_data'; atomic_json(root/'run/recovery.json', state)
        await asyncio.to_thread(pg_command, pg_bin, 'pg_restore', ['--exit-on-error','--single-transaction',
            '--data-only','--no-owner','--no-privileges','--disable-triggers','--dbname='+database,str(directory/'database.dump')], data, database)
        trusted_backup(directory, state['backup_sha256'])
        async with conn.transaction(isolation='repeatable_read'):
            actual = await inventory(conn)
            expected = manifest['inventory']
            if actual['tables'] != expected['tables'] or actual['sequences'] != expected['sequences']:
                raise InstanceError('Restored rows or sequence definitions differ from the backup')
            if actual['security'] != mapped_security(expected['security'], saved, data):
                raise InstanceError('Rebuilt ownership, ACLs or row policies differ from the reviewed backup')
            await verify(conn, data['roles'], policy)
            await verify_sequence_positions(conn)
            if await verify_keys(conn, root) != manifest['keys_checked']:
                raise InstanceError('Restored encryption keys do not match')
            before = actual['tables']
            old_bindings = await bound_rows(conn)
            old_audit = await audit_rows(conn)
            request_id = str(uuid.UUID(data['instance_id']))
            for name,value in {'app.user_ref_id':'','app.workspace_id':'','app.role_code':'system',
                    'app.request_id':request_id,'app.client_ip':'','app.user_agent':'SalesBuddy instance recovery',
                    'app.job_id':request_id}.items():
                await conn.execute('SELECT set_config($1,$2,true)',name,value)
            audit_context = await conn.fetchrow('SELECT clock_timestamp() AS started_at,txid_current() AS transaction_id')
            changed = {}
            for row in await conn.fetch("SELECT id,storage_key,file_path FROM activity.visit_import WHERE storage_driver='local'"):
                path = material_destination(saved['root'], row['storage_key'] or row['file_path'], root)
                await conn.execute('SELECT control.rebind_restored_local_original($1,$2)',row['id'],str(path))
                changed[str(row['id'])] = str(path)
            allocation_changes = {}
            for row in await conn.fetch('SELECT import_id,driver,storage_key,staging_path FROM control.storage_allocation'):
                fields = {}
                if row['staging_path']:
                    fields['staging_path'] = str(material_destination(saved['root'],row['staging_path'],root))
                if row['driver'] == 'local' and str(row['import_id']) not in changed:
                    fields['storage_key'] = str(material_destination(saved['root'],row['storage_key'],root))
                if fields:
                    allocation_changes[str(row['import_id'])] = fields
                    # Field names are fixed above, values remain parameters.
                    await conn.execute('UPDATE control.storage_allocation SET '+
                        ','.join(name+'=$'+str(index+2) for index,name in enumerate(fields))+
                        ',updated_at=clock_timestamp() WHERE import_id=$1',row['import_id'],*fields.values())
            await conn.execute('UPDATE public.salesbuddy_installation SET instance_id=$1::uuid,metadata_digest=$2',data['instance_id'],metadata_digest(data))
            # Opaque refresh tokens, platform cookies and closed-company export
            # cookies survive signing-secret rotation. Revoke, retaining rows.
            await revoke_source_sessions(conn)
            new_bindings = await bound_rows(conn)
            verify_bound_rows(old_bindings,new_bindings,changed,data,allocation_changes)
            await verify_source_sessions_revoked(conn)
            expected_audit = await expected_recovery_audit(conn,old_bindings['activity.visit_import'],
                new_bindings['activity.visit_import'],changed,request_id,audit_context['transaction_id'])
            verify_recovery_audit(old_audit,await audit_rows(conn),expected_audit,
                audit_context['started_at'],await conn.fetchval('SELECT clock_timestamp()'))
            actual = await inventory(conn)
            allowed = {*old_bindings,'ops.audit_log'}
            if any(actual['tables'][name] != item for name,item in before.items() if name not in allowed):
                raise InstanceError('Recovery changed data outside explicit path and identity bindings')
            if await originals(conn, root, material_tree(root)) != manifest['originals']:
                raise InstanceError('Restored original bytes or database association differ')
            state.update(stage='committing', post_inventory_sha256=hashlib.sha256(json_bytes(actual)).hexdigest())
            atomic_json(root/'run/recovery.json',state)
        state['stage'] = 'database_verified'; atomic_json(root/'run/recovery.json',state)
    except Exception:
        if conn: await conn.close(); conn = None
        # Keep the journal for deterministic resume. Never delete an existing or
        # ambiguous database, nor assume a connection error means COMMIT failed.
        state['last_attempt_failed'] = True
        atomic_json(root/'run/recovery.json',state)
        raise
    finally:
        if conn: await conn.close()
        await admin.close()


async def resume(root, pg_bin):
    root = root_path(root)
    with locked(root):
        state = read_private(root/'run/recovery.json')
        data = instance(root)
        if state.get('format') != FORMAT or state.get('instance_id') != data['instance_id']:
            raise InstanceError('Recovery journal does not match this instance')
        directory = root_path(Path(state['backup']))
        manifest, saved, policy = trusted_backup(directory,state['backup_sha256'])
        expected,_ = expected_metadata(root,state,directory)
        if data != expected and not (state['stage'] == 'prepared' and data == state['original_metadata']):
            raise InstanceError('Recovery target changed outside its recorded explicit configuration')
        if any((root/'run'/f'{name}.json').exists() for name in ('api','worker','feishu')):
            raise InstanceError('Recovery requires all instance services to remain stopped')
        if state['stage'] == 'prepared': data = prepare_files(root,state,directory,saved)
        recovery_source_check(root,state)
        await build_database(root,directory,manifest,saved,policy,data,state,pg_bin)
        recovery_source_check(root,state)
        result = {'format':FORMAT,'status':'passed','instance_id':data['instance_id'],'root':str(root),
            'revision':data['revision'],'schema':data['expected_schema'],'backup_sha256':state['backup_sha256'],
            'database_verified':True,'services_started':False,'worker_and_feishu_started':False,
            'external_integrations_verified':False,'offsite_recovery_verified':False,
            'fresh_runtime_credentials':True,'accounts_and_mfa_preserved':True,'source_sessions_revoked':True,
            'login_instruction':'Use the original current account password and enrolled MFA; no password reset occurred'}
        atomic_json(root/'run/initialized.json',{'instance_id':data['instance_id'],'revision':data['revision']})
        atomic_json(root/'run/last-recovery.json',result)
        (root/'release/RECOVERY_PENDING').unlink(missing_ok=True)
        source_check(root)
        (root/'run/recovery.json').unlink()
        for directory in (root/'release',root/'run'):
            descriptor = os.open(directory,os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    restore = sub.add_parser('restore')
    restore.add_argument('--backup',type=Path,required=True)
    restore.add_argument('--backup-sha256',required=True)
    restore.add_argument('--root',type=Path,required=True)
    restore.add_argument('--db-host',required=True)
    restore.add_argument('--db-sslmode',choices=('disable','verify-full'),required=True)
    restore.add_argument('--db-port',type=int,required=True)
    restore.add_argument('--db-user',required=True)
    restore.add_argument('--database',required=True)
    restore.add_argument('--pg-bin',type=Path,required=True)
    retry = sub.add_parser('resume'); retry.add_argument('--root',type=Path,required=True); retry.add_argument('--pg-bin',type=Path,required=True)
    args = parser.parse_args()
    root = begin(args) if args.command == 'restore' else args.root
    print(json.dumps(asyncio.run(resume(root,args.pg_bin)),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try: main()
    except (InstanceError,ValueError) as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        location = ','.join(Path(f.filename).name+':'+str(f.lineno) for f in traceback.extract_tb(exc.__traceback__))
        raise SystemExit('Recovery failed ('+type(exc).__name__+'; '+location+'); retain the journal and use resume') from None
