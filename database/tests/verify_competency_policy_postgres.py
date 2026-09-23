"""Verify V073 -> V074 policy compatibility and non-bypass authorization.

Only a randomized disposable database and NOLOGIN role are created via the local
maintenance socket. No business DATABASE_URL, credentials or model calls are used.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

import asyncpg

from verify_fde_postgres import context
from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate import migrate

CODE = "agent_execution.competency_review"
DEFAULT = {"schema_version": 1, "strategy": "inherit", "override_budget": False,
           "platform_seconds": 12, "total_seconds": 45}


async def expect_denied(connection, query, *args):
    try:
        async with connection.transaction():
            await connection.fetchval(query, *args)
    except asyncpg.InsufficientPrivilegeError:
        return
    raise AssertionError("Unauthorized role accepted a technical policy mutation")


async def main():
    config = {"host": os.environ.get("PGHOST", "/tmp"), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid.uuid4().hex[:12]
    name, runtime = "salegent_verify_competency_" + suffix, "salegent_competency_role_" + suffix
    connection, role_created = None, False
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        await migrate_through(connection, 73)
        prior = await connection.fetch(
            "SELECT id,rule_code,definition::text,status FROM config.rule_set ORDER BY id"
        )
        assert not await connection.fetchval("SELECT security.company_rule_supported($1)", CODE)
        # Compare V074 with its immediate predecessor. Later migrations add
        # other policies and intentionally revise existing scoring definitions.
        await migrate_through(connection, 74)
        assert prior == await connection.fetch(
            "SELECT id,rule_code,definition::text,status FROM config.rule_set "
            "WHERE rule_code<>$1 ORDER BY id", CODE
        )
        supported_codes = {row["rule_code"] for row in prior if row["rule_code"] in {
            "customer_quadrant", "visit_admission", "home_display", "task_schedule", "fde_capabilities",
            "score.maturity", "score.efficiency", "score.competency"}
            or row["rule_code"].startswith("agent_execution.")}
        for code in supported_codes:
            assert await connection.fetchval("SELECT security.company_rule_supported($1)", code)
        checks.append("forward_upgrade_preserves_all_existing_policy_content_and_supported_codes")
        await migrate(connection)
        assert all(row["status"] == "unchanged" for row in await migrate(connection))
        assert await connection.fetchval("SELECT count(*) FROM config.rule_set WHERE rule_code=$1", CODE) == 1
        checks.append("repeat_migration_does_not_duplicate_policy")
        workspace, team = uuid.uuid4(), uuid.uuid4()
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Policy regression')",
            workspace, str(workspace),
        )
        await connection.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'POLICY','Policy')", team, workspace
        )
        users = {}
        for role in ("administrator", "operations", "sales"):
            person = uuid.uuid4()
            users[role] = person
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
                "VALUES($1,$2,$3,$4,$4)", person, workspace, str(person), role.upper(),
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                "VALUES($1,$2,$3,$4,$5)", workspace, person, role, "self" if role == "sales" else "workspace", team,
            )
            await connection.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
                "VALUES($1,$2,$3,$4)", workspace, person, team, role,
            )
        await admin.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        role_created = True
        for schema in ("common", "security", "config", "ops"):
            await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{runtime}"')
            await connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{runtime}"')
        await connection.execute(f'GRANT SELECT ON config.rule_set,ops.audit_log TO "{runtime}"')
        await connection.execute("SET row_security = on")
        await connection.execute(f'SET ROLE "{runtime}"')
        await context(connection, workspace, users["administrator"], "administrator", team)
        baseline = await connection.fetchval("SELECT security.active_company_rule($1)", CODE)
        assert json.loads(await connection.fetchval("SELECT definition::text FROM config.rule_set WHERE id=$1", baseline)) == DEFAULT
        checks.append("new_workspace_inherits_global_policy_without_workspace_seed")
        definition = {**DEFAULT, "strategy": "direct_only"}
        query = "SELECT security.save_company_rule($1,'Competency policy',$2::jsonb,'Regression',NULL,NULL,$3)"
        for role in ("operations", "sales"):
            await context(connection, workspace, users[role], role, team)
            await expect_denied(connection, query, CODE, json.dumps(definition), baseline)
        checks.append("operations_and_sales_cannot_draft_technical_execution_policy")
        await context(connection, workspace, users["administrator"], "administrator", team)
        draft = await connection.fetchval(query, CODE, json.dumps(definition), baseline)
        assert await connection.fetchval("SELECT security.active_company_rule($1)", CODE) == baseline
        for role in ("operations", "sales"):
            await context(connection, workspace, users[role], role, team)
            await expect_denied(connection, "SELECT security.publish_company_rule($1,1)", draft)
        await context(connection, workspace, users["administrator"], "administrator", team)
        assert await connection.fetchval("SELECT security.publish_company_rule($1,1)", draft) == draft
        assert await connection.fetchval("SELECT security.active_company_rule($1)", CODE) == draft
        checks.append("administrator_publish_uses_existing_draft_and_effective_version_lifecycle")
        restored = await connection.fetchval(
            "SELECT security.save_company_rule($1,'Competency restored',$2::jsonb,'Restore',NULL,NULL,$3,$4)",
            CODE, json.dumps(DEFAULT), draft, baseline,
        )
        await connection.fetchval("SELECT security.publish_company_rule($1,1)", restored)
        assert await connection.fetchval("SELECT status FROM config.rule_set WHERE id=$1", draft) == "retired"
        assert await connection.fetchval("SELECT restored_from_id FROM config.rule_set WHERE id=$1", restored) == baseline
        assert await connection.fetchval(
            "SELECT count(*) FROM ops.audit_log WHERE object_type='company_rule' "
            "AND object_id=ANY($1::uuid[]) AND action_code='company_rule.publish'", [draft, restored]
        ) == 2
        checks.append("restore_keeps_prior_policy_and_both_publish_audit_events")
        await connection.execute("RESET ROLE")
        assert all(row["status"] == "unchanged" for row in await migrate(connection))
        await connection.execute("SET row_security = on")
        await connection.execute(f'SET ROLE "{runtime}"')
        assert await connection.fetchval("SELECT security.active_company_rule($1)", CODE) == restored
        checks.append("redeployment_preserves_published_workspace_override")
        print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))
    finally:
        if connection:
            await connection.close()
        assert name.startswith("salegent_verify_competency_")
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if role_created:
            await admin.execute(f'DROP ROLE "{runtime}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
