"""Verify packaged migrations in new, disposable loopback PostgreSQL databases.

This never uses the current application database and does not provision runtime
roles. Full installation acceptance additionally needs restricted roles and HTTP.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

import asyncpg

from release_bundle import BundleError, read_verified, upgrade_plan


def migration_module(root):
    name = 'packaged_migrate_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, root/'database/scripts/migrate.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def extract_database(files, root):
    for name, content in files.items():
        if not name.startswith('database/'):
            continue
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


async def compare_ledger(connection, manifest):
    actual = dict(await connection.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
    expected = {row['key']: row['sha256'] for row in manifest['migrations']}
    if actual != expected:
        raise BundleError('Actual migration ledger differs from packaged migration manifest')
    if await connection.fetchval('SELECT count(*) FROM platform.workspace'):
        raise BundleError('Empty package install unexpectedly inserted company data')


async def verify(args):
    files, incoming = read_verified(args.archive, args.sha256)
    prior_files, prior = read_verified(args.previous, args.previous_sha256) if args.previous else (None, None)
    plan = upgrade_plan(prior, incoming) if prior else None
    if args.port == 15432:
        raise BundleError('The persistent local acceptance PostgreSQL port is not a disposable test cluster')
    # Fixed loopback host and random database names. Never accept a DSN, existing
    # database name, or server host from a package.
    config = {'host': '127.0.0.1', 'port': args.port, 'user': args.user,
              'password': os.environ.get('BUNDLE_VERIFY_PG_PASSWORD'), 'command_timeout': 300}
    admin = await asyncpg.connect(database='postgres', **config)
    database = 'salesbuddy_bundle_verify_' + uuid.uuid4().hex
    connection, created = None, False
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        connection = await asyncpg.connect(database=database, **config)
        with tempfile.TemporaryDirectory(prefix='salesbuddy-package-sql-') as temporary:
            directory = Path(temporary)
            if prior:
                old = directory/'previous'; extract_database(prior_files, old)
                await migration_module(old).migrate(connection, old/'database')
                await compare_ledger(connection, prior)
            root = directory/'incoming'; extract_database(files, root)
            migrations = migration_module(root)
            first = await migrations.migrate(connection, root/'database')
            await compare_ledger(connection, incoming)
            repeated = await migrations.migrate(connection, root/'database')
            if any(row['status'] != 'unchanged' for row in repeated):
                raise BundleError('Repeated packaged migration changed the database')
            applied = [row['key'] for row in first if row['status'] == 'applied']
            if plan and applied != plan['pending_migrations']:
                raise BundleError('Applied upgrade differs from the reviewed package comparison')
            result = {'status': 'passed', 'kind': 'packaged-database-upgrade' if prior else 'packaged-empty-database',
                'from_revision': prior['revision'] if prior else None, 'to_revision': incoming['revision'],
                'schema': incoming['expected_schema'], 'migrations': len(incoming['migrations']),
                'applied': applied, 'repeat': 'unchanged', 'tenant_data': 'empty',
                'runtime_roles_verified': False, 'http_verified': False,
                'production_changed': False, 'complete_installation_verified': False}
    finally:
        if connection:
            await connection.close()
        if created:
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()
    result['disposable_database_removed'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--previous-sha256')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    if bool(args.previous) != bool(args.previous_sha256):
        parser.error('Both previous artifact path and digest are required for an upgrade test')
    if not 1 <= args.port <= 65535:
        parser.error('Invalid disposable PostgreSQL port')
    print(json.dumps(asyncio.run(verify(args)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except BundleError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit('Packaged database verification failed; inspect the isolated test locally') from None
