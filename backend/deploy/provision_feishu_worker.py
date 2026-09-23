"""Root-only one-time dedicated DB login provisioning. Never reads existing secrets.

Creates a NEW role and a NEW root-only environment file. Existing paths/roles are
not overwritten. SQL and the generated password travel only over stdin; captured
errors are intentionally discarded. Does not start a service or enable syncing.
"""
import argparse
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote


def provision(path, *, role, database, socket, port, runner=subprocess.run):
    if os.geteuid() != 0:
        raise ValueError('Root is required')
    if not all(re.fullmatch(r'[a-z][a-z0-9_]{1,62}', value) for value in (role, database)):
        raise ValueError('Invalid database identity')
    if not 1 <= port <= 65535 or not Path(socket).is_absolute():
        raise ValueError('Invalid database endpoint')
    # Refuse to adopt an existing role: even membership could bring extra rights.
    command = ['sudo', '-u', 'postgres', 'psql', '-X', '-h', socket, '-p', str(port),
               '-d', database, '-v', 'ON_ERROR_STOP=1', '-At']
    query = f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';\n"  # noqa: S608 - strict identifier regex above
    check = runner(command, input=query,
                   text=True, capture_output=True)
    if check.returncode or check.stdout.strip() != '0':
        raise ValueError('Role exists or database preflight failed')
    password = secrets.token_urlsafe(48)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as output:
            dsn = f'postgresql://{role}:{quote(password, safe="")}@127.0.0.1:{port}/{database}'
            output.write(f'FEISHU_SYNC_DATABASE_URL={dsn}\n')
            output.flush()
            os.fsync(output.fileno())
        result = runner(command, input=(f"BEGIN; CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS "
                         f"NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{password}'; "
                         f"GRANT salegent_feishu_worker TO {role}; COMMIT;\n"),
                        text=True, capture_output=True)
        if result.returncode:
            raise ValueError('Dedicated role provisioning failed')
    except Exception:
        # Only the new file created by this invocation can be removed.
        Path(path).unlink(missing_ok=True)
        raise
    return {'provisioned': True, 'role': role, 'service_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment-file', default='/etc/shenma-sales/feishu-sync.env')
    parser.add_argument('--role', default='shenma_feishu_sync')
    parser.add_argument('--database', default='shenma_sales')
    parser.add_argument('--socket', default='/var/run/postgresql')  # noqa: S108 - existing PostgreSQL Unix socket, not a file
    parser.add_argument('--port', type=int, default=5432)
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.environment_file, role=args.role, database=args.database,
                                   socket=args.socket, port=args.port)))
    except Exception:
        print(json.dumps({'provisioned': False, 'code': 'FEISHU_ROLE_PROVISIONING_FAILED'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
