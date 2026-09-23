#!/usr/bin/env python3
"""Versioned, atomic PostgreSQL deployment. Credentials are read only from the environment.

Use the backend Python runtime (asyncpg). The session advisory lock covers the whole
deployment; each file and its checksum commit together. Historical self-contained
BEGIN/COMMIT wrappers are removed in memory, never edited on disk.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LOCK = 202609102101


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    key: str
    path: Path
    checksum: str


def item(key: str, path: Path) -> Migration:
    return Migration(key, path, hashlib.sha256(path.read_bytes()).hexdigest())


def discover(root: Path = ROOT) -> list[Migration]:
    # Root V035-V039 are immutable historical releases. New migrations go in migrations/.
    paths = [*root.glob('V*.sql'), *(root / 'migrations').glob('V*.sql')]
    versions: dict[int, Path] = {}
    for path in paths:
        match = re.fullmatch(r'V(\d+)__.+\.sql', path.name)
        if not match:
            raise MigrationError(f'Invalid migration filename: {path.name}')
        version = int(match[1])
        if version in versions:
            raise MigrationError(f'Duplicate migration version: V{version:03d}')
        versions[version] = path
    return [item(f'V{version:03d}', path) for version, path in sorted(versions.items())]


def transactional_sql(path: Path) -> str:
    content = path.read_text(encoding='utf-8')
    # pg_dump restrict keys are psql commands, not schema or user data.
    content = re.sub(r'^\\(?:un)?restrict\s+\S+\s*$', '', content, flags=re.M)
    if re.search(r'^\s*\\', content, flags=re.M):
        raise MigrationError(f'Unsupported psql directive: {path.name}')
    lines = content.splitlines()
    significant = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith('--')]
    if significant and lines[significant[0]].strip().upper() == 'BEGIN;':
        if lines[significant[-1]].strip().upper() != 'COMMIT;':
            raise MigrationError(f'Unbalanced transaction wrapper: {path.name}')
        lines[significant[0]] = lines[significant[-1]] = ''
    if any(line.strip().upper() in {'BEGIN;', 'COMMIT;', 'ROLLBACK;'} for line in lines):
        raise MigrationError(f'Internal transaction control is not allowed: {path.name}')
    return '\n'.join(lines)


async def ensure_ledger(connection) -> None:
    await connection.execute('''
        CREATE TABLE IF NOT EXISTS ops.migration_checksum (
          migration_key text PRIMARY KEY, checksum_sha256 text NOT NULL CHECK(length(checksum_sha256)=64),
          source_file text NOT NULL, provenance text NOT NULL CHECK(provenance IN ('executed','adopted')),
          recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        REVOKE ALL ON ops.migration_checksum FROM PUBLIC;
        COMMENT ON TABLE ops.migration_checksum IS
          'Deployment file checksums; adopted records attest repository baseline, not historical execution contents';
    ''')


async def record(connection, migration: Migration, root: Path, provenance: str) -> None:
    await connection.execute('''
        INSERT INTO ops.migration_checksum(migration_key,checksum_sha256,source_file,provenance)
        VALUES($1,$2,$3,$4)
    ''', migration.key, migration.checksum, str(migration.path.relative_to(root)), provenance)


async def guard_v036(connection) -> None:
    # Same transaction as V036. Never let its historical demo cleanup rename customer data.
    await connection.execute('LOCK TABLE crm.opportunity IN SHARE ROW EXCLUSIVE MODE')
    duplicate = await connection.fetchval(r'''
        SELECT EXISTS(SELECT 1 FROM crm.opportunity WHERE deleted_at IS NULL
        GROUP BY workspace_id,customer_id,lower(regexp_replace(name,'\s+','','g')) HAVING count(*)>1)
    ''')
    if duplicate:
        raise MigrationError('V036 blocked: duplicate opportunity names; resolve with an approved data repair first')


async def apply(connection, migration: Migration, root: Path, *, adopt_existing: bool = False) -> str:
    previous = await connection.fetchval(
        'SELECT checksum_sha256 FROM ops.migration_checksum WHERE migration_key=$1', migration.key)
    if previous:
        if previous != migration.checksum:
            raise MigrationError(f'Applied checksum mismatch: {migration.key}')
        return 'unchanged'
    registered = migration.key.startswith('V') and await connection.fetchval(
        'SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version=$1)', migration.key)
    if registered:
        if not adopt_existing:
            raise MigrationError(f'{migration.key} has no historical checksum; use --adopt-existing after review')
        async with connection.transaction():
            await record(connection, migration, root, 'adopted')
        return 'adopted'
    async with connection.transaction():
        if migration.key == 'V036':
            await guard_v036(connection)
        await connection.execute(transactional_sql(migration.path))
        if migration.key.startswith('V'):
            await connection.execute('''INSERT INTO ops.schema_migration(version,description) VALUES($1,$2)
                ON CONFLICT(version) DO NOTHING''', migration.key, migration.path.stem.split('__', 1)[-1])
        await record(connection, migration, root, 'executed')
    return 'applied'


async def migrate(connection, root: Path = ROOT, *, adopt_existing: bool = False) -> list[dict]:
    migrations = discover(root)  # Validate all file names before any database mutation.
    baseline = item('BASELINE_V1', root / 'baseline/V001__current_schema.sql')
    seeds = [item(path.stem.split('__', 1)[0], path) for path in sorted((root / 'seeds').glob('S*.sql'))]
    results = []
    await connection.execute('SELECT pg_advisory_lock($1)', DEPLOY_LOCK)
    try:
        exists = await connection.fetchval("SELECT to_regclass('ops.schema_migration') IS NOT NULL")
        if not exists:
            partial = await connection.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=ANY($1))",
                                                ['crm', 'activity', 'agent', 'platform', 'ops', 'config'])
            if partial:
                raise MigrationError('Partial/unrecognized schema detected; restore or review before deployment')
            async with connection.transaction():
                await connection.execute(transactional_sql(baseline.path))
                await ensure_ledger(connection)
                await record(connection, baseline, root, 'executed')
            results.append({'key': baseline.key, 'status': 'applied'})
        else:
            await ensure_ledger(connection)
            checksum = await connection.fetchval(
                'SELECT checksum_sha256 FROM ops.migration_checksum WHERE migration_key=$1', baseline.key)
            if checksum and checksum != baseline.checksum:
                raise MigrationError('Applied checksum mismatch: BASELINE_V1')
            if not checksum:
                if not adopt_existing:
                    raise MigrationError('Existing schema requires reviewed --adopt-existing on its first managed deployment')
                async with connection.transaction():
                    await record(connection, baseline, root, 'adopted')
                results.append({'key': baseline.key, 'status': 'adopted'})
        for migration in [*seeds, *migrations]:
            state = await apply(connection, migration, root, adopt_existing=adopt_existing)
            results.append({'key': migration.key, 'status': state})
        # Runtime group roles can be provisioned after an empty installation.
        # Reconcile the explicit relationship-table contract on every deployment,
        # not just when the one-off forward migration happens to run.
        if await connection.fetchval("SELECT to_regprocedure('security.reconcile_runtime_grants()') IS NOT NULL"):
            async with connection.transaction():
                await connection.fetchval('SELECT security.reconcile_runtime_grants()')
        return results
    finally:
        await connection.execute('SELECT pg_advisory_unlock($1)', DEPLOY_LOCK)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='List files without connecting to a database')
    parser.add_argument('--adopt-existing', action='store_true', help='Record unchecksummed historical migrations without replay')
    args = parser.parse_args()
    if args.plan:
        files = [item('BASELINE_V1', ROOT / 'baseline/V001__current_schema.sql'),
                 *(item(p.stem.split('__', 1)[0], p) for p in sorted((ROOT / 'seeds').glob('S*.sql'))), *discover()]
        print(json.dumps([{'key': m.key, 'file': str(m.path.relative_to(ROOT)), 'sha256': m.checksum}
                          for m in files], indent=2))
        return
    dsn = os.environ.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://', 1)
    if not dsn:
        raise MigrationError('DATABASE_URL is required')
    import asyncpg
    connection = await asyncpg.connect(dsn=dsn, command_timeout=300)
    try:
        print(json.dumps(await migrate(connection, adopt_existing=args.adopt_existing), indent=2))
    finally:
        await connection.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except MigrationError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # Connection exceptions can contain credentials or business values. Keep console output bounded.
        raise SystemExit(f'Database deployment failed ({type(error).__name__}); current file rolled back') from None
