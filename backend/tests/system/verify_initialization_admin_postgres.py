"""Reproduce the initialization ACL failure with the unchanged V233 API policy.

Runs only in the existing private, disposable Unix-socket PostgreSQL harness.
Synthetic fixtures, authenticated non-bypass API role, no running app or secrets.
"""
from __future__ import annotations

import getpass
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
from fastapi import HTTPException

import verify_legacy_adoption_postgres as harness
from runtime_roles import SERVICES, load_policy, provision_new, verify
from verify_legacy_definer_dependencies_postgres import settings

from sales_backend.api.opportunity_initialization import options
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.repositories.opportunity_initialization import authorize

ROOT = Path(__file__).resolve().parents[3]


async def exercise(root, host, pg_bin):
    config = {"host": host, "user": getpass.getuser(), "port": 5432}
    owner = await asyncpg.connect(database="postgres", **config)
    database = None
    checks = []
    try:
        await owner.execute("CREATE DATABASE initialization_fixture TEMPLATE template0")
        await owner.close()
        owner = await asyncpg.connect(database="initialization_fixture", **config)
        await harness.fixture.migrate.migrate(owner, ROOT / "database")
        roles = {s: "adoption_" + s for s in SERVICES}
        policy = load_policy(ROOT / "tools/installation/runtime_permissions.json")
        passwords = await provision_new(owner, database="initialization_fixture", roles=roles, policy=policy)
        assert await owner.fetchval("SELECT count(*) FROM ops.migration_checksum") == 197
        assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", roles["api"],
                                        "security.company_administrator_identity(uuid,uuid)")
        assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", roles["api"],
                                    "security.has_active_role(text)")
        checks.append("unchanged_197_policy_private_denied_public_predicate_allowed")
        workspaces = [uuid4(), uuid4()]
        for index, ws in enumerate(workspaces):
            await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$3)",
                                ws, f"initialization-fixture-{index}", f"Synthetic {index}")
            await owner.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)

        async def person(code, assigned, *, workspace=workspaces[0], display_role=RoleCode.ADMINISTRATOR):
            uid = uuid4()
            await owner.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
                                "VALUES($1,$2,$3,$3,$3)", uid, workspace, code)
            for role in assigned:
                await owner.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
                                    "VALUES($1,$2,$3,'workspace')", workspace, uid, role)
            return ActorContext(workspace_id=str(workspace), user_id=str(uid), role=display_role,
                                data_scope=DataScope.WORKSPACE)

        admin = await person("ADMIN", ["administrator"])
        operator = await person("OPS", ["operations"])
        dual = await person("DUAL", ["operations", "administrator"], display_role=RoleCode.OPERATIONS)
        foreign = await person("FOREIGN", ["administrator"], workspace=workspaces[1])
        database = Database(settings(f"postgresql://{roles['api']}:{passwords['api']}@/initialization_fixture?host={host}&port=5432"))
        await database.connect()
        async with database.transaction(admin, readonly=True) as conn:
            assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert await conn.fetchval("SELECT current_user=session_user")
            try:
                async with conn.transaction():
                    await conn.fetchval("SELECT security.company_administrator_identity(common.current_workspace_id(),common.current_user_ref_id())")
            except asyncpg.InsufficientPrivilegeError:
                checks.append("old_call_reproduces_insufficient_privilege_with_authenticated_api")
            else:
                raise AssertionError("Private call unexpectedly allowed")

        async def allowed(actor, name):
            result = await options(identity=SimpleNamespace(actor=actor), database=database)
            assert result["fields"] and result["participant_roles"]
            checks.append(name)

        async def denied(actor, name):
            try:
                await options(identity=SimpleNamespace(actor=actor), database=database)
            except HTTPException as exc:
                assert exc.status_code == 403
                checks.append(name)
            else:
                raise AssertionError("Expected forbidden initialization options")

        await allowed(admin, "active_company_admin_options_readonly_success")
        await denied(operator, "display_admin_without_real_admin_binding_denied")
        await allowed(dual, "real_admin_binding_works_with_operations_display_role")
        await allowed(foreign, "second_tenant_own_admin_options_success")
        async with database.transaction(operator, readonly=True) as conn:
            assert not await conn.fetchval("SELECT security.has_active_role('administrator')")
            checks.append("other_tenant_admin_does_not_authorize_current_operator")
        async with database.transaction(admin, readonly=True) as conn:
            try:
                await authorize(conn, [uuid4()])
            except PermissionError:
                checks.append("object_read_update_authorization_retained")
            else:
                raise AssertionError("Missing object scope unexpectedly allowed")
        await owner.execute("UPDATE platform.role_binding SET valid_to=clock_timestamp()-interval '1 second' "
                            "WHERE user_ref_id=$1::uuid AND role_code='administrator'", dual.user_id)
        await denied(dual, "expired_admin_with_live_operations_binding_denied")
        await owner.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", admin.user_id)
        await denied(admin, "inactive_user_denied_by_fresh_console_grants")
        await owner.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", workspaces[1])
        await denied(foreign, "suspended_tenant_denied")
        await verify(owner, roles, policy)
        checks.append("runtime_acl_and_rls_policy_still_exact")
        print(json.dumps({"status": "passed", "checks": checks, "count": len(checks),
                          "actual_authenticated_api_role": True, "schema_checksum_count": 197,
                          "policy_changed": False, "production_or_local_access": False,
                          "http_login_test": False, "options_route_invoked_with_real_database": True}, indent=2))
    finally:
        if database is not None:
            await database.close()
        await owner.close()


if __name__ == "__main__":
    harness.exercise = exercise
    harness.main()
