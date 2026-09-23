"""Create a fresh schema, disposable organization and non-bypass role for the entire SQL suite."""

import asyncio
import os
from pathlib import Path
import sys
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "database/scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))
from sales_backend.auth.passwords import encode_password
from migrate import migrate


async def main(serve=None):
    config = {"host": os.environ.get("PGHOST", "/tmp"), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid.uuid4().hex[:12]
    name = "salegent_verify_integration_" + suffix
    role = "salegent_verify_role_" + suffix
    conn = None
    created_role = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(database=name, **config)
        await migrate(conn)
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role = True
        for schema in [
            "platform",
            "crm",
            "activity",
            "workflow",
            "insight",
            "ops",
            "config",
            "common",
            "security",
            "agent",
        ]:
            await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        await conn.execute(f'REVOKE INSERT,UPDATE,DELETE ON crm.customer_sales_member FROM "{role}"')
        await conn.execute(f'REVOKE ALL ON platform.password_credential,security.login_throttle FROM "{role}"')
        workspace = uuid.uuid4()
        team = uuid.uuid4()
        await conn.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,'demo-sales-workspace','隔离验收')",
            workspace,
        )
        await conn.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'south','南区')", team, workspace
        )
        for code, r, scope in [
            ("XS001", "sales", "self"),
            ("ZJ001", "supervisor", "team"),
            ("ZJL001", "manager", "workspace"),
            ("XS002", "sales", "self"),
            ("OPS001", "operations", "workspace"),
            ("ADMIN001", "administrator", "workspace"),
        ]:
            user = uuid.uuid4()
            await conn.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)",
                user,
                workspace,
                code,
            )
            await conn.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)",
                workspace,
                user,
                r,
                scope,
                team,
            )
            await conn.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)",
                workspace,
                user,
                team,
                r,
            )
            if r in ("operations", "administrator"):
                await conn.execute(
                    "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,$3,false)",
                    user,
                    workspace,
                    encode_password("Isolated-Testing-2026"),
                )
            if code == "XS001":
                await conn.execute(
                    "INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,created_by_user_ref_id,level_code) VALUES($1,'隔离测试科技','隔离测试科技',$2,$3,$2,'Tier-2')",
                    workspace,
                    user,
                    team,
                )
        # A historical baseline may have a different owner from new credential
        # tables. CREATE OR REPLACE keeps that owner: reproduce the real upgrade
        # failure, replay the corrective migration and keep credentials private.
        await conn.execute(f'ALTER FUNCTION security.resolve_demo_actor(text,text) OWNER TO "{role}"')
        try:
            await conn.fetch("SELECT * FROM security.resolve_demo_actor('demo-sales-workspace','XS001')")
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("legacy resolver owner unexpectedly accessed private credentials")
        await conn.execute((ROOT / "database/migrations/V054__legacy_identity_function_owner.sql").read_text())
        # Replaying the legacy owner repair removes the temporary owner's
        # implicit EXECUTE privilege. V105 no longer grants it via PUBLIC;
        # restore only this disposable application role's explicit grant.
        await conn.execute(f'GRANT EXECUTE ON FUNCTION security.resolve_demo_actor(text,text) TO "{role}"')
        assert await conn.fetchval(
            "SELECT count(*) FROM security.resolve_demo_actor('demo-sales-workspace','XS001')"
        ) == 1
        assert not await conn.fetchval("SELECT has_table_privilege($1,'platform.password_credential','SELECT')", role)
        assert all(item["status"] == "unchanged" for item in await migrate(conn))
        print("Legacy resolver owner repair verified; application credential access remains denied", flush=True)
        if serve is not None:
            await serve(config, name, role)
            return
        env = {
            **os.environ,
            "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
            "SALES_TEST_ROLE": role,
            "SALES_TEST_WORKSPACE": "demo-sales-workspace",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "backend/src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "backend/tests/integration"),
            "-q",
            "-p",
            "no:cacheprovider",
            env=env,
            cwd=str(ROOT / "backend"),
        )
        code = await process.wait()
        if code:
            raise RuntimeError(f"Integration suite failed: exit {code}")
        model_api = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / 'backend/tests/system/verify_model_api_postgres.py'),
            env=env, cwd=str(ROOT),
        )
        if await model_api.wait():
            raise RuntimeError('Model API configuration verification failed')
        concurrent = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / 'backend/tests/system/verify_position_claims_postgres.py'),
            env=env, cwd=str(ROOT / 'backend'),
        )
        if await concurrent.wait():
            raise RuntimeError('Concurrent position claim verification failed')
        risk_contract = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / 'database/tests/verify_customer_risk_assessment_postgres.py'),
            env=env, cwd=str(ROOT),
        )
        if await risk_contract.wait():
            raise RuntimeError('Customer risk receipt/queue/RLS verification failed')
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if created_role:
            await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
