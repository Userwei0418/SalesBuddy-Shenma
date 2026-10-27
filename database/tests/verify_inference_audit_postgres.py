#!/usr/bin/env python3
"""V192 -> V194 audit completion and canonical runtime permission preservation.

Default starts a disposable UTF8 PG16 cluster on a private Unix socket. CI may
provide --port/--user; only a new random database and roles are ever modified.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import asyncpg

from verify_runtime_grants_postgres import migrate_through
from verify_canonical_v192_grants_postgres import verify as verify_canonical_upgrade
from verify_task_import_postgres import context, fixture, rejected

FINISH = "security.finish_inference_operation(uuid,text,jsonb,jsonb,text)"


async def runtime(owner, name, privileges):
    await owner.execute(f'CREATE ROLE "{name}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    await owner.execute(f'GRANT USAGE ON SCHEMA agent,security,common TO "{name}"')
    await owner.execute(f'GRANT {privileges} ON agent.inference_operation TO "{name}"')
    await owner.execute(f'''GRANT EXECUTE ON FUNCTION common.current_workspace_id(),
      common.current_user_ref_id(),common.current_role_code(),security.workspace_is_active(uuid) TO "{name}"''')


async def receipt(connection, data):
    operation = uuid4()
    await connection.execute('''INSERT INTO agent.inference_operation
      (id,workspace_id,actor_user_ref_id,actor_role_code,capability)
      VALUES($1,$2,$3,'sales','task_import')''', operation, data['ws'], data['creator'])
    return operation


async def verify(config):
    suffix = uuid4().hex[:12]
    database = 'sb_inference_db_' + suffix
    names = {kind: 'sb_inference_' + kind + '_' + suffix for kind in ('api', 'worker', 'reader', 'late')}
    admin = await asyncpg.connect(database='postgres', **config)
    connections, roles, checks = [], [], []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0 ENCODING \'UTF8\'')
        owner = await asyncpg.connect(database=database, **config)
        connections.append(owner)
        await migrate_through(owner, 192)
        data = await fixture(owner)
        for kind, privileges in (('api', 'INSERT,UPDATE'), ('worker', 'SELECT,INSERT,UPDATE'), ('reader', 'SELECT')):
            await runtime(owner, names[kind], privileges)
            roles.append(names[kind])
        # Legacy maintenance defaults must not silently grant the new private
        # completion entry point to an unrelated read-only observer.
        await owner.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA security GRANT EXECUTE ON FUNCTIONS TO "{names["reader"]}"')
        await owner.execute(f'GRANT UPDATE(trace) ON agent.inference_operation TO "{names["api"]}"')
        api = await asyncpg.connect(database=database, **config)
        connections.append(api)
        await api.execute(f'SET ROLE "{names["api"]}"')
        await context(api, data)
        operation = await receipt(api, data)
        await rejected(api, "UPDATE agent.inference_operation SET status='failed' WHERE id=$1 AND status='running'", operation)
        checks.append('v192_api_insert_succeeds_but_update_without_select_fails')

        await migrate_through(owner, 193)
        for kind in ('api', 'worker'):
            assert await owner.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', names[kind], FINISH)
            assert not await owner.fetchval("SELECT has_table_privilege($1,'agent.inference_operation','UPDATE')", names[kind])
            assert not await owner.fetchval("SELECT has_column_privilege($1,'agent.inference_operation','trace','UPDATE')", names[kind])
        assert not await owner.fetchval("SELECT has_table_privilege($1,'agent.inference_operation','SELECT')", names['api'])
        assert await owner.fetchval("SELECT has_table_privilege($1,'agent.inference_operation','SELECT')", names['worker'])
        checks.append('upgrade_grants_finish_and_revokes_table_and_column_update_without_expanding_read')
        assert await api.fetchval("SELECT security.finish_inference_operation($1,'failed','{}','[]','TimeoutError')", operation)
        first = dict(await owner.fetchrow('SELECT * FROM agent.inference_operation WHERE id=$1', operation))
        assert first['status'] == 'failed' and first['completed_at'] is not None
        assert not await api.fetchval("SELECT security.finish_inference_operation($1,'accepted','{}','[]',NULL)", operation)
        assert dict(await owner.fetchrow('SELECT * FROM agent.inference_operation WHERE id=$1', operation)) == first
        await rejected(api, 'SELECT * FROM agent.inference_operation')
        await rejected(api, "UPDATE agent.inference_operation SET status='accepted'")
        await rejected(api, 'DELETE FROM agent.inference_operation')
        checks.append('owned_receipt_finishes_once_without_private_read_or_direct_mutation')

        await runtime(owner, names['late'], 'INSERT,UPDATE')
        roles.append(names['late'])
        assert not await owner.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', names['late'], FINISH)
        await owner.fetchval('SELECT security.reconcile_runtime_grants()')
        assert await owner.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', names['late'], FINISH)
        assert not await owner.fetchval("SELECT has_table_privilege($1,'agent.inference_operation','UPDATE')", names['late'])
        checks.append('late_runtime_receives_same_narrow_finish_grant')

        for role in (names['reader'], 'salegent_feishu_worker', 'pg_write_all_data'):
            assert not await owner.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', role, FINISH)
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.oid=$1::regprocedure AND a.grantee=0)", FINISH)
        for kind in ('api', 'worker', 'reader', 'late'):
            assert not await owner.fetchval("SELECT has_function_privilege($1,'security.reconcile_runtime_grants()','EXECUTE')", names[kind])
        assert await owner.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid='agent.inference_operation'::regclass")
        checks.append('public_reader_feishu_and_runtime_maintenance_access_denied_with_rls_preserved')
    finally:
        for connection in reversed(connections):
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
    checks.extend(await verify_canonical_upgrade(config))
    print(json.dumps({'passed': len(checks), 'checks': checks, 'production_data': False, 'external_calls': False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', default='/opt/homebrew/opt/postgresql@16/bin')
    parser.add_argument('--port', type=int)
    parser.add_argument('--user', default='postgres')
    args = parser.parse_args()
    if args.port:
        if args.port == 15432 or not 1 <= args.port <= 65535:
            raise ValueError('Do not use the acceptance database server')
        asyncio.run(verify(dict(host='127.0.0.1', port=args.port, user=args.user)))
        return
    with tempfile.TemporaryDirectory(prefix='sb-inference-audit-', dir='/tmp') as temporary:
        root, binary = Path(temporary), Path(args.pg_bin)
        data, socket = root / 'data', root / 'socket'
        socket.mkdir()
        subprocess.run([str(binary / 'initdb'), '-D', str(data), '-U', getpass.getuser(), '-A', 'trust', '--no-locale', '-E', 'UTF8'], check=True, capture_output=True)
        subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'), '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
        try:
            asyncio.run(verify(dict(host=str(socket), user=getpass.getuser())))
        finally:
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


if __name__ == '__main__':
    main()
