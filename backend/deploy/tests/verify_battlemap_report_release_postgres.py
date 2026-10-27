"""Synthetic V297 -> V298 release-helper verification, never production evidence.

PG_BIN=/path/to/pg16/bin python backend/deploy/tests/verify_battlemap_report_release_postgres.py
Only a new private Unix-socket cluster is used and removed, with no DSN or secrets.
"""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import asyncpg

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/p) for p in ('backend/deploy','database/scripts','tools/installation')]
import migrate as m
import release_battlemap_report_security as security
import release_battlemap_report_contract as contract
from release_bundle_control import digest
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles

PORT=56429


async def verify(socket):
    conn=await asyncpg.connect(host=str(socket),port=PORT,user='postgres',database='postgres',
        password='',passfile='/dev/null',ssl=False,command_timeout=120)
    try:
        for role in ('salegent_feishu_worker','salegent_api','salegent_worker','salegent_platform'):
            await conn.execute(f'CREATE ROLE {role} NOLOGIN')
        for role in security.ROLES.values():
            await conn.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS')
        await conn.execute('GRANT salegent_feishu_worker TO sales_feishu_saas WITH INHERIT FALSE, SET FALSE')
        migrations=m.discover(ROOT/'database')
        with patch.object(m,'discover',return_value=[v for v in migrations if v.key!='V298']):
            with redirect_stdout(io.StringIO()): await m.migrate(conn,ROOT/'database',adopt_existing=True)
        target=load_policy();source=deepcopy(target)
        source.update(schema='V297',migration_fingerprint=contract.BASE_LEDGER,
                      rls_fingerprint='196edfb67a8acd88c0f6cd64d7fdeb36c54f9d2515758f7ea26859049a2cb943')
        async with conn.transaction(): await reconcile_service_roles(conn,security.ROLES,source)
        await verify_service_roles(conn,security.ROLES,source)
        checks=[]
        async with conn.transaction(isolation='repeatable_read'):
            await conn.execute("SET LOCAL statement_timeout='30s'")
            await security.require_no_added_columns(conn)
            original_privileges=await security.source_objects(conn)
            before=await security.snapshot(conn);data=await security.business_state(conn)
            assert await conn.fetchval('SHOW statement_timeout')=='30s'
            prior=dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
            registered=await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
            assert digest(prior)==contract.BASE_LEDGER
            source_registered=registered
            assert security.GRANTS==()
            assert original_privileges['worker_task_execution_select']==dict.fromkeys(security.WORKER_COLUMNS,True)
            with redirect_stdout(io.StringIO()):
                assert await m.apply(conn,next(v for v in migrations if v.key=='V298'),ROOT/'database')=='applied'
            assert await security.snapshot(conn)==before
            security.validate_new_acl(await security.new_objects(conn))
            await security.verify_effective_privileges(conn)
            await verify_service_roles(conn,security.ROLES,target)
            assert await security.source_objects(conn)==original_privileges
            assert await security.business_state(conn)==data
            after=dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
            registered=await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
            assert digest(after)==contract.TARGET_LEDGER
            assert registered==sorted(source_registered+['V298'])
            checks.append('exact_v297_to_v298_and_only_two_policies_without_any_runtime_grant')
            checks.append('all_old_catalog_acl_rls_functions_views_indexes_roles_privileges_and_business_unchanged')
        for sql,new_object_failure in (
          ('ALTER POLICY self_quadrant_update ON insight.quadrant_score USING (true)',True),
          ('CREATE POLICY extra_score ON insight.quadrant_score FOR SELECT USING (true)',False),
          ('CREATE POLICY self_quadrant_insert ON crm.customer FOR INSERT WITH CHECK (true)',False),
          ('GRANT SELECT(updated_at) ON workflow.task_execution TO sales_worker_saas',False),
          ('GRANT INSERT ON insight.quadrant_score TO sales_api_saas',False),
          ('ALTER FUNCTION security.authorization_refresh() COST 101',False),
        ):
            tx=conn.transaction();await tx.start()
            try:
                await conn.execute(sql)
                if new_object_failure:
                    try: security.validate_new_acl(await security.new_objects(conn))
                    except Exception as error:
                        from release_bundle_control import Refused
                        assert isinstance(error,Refused)
                    else: raise AssertionError('Broader policy escaped exact check')
                else: assert await security.snapshot(conn)!=before,sql
            finally: await tx.rollback()
        checks.append('policy_broadening_extra_policies_unrelated_grants_and_old_function_changes_rejected')
        print(json.dumps({'status':'passed','synthetic':True,'production_restore_evidence':False,
          'running_business_data_touched':False,'checks':checks,
          'source_checksum_ledger_sha256':contract.BASE_LEDGER,'target_checksum_ledger_sha256':contract.TARGET_LEDGER,
          'historical_registrations':'fresh_install_only_production_full_ledger_requires_restore',
          'migration_sha256':contract.SQL['V298'][1]},indent=2),flush=True)
    finally: await conn.close()


def run():
    binary=Path(os.environ['PG_BIN'])
    with tempfile.TemporaryDirectory(prefix='sb-battlemap-release-',dir='/tmp') as temporary:
        root=Path(temporary);data=root/'pg';socket=root/'socket';socket.mkdir()
        subprocess.run([str(binary/'initdb'),'-D',str(data),'-U','postgres','--auth=trust','--encoding=UTF8','--no-locale'],check=True,capture_output=True)
        try:
            subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),'-o',
              f"-k {socket} -p {PORT} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
            asyncio.run(verify(socket))
        finally:
            if (data/'postmaster.pid').exists():
                subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],check=True,capture_output=True)
    print('Synthetic PostgreSQL stopped and removed',flush=True)


if __name__=='__main__':run()
