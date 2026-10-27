"""Optional one-shot catalog/ACL check in a disposable Unix-only PG16 database.

PG_BIN=/path/to/pg16/bin python backend/tests/test_release126_task_postgres.py
No application DSN, credentials, production connection, model or worker calls.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'backend/deploy'), str(ROOT/'database/scripts'), str(ROOT/'tools/installation')]
import migrate
import release126_task_security as security
from runtime_roles import load_policy, fingerprint, SCHEMAS
from upgrade_permissions import reconcile_service_roles


async def verify(socket):
    conn = await asyncpg.connect(host=str(socket), port=56486, user='postgres', database='postgres')
    await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
    for name in ('salegent_feishu_worker','salegent_api','salegent_worker','salegent_platform'):
        await conn.execute(f'CREATE ROLE {name} NOLOGIN')
    discover = migrate.discover
    migrate.discover = lambda root: [m for m in discover(root) if int(m.key[1:]) <= 284]
    try:
        await migrate.migrate(conn, ROOT/'database', adopt_existing=True)
    finally:
        migrate.discover = discover
    await conn.execute('SET row_security=on')
    async with conn.transaction():
        policy = load_policy()
        policy['schema'] = 'V284'
        policy['migration_fingerprint'] = fingerprint(dict(await conn.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')))
        # Use the exact previous policy matrix even when invoked after integration.
        for svc in ('api','worker'):
            policy['services'][svc]['functions'] = [sig for sig in policy['services'][svc]['functions']
                if sig not in security.FUNCTION_GRANTS]
        for role in security.ROLES.values():
            await conn.execute(f'CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS')
        for svc, role in security.ROLES.items():
            for member in policy['services'][svc]['memberships']:
                await conn.execute(f'GRANT {member} TO {role} WITH INHERIT FALSE, SET FALSE')
        tables = [dict(r) for r in await conn.fetch("SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1", SCHEMAS)]
        policies = [dict(r) for r in await conn.fetch('SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3', SCHEMAS)]
        for row in policies:
            row['roles'] = sorted(role for role in row['roles'] if role not in security.ROLES.values())
        policy['rls_fingerprint'] = fingerprint({'tables':tables,'policies':policies})
        await reconcile_service_roles(conn, security.ROLES, policy)
    # Production's historical task table is owned by sales_owner. New indexes
    # inherit the table owner even when postgres runs CREATE INDEX.
    await conn.execute('CREATE ROLE sales_owner NOLOGIN')
    await conn.execute('ALTER TABLE workflow.task OWNER TO sales_owner')
    ws, user, task = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'synthetic release')", ws, str(ws))
    await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,'synthetic','synthetic','synthetic')", user, ws)
    await conn.execute("INSERT INTO workflow.task(id,workspace_id,creator_user_ref_id,title,description,due_at,association_kind,source_code) VALUES($1,$2,$3,'synthetic legacy','preserve',clock_timestamp()+interval '1 day','daily','mini_program')", task, ws, user)
    async with conn.transaction(readonly=True):
        before, data = await security.snapshot(conn), await security.business_state(conn)
    identity = dict(await conn.fetchrow("SELECT current_database() AS database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid"))
    # Fresh baseline schema omits the historical pre-V035 registration rows.
    # Add only synthetic fixture history to match the already-frozen production
    # prefix. The release helper itself never adopts or changes old ledgers.
    await conn.executemany("INSERT INTO ops.schema_migration(version,description) VALUES($1,'synthetic historical fixture')",
        [(f'V{i:03}',) for i in range(1,35) if i != 14])
    # Synthetic fixture proof only: this receipt is never used for production.
    result = await security.rehearse(conn, expected_database=identity, source_backup_sha256='0'*64)
    assert result['new_objects']['task_index'][0]['owner'] == 'sales_owner'
    assert result['new_objects']['task_index'][0]['table_owner'] == 'sales_owner'
    security.validate_restore_evidence(result['evidence'], result['new_objects'], digest=security.digest, require=security.require)
    async with conn.transaction(readonly=True):
        assert await security.snapshot(conn) == before
        assert await security.business_state(conn) == data
        await security.verify_effective_privileges(conn)
    print('PASS: V284→V286 nine old bodies, preserved catalog/business state, exact two grants, no private helper/table access')
    print('new_objects_sha256 (synthetic only):', security.digest(await security.new_objects(conn)))
    await conn.close()


if __name__ == '__main__':
    binaries = Path(os.environ['PG_BIN'])
    with tempfile.TemporaryDirectory(prefix='sb-release126-') as temporary:
        root = Path(temporary)
        subprocess.run([str(binaries/'initdb'),'-D',str(root/'pg'),'-U','postgres','--auth=trust','--encoding=UTF8','--no-locale'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(binaries/'pg_ctl'),'-D',str(root/'pg'),'-l',str(root/'postgres.log'),'-o',f"-k {root} -p 56486 -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
        try:
            asyncio.run(verify(root))
        finally:
            subprocess.run([str(binaries/'pg_ctl'),'-D',str(root/'pg'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
