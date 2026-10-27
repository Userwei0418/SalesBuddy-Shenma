"""Run the unmodified released mini-program HTTP client against a disposable new API.

Uses a fresh loopback database and real restricted API login, never daily acceptance
data or a supplied URL. No worker or external model/Feishu request is started.
"""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch
import uuid

import asyncpg
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'tools/installation'), str(ROOT / 'tools/local'),
               str(ROOT / 'database/scripts'), str(ROOT / 'backend/src')]
from migrate import migrate
from runtime_roles import provision_new, verify
from bootstrap import seed, identity
from sales_backend.config import get_settings
from history_policy_checks import verify_history_policies

CLIENT_REVISION = '5a84eb999f5fb8d9585056a1beec6f0f4696a3ad'


def extract_client(target, revision=CLIENT_REVISION):
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Client revision must be a full committed SHA')
    archive = subprocess.check_output(['git', 'archive', '--format=tar', revision,
                                       'frontend/miniprogram'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(target, filter='data')
    client = target / 'frontend/miniprogram/utils/apiClient.js'
    original = subprocess.check_output(['git', 'show', revision +
                                       ':frontend/miniprogram/utils/apiClient.js'], cwd=ROOT)
    assert client.read_bytes() == original
    return client, hashlib.sha256(original).hexdigest()


async def main(args):
    if args.port == 15432 or not 1 <= args.port <= 65535:
        raise ValueError('Use a disposable loopback cluster, not the daily acceptance database')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.user):
        raise ValueError('Invalid test cluster user')
    config = dict(host='127.0.0.1', port=args.port, user=args.user, command_timeout=300)
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid.uuid4().hex[:12]
    database = 'salesbuddy_legacy_client_' + suffix
    roles = {service: 'sb_legacy_' + suffix + '_' + service
             for service in ('api', 'worker', 'platform', 'feishu')}
    created = False
    connection = None
    server = None
    server_task = None
    listener = None
    result = None
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        created = True
        connection = await asyncpg.connect(database=database, **config)
        await migrate(connection)
        passwords = await provision_new(connection, database=database, roles=roles)
        await verify(connection, roles)
        test_password = secrets.token_urlsafe(32)
        fixtures = await seed(connection, {'test_password': test_password})
        await connection.execute("UPDATE control.tenant_module SET enabled=true,source='legacy-client-test'")
        history_checks = await verify_history_policies(connection, config, database, roles['api'], passwords['api'])
        # A pair of deliberately ambiguous accounts must never be silently resolved.
        for prefix in ('A', 'B'):
            await connection.execute("UPDATE platform.user_ref SET account_code='SHARED' WHERE id=$1",
                                     identity(prefix + 'OPS'))
        from sales_backend.main import app
        settings = replace(get_settings(), app_env='production', auth_mode='password',
            database_url=f'postgresql://{roles["api"]}:{passwords["api"]}@127.0.0.1:{args.port}/{database}',
            platform_database_url='', access_token_secret=secrets.token_urlsafe(48),
            database_min_pool_size=1, database_max_pool_size=3,
            senseaudio_api_key='', agent_platform_bindings_json='{}')
        with tempfile.TemporaryDirectory(prefix='salesbuddy-legacy-client-') as temporary:
            client, client_hash = extract_client(Path(temporary), args.client_revision)
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            api_port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False,
                                                   loop='asyncio', lifespan='on'))
            with patch('sales_backend.main.get_settings', return_value=settings):
                server_task = asyncio.create_task(server.serve(sockets=[listener]))
                async with asyncio.timeout(30):
                    while not server.started:
                        if server_task.done():
                            await server_task
                            raise RuntimeError('Disposable API did not start')
                        await asyncio.sleep(0.05)
                payload = {'client': str(client), 'base_url': f'http://127.0.0.1:{api_port}/api/v1',
                           'password': test_password, 'companies': fixtures['companies'],
                           'check_new_windows': args.check_new_windows,
                           'probe_historical_large_amount_page': args.client_revision == CLIENT_REVISION}
                process = await asyncio.create_subprocess_exec(
                    args.node, str(ROOT / 'backend/tests/system/legacy_client_http.cjs'),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), 180)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                    raise
                result = json.loads(stdout)
                if process.returncode:
                    print(json.dumps(result, ensure_ascii=False))
                    raise RuntimeError('Released client compatibility failed; see sanitized check results')
                assert not stderr.strip(), 'Unexpected client stderr'
                # The old request body must still produce ordinary tenant-scoped records.
                assert await connection.fetchval('SELECT count(*) FROM crm.opportunity') == 1
                assert await connection.fetchval('SELECT count(*) FROM workflow.task') == 3
                assert await connection.fetchval('SELECT count(*) FROM agent.run') == 0
                assert await connection.fetchval('SELECT count(*) FROM activity.visit') == 0
                result.update(client_revision=args.client_revision, client_sha256=client_hash,
                              history_policy_checks=history_checks,
                              backend_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                              backend_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
                              restricted_api_role=True, external_calls=0, production_changed=False,
                              scope='Released apiClient.js request/response compatibility; not WeChat device rendering or AI quality')
                server.should_exit = True
                await asyncio.wait_for(server_task, 20)
                server_task = None
    finally:
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            await asyncio.wait_for(server_task, 20)
        if listener is not None:
            listener.close()
        if connection is not None:
            await connection.close()
        if created:
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        for role in roles.values():
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
    result['disposable_database_and_roles_removed'] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--node', default='node')
    parser.add_argument('--client-revision', default=CLIENT_REVISION)
    parser.add_argument('--check-new-windows', action='store_true')
    asyncio.run(main(parser.parse_args()))
