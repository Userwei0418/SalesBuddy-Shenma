"""Database setup/check subprocess, launched using the verified artifact runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

if __name__ == '__main__': sys.dont_write_bytecode = True

import asyncpg

from instance_files import InstanceError, read_private, source_check, metadata_digest
from runtime_roles import provision_new, verify, identifier


def database_config(data, *, admin=False, password=None):
    config = {'host': data['database']['host'], 'port': data['database']['port'],
              'user': data['database']['administrator'] if admin else None,
              'password': password, 'command_timeout': 300}
    if data['database']['sslmode'] != 'disable': config['ssl'] = data['database']['sslmode']
    return config


async def setup(root, *, check_only=False):
    data, manifest = source_check(root)
    root = Path(root)
    database = data['database']['name']
    name = identifier(database)
    config = database_config(data, admin=True, password=os.environ.get('SALESBUDDY_INSTALL_DB_PASSWORD'))
    admin = await asyncpg.connect(database='postgres', **config)
    marker = 'salesbuddy-instance-v1:'+data['instance_id']
    conn = None
    try:
        if not await admin.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'):
            raise InstanceError('Database setup requires the explicit maintenance administrator')
        if not 160000 <= int(await admin.fetchval('SHOW server_version_num')) < 170000:
            raise InstanceError('This installer requires PostgreSQL 16')
        # Global session lock serializes creation/resumption of the chosen database.
        await admin.execute('SELECT pg_advisory_lock(hashtextextended($1,0))', 'salesbuddy-install:'+database)
        found = await admin.fetchrow("SELECT oid,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=$1", database)
        if found and found['marker'] != marker:
            raise InstanceError('Database already exists and does not belong to this instance')
        if not found:
            if check_only: raise InstanceError('Instance database is missing')
            await admin.execute(f'CREATE DATABASE {name} TEMPLATE template0')
            literal = await admin.fetchval('SELECT quote_literal($1::text)', marker)
            await admin.execute(f'COMMENT ON DATABASE {name} IS {literal}')
        conn = await asyncpg.connect(database=database, **config)
        sys.path[:0] = [str(root/'release/database/scripts'), str(root/'release/backend/src')]
        from migrate import migrate
        from sales_backend.auth.passwords import encode_password
        existing = await conn.fetchval("SELECT to_regclass('public.salesbuddy_installation') IS NOT NULL")
        if existing:
            stored = await conn.fetchrow('SELECT * FROM public.salesbuddy_installation')
            if not stored or stored['instance_id'] != uuid.UUID(data['instance_id']) or stored['metadata_digest'] != metadata_digest(data):
                raise InstanceError('Database installation record does not match the selected instance')
            await verify(conn, data['roles'])
            if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM control.operator WHERE id=$1)', stored['operator_id']):
                raise InstanceError('Initial platform operator no longer exists')
        else:
            if check_only: raise InstanceError('Database setup has not committed')
            await migrate(conn, root/'release/database')
            expected = {item['key']:item['sha256'] for item in manifest['migrations']}
            actual = dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
            if actual != expected: raise InstanceError('Database does not match the selected artifact')
            bootstrap = read_private(root/'private/bootstrap.json')
            async with conn.transaction():
                await provision_new(conn, database=database, roles=data['roles'], passwords=bootstrap['database_passwords'])
                operator = await conn.fetchval('''INSERT INTO control.operator(account_code,display_name,password_hash)
                    VALUES($1,$2,$3) RETURNING id''', data['operator']['account'], data['operator']['name'],
                    encode_password(bootstrap['operator_password']))
                await conn.execute('''CREATE TABLE public.salesbuddy_installation(
                    singleton boolean PRIMARY KEY CHECK(singleton), instance_id uuid NOT NULL,
                    metadata_digest text NOT NULL, operator_id uuid NOT NULL,
                    installed_at timestamptz NOT NULL DEFAULT clock_timestamp());
                    REVOKE ALL ON public.salesbuddy_installation FROM PUBLIC''')
                await conn.execute('INSERT INTO public.salesbuddy_installation(singleton,instance_id,metadata_digest,operator_id) VALUES(true,$1::uuid,$2,$3)',
                                   data['instance_id'], metadata_digest(data), operator)
                await verify(conn, data['roles'])
        return {'database_checked': True, 'revision': data['revision'], 'schema': manifest['expected_schema'],
                'migrations': len(manifest['migrations']), 'runtime_permissions': 'verified',
                'platform_account': data['operator']['account'], 'company_data_seeded': False}
    finally:
        if conn: await conn.close()
        await admin.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    try: print(json.dumps(asyncio.run(setup(args.root, check_only=args.check_only))))
    except (InstanceError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit('Database setup/check failed; the private instance and database were retained for diagnosis') from None
