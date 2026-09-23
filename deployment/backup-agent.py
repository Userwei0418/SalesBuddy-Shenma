#!/usr/bin/env python3
"""Quiesce the customer Agent instance, back it up, verify DB restores, restart."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile
import time


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'opsbuddy':
        raise SystemExit('Run as root on the customer opsbuddy host only')
    os.umask(0o077)
    base = Path('/opt/raccoon-agent')
    runtime = base / 'runtime'
    meta = json.loads((base / 'instance.json').read_text())
    if meta['public_url'] != 'https://ops-salesbuddy.shenzhoukuntai.com:18899':
        raise SystemExit('Unexpected customer instance')
    provision = Path('/var/lib/shenma-provision')
    provision.mkdir(mode=0o700, exist_ok=True)
    lock = (provision / 'agent-backup.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    target = Path('/var/backups/shenma-agent') / stamp
    target.mkdir(mode=0o700, parents=True)
    compose = ['docker', 'compose', '-f', str(runtime / 'compose.json')]
    log = (target / 'operation.log').open('ab', buffering=0)

    def run(args, *, data=None, output=None):
        return subprocess.run(args, cwd=runtime, input=data, stdout=output or log,
                              stderr=log, check=True)

    def capture(args, *, data=None):
        return subprocess.run(args, cwd=runtime, input=data, stdout=subprocess.PIPE,
                              stderr=log, check=True).stdout

    def psql(database, sql):
        return capture(compose + ['exec', '-T', 'db_postgres', 'sh', '-c',
            'exec psql -X -v ON_ERROR_STOP=1 -A -t -U "$POSTGRES_USER" -d "$1"',
            'shenma-backup', database], data=sql.encode())

    def row_counts(database):
        sql = psql(database, "SELECT format('SELECT %L, count(*) FROM %I.%I;', "
            "schemaname || '.' || tablename, schemaname, tablename) FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY schemaname,tablename;")
        return psql(database, sql.decode()).decode().strip().splitlines()

    raw = capture(compose + ['ps', '-a', '--format', 'json']).decode()
    states = [json.loads(line) for line in raw.splitlines() if line.strip()]
    running = [row['Service'] for row in states if row['State'] == 'running']
    if len(states) != 17 or len(running) != 16 or 'db_postgres' not in running:
        raise SystemExit('Expected healthy complete customer instance before backup')
    if any(row.get('Health') not in ('', 'healthy', None) for row in states):
        raise SystemExit('Unhealthy services; inspect before taking a baseline backup')
    others = [name for name in running if name != 'db_postgres']
    evidence = {'created_at': stamp, 'instance': meta, 'databases': {},
                'contains_credentials_and_customer_data': True}
    completed = False
    try:
        run(compose + ['stop', '--timeout', '60', *others])
        databases = psql('postgres', 'SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname;').decode().splitlines()
        if set(databases) != {'postgres', 'dify', 'dify_plugin'}:
            raise RuntimeError('Unexpected databases; stop and inspect backup scope')
        for index, name in enumerate(('dify', 'dify_plugin')):
            before = row_counts(name)
            dump = target / (name + '.dump')
            with dump.open('xb') as stream:
                run(compose + ['exec', '-T', 'db_postgres', 'sh', '-c',
                    'exec pg_dump -U "$POSTGRES_USER" -Fc --dbname "$1"',
                    'shenma-backup', name], output=stream)
            temporary = 'shenma_restore_' + stamp.lower() + '_' + str(index)
            assert re.fullmatch(r'[a-z0-9_]+', temporary)
            # CREATE must succeed before this invocation is allowed to DROP the temporary DB.
            psql('postgres', 'CREATE DATABASE ' + temporary + ';')
            try:
                run(compose + ['exec', '-T', 'db_postgres', 'sh', '-c',
                    'exec pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$1"',
                    'shenma-restore', temporary], data=dump.read_bytes())
                after = row_counts(temporary)
                if before != after:
                    raise RuntimeError('Restored table row counts differ: ' + name)
                evidence['databases'][name] = {'restore': 'passed', 'tables': len(before),
                    'table_row_counts_sha256': hashlib.sha256('\n'.join(before).encode()).hexdigest()}
            finally:
                psql('postgres', 'DROP DATABASE ' + temporary + ';')
        run(compose + ['stop', '--timeout', '60', 'db_postgres'])
        archive = target / 'instance-files.tar.gz'
        include = ['instance.json', 'offline-provenance.json', 'runtime/.env',
                   'runtime/compose.json', 'runtime/nginx', 'runtime/tls', 'runtime/volumes']
        with tarfile.open(archive, 'x:gz') as tar:
            for name in include:
                tar.add(base / name, arcname='instance/' + name)
            # Include the connection handover created after first installation.
            # The encrypted provider credentials and encryption keys are already
            # covered by database dumps and runtime files respectively.
            for name in ('agent-admin.json', 'agent-runtime-bindings.json',
                         'agent-catalog-publications.json'):
                path = provision / name
                if path.is_file():
                    tar.add(path, arcname='provision/' + name)
        checked = 0
        with tarfile.open(archive) as tar:
            for member in tar:
                if member.isfile():
                    with tar.extractfile(member) as stream:
                        for _ in iter(lambda: stream.read(1024 * 1024), b''):
                            pass
                    checked += 1
        evidence['archive_readable_files'] = checked
        completed = True
    finally:
        # Recreate neither containers nor volumes; restore services that were running on entry.
        run(compose + ['up', '-d', '--no-build', '--no-recreate', '--pull', 'never', *running])
        run(['python3', str(base / 'healthcheck.py'), '--wait', '300'])
    evidence['services_restored'] = True
    evidence['completed'] = completed
    (target / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    files = [path for path in sorted(target.iterdir()) if path.is_file() and path.name != 'operation.log']
    with (target / 'SHA256SUMS').open('x') as stream:
        for path in files:
            h = hashlib.sha256()
            with path.open('rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    h.update(chunk)
            stream.write(h.hexdigest() + '  ' + path.name + '\n')
    print(json.dumps({'backup': str(target), 'verified_databases': list(evidence['databases']),
                      'services_restored': True, 'completed': completed}))


if __name__ == '__main__':
    main()
