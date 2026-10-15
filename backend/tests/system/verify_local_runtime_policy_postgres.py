"""Converge legacy local grants on disposable identities; never open daily PG15432."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'tools/local'), str(ROOT/'tools/installation'),
               str(ROOT/'database/scripts'), str(ROOT/'backend/src')]
import bootstrap
from migrate import migrate
from runtime_roles import SCHEMAS, RolePolicyError, load_policy, verify, verify_service_roles
from upgrade_permissions import reconcile
from sales_backend.auth.passwords import encode_password
from sales_backend.security.platform_mfa import PlatformMFACipher
from sales_backend.services.platform_control import PUBLIC_FUNCTIONS


async def data_hashes(conn):
    """Compare all fixture rows without emitting any identity or credential data."""
    result = {}
    for row in await conn.fetch("""SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p')
        AND n.nspname=ANY($1::text[]) ORDER BY 1,2""", [*SCHEMAS, 'public']):
        name = '.'.join('"'+row[k].replace('"','""')+'"' for k in ('nspname','relname'))
        result[name] = tuple(await conn.fetchrow(
            'SELECT count(*),md5(COALESCE(string_agg(h,\'\' ORDER BY h),\'\')) '
            'FROM (SELECT md5(to_jsonb(t)::text) h FROM '+name+' t) rows'))
    return result


async def acl_snapshot(conn):
    return [dict(r) for r in await conn.fetch("""SELECT 'relation' kind,n.nspname||'.'||c.relname name,
        c.relacl::text acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) UNION ALL
        SELECT 'function',p.oid::regprocedure::text,p.proacl::text FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])
        UNION ALL SELECT 'schema',nspname,nspacl::text FROM pg_namespace WHERE nspname=ANY($1::text[])
        ORDER BY 1,2""", [*SCHEMAS,'public'])]


async def denied(conn, statement):
    try:
        await conn.execute(statement)
    except asyncpg.InsufficientPrivilegeError:
        return
    raise AssertionError('Runtime identity unexpectedly crossed its service boundary')


async def run(args):
    if args.port == 15432 or not 1 <= args.port <= 65535:
        raise ValueError('A disposable loopback PostgreSQL port is required')
    config = dict(host='127.0.0.1', port=args.port, user=args.user, command_timeout=300)
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid.uuid4().hex[:12]
    database = 'sb_local_policy_'+suffix
    roles = {s:'sb_local_policy_'+suffix+'_'+s for s in ('api','worker','platform')}
    failed_roles = {s:'sb_local_failed_'+suffix+'_'+s for s in roles}
    cfg = {s+'_password':secrets.token_urlsafe(40) for s in roles}
    cfg['test_password'] = secrets.token_urlsafe(40)
    created = False; conn = None; result = None
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0'); created = True
        conn = await asyncpg.connect(database=database, **config)
        await migrate(conn)
        await bootstrap.seed(conn, cfg)
        operator = uuid.uuid4()
        mfa = PlatformMFACipher('synthetic', {'synthetic': secrets.token_bytes(32)}).seal(str(operator), secrets.token_bytes(20))
        await conn.execute('''INSERT INTO control.operator(id,account_code,display_name,password_hash,mfa_secret)
            VALUES($1,'SYNTHETIC','隔离本地权限验收',$2,$3::jsonb)''', operator,
            encode_password(cfg['test_password']), json.dumps(mfa))
        before = await data_hashes(conn)
        # Reproduce the previous local bootstrap's broad grants, with random
        # disposable role names rather than any daily local identity.
        for service, role in roles.items():
            password = await conn.fetchval('SELECT quote_literal($1::text)', cfg[service+'_password'])
            await conn.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {password}')
            if service == 'platform':
                await conn.execute(f'GRANT USAGE ON SCHEMA control TO "{role}"')
                for signature in PUBLIC_FUNCTIONS:
                    await conn.execute(f'GRANT EXECUTE ON FUNCTION control.{signature} TO "{role}"')
            else:
                for schema in set(SCHEMAS)-{'control'}:
                    await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
                    await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
                    await conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO "{role}"')
                    await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        await conn.execute('SELECT security.reconcile_runtime_grants()')
        assert await conn.fetchval("SELECT has_function_privilege($1,'security.save_account_authorization(uuid,jsonb)','EXECUTE')", roles['worker'])
        checked = await bootstrap.reconcile_local_runtime(conn, cfg, roles=roles)
        assert checked['services'] == ['api','platform','worker']
        assert await data_hashes(conn) == before
        await bootstrap.reconcile_local_runtime(conn, cfg, roles=roles)
        assert await data_hashes(conn) == before
        for service, role in roles.items():
            login = await asyncpg.connect(host='127.0.0.1',port=args.port,database=database,
                                          user=role,password=cfg[service+'_password'])
            try:
                await denied(login, 'SELECT * FROM platform.password_credential')
                await denied(login, 'SELECT security.reconcile_runtime_grants()')
                if service == 'platform':
                    await denied(login, 'SELECT * FROM crm.customer')
                else:
                    assert await login.fetchval('SELECT count(*) FROM crm.customer') == 0
                if service != 'api':
                    await denied(login, "SELECT security.save_account_authorization(NULL,'{}')")
                    await denied(login, "SELECT security.target_scope_write('person',NULL,NULL,'sales')")
                else:
                    assert not await login.fetchval("SELECT security.target_scope_write('person',NULL,NULL,'sales')")
            finally:
                await login.close()
        # Normal installation/upgrades retain the strict four-identity API.
        for operation in (verify, reconcile):
            try:
                await operation(conn, roles, load_policy())
            except RolePolicyError:
                pass
            else:
                raise AssertionError('Product four-service guard accepted a local subset')
        await conn.execute(f'GRANT EXECUTE ON FUNCTION security.save_account_authorization(uuid,jsonb) TO "{roles["worker"]}"')
        try:
            await verify_service_roles(conn, roles)
        except RolePolicyError:
            pass
        else:
            raise AssertionError('Read-only local verification accepted an overprivileged worker')
        prior_acl = await acl_snapshot(conn)
        bad = copy.deepcopy(load_policy())
        bad['services']['platform']['functions'].append('control.synthetic_missing_function()')
        for selected in (roles, failed_roles):
            try:
                with patch.object(bootstrap, 'load_policy', return_value=bad):
                    await bootstrap.reconcile_local_runtime(conn, cfg, roles=selected)
            except asyncpg.UndefinedFunctionError:
                pass
            else:
                raise AssertionError('Late grant failure unexpectedly committed')
            assert await acl_snapshot(conn) == prior_acl
            assert await data_hashes(conn) == before
            assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))', list(failed_roles.values()))
        await bootstrap.reconcile_local_runtime(conn, cfg, roles=roles)
        await verify_service_roles(conn, roles)
        assert await data_hashes(conn) == before
        result = {'status':'passed','services':sorted(roles),'shared_policy_schema':load_policy()['schema'],
                  'legacy_grants_converged':True,'all_rows_passwords_mfa_preserved':True,
                  'repeated_apply_idempotent':True,'strict_product_four_role_guard':True,
                  'runtime_password_login_and_boundaries':True,'drift_detected_readonly':True,
                  'late_failure_acl_data_and_new_roles_rolled_back':True,
                  'daily_pg15432_accessed':False,'production_changed':False}
    finally:
        if conn: await conn.close()
        if created: await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        for role in [*roles.values(),*failed_roles.values()]:
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        cleanup = not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)',database)
        cleanup = cleanup and not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))',[*roles.values(),*failed_roles.values()])
        await admin.close()
        if result:
            result['temporary_database_and_roles_removed'] = cleanup
            print(json.dumps(result))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--user',required=True)
    asyncio.run(run(parser.parse_args()))
