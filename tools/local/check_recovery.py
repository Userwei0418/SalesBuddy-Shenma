"""Exercise actual PostgreSQL and legacy-file originals in an owned local restore drill."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
from recovery import backup, config, connect, owner, restore_check, state_from_config
from local_paths import ensure_private_directory
from bootstrap import identity


async def main():
    cfg=config();admin=await owner(cfg);await admin.close()
    local=state_from_config(cfg);conn=await connect(cfg)
    identifiers=[];legacy=None
    try:
        if await conn.fetchval('SELECT version FROM public.salesbuddy_local_fixture WHERE id=1')!=1:
            raise RuntimeError('Marked synthetic local database required')
        actor=await conn.fetchrow('SELECT id,workspace_id FROM platform.user_ref WHERE id=$1 AND workspace_id=$2',identity('ASALES'),identity('A'))
        if actor is None:raise RuntimeError('Synthetic local fixture account is missing')
        ensure_private_directory(local/'materials')
        for driver in ('postgres','local'):
            key=uuid.uuid4();identifiers.append(key)
            content=('Synthetic restore original '+driver+' '+str(key)).encode()
            if driver=='local':
                legacy=local/'materials'/('recovery-'+str(key)+'.txt')
                legacy.touch(mode=0o600);legacy.write_bytes(content)
            async with conn.transaction():
                await conn.execute('''INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,
                    file_path,file_size,status,storage_profile,storage_driver,storage_key,content_sha256)
                    VALUES($1,$2,$3,'synthetic-recovery.txt',$4,$5,'succeeded',$6,$7,$8,$9)''',key,actor['workspace_id'],actor['id'],
                    str(legacy) if driver=='local' else '',len(content),'legacy-local' if driver=='local' else 'postgres',driver,
                    str(legacy) if driver=='local' else str(actor['workspace_id'])+'/'+str(key),hashlib.sha256(content).hexdigest())
                if driver=='postgres':
                    await conn.execute('INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)',key,actor['workspace_id'],content)
        directory=await backup(cfg)
        # The shared runtime policy creates real default ACLs. Their PostgreSQL
        # internal "char" kind must survive the JSON manifest as text, with the
        # full owner/schema/grants preserved by the restore's inventory compare.
        default_acls=json.loads((directory/'manifest.json').read_text())['inventory']['security']['defaults']
        assert default_acls, 'Default ACL backup/restore regression fixture is missing'
        assert all(isinstance(row['defaclobjtype'],str) and len(row['defaclobjtype'])==1
                   and row['owner'] and isinstance(row['schema'],str)
                   and isinstance(row['acl'],list) for row in default_acls)
        report=await restore_check(cfg,directory)
        assert report['status']=='passed' and report['original_count']>=2
        print('PASS fresh cluster restores PostgreSQL and legacy originals, private keys, default ACLs, restricted logins and tenant isolation')
    finally:
        # Delete only IDs and file created by this invocation, never seed/user records.
        async with conn.transaction():
            await conn.execute('DELETE FROM activity.visit_import_content WHERE import_id=ANY($1::uuid[])',identifiers)
            await conn.execute('DELETE FROM activity.visit_import WHERE id=ANY($1::uuid[])',identifiers)
        await conn.close()
        if legacy:legacy.unlink(missing_ok=True)


if __name__=='__main__':asyncio.run(main())
