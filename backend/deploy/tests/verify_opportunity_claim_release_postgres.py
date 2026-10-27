"""Synthetic V296 -> V297 release-helper verification, never production evidence.

PG_BIN=/path/to/pg16/bin python backend/deploy/tests/verify_opportunity_claim_release_postgres.py
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
import release_opportunity_claim_security as security
import release_opportunity_claim_contract as contract
from release_bundle_control import digest
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles

PORT=56428


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
        with patch.object(m,'discover',return_value=[v for v in migrations if v.key!='V297']):
            with redirect_stdout(io.StringIO()): await m.migrate(conn,ROOT/'database',adopt_existing=True)
        target=load_policy();source=deepcopy(target)
        source.update(schema='V296',migration_fingerprint=contract.BASE_LEDGER)
        source['services']['api']['functions']=[sig for sig in source['services']['api']['functions'] if sig not in security.FUNCTION_GRANTS]
        source['services']['worker']['columns'].pop(security.WORKER_TABLE)
        async with conn.transaction(): await reconcile_service_roles(conn,security.ROLES,source)
        checks=[]
        async with conn.transaction(isolation='repeatable_read'):
            await conn.execute("SET LOCAL statement_timeout='30s'")
            await security.require_no_added_columns(conn)
            before=await security.snapshot(conn);data=await security.business_state(conn)
            assert await conn.fetchval('SHOW statement_timeout')=='30s'
            prior=dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
            registered=await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
            assert digest(prior)==contract.BASE_LEDGER
            source_registered=registered  # Fresh installs omit pre-baseline historical registrations.
            assert await security.source_objects(conn)=={'worker_task_execution_select':dict.fromkeys(security.WORKER_COLUMNS,False)}
            with redirect_stdout(io.StringIO()):
                assert await m.apply(conn,next(v for v in migrations if v.key=='V297'),ROOT/'database')=='applied'
            assert await security.snapshot(conn)==before
            security.validate_new_acl(await security.new_objects(conn),allow_ungranted=True)
            for sql in security.GRANTS: await conn.execute(sql)
            security.validate_new_acl(await security.new_objects(conn))
            await security.verify_effective_privileges(conn)
            await verify_service_roles(conn,security.ROLES,target)
            assert await security.snapshot(conn)==before
            assert await security.business_state(conn)==data
            after=dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum'))
            registered=await conn.fetchval('SELECT array_agg(version ORDER BY version) FROM ops.schema_migration')
            assert digest(after)==contract.TARGET_LEDGER
            assert registered==sorted(source_registered+['V297'])
            checks.append('exact_v296_to_v297_ledger_and_only_api_three_execute_worker_three_column_delta')
            checks.append('all_old_catalog_rls_functions_views_indexes_roles_and_business_rows_unchanged')
        # An unrelated ACL or old function edit is never normalized out.
        for sql in (
          'GRANT SELECT(updated_at) ON workflow.task_execution TO sales_worker_saas',
          'GRANT UPDATE(state) ON workflow.task_execution TO sales_worker_saas',
          'GRANT SELECT ON workflow.task_execution TO sales_worker_saas',
          'GRANT EXECUTE ON FUNCTION security.opportunity_create_customer_reason(uuid) TO sales_worker_saas',
          'ALTER FUNCTION security.authorization_refresh() COST 101',
        ):
            tx=conn.transaction();await tx.start()
            try:
                await conn.execute(sql)
                if 'opportunity_create_customer_reason' in sql:
                    try: security.validate_new_acl(await security.new_objects(conn))
                    except Exception as error:
                        from release_bundle_control import Refused
                        assert isinstance(error,Refused)
                    else: raise AssertionError('Private helper grant escaped exact check')
                else: assert await security.snapshot(conn)!=before,sql
            finally: await tx.rollback()
        checks.append('unrelated_column_table_private_helper_and_existing_function_changes_rejected')
        print(json.dumps({'status':'passed','synthetic':True,'production_restore_evidence':False,
          'running_business_data_touched':False,'checks':checks,
          'source_checksum_ledger_sha256':contract.BASE_LEDGER,'target_checksum_ledger_sha256':contract.TARGET_LEDGER,
          'historical_registrations':'fresh_install_only_production_full_ledger_requires_restore',
          'migration_sha256':contract.SQL['V297'][1]},indent=2),flush=True)
    finally: await conn.close()


def run():
    binary=Path(os.environ['PG_BIN'])
    with tempfile.TemporaryDirectory(prefix='sb-claim-release-',dir='/tmp') as temporary:
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
