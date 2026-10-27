#!/usr/bin/env python3
"""V188 -> V189 authentication boundaries in a disposable PostgreSQL database.

Default: private Unix-socket-only PG16 cluster. CI: --port 5432 --user postgres.
No runtime env files, business databases, real accounts or external calls.
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
from migrate import migrate

PLATFORM_FUNCTIONS = (
    'control.tenants(text)', 'control.session_profile(text)', 'control.login_candidate(text)',
    'control.issue_session(uuid,text,text,text,integer,bigint)', 'control.prepare_mfa(text,jsonb)',
    'control.mfa_candidate(text,text)', 'control.finish_mfa(text,text,jsonb,integer,bigint,text)',
    'control.logout(text)',
)
NEW_FUNCTIONS = (
    'control.begin_login(uuid,text,text,text,integer)',
    'control.complete_login(text,jsonb,integer,bigint,text,text)',
    'control.confirm_mfa(text,jsonb,integer,bigint,text,text)',
)
PRIVATE = 'control.rotate_mfa_session(text,text,jsonb,integer,bigint,text,text)'


def token():
    return uuid4().hex + uuid4().hex


async def value(conn, query, *args):
    result = await conn.fetchval(query, *args)
    return json.loads(result) if isinstance(result, str) else result


async def denied(conn, query, *args, codes=('P7401', 'P7403')):
    try:
        async with conn.transaction():
            await conn.fetchval(query, *args)
    except asyncpg.PostgresError as error:
        assert error.sqlstate in codes, error.sqlstate
    else:
        raise AssertionError('Expected rejection')


async def operator(owner, enrolled=True):
    uid = uuid4()
    secret = {'synthetic': uid.hex} if enrolled else None
    await owner.execute('''INSERT INTO control.operator(id,account_code,display_name,password_hash,mfa_secret)
      VALUES($1,$2,'Synthetic operator','synthetic-password-hash',$3::jsonb)''',
      uid, 'QA' + uid.hex.upper(), json.dumps(secret) if secret else None)
    return uid, secret


async def begin(conn, uid):
    old = token()
    profile = await value(conn, 'SELECT control.begin_login($1,$2,$3,$4,$5)',
                          uid, 'synthetic-password-hash', old, 'synthetic-throttle', 0)
    assert not profile['mfa_verified'] and 0 < profile['expires_in'] <= 300
    assert profile['expires_at'] and profile['sensitive_until'] is None
    return old, profile


async def finish(conn, old, secret, new, *, revision=0, step=None, enroll=False):
    if step is None:
        step = await conn.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint')
    function = 'confirm_mfa' if enroll else 'complete_login'
    return await value(conn, f'SELECT control.{function}($1,$2::jsonb,$3,$4,$5,$6)',
                       old, json.dumps(secret), revision, step, 'synthetic-password-hash', new)


async def verify(config):
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid4().hex[:12]
    database = 'salegent_verify_login_entry_' + suffix
    platform, api, observer = ['sb_entry_' + kind + '_' + suffix for kind in ('platform', 'api', 'observer')]
    roles = []
    connections = []
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        owner = await asyncpg.connect(database=database, **config)
        connections.append(owner)
        await migrate_through(owner, 188)
        for role in (platform, api, observer):
            await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            roles.append(role)
        await owner.execute(f'GRANT USAGE ON SCHEMA control TO "{platform}"')
        for signature in PLATFORM_FUNCTIONS:
            await owner.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{platform}"')
        await owner.execute(f'GRANT USAGE ON SCHEMA security TO "{api}"')
        await owner.execute(f'GRANT EXECUTE ON FUNCTION security.save_account_authorization(uuid,jsonb) TO "{api}"')
        await owner.execute(f'GRANT USAGE ON SCHEMA control,security TO "{observer}"')
        for schema in ('control', 'security'):
            await owner.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO "{observer}"')

        # A pre-upgrade authenticated session remains unchanged and valid.
        uid, _ = await operator(owner)
        legacy = token()
        step = await owner.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint')
        await owner.fetchval('SELECT control.issue_session($1,$2,$3,$4,$5,$6)',
                             uid, 'synthetic-password-hash', legacy, 'synthetic-throttle', 0, step)
        prior = dict(await owner.fetchrow('SELECT * FROM control.session WHERE token_hash=$1', legacy))
        await migrate(owner)
        assert prior == dict(await owner.fetchrow('SELECT * FROM control.session WHERE token_hash=$1', legacy))
        assert (await value(owner, 'SELECT control.session_profile($1)', legacy))['mfa_verified']
        checks.append('upgrade_preserves_existing_session')

        for signature in NEW_FUNCTIONS:
            assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", platform, signature)
            for role in (api, observer, 'salegent_feishu_worker'):
                assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        for role in (platform, api, observer, 'salegent_feishu_worker'):
            assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, PRIVATE)
            assert await owner.fetchval("SELECT has_function_privilege($1,'security.company_entry(text)','EXECUTE')", role) == (role == api)
        assert all(item['status'] == 'unchanged' for item in await migrate(owner))
        checks.append('api_platform_and_private_acl_separation_including_default_grants')

        one, two = [await asyncpg.connect(database=database, **config) for _ in range(2)]
        connections.extend((one, two))
        for conn in (one, two):
            await conn.execute(f'SET ROLE "{platform}"')
        uid, secret = await operator(owner)
        for observed_hash, revision in (('wrong', 0), ('synthetic-password-hash', 1)):
            await denied(one, 'SELECT control.begin_login($1,$2,$3,$4,$5)',
                         uid, observed_hash, token(), 'synthetic-throttle', revision)
        assert await owner.fetchval('SELECT count(*) FROM control.session WHERE operator_id=$1', uid) == 0
        old, profile = await begin(one, uid)
        assert profile['mfa_enrolled']
        await denied(one, 'SELECT control.tenants($1)', old)
        assert await value(one, "SELECT control.mfa_candidate($1,'verify')", old) is None
        candidate = await value(one, "SELECT control.mfa_candidate($1,'login')", old)
        assert candidate['id'] == str(uid) and candidate['password_hash'] == 'synthetic-password-hash'
        step = await owner.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint')
        for purpose in ('login', 'verify', 'unexpected', None):
            await denied(one, 'SELECT control.finish_mfa($1,$2,$3::jsonb,$4,$5,$6)',
                         old, purpose, json.dumps(secret), 0, step, 'synthetic-password-hash')
        for secret_arg, revision, step_arg, observed_hash, new in (
            ({'wrong': True}, 0, step, 'synthetic-password-hash', token()),
            (secret, 1, step, 'synthetic-password-hash', token()),
            (secret, 0, step, 'wrong', token()),
            (secret, 0, step - 10, 'synthetic-password-hash', token()),
            (secret, 0, None, 'synthetic-password-hash', token()),
            (secret, 0, step, 'synthetic-password-hash', old),
        ):
            await denied(one, 'SELECT control.complete_login($1,$2::jsonb,$3,$4,$5,$6)',
                         old, json.dumps(secret_arg), revision, step_arg, observed_hash, new)
        assert await owner.fetchval('SELECT mfa_last_step FROM control.operator WHERE id=$1', uid) == -1
        # A token collision must roll back both OTP consumption and revocation.
        await denied(one, 'SELECT control.complete_login($1,$2::jsonb,$3,$4,$5,$6)',
                     old, json.dumps(secret), 0, step, 'synthetic-password-hash', legacy,
                     codes=('23505',))
        assert await owner.fetchval('SELECT mfa_last_step FROM control.operator WHERE id=$1', uid) == -1
        assert await value(one, 'SELECT control.session_profile($1)', old)
        new = token()
        profile = await finish(one, old, secret, new)
        assert profile['mfa_verified'] and 0 < profile['expires_in'] <= 1800
        assert await value(one, 'SELECT control.session_profile($1)', old) is None
        assert await value(one, 'SELECT control.tenants($1)', new) == []
        await denied(one, 'SELECT control.complete_login($1,$2::jsonb,$3,$4,$5,$6)',
                     old, json.dumps(secret), 0, step, 'synthetic-password-hash', token())
        assert await value(one, "SELECT control.mfa_candidate($1,'login')", new) is None
        checks.append('password_gate_purpose_proof_expiry_metadata_and_token_rotation')

        # First enrollment rotates the token and invalidates every older family.
        uid, _ = await operator(owner, enrolled=False)
        first, _ = await begin(one, uid)
        other, _ = await begin(one, uid)
        assert await value(one, "SELECT control.mfa_candidate($1,'login')", first) is None
        secret = {'synthetic': 'enrollment'}
        await one.execute('SELECT control.prepare_mfa($1,$2::jsonb)', first, json.dumps(secret))
        await denied(one, 'SELECT control.complete_login($1,$2::jsonb,$3,$4,$5,$6)',
                     first, json.dumps(secret), 0, step, 'synthetic-password-hash', token())
        new = token()
        profile = await finish(one, first, secret, new, enroll=True)
        assert profile['mfa_enrolled'] and profile['mfa_verified'] and profile['expires_in'] <= 1800
        for old in (first, other):
            assert await value(one, 'SELECT control.session_profile($1)', old) is None
        assert await owner.fetchval('SELECT mfa_revision FROM control.operator WHERE id=$1', uid) == 1
        await denied(one, 'SELECT control.confirm_mfa($1,$2::jsonb,$3,$4,$5,$6)',
                     first, json.dumps(secret), 0, step, 'synthetic-password-hash', token())
        checks.append('initial_enrollment_rotates_and_revokes_other_sessions')

        # Concurrent completion of one pending session consumes the OTP once.
        uid, secret = await operator(owner)
        old, _ = await begin(one, uid)
        step = await owner.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint')
        outputs = await asyncio.gather(*[finish(conn, old, secret, token(), step=step) for conn in (one, two)],
                                       return_exceptions=True)
        assert sum(isinstance(output, dict) for output in outputs) == 1
        assert sum(isinstance(output, asyncpg.PostgresError) and output.sqlstate == 'P7401' for output in outputs) == 1
        assert await owner.fetchval('SELECT count(*) FROM control.session WHERE operator_id=$1 AND revoked_at IS NULL', uid) == 1
        checks.append('concurrent_same_pending_session_has_one_winner')

        uid, secret = await operator(owner)
        pending = [(await begin(one, uid))[0] for _ in range(2)]
        step = await owner.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint')
        outputs = await asyncio.gather(*[finish(conn, old, secret, token(), step=step)
                                        for conn, old in zip((one, two), pending)], return_exceptions=True)
        assert sum(isinstance(output, dict) for output in outputs) == 1
        assert sum(isinstance(output, asyncpg.PostgresError) and output.sqlstate == 'P7403' for output in outputs) == 1
        assert await owner.fetchval('SELECT count(*) FROM control.session WHERE operator_id=$1 AND mfa_verified_at IS NOT NULL', uid) == 1
        checks.append('concurrent_distinct_sessions_share_operator_otp_replay_guard')

        for mutation in ('expired', 'logout', 'disabled', 'recovery'):
            uid, secret = await operator(owner)
            old, _ = await begin(one, uid)
            assert await value(one, "SELECT control.mfa_candidate($1,'login')", old)
            if mutation == 'expired':
                await owner.execute("UPDATE control.session SET expires_at=clock_timestamp()-interval '1 second' WHERE token_hash=$1", old)
            elif mutation == 'logout':
                await one.execute('SELECT control.logout($1)', old)
            elif mutation == 'disabled':
                await owner.execute('UPDATE control.operator SET enabled=false WHERE id=$1', uid)
            else:
                async with owner.transaction():
                    await owner.execute("UPDATE control.operator SET password_hash='recovered',mfa_secret=NULL,mfa_revision=mfa_revision+1,mfa_last_step=-1 WHERE id=$1", uid)
                    await owner.execute('UPDATE control.session SET revoked_at=clock_timestamp(),mfa_pending=NULL WHERE operator_id=$1', uid)
            await denied(one, 'SELECT control.complete_login($1,$2::jsonb,$3,$4,$5,$6)',
                         old, json.dumps(secret), 0, step, 'synthetic-password-hash', token())
            assert await value(one, 'SELECT control.session_profile($1)', old) is None
        checks.append('candidate_cannot_survive_expiry_logout_disablement_or_owner_recovery')

        # Hold logout's session row lock while MFA completion is in flight.
        uid, secret = await operator(owner)
        old, _ = await begin(one, uid)
        transaction = owner.transaction()
        await transaction.start()
        transaction_open = True
        task = None
        try:
            await owner.execute('UPDATE control.session SET revoked_at=clock_timestamp() WHERE token_hash=$1', old)
            pid = await one.fetchval('SELECT pg_backend_pid()')
            task = asyncio.create_task(finish(one, old, secret, token()))
            for _ in range(100):
                if await owner.fetchval('SELECT cardinality(pg_blocking_pids($1))>0', pid):
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('Completion did not wait on the logout row lock')
            await transaction.commit()
            transaction_open = False
            try:
                await task
            except asyncpg.PostgresError as error:
                assert error.sqlstate == 'P7401'
            else:
                raise AssertionError('Concurrent logout was reversed')
        finally:
            if transaction_open:
                await transaction.rollback()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        checks.append('locked_session_rechecked_after_concurrent_logout')

        # Old one-step login and sensitive re-verification remain usable.
        uid, secret = await operator(owner)
        old = token()
        step = await owner.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())/30)::bigint')
        profile = await value(one, 'SELECT control.issue_session($1,$2,$3,$4,$5,$6)',
                              uid, 'synthetic-password-hash', old, 'synthetic-throttle', 0, step - 1)
        assert profile['mfa_verified']
        profile = await value(one, "SELECT control.finish_mfa($1,'verify',$2::jsonb,$3,$4,$5)",
                              old, json.dumps(secret), 0, step, 'synthetic-password-hash')
        assert profile['mfa_verified'] and await value(one, 'SELECT control.session_profile($1)', old)
        uid, _ = await operator(owner, enrolled=False)
        old, secret = token(), {'synthetic': 'legacy-enrollment'}
        await one.execute('SELECT control.issue_session($1,$2,$3,$4,$5,$6)',
                          uid, 'synthetic-password-hash', old, 'synthetic-throttle', 0, None)
        await one.execute('SELECT control.prepare_mfa($1,$2::jsonb)', old, json.dumps(secret))
        profile = await value(one, "SELECT control.finish_mfa($1,'enroll',$2::jsonb,$3,$4,$5)",
                              old, json.dumps(secret), 0, step, 'synthetic-password-hash')
        assert profile['mfa_verified'] and await value(one, 'SELECT control.session_profile($1)', old)
        checks.append('legacy_one_step_login_and_sensitive_verification_preserved')

        # Exact public-safe lookup is exposed only to the API runtime family.
        api_conn = await asyncpg.connect(database=database, **config)
        connections.append(api_conn)
        await api_conn.execute(f'SET ROLE "{api}"')
        for status in ('active', 'provisioning', 'suspended', 'closed'):
            await owner.execute('INSERT INTO platform.workspace(external_workspace_id,name,status) VALUES($1,$2,$3)',
                                'qa-entry-' + status, 'Synthetic ' + status, status)
        await owner.execute("INSERT INTO platform.workspace(external_workspace_id,name,deleted_at) VALUES('qa-entry-deleted','Synthetic deleted',clock_timestamp())")
        expected = {'company_code': 'qa-entry-active', 'company_name': 'Synthetic active'}
        assert await value(api_conn, 'SELECT security.company_entry($1)', '  QA-ENTRY-ACTIVE  ') == expected
        await owner.execute("INSERT INTO platform.workspace(external_workspace_id,name) VALUES('Legacy_Code.X','Synthetic legacy')")
        assert await value(api_conn, 'SELECT security.company_entry($1)', ' legacy_CODE.x ') == {
            'company_code': 'Legacy_Code.X', 'company_name': 'Synthetic legacy'}
        for code in ('qa-entry', 'qa-entry-%', 'qa-entry-provisioning', 'qa-entry-suspended',
                     'qa-entry-closed', 'qa-entry-deleted', 'not-found', '', ' ', None, 'x' * 129):
            assert await value(api_conn, 'SELECT security.company_entry($1)', code) is None
        assert await owner.fetchval("SELECT count(*) FROM pg_proc WHERE pronamespace='security'::regnamespace AND proname='company_entry'") == 1
        checks.append('company_lookup_exact_normalized_active_only_two_fields')
        assert all(item['status'] == 'unchanged' for item in await migrate(owner))
        print(json.dumps({'passed': len(checks), 'checks': checks, 'production_data': False,
                          'external_calls': False}, ensure_ascii=False, indent=2))
    finally:
        for conn in reversed(connections):
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', default='/opt/homebrew/opt/postgresql@16/bin')
    parser.add_argument('--port', type=int, help='Isolated CI server on localhost; creates its own random database')
    parser.add_argument('--user', default='postgres')
    args = parser.parse_args()
    if args.port:
        if args.port == 15432 or not 1 <= args.port <= 65535:
            raise ValueError('Do not use the acceptance database server')
        asyncio.run(verify(dict(host='127.0.0.1', port=args.port, user=args.user)))
        return
    with tempfile.TemporaryDirectory(prefix='sb-login-entry-', dir='/tmp') as temporary:
        root = Path(temporary)
        data, socket, binary = root / 'data', root / 'socket', Path(args.pg_bin)
        socket.mkdir()
        subprocess.run([str(binary / 'initdb'), '-D', str(data), '-U', getpass.getuser(), '-A', 'trust', '--no-locale'],
                       check=True, capture_output=True)
        subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'),
                        '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
        try:
            asyncio.run(verify(dict(host=str(socket), user=getpass.getuser())))
        finally:
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                           check=True, capture_output=True)


if __name__ == '__main__':
    main()
