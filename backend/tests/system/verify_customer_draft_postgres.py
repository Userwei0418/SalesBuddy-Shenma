"""Disposable Unix-socket-only PG16; synthetic data, no existing database access."""
# ruff: noqa: E402 -- standalone runner loads repository modules explicitly.

import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
for source in ("backend/src", "backend/deploy", "tools/installation"):
    sys.path.insert(0, str(ROOT / source))

import release122_knowledge_silent_publication_contract as contract
import verify_legacy_adoption_postgres as cluster
from run_integration_postgres import migrate
from runtime_roles import SERVICES, fingerprint, identifier
from upgrade_permissions import reconcile

from sales_backend.auth.passwords import encode_password


async def verify(config, name, roles):
    assert name.startswith("salegent_verify_integration_")
    env = {
        **os.environ,
        "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
        "SALES_TEST_ROLE": roles["api"],
        "SALES_TEST_WORKER_ROLE": roles["worker"],
        "SALES_TEST_WORKSPACE": "demo-sales-workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    paths = [
        "tests/integration/test_customer_draft_permissions.py",
        "tests/integration/test_authorization_customers.py::test_fde_customer_creation_and_scoped_review_do_not_imply_release",
    ]
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", *paths, "-q", "-rs", "-p", "no:cacheprovider", cwd=ROOT / "backend", env=env
    )
    if await process.wait():
        raise RuntimeError("Customer draft restricted PostgreSQL verification failed")


async def exercise(root, host, pg_bin):
    config = {"host": host, "user": getpass.getuser()}
    suffix = uuid4().hex[:12]
    name = "salegent_verify_integration_" + suffix
    roles = {service: "salegent_verify_role_" + uuid4().hex[:12] for service in SERVICES}
    created_roles = []
    admin = await asyncpg.connect(database="postgres", **config)
    connection = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        await migrate(connection)
        frozen = json.loads((ROOT / "backend/tests/fixtures/release122_knowledge_contract.frozen.json").read_text())
        policy = frozen["normal_policy"]
        policy.update(schema="V278", migration_fingerprint=contract.TARGET_LEDGER)
        assert fingerprint(policy) == contract.POLICY_SHA
        assert fingerprint(policy["services"]) == contract.SERVICES_SHA
        async with connection.transaction():
            for service, role in roles.items():
                await connection.execute(
                    f"CREATE ROLE {identifier(role)} LOGIN NOSUPERUSER NOBYPASSRLS "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                created_roles.append(role)
                for group in policy["services"][service]["memberships"]:
                    await connection.execute(
                        f"GRANT {identifier(group)} TO {identifier(role)} WITH INHERIT FALSE, SET FALSE"
                    )
            await connection.execute(f"REVOKE CREATE ON DATABASE {identifier(name)} FROM PUBLIC")
            await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            # Official verifier checks exact ACL, RLS, ledger and non-inheriting
            # role memberships. Never substitute a broad-DML fixture role.
            await reconcile(connection, roles, policy)
        for role in roles.values():
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1,'platform.password_credential','SELECT')", role
            )
            for signature in (
                "security.authorization_grants_for(uuid,uuid)",
                "security.authorization_user_allows(text,uuid,uuid,uuid,uuid[],boolean)",
                "security.reconcile_runtime_grants()",
            ):
                assert not await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        workspace, team = uuid4(), uuid4()
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) "
            "VALUES($1,'demo-sales-workspace','隔离建档验收')",
            workspace,
        )
        await connection.execute(
            "UPDATE control.tenant_module SET enabled=true,source='isolated-test-fixture' WHERE workspace_id=$1",
            workspace,
        )
        await connection.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'south','南区')", team, workspace
        )
        for code, business_role, scope in (
            ("XS001", "sales", "self"),
            ("XS002", "sales", "self"),
            ("ZJ001", "supervisor", "team"),
            ("ZJL001", "manager", "workspace"),
            ("OPS001", "operations", "workspace"),
            ("ADMIN001", "administrator", "workspace"),
        ):
            user = uuid4()
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
                "VALUES($1,$2,$3,$3,$3)",
                user,
                workspace,
                code,
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                "VALUES($1,$2,$3,$4,$5)",
                workspace,
                user,
                business_role,
                scope,
                team,
            )
            await connection.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
                "VALUES($1,$2,$3,$4)",
                workspace,
                user,
                team,
                business_role,
            )
            if business_role in ("operations", "administrator"):
                await connection.execute(
                    "INSERT INTO platform.password_credential"
                    "(user_ref_id,workspace_id,password_hash,must_change_password) "
                    "VALUES($1,$2,$3,false)",
                    user,
                    workspace,
                    encode_password("Isolated-Testing-2026"),
                )
        print("Fresh V278 fixture; exact managed service ACL/RLS verified; private access denied", flush=True)
        await verify(config, name, roles)
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for role in created_roles:
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    cluster.exercise = exercise
    cluster.main()
