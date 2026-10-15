"""Offline upgrade of an owned instance, with durable commit-aware recovery.

Run from the reviewed new installer using Python 3.12 + backend dependencies.
Never adopts a database, restores over it, or silently rolls back a migration.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

if __name__ == '__main__': sys.dont_write_bytecode = True

import asyncpg

from instance import run_private
from instance_backup import (backup, restore_check, validate_backup, digest, owned_file,
                             inventory, material_tree, verify_sequence_positions, wait_for_offline, check_record)
from instance_database import database_config
from instance_files import (InstanceError, atomic_json, clean_env, locked, metadata_digest,
                            no_pending_upgrade, read_private, root_path, source_check, runtime_path)
from instance_process import service_lock
from release_bundle import MANIFEST, json_bytes, read_verified, upgrade_plan
from runtime_roles import verify, validate_policy, fingerprint
from upgrade_permissions import cluster_identity, reconcile
from database_inventory import sql_name

CONTRACT = {'format':'salesbuddy-instance-upgrade-v1','instance_format':'salesbuddy-instance-v2',
            'database':'transactional-postgresql-16','automatic_database_rollback':False}


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def move(source, target):
    if target.exists() or target.is_symlink(): raise InstanceError('Upgrade destination already exists')
    os.rename(source, target)
    fsync_directory(source.parent); fsync_directory(target.parent)


def checkpoint(root, journal, phase):
    journal['phase'] = phase
    atomic_json(root/'run/upgrade.json', journal)


def verify_tree(path, files, manifest):
    root_path(path)
    found = {}
    for item in path.rglob('*'):
        if item.is_symlink() or item.resolve() != item:
            raise InstanceError('Upgrade source tree is redirected')
        if item.is_file(): found[str(item.relative_to(path))] = digest(owned_file(item))
        elif not item.is_dir(): raise InstanceError('Upgrade source contains a special file')
    expected = {k:hashlib.sha256(v).hexdigest() for k,v in {**files,MANIFEST:json_bytes(manifest)}.items()}
    if found != expected: raise InstanceError('Upgrade source tree differs from its verified archive')


def new_metadata(old, manifest, sha, identity):
    return {**old,'format':'salesbuddy-instance-v2','revision':manifest['revision'],
            'expected_schema':manifest['expected_schema'],'artifact_sha256':sha,
            'runtime_directory':'upgrades/'+identity+'/runtime'}


def prepare(root, archive, sha, *, acknowledge_api_change=False):
    no_pending_upgrade(root)
    old, previous = source_check(root)
    files, incoming = read_verified(archive, sha)
    plan = upgrade_plan(previous, incoming)
    if previous['profile'] != incoming['profile'] or previous['revision'] == incoming['revision']:
        raise InstanceError('Upgrade requires a different revision in the same deployment profile')
    if plan['api_changed'] and not acknowledge_api_change:
        raise InstanceError('Review existing-client compatibility and explicitly acknowledge the API change')
    if json.loads(files.get('tools/installation/upgrade_contract.json', b'{}')) != CONTRACT:
        raise InstanceError('Incoming artifact does not support this upgrade protocol')
    policy = validate_policy(json.loads(files['tools/installation/runtime_permissions.json']))
    if (policy['schema'] != incoming['expected_schema'] or policy['migration_fingerprint'] !=
            fingerprint({row['key']:row['sha256'] for row in incoming['migrations']})):
        raise InstanceError('Incoming role contract does not match its schema')
    parent=root/'upgrades'; parent.mkdir(mode=0o700,exist_ok=True); root_path(parent)
    identity=uuid.uuid4().hex; stage=parent/identity; stage.mkdir(mode=0o700)
    shutil.copyfile(archive, stage/'artifact.zip'); (stage/'artifact.zip').chmod(0o600)
    with (stage/'artifact.zip').open('rb') as stream: os.fsync(stream.fileno())
    copied, checked=read_verified(stage/'artifact.zip',sha)
    if checked != incoming: raise InstanceError('Candidate changed while preparing upgrade')
    release=stage/'release'; release.mkdir(mode=0o700)
    for name,value in {**copied,MANIFEST:json_bytes(incoming)}.items():
        path=release/name; path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with path.open('xb') as stream:
            os.chmod(path,0o600); stream.write(value); stream.flush(); os.fsync(stream.fileno())
    for path in sorted((p for p in release.rglob('*') if p.is_dir()),reverse=True): fsync_directory(path)
    fsync_directory(release); fsync_directory(stage); fsync_directory(parent)
    journal={'format':CONTRACT['format'],'root':str(root),'id':identity,'old':old,
             'new':new_metadata(old,incoming,sha,identity),'plan':plan,'phase':'prepared'}
    checkpoint(root,journal,'prepared')
    return journal


def load(root):
    journal=read_private(root/'run/upgrade.json')
    if (journal.get('format') != CONTRACT['format'] or journal.get('root') != str(root)
            or not re.fullmatch(r'[0-9a-f]{32}',journal.get('id',''))):
        raise InstanceError('Invalid pending upgrade identity')
    stage=root/'upgrades'/journal['id']; root_path(stage)
    old,new=journal['old'],journal['new']
    current=read_private(root/'INSTANCE.json')
    if current not in (old,new) or old['root'] != str(root):
        raise InstanceError('Pending upgrade does not belong to the current instance')
    packages={}
    for label, data in (('old',old),('new',new)):
        choices=(root/'artifact.zip',stage/'artifact.zip',stage/'previous.zip')
        matching=[p for p in choices if p.exists() and digest(owned_file(p)) == data['artifact_sha256']]
        if len(matching) != 1: raise InstanceError('Missing or ambiguous upgrade archive')
        files,manifest=read_verified(matching[0],data['artifact_sha256'])
        packages[label]=(files,manifest)
    if new != new_metadata(old,packages['new'][1],new['artifact_sha256'],journal['id']):
        raise InstanceError('Upgrade attempts to change instance endpoint, credentials or identity')
    plan=upgrade_plan(packages['old'][1],packages['new'][1])
    if plan != journal['plan'] or packages['old'][1]['revision'] != old['revision']:
        raise InstanceError('Upgrade plan differs from the verified packages')
    if json.loads(packages['new'][0].get('tools/installation/upgrade_contract.json',b'{}')) != CONTRACT:
        raise InstanceError('Unknown upgrade contract')
    for name, expected in old['private_hashes'].items():
        if hashlib.sha256(json_bytes(read_private(root/'private'/name))).hexdigest() != expected:
            raise InstanceError('Instance credentials changed during upgrade')
    return journal,stage,packages


async def connect(journal):
    data=journal['old']
    config=database_config(data,admin=True,password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'))
    return await asyncpg.connect(database=data['database']['name'],**config)


async def database_state(conn,journal,packages):
    data=journal['old']
    if not await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'):
        raise InstanceError('Upgrade requires the explicit maintenance administrator')
    marker=await conn.fetchval("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")
    row=await conn.fetchrow('SELECT instance_id,metadata_digest FROM public.salesbuddy_installation')
    if (await conn.fetchval('SELECT current_database()') != data['database']['name']
            or marker != 'salesbuddy-instance-v1:'+data['instance_id'] or not row
            or row['instance_id'] != uuid.UUID(data['instance_id'])):
        raise InstanceError('Database identity differs from the pending upgrade')
    label=next((key for key in ('old','new') if row['metadata_digest']==metadata_digest(journal[key])),None)
    if label is None: raise InstanceError('Database commit state is unknown; no files or data were replaced')
    policy=validate_policy(json.loads(packages[label][0]['tools/installation/runtime_permissions.json']))
    await verify(conn,data['roles'],policy)
    return label


async def no_connections(conn):
    await wait_for_offline(conn)


def install_dependencies(root,stage,release=None,*,reinstall=False):
    release = stage/'release' if release is None else release
    runtime_path(root,{'runtime_directory':str((stage/'runtime').relative_to(root))})
    env={**clean_env(),'UV_PROJECT_ENVIRONMENT':str(stage/'runtime'),'UV_LINK_MODE':'copy',
         'PYTHONPYCACHEPREFIX':str(root/'cache/pycache')}
    uv=shutil.which('uv',path=env.get('PATH'))
    if not uv: raise InstanceError('Install uv before upgrading')
    run_private([uv,'--no-config','sync','--locked','--no-dev','--no-editable',*(['--reinstall'] if reinstall else []),'--python','3.12',
                 '--project',str(release/'backend')],root,'upgrade-dependencies.log',env=env)
    (stage/'runtime').chmod(0o700); root_path(stage/'runtime')


async def migrate_database(root,journal,stage,packages,snapshot):
    conn=await connect(journal)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='5s'")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('salesbuddy-instance-upgrade:'||current_database(),0))")
            if await database_state(conn,journal,packages) != 'old':
                raise InstanceError('Database already changed; re-run resume to inspect its commit state')
            await no_connections(conn)
            # Prevent business writes racing the snapshot comparison/migration.
            tables=await conn.fetch("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2""")
            for table in tables:
                await conn.execute('LOCK TABLE '+sql_name(table['nspname'])+'.'+sql_name(table['relname'])+' IN ACCESS EXCLUSIVE MODE')
            if await inventory(conn) != snapshot['inventory']:
                raise InstanceError('Database changed after verified backup; abort this upgrade and take a new backup')
            before=await cluster_identity(conn)
            script=stage/'release/database/scripts/migrate.py'
            spec=importlib.util.spec_from_file_location('salesbuddy_upgrade_migration',script)
            migration=importlib.util.module_from_spec(spec); sys.modules[spec.name]=migration; spec.loader.exec_module(migration)
            results=await migration.migrate(conn,stage/'release/database')
            applied=[r['key'] for r in results if r['status'] != 'unchanged']
            if applied != journal['plan']['pending_migrations']:
                raise InstanceError('Actual migrations differ from the reviewed upgrade plan')
            policy=validate_policy(json.loads(packages['new'][0]['tools/installation/runtime_permissions.json']))
            await reconcile(conn,journal['old']['roles'],policy)
            if before != await cluster_identity(conn):
                raise InstanceError('Upgrade changed cluster roles; transaction rolled back')
            await verify_sequence_positions(conn)
            await conn.execute('UPDATE public.salesbuddy_installation SET metadata_digest=$1 WHERE singleton',metadata_digest(journal['new']))
            checkpoint(root,journal,'before_database_commit')
        checkpoint(root,journal,'database_committed')
    finally: await conn.close()


def switch_files(root,journal,stage,packages):
    # Every rename has an unambiguous source/destination and durable parent sync.
    # Database is committed before this begins; interruption must finish forward.
    oldfiles,oldmanifest=packages['old']; newfiles,newmanifest=packages['new']
    old=stage/'previous-release'; current=root/'release'; incoming=stage/'release'
    if not old.exists():
        verify_tree(current,oldfiles,oldmanifest); verify_tree(incoming,newfiles,newmanifest)
        move(current,old); checkpoint(root,journal,'old_release_saved')
    else: verify_tree(old,oldfiles,oldmanifest)
    if not current.exists():
        verify_tree(incoming,newfiles,newmanifest); move(incoming,current)
        checkpoint(root,journal,'new_release_installed')
    verify_tree(current,newfiles,newmanifest)
    if not (stage/'previous.zip').exists():
        if digest(owned_file(root/'artifact.zip')) != journal['old']['artifact_sha256']:
            raise InstanceError('Old artifact changed during upgrade')
        move(root/'artifact.zip',stage/'previous.zip'); checkpoint(root,journal,'old_archive_saved')
    if not (root/'artifact.zip').exists():
        if digest(owned_file(stage/'artifact.zip')) != journal['new']['artifact_sha256']:
            raise InstanceError('Incoming artifact changed during upgrade')
        move(stage/'artifact.zip',root/'artifact.zip'); checkpoint(root,journal,'new_archive_installed')
    if digest(owned_file(root/'artifact.zip')) != journal['new']['artifact_sha256']:
        raise InstanceError('Installed artifact is not the new version')
    atomic_json(root/'INSTANCE.json',journal['new']); checkpoint(root,journal,'metadata_switched')
    source_check(root)
    if not (stage/'runtime/bin/python').is_file(): raise InstanceError('New dependency runtime is missing')
    atomic_json(root/'run/initialized.json',{'instance_id':journal['new']['instance_id'],'revision':journal['new']['revision']})


def finish(root,journal,stage,status):
    receipt={'status':status,'from_revision':journal['old']['revision'],
             'revision':journal['old' if status == 'aborted' else 'new']['revision'],
             'candidate_revision':journal['new']['revision'],
             'database_restored':False,'production_deployed':False,'services_started':False,
             'backup':journal.get('backup'),'restart_required':True}
    atomic_json(root/'run/last-upgrade.json',receipt)
    checkpoint(root,journal,status)
    move(root/'run/upgrade.json',stage/(status+'.json'))
    return receipt


async def abort_pending(root):
    # Cancelling before commit must not depend on the damaged incoming package.
    # The original source, credentials, DB identity and full role policy remain
    # mandatory, so a committed/unknown DB can never be cancelled as "old".
    journal=read_private(root/'run/upgrade.json')
    if (journal.get('format') != CONTRACT['format'] or journal.get('root') != str(root)
            or not re.fullmatch(r'[0-9a-f]{32}',journal.get('id',''))):
        raise InstanceError('Invalid pending upgrade identity')
    stage=root/'upgrades'/journal['id']; root_path(stage)
    current,_=source_check(root)
    if current != journal['old']: raise InstanceError('Old instance is no longer current; cancellation refused')
    if not re.fullmatch(r'[0-9a-f]{40}',journal.get('new',{}).get('revision','')):
        raise InstanceError('Unknown candidate revision in pending upgrade')
    conn=await connect(journal)
    try:
        await check_record(conn,current)
        await verify(conn,current['roles'],json.loads((root/'release/tools/installation/runtime_permissions.json').read_text()))
        await no_connections(conn)
    finally: await conn.close()
    return finish(root,journal,stage,'aborted')


async def perform(root,pg_bin,*,abort=False):
    if abort: return await abort_pending(root)
    journal,stage,packages=load(root)
    conn=await connect(journal)
    try:
        state=await database_state(conn,journal,packages)
        await no_connections(conn)
    finally: await conn.close()
    if state == 'old':
        if source_check(root)[0] != journal['old']: raise InstanceError('Old source changed before database commit')
        verify_tree(stage/'release',*packages['new'])
        install_dependencies(root,stage)
        verify_tree(stage/'release',*packages['new'])
        checkpoint(root,journal,'dependencies_ready')
        if not journal.get('backup'):
            result=await backup(root,pg_bin)
            checked=await restore_check(root,Path(result['backup']),pg_bin)
            atomic_json(root/'run/last-restore-check.json',{**checked,'probe_database_removed':True})
            journal['backup']=result['backup']; journal['backup_manifest_sha256']=checked['backup_manifest_sha256']
            checkpoint(root,journal,'backup_verified')
        directory=Path(journal['backup'])
        snapshot,_=validate_backup(root,directory)
        if digest(directory/'BACKUP.json') != journal['backup_manifest_sha256']:
            raise InstanceError('Recovery verification no longer matches this backup')
        if material_tree(root) != {k:v for k,v in snapshot['files'].items() if k.startswith('materials/')}:
            raise InstanceError('Original files changed after backup')
        # Recheck private and candidate bytes immediately before entering SQL.
        load(root); source_check(root); verify_tree(stage/'release',*packages['new'])
        await migrate_database(root,journal,stage,packages,snapshot)
    else:
        # A committed database may outlive an interrupted dependency install or
        # filesystem write. Reconcile the locked runtime before completing files.
        release=stage/'release' if (stage/'release').exists() else root/'release'
        verify_tree(release,*packages['new'])
        install_dependencies(root,stage,release,reinstall=True)
        verify_tree(release,*packages['new'])
    switch_files(root,journal,stage,packages)
    conn=await connect(journal)
    try:
        if await database_state(conn,journal,packages) != 'new': raise InstanceError('Final database version is not the installed version')
    finally: await conn.close()
    return finish(root,journal,stage,'completed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('upgrade','resume','abort'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--acknowledge-api-change',action='store_true')
    args=parser.parse_args()
    if args.command == 'upgrade' and (not args.archive or not args.sha256): parser.error('upgrade requires --archive and --sha256')
    root=root_path(args.root)
    with locked(root), ExitStack() as locks:
        for service in ('api','worker','feishu'): locks.callback(os.close,service_lock(root,service))
        if args.command == 'upgrade': prepare(root,args.archive,args.sha256,acknowledge_api_change=args.acknowledge_api_change)
        result=asyncio.run(perform(root,args.pg_bin,abort=args.command=='abort'))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try: main()
    except (InstanceError, ValueError) as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        # Do not expose database exceptions, SQL parameters or credentials.
        raise SystemExit('Upgrade stopped ('+type(exc).__name__+'); private state retained; inspect and resume, never replace the database blindly') from None
