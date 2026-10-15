"""Backup acceptance helpers, invoked only inside the disposable installer fixture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import asyncpg

from instance_backup import inventory
from instance_database import database_config
from instance_files import read_private
from sales_backend.security.runtime_credentials import CredentialCipher


def backup_command(root, pg_bin, action, *, directory=None, expect=True):
    args = [str(root/'runtime/bin/python'), str(root/'release/tools/installation/instance_backup.py'),
            action, '--root', str(root), '--pg-bin', str(pg_bin)]
    if directory: args.extend(['--backup', str(directory)])
    env = {k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SALESBUDDY_INSTALL_DB_PASSWORD') if k in os.environ}
    env['PYTHONPYCACHEPREFIX'] = str(root/'cache/pycache')
    result = subprocess.run(args,capture_output=True,text=True,env=env,timeout=600)
    descriptor = os.open(root.parent/'backup-driver.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor,'a') as stream: stream.write(action+' exit='+str(result.returncode)+'\n'+result.stdout+result.stderr+'\n')
    if not expect:
        assert result.returncode, 'Unsafe backup or restore unexpectedly accepted'
        return
    if result.returncode: raise AssertionError('Instance backup check failed; private fixture: '+str(root))
    return json.loads(result.stdout)


async def exercise(root, conn, pg_bin, command, *, include_pending=False):
    # Live services and an independent DB session both block offline backup.
    backup_command(root,pg_bin,'backup',expect=False)
    command(root,'stop','--root',str(root))
    backup_command(root,pg_bin,'backup',expect=False)
    # A migrated connection retains search_path='', while a new maintenance
    # connection sees public extension types. Both must produce the complete
    # same inventory, not just equivalent table counts or omitted ACL fields.
    metadata = read_private(root/'INSTANCE.json')
    fresh = await asyncpg.connect(database=metadata['database']['name'],
        **database_config(metadata,admin=True,password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD')))
    try:
        async with conn.transaction(isolation='repeatable_read',readonly=True):
            await conn.execute("SET LOCAL search_path=''")
            migrated_inventory = await inventory(conn)
        async with fresh.transaction(isolation='repeatable_read',readonly=True):
            fresh_inventory = await inventory(fresh)
        assert migrated_inventory == fresh_inventory
        assert any(row['nspname']=='public' and row['proname']=='gbt_bit_same'
                   for row in fresh_inventory['security']['functions'])
    finally:
        await fresh.close()
    actor = await conn.fetchrow('SELECT id,workspace_id FROM platform.user_ref ORDER BY id LIMIT 1')
    for driver in ('postgres','local'):
        key = uuid.uuid4(); content = ('Synthetic backup material '+driver).encode()
        path = root/'materials'/('fixture-'+str(key)+'.txt')
        if driver == 'local': path.touch(mode=0o600); path.write_bytes(content)
        await conn.execute('''INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,
            file_path,file_size,status,storage_profile,storage_driver,storage_key,content_sha256)
            VALUES($1,$2,$3,'synthetic-backup.txt',$4,$5,'succeeded',$6,$7,$8,$9)''',key,actor['workspace_id'],actor['id'],
            str(path) if driver == 'local' else '',len(content),'legacy-local' if driver == 'local' else 'postgres',driver,
            str(path) if driver == 'local' else str(actor['workspace_id'])+'/'+str(key),hashlib.sha256(content).hexdigest())
        if driver == 'postgres':
            await conn.execute('INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)',key,actor['workspace_id'],content)
    if include_pending:
        key = uuid.uuid4(); content = b'Synthetic interrupted upload preserved for reconciliation'
        path = root/'materials'/(str(key)+'.txt'); path.touch(mode=0o600); path.write_bytes(content)
        await conn.execute('''INSERT INTO control.storage_allocation(import_id,workspace_id,actor_id,receipt,size_bytes,
            state,driver,profile,storage_key,filename,content_sha256,staging_path,actor_role)
            VALUES($1,$2,$3,$4,$5,'reserved','local','legacy-local',$6,'pending.txt',$7,$6,'administrator')''',
            key,actor['workspace_id'],actor['id'],uuid.uuid4(),len(content),str(path),hashlib.sha256(content).hexdigest())
        await conn.execute('UPDATE control.tenant_storage_quota SET reserved_bytes=reserved_bytes+$2 WHERE workspace_id=$1',actor['workspace_id'],len(content))
    cipher = CredentialCipher.from_file(str(root/'private/provider-keys.json'),'instance-v1')
    encrypted = cipher.encrypt(str(actor['workspace_id']),'fixture-only-not-a-live-provider-key')
    await conn.execute('''INSERT INTO config.model_api_test(id,workspace_id,purpose,actor_user_ref_id,
        expected_version,request_digest,config_snapshot,api_key_ciphertext,cipher_format,encryption_key_id,api_key_tail,status)
        VALUES($1,$2,'text',$3,0,'synthetic-backup-key','{}'::jsonb,$4,$5,$6,$7,'passed')''',
        uuid.uuid4(),actor['workspace_id'],actor['id'],encrypted['api_key_ciphertext'],encrypted['cipher_format'],
        encrypted['encryption_key_id'],encrypted['api_key_tail'])
    # Export acceptance can already have created originals in this instance.
    # Compare the complete source inventory instead of assuming an empty fixture.
    expected_originals = {
        str(row['id']): {'id': str(row['id']), 'driver': row['storage_driver'],
                         'bytes': row['file_size'], 'sha256': row['content_sha256']}
        for row in await conn.fetch('''SELECT id,storage_driver,file_size,content_sha256
            FROM activity.visit_import WHERE storage_driver IN ('postgres','local')''')
    }
    expected_files = sum(path.is_file() for path in (root/'materials').rglob('*'))
    assert len(expected_originals) >= 2 and expected_files >= 1
    await conn.close()
    first = backup_command(root,pg_bin,'backup')
    directory = Path(first['backup'])
    assert first['originals'] == len(expected_originals) and first['material_files'] == expected_files
    manifest = json.loads((directory/'BACKUP.json').read_text())
    assert len(manifest['originals']) == len(expected_originals)
    assert {item['id']: item for item in manifest['originals']} == expected_originals
    report = backup_command(root,pg_bin,'restore-check',directory=directory)
    assert report['status'] == 'passed' and report['keys_checked']['mfa'] >= 1 and report['keys_checked']['provider'] == 1
    receipt = json.loads((root/'run/last-restore-check.json').read_text())
    assert receipt['probe_database_removed'] is True
    assert receipt['backup_manifest_sha256'] == hashlib.sha256((directory/'BACKUP.json').read_bytes()).hexdigest()
    # Damage only an owned synthetic backup member, verify refusal before DB
    # creation, then restore its bytes so the private evidence remains usable.
    file = next((directory/'materials').glob('*.txt'))
    original = file.read_bytes(); file.write_bytes(b'changed synthetic bytes')
    try: backup_command(root,pg_bin,'restore-check',directory=directory,expect=False)
    finally: file.write_bytes(original)
    # Restoration and failed probes must leave the source install runnable.
    command(root,'start','--root',str(root),'--feishu')
    command(root,'check','--root',str(root),'--feishu')
    return {**report,'live_services_refused':True,'live_database_session_refused':True,
            'damaged_member_refused':True,'source_restarted_after_drill':True,
            'migration_and_fresh_connection_inventory_equal':True}
