"""Disposable PG16 V152 adoption, backup restore and commit-failure rehearsal.

Uses only synthetic fixtures and a private fresh Unix-socket-only cluster. Does
not contact a running application, read production credentials or claim systemd
acceptance. Run with the candidate Python and --pg-bin /path/to/postgresql16/bin.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'backend/deploy'))
sys.path.insert(0, str(ROOT/'tools/installation'))
sys.path.insert(0, str(ROOT/'backend/src'))
import adopt_legacy_saas as adoption
import legacy_adoption_database as database
import legacy_adoption_backup as backup
from legacy_adoption_state import AdoptionError, atomic_json, fingerprint, read_private
from upgrade_permissions import cluster_identity
from sales_backend.security.runtime_credentials import CredentialCipher

spec = importlib.util.spec_from_file_location('legacy_upgrade_fixture', ROOT/'database/tests/verify_v152_upgrade_postgres.py')
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)


async def exercise(root, host, pg_bin):
    fixture.frozen_migrations()
    config = {'host': host, 'user': getpass.getuser(), 'port': 5432}
    admin = await asyncpg.connect(database='postgres', **config)
    await admin.execute('CREATE DATABASE adoption_fixture TEMPLATE template0')
    await admin.close()
    conn = await asyncpg.connect(database='adoption_fixture', **config)
    try:
        historical = fixture.archive_baseline(root/'historical')
        await fixture.migrate.migrate(conn, historical)
        actors = await fixture.fixture(conn)
        key = base64.b64encode(secrets.token_bytes(32)).decode()
        key_file = root/'provider.json'
        atomic_json(key_file, {'keys': {'synthetic': key}})
        cipher = CredentialCipher.from_file(str(key_file), 'synthetic')
        for row in await conn.fetch('SELECT workspace_id FROM config.agent_runtime_config'):
            encrypted = cipher.encrypt(str(row['workspace_id']), 'synthetic-key-never-used')
            await conn.execute('UPDATE config.agent_runtime_config SET api_key_ciphertext=$2 WHERE workspace_id=$1',
                               row['workspace_id'], encrypted['api_key_ciphertext'])
        for row in await conn.fetch('SELECT id,workspace_id FROM config.feishu_connection'):
            encrypted = cipher.encrypt(str(row['workspace_id']), 'synthetic-feishu-never-used')
            await conn.execute('INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) VALUES($1,$2,$3,$4)',
                               row['id'], row['workspace_id'], encrypted['api_key_ciphertext'], 'synthetic')
        # Preserve an actual local original alongside the PostgreSQL original.
        local = root/'original.txt'; local.write_bytes(b'synthetic local original')
        local.chmod(0o600)
        await conn.execute("UPDATE activity.visit_import SET storage_driver='local',file_path=$1,storage_key=$1,file_size=$2,content_sha256=$3 WHERE id=(SELECT id FROM activity.visit_import ORDER BY id LIMIT 1)", str(local), local.stat().st_size, backup.digest(local))
        await conn.execute("CREATE ROLE adoption_old LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
        await conn.execute('CREATE ROLE sales_app NOLOGIN NOINHERIT')
        await conn.execute('GRANT sales_app TO adoption_old')
        spec = {'maintenance_auth': {'mode': 'peer', 'os_user': getpass.getuser()}, 'database': {'name': 'adoption_fixture', 'host': host, 'port': 5432,
                             'administrator': getpass.getuser(), 'sslmode': 'disable'},
                'roles': {s: 'adoption_'+s for s in adoption.SERVICES}, 'retire_roles': ['adoption_old'],
                'new_release': str(ROOT), 'runtime_host': host, 'pg_bin': str(pg_bin),
                'backup_paths': [str(key_file), str(local)],
                'provider_keyring': {'path': str(key_file), 'key_id': 'synthetic'}}
        before = await database.ledger(conn)
        target = database.migration_ledger(ROOT)
        assert len(target) == fixture.TARGET_CHECKSUM_COUNT
        journal = {'spec': spec, 'before_ledger': before, 'after_ledger': target,
                   'before_cluster': fingerprint(await cluster_identity(conn)),
                   'database_identity': await database.identity(conn, spec), 'pending': sorted(set(target)-set(before))}
        credentials = {service: secrets.token_urlsafe(48) for service in adoption.SERVICES}
        atomic_json(root/'credentials.json', credentials)
        atomic_json(root/'ADOPTION.json', journal)
        archive = root/'backup'
        sha = await backup.create_backup(conn, spec, archive)
        restored = await backup.restore_check(spec, archive, sha)
        assert restored['database_rows_acl_rls_verified'] and restored['encrypted_credentials'] == 4
        assert restored['originals'] == 2
        manifest = backup.validate_backup(archive, sha)
        original_hash = fingerprint(manifest['inventory'])
        # Both an early role failure and a late migration/ACL failure must roll
        # back the full outer transaction and leave durable passwords unchanged.
        failures = []
        for phase in ('roles_uncommitted', 'migrations_uncommitted', 'before_database_commit'):
            def fault(at):
                if at == phase:
                    raise RuntimeError('synthetic interrupted maintenance')
            try:
                await database.migrate(conn, root, journal, manifest, credentials, fault=fault)
            except RuntimeError:
                pass
            else:
                raise AssertionError('Fault checkpoint not reached')
            assert await database.state(conn, journal) == 'old'
            async with conn.transaction(readonly=True):
                assert fingerprint(await backup.inventory(conn)) == original_hash
            assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(spec['roles'].values()))
            assert await conn.fetchval("SELECT rolcanlogin FROM pg_roles WHERE rolname='adoption_old'")
            assert read_private(root/'credentials.json') == credentials
            failures.append(phase)
        # Lost COMMIT acknowledgment leaves a durable pre-commit target proof.
        def lost_ack(at):
            if at == 'commit_acknowledgment':
                raise RuntimeError('synthetic lost acknowledgment')
        try:
            await database.migrate(conn, root, journal, manifest, credentials, fault=lost_ack)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Lost acknowledgment checkpoint not reached')
        saved = read_private(root/'ADOPTION.json')
        assert saved['phase'] == 'before_database_commit'
        assert await database.state(conn, saved) == 'new'
        assert not await conn.fetchval("SELECT rolcanlogin FROM pg_roles WHERE rolname='adoption_old'")
        assert await conn.fetchval('SELECT count(*) FROM platform.workspace') == 2
        assert await conn.fetchval('SELECT count(*) FROM activity.visit_import') == 2
        assert await conn.fetchval('SELECT max(version) FROM ops.schema_migration') == fixture.TARGET
        assert await conn.fetchval('SELECT count(*) FROM ops.migration_checksum') == fixture.TARGET_CHECKSUM_COUNT
        await fixture.verify_v193_v194_grants(conn, [
            (spec['roles']['api'], True, True), (spec['roles']['worker'], True, False),
            (spec['roles']['platform'], False, False), (spec['roles']['feishu'], False, False),
        ])
        transaction = conn.transaction()
        await transaction.start()
        try:
            await fixture.verify_v193_v194_calls(conn, spec['roles']['api'], actors)
        finally:
            await transaction.rollback()
        await database.verify_connections(spec, credentials)
        try:
            await database.verify_connections(spec, {**credentials, 'api': 'deliberately-wrong-password'})
        except asyncpg.InvalidPasswordError:
            pass
        else:
            raise AssertionError('Runtime connections did not authenticate the saved password')
        try:
            await database.migrate(conn, root, saved, manifest, credentials)
        except AdoptionError:
            pass
        else:
            raise AssertionError('Committed database replayed')
        (archive/'database.dump').write_bytes(b'corrupt fixture')
        try:
            backup.validate_backup(archive, sha)
        except AdoptionError:
            pass
        else:
            raise AssertionError('Corrupted backup accepted')
        print(json.dumps({'status': 'passed', 'from': 'V152', 'to': fixture.TARGET, 'applied': len(journal['pending']),
                          'backup_restore': restored, 'rollback_checkpoints': failures,
                          'lost_commit_ack_reconciled': True, 'four_authenticated_identities': True,
                          'legacy_login_retired': True, 'synthetic_tenants_preserved': 2,
                          'v193_v194_narrow_runtime_grants_and_owned_calls': True,
                          'corrupt_backup_rejected': True, 'systemd_rehearsal': False,
                          'production_data': False}, indent=2))
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', default='/opt/homebrew/opt/postgresql@16/bin')
    args = parser.parse_args()
    binary = Path(args.pg_bin)
    with tempfile.TemporaryDirectory(prefix='sb-adoption-', dir='/tmp') as temporary:
        root = Path(temporary).resolve(); root.chmod(0o700)
        data, socket = root/'data', root/'socket'; socket.mkdir()
        subprocess.run([str(binary/'initdb'), '-D', str(data), '-U', getpass.getuser(), '-A', 'trust',
                        '--no-locale', '--encoding=UTF8'], check=True, capture_output=True)
        hba = data/'pg_hba.conf'
        hba.write_text('local all adoption_api,adoption_worker,adoption_platform,adoption_feishu scram-sha-256\nlocal all all peer\n'+hba.read_text())
        subprocess.run([str(binary/'pg_ctl'), '-D', str(data), '-l', str(root/'postgres.log'),
                        '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
        try:
            asyncio.run(exercise(root, str(socket), binary))
        finally:
            subprocess.run([str(binary/'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


if __name__ == '__main__':
    main()
