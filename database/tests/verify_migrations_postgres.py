"""Destructive tests ONLY in databases created by this process; never connects to sales_saas.

Run with the backend Python runtime as a PostgreSQL maintenance OS user. A local
socket and a maintenance database with CREATEDB permission are required. All test
databases use a randomized salegent_verify_ prefix and are dropped in finally.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from migrate import MigrationError, apply, guard_v036, item, migrate, transactional_sql

ROOT = Path(__file__).resolve().parents[1]


async def main():
    config = {'host': os.environ.get('PGHOST', '/tmp'), 'user': os.environ.get('PGUSER', 'postgres')}
    admin = await asyncpg.connect(database='postgres', **config)
    name = 'salegent_verify_' + uuid.uuid4().hex[:16]
    results = []
    connection = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        competing = await asyncpg.connect(database=name, **config)
        try:
            runs = await asyncio.gather(migrate(connection), migrate(competing))
        finally:
            await competing.close()
        assert any(all(step['status'] == 'applied' for step in run) for run in runs)
        assert any(all(step['status'] == 'unchanged' for step in run) for run in runs)
        results.append('concurrent_empty_deployments_serialize_without_partial_schema')
        assert await connection.fetchval("SELECT count(*) FROM config.form_definition") >= 1
        assert await connection.fetchval("SELECT count(*) FROM config.field_definition WHERE object_type='visit'") >= 12
        assert await connection.fetchval("SELECT to_regclass('crm.customer_actual') IS NOT NULL")
        assert await connection.fetchval("SELECT to_regclass('activity.visit_import') IS NOT NULL")
        results.append('empty_database_system_seed_and_all_migrations')
        second = await migrate(connection)
        assert all(step['status'] == 'unchanged' for step in second)
        results.append('repeat_deployment_is_noop')
        # An old deployment has only schema_migration registrations, not checksums.
        await connection.execute('TRUNCATE ops.migration_checksum')
        try:
            await migrate(connection)
            raise AssertionError('unreviewed adoption accepted')
        except MigrationError:
            pass
        upgraded = await migrate(connection, adopt_existing=True)
        assert all(s['status'] == 'adopted' for s in upgraded if s['key'].startswith('V'))
        results.append('existing_environment_adopts_history_without_replay')
        await connection.execute("UPDATE ops.migration_checksum SET checksum_sha256=repeat('0',64) WHERE migration_key='V035'")
        try:
            await migrate(connection)
            raise AssertionError('modified migration accepted')
        except MigrationError as error:
            assert 'checksum mismatch' in str(error)
        await connection.execute("UPDATE ops.migration_checksum SET checksum_sha256=$1 WHERE migration_key='V035'",
                                 item('V035', ROOT / 'V035__visit_closed_loop.sql').checksum)
        results.append('applied_checksum_mismatch_blocks_upgrade')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'V990__transaction_probe.sql'
            path.write_text('BEGIN;\nCREATE TABLE ops.rollback_probe(id integer);\nSELECT 1/0;\nCOMMIT;')
            try:
                await apply(connection, item('V990', path), root)
                raise AssertionError('failing migration accepted')
            except asyncpg.DivisionByZeroError:
                pass
            assert not await connection.fetchval("SELECT to_regclass('ops.rollback_probe') IS NOT NULL")
            assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM ops.migration_checksum WHERE migration_key='V990')")
            path.write_text('BEGIN;\nCREATE TABLE ops.rollback_probe(id integer);\nCOMMIT;')
            assert await apply(connection, item('V990', path), root) == 'applied'
        results.append('file_and_ledger_rollback_together_and_retry_succeeds')
        # Isolated transaction replaces only this test DB table for the historical repair guard.
        transaction = connection.transaction()
        await transaction.start()
        try:
            await connection.execute('ALTER TABLE crm.opportunity RENAME TO opportunity_saved')
            await connection.execute('CREATE TABLE crm.opportunity(workspace_id uuid,customer_id uuid,name text,deleted_at timestamptz)')
            workspace, customer = uuid.uuid4(), uuid.uuid4()
            await connection.executemany('INSERT INTO crm.opportunity VALUES($1,$2,$3,NULL)',
                                        [(workspace, customer, '客户正式项目'), (workspace, customer, '客户 正式项目')])
            try:
                await guard_v036(connection)
                raise AssertionError('duplicate business names accepted')
            except MigrationError:
                pass
            assert await connection.fetchval("SELECT count(*) FROM crm.opportunity WHERE name LIKE '%演示%'") == 0
        finally:
            await transaction.rollback()
        results.append('historical_demo_rename_is_blocked_for_duplicate_business_data')
        print(json.dumps({'checks': results, 'passed': len(results)}, ensure_ascii=False))
    finally:
        if connection:
            await connection.close()
        assert name.startswith('salegent_verify_')
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(main())
