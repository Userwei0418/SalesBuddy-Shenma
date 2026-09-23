"""Customer-host smoke check. Never prints credentials or session tokens."""
import asyncio
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import urllib.error
import urllib.request

import asyncpg


def request(path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request('http://127.0.0.1:8080/api/v1/' + path,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = response.read()
            return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        # Only the readiness response may be reported; auth failure bodies are omitted.
        return error.code, json.loads(error.read()) if path == 'health/ready' else None


async def main():
    if os.geteuid() != 0 or socket.gethostname() != 'salesbuddy':
        raise SystemExit('Run as root on salesbuddy only')
    env = dict(line.split('=', 1) for line in Path('/etc/shenma-sales/runtime.env').read_text().splitlines()
               if line and not line.startswith('#'))
    connection = await asyncpg.connect(env['DATABASE_URL'])
    try:
        flags = dict(await connection.fetchrow('SELECT current_user AS role,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user'))
        assert flags == {'role': 'shenma_runtime', 'rolsuper': False, 'rolbypassrls': False}
        for table in ('platform.password_credential', 'security.login_throttle'):
            assert not await connection.fetchval("SELECT has_table_privilege(current_user,$1,'SELECT')", table)
        assert not await connection.fetchval("SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE')")
    finally:
        await connection.close()
    service = pwd.getpwnam('shenma-sales')
    root = Path('/opt/shenma-sales/current').resolve()
    checked = subprocess.run([str(root / 'backend/.venv/bin/python'), str(root / 'backend/scripts/runtime_config_credentials.py'), 'keyring-check'],
        env={**env, 'PATH': os.defpath}, user=service.pw_uid, group=service.pw_gid, extra_groups=[],
        capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0, 'Service UID cannot load its private keyring'
    credentials = json.loads(Path('/var/lib/shenma-provision/initial-admin.json').read_text())
    status, session = request('auth/password/login', {'account_code': credentials['account'], 'password': credentials['password']})
    assert status == 200, f'Initial administrator login failed: HTTP {status}'
    token = session['access_token']
    try:
        assert session['must_change_password'] is True and session['actor']['role'] == 'administrator'
        me_status, me = request('auth/me', token=token)
        assert me_status == 200 and me['account_code'] == credentials['account']
    finally:
        logout_status, _ = request('auth/logout', {}, token)
        assert logout_status == 204, f'Logout failed: HTTP {logout_status}'
    live_status, _ = request('health/live')
    version_status, version = request('health/version')
    ready_status, ready = request('health/ready')
    assert live_status == version_status == 200
    assert version['revision'] == (root / 'REVISION').read_text().strip()
    print(json.dumps({'runtime_role': flags, 'private_data_access_denied': True,
        'service_keyring_readable': True, 'initial_login_me_logout_passed': True,
        'initial_password_change_required': True, 'version': version,
        'readiness_http': ready_status, 'readiness': ready}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
