"""Restore a full synthetic V202 backup into a NEW disposable database.

The original synthetic DB retains a post-backup write. No production URL, daily
acceptance database, external provider, service restart or in-place restore.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'database/scripts'), str(ROOT/'backend/deploy')]
from migrate import migrate
from release_bundle_control import digest


async def main(args):
    if args.port == 15432 or not 1 <= args.port <= 65535:
        raise ValueError('Use only a separate disposable loopback cluster')
    config=dict(host='127.0.0.1',port=args.port,user='postgres')
    admin=await asyncpg.connect(database='postgres',**config)
    names=['sb_bundle_restore_'+uuid.uuid4().hex[:12] for _ in range(2)]
    created=[]; connections=[]
    try:
        for name in names:
            await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0');created.append(name)
        original=await asyncpg.connect(database=names[0],**config);connections.append(original)
        await migrate(original)
        await original.execute('CREATE TABLE public.recovery_canary(id integer PRIMARY KEY, note text NOT NULL)')
        await original.execute("INSERT INTO public.recovery_canary VALUES(1,'before backup')")
        expected=dict(await original.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
        with tempfile.TemporaryDirectory(prefix='sb-bundle-restore-') as directory:
            archive=Path(directory)/'synthetic.dump'
            base=['-h','127.0.0.1','-p',str(args.port),'-U','postgres']
            for command in [
                [str(args.pg_bin/'pg_dump'),*base,'-Fc','-f',str(archive),names[0]],
                [str(args.pg_bin/'pg_restore'),'--list',str(archive)],
            ]:
                result=await asyncio.to_thread(subprocess.run,command,capture_output=True,timeout=120)
                if result.returncode: raise RuntimeError('Synthetic backup or catalog validation failed')
            await original.execute("INSERT INTO public.recovery_canary VALUES(2,'after backup; must remain live')")
            result=await asyncio.to_thread(subprocess.run,[str(args.pg_bin/'pg_restore'),*base,
                '--exit-on-error','--no-owner','-d',names[1],str(archive)],capture_output=True,timeout=120)
            if result.returncode: raise RuntimeError('Synthetic restore into separate database failed')
            restored=await asyncpg.connect(database=names[1],**config);connections.append(restored)
            actual=dict(await restored.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
            assert expected==actual
            assert await restored.fetchval('SELECT count(*) FROM public.recovery_canary')==1
            assert await original.fetchval('SELECT count(*) FROM public.recovery_canary')==2
            assert all(row['status']=='unchanged' for row in await migrate(restored))
            report={'status':'passed','schema':await restored.fetchval('SELECT max(version) FROM ops.schema_migration'),
                    'migration_ledger_sha256':digest(actual),'migration_checksums':len(actual),
                    'archive_bytes':archive.stat().st_size,'restored_canaries':1,'original_canaries':2,
                    'post_backup_write_preserved_in_original':True,'production_changed':False,
                    'scope':'Full synthetic archive restore; not offsite or production backup recovery'}
    finally:
        for connection in connections: await connection.close()
        for name in reversed(created): await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
    report['disposable_databases_removed']=True
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--pg-bin',type=Path,default=Path('/opt/homebrew/bin'))
    asyncio.run(main(parser.parse_args()))
