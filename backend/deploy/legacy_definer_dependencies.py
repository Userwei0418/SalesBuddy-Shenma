"""Exact dependencies of the retained V152 SECURITY DEFINER owners.

Runtime grants do not grant authority to a nested function's owner. Preserve
legacy ownership, and repair only the four reviewed V154/V160 dependencies.
Other missing privileges are diagnostic failures, never general GRANT targets.
"""

from __future__ import annotations

from legacy_adoption_state import AdoptionError, fingerprint
from runtime_roles import SCHEMAS, identifier, verify

CALLERS = (
    "ops.claim_job(text,integer)",
    "ops.claim_job(text,integer,text[],boolean)",
    "ops.enqueue_daily_sales_competency_reviews()",
    "ops.reject_invalid_job_actor(uuid,uuid)",
    "ops.run_priority_reminder_monitor()",
    "security.auth_session_is_active(uuid,uuid,uuid)",
    "security.create_auth_session(uuid,uuid,uuid,text,timestamp with time zone,jsonb)",
    "security.has_active_role(text)",
    "security.has_customer_access(uuid)",
    "security.revoke_auth_session(uuid,uuid,uuid)",
    "security.rotate_auth_session(text,text,timestamp with time zone)",
    "workflow.enqueue_battle_map_leader_notifications(uuid,uuid,uuid,text,text,numeric,numeric,text)",
    "workflow.enqueue_task_notification(uuid,uuid,text,text,text,uuid,text,jsonb)",
)
CALLEES = (
    "security.workspace_is_active(uuid)",
    "security.workspace_job_enabled(uuid,text)",
    "security.authorization_customer(text,uuid)",
    "security.account_team_scope(uuid,uuid,text)",
    "security.fde_can_coordinate_task(uuid)",
)
READ_TABLES = (
    "config.sales_competency_framework",
    "platform.role_binding",
    "platform.team_membership",
    "platform.user_ref",
    "platform.team",
    "platform.workspace",
    "crm.customer",
    "insight.risk",
    "workflow.task",
    "workflow.task_assignee",
    "workflow.task_candidate",
    "workflow.task_event",
    "ops.audit_log",
    "ops.job_effect",
)
WRITE_TABLES = {
    "ops.job": ("SELECT", "INSERT", "UPDATE"),
    "insight.sales_competency_review": ("SELECT", "INSERT", "UPDATE"),
    "workflow.notification": ("SELECT", "INSERT", "UPDATE"),
    "platform.auth_session": ("SELECT", "INSERT", "UPDATE"),
    "agent.run": ("SELECT", "UPDATE"),
    "activity.visit_import": ("SELECT", "UPDATE"),
    "insight.business_advice": ("SELECT", "UPDATE"),
}
REPAIRS = {
    "workspace_active_execute": "GRANT EXECUTE ON FUNCTION security.workspace_is_active(uuid) TO {owner}",
    "control_usage": "GRANT USAGE ON SCHEMA control TO {owner}",
    "schedule_select": "GRANT SELECT ON TABLE control.tenant_job_schedule TO {owner}",
    "schedule_update": "GRANT UPDATE(last_claimed_at) ON TABLE control.tenant_job_schedule TO {owner}",
}


async def functions(conn):
    rows = await conn.fetch(
        """SELECT p.oid::regprocedure::text AS signature,
        pg_get_userbyid(p.proowner) AS owner,p.prosecdef AS security_definer,
        r.rolsuper AS owner_superuser,r.rolbypassrls AS owner_bypass,
        pg_get_functiondef(p.oid) AS definition,p.proacl::text AS acl
        FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
        WHERE p.oid=ANY(ARRAY(SELECT to_regprocedure(x) FROM unnest($1::text[]) x))
        ORDER BY 1""",
        [*CALLERS, *CALLEES],
    )
    result = []
    for row in rows:
        item = dict(row)
        item["definition_sha256"] = fingerprint(item.pop("definition"))
        result.append(item)
    if {row["signature"] for row in result} != set(CALLERS) | set(CALLEES):
        raise AdoptionError("Reviewed legacy definer dependency function is missing")
    if any(not row["security_definer"] for row in result):
        raise AdoptionError("Reviewed dependency is no longer SECURITY DEFINER")
    if any(not row["owner_superuser"] for row in result if row["signature"] in CALLEES):
        raise AdoptionError("Nested dependency owner requires a separate closure review")
    return result


async def build_plan(conn, roles, policy):
    """Read-only plan; no credentials or business rows enter the receipt."""
    await verify(conn, roles, policy)
    if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"):
        raise AdoptionError("Definer repair requires the explicit maintenance administrator")
    items = await functions(conn)
    owners = sorted({r["owner"] for r in items if not r["owner_superuser"]})
    unsupported = await conn.fetch(
        """SELECT p.oid::regprocedure::text AS signature
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_roles r ON r.oid=p.proowner
        WHERE n.nspname=ANY($1::text[]) AND p.prosecdef AND NOT r.rolsuper
          AND p.oid::regprocedure::text<>ALL($2::text[]) ORDER BY 1""",
        SCHEMAS,
        list(CALLERS),
    )
    if unsupported:
        raise AdoptionError("Unreviewed non-superuser definer; review its dependencies separately")
    checks, grants, before_acl = [], [], []
    for owner in owners:
        identifier(owner)
        if owner in roles.values() or not all(r["owner_bypass"] for r in items if r["owner"] == owner):
            raise AdoptionError("Historical definer owner is not the reviewed independent bypass identity")
        if not await conn.fetchval(
            """SELECT count(*)=3 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
            WHERE r.rolname=$1 AND n.nspname=ANY(ARRAY['security','ops','workflow'])""",
            owner,
        ):
            raise AdoptionError("Historical definer owner does not own the reviewed legacy schemas")
        for signature in CALLEES:
            allowed = await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", owner, signature)
            checks.append([owner, signature, "EXECUTE", allowed])
            if not allowed:
                if signature != "security.workspace_is_active(uuid)":
                    raise AdoptionError("Missing preexisting function dependency; not an approved repair target")
                grants.append([owner, "workspace_active_execute"])
        for table, privileges in {**{t: ("SELECT",) for t in READ_TABLES}, **WRITE_TABLES}.items():
            if not await conn.fetchval("SELECT has_schema_privilege($1,$2,'USAGE')", owner, table.split(".")[0]):
                raise AdoptionError("Missing preexisting schema privilege; not an approved repair target")
            for privilege in privileges:
                allowed = await conn.fetchval("SELECT has_table_privilege($1,$2,$3)", owner, table, privilege)
                checks.append([owner, table, privilege, allowed])
                if not allowed:
                    raise AdoptionError("Missing preexisting table dependency; not an approved repair target")
        # claim_job locks workspace rows; SELECT alone does not authorize row locks.
        workspace_lock = await conn.fetchval("SELECT has_any_column_privilege($1,'platform.workspace','UPDATE')", owner)
        checks.append([owner, "platform.workspace", "UPDATE_ANY_COLUMN", workspace_lock])
        if not workspace_lock:
            raise AdoptionError("Missing preexisting workspace row-lock privilege")
        for key, query in (
            ("control_usage", "SELECT has_schema_privilege($1,'control','USAGE')"),
            ("schedule_select", "SELECT has_table_privilege($1,'control.tenant_job_schedule','SELECT')"),
            (
                "schedule_update",
                "SELECT has_column_privilege($1,'control.tenant_job_schedule','last_claimed_at','UPDATE')",
            ),
        ):
            allowed = await conn.fetchval(query, owner)
            checks.append([owner, key, allowed])
            if not allowed:
                grants.append([owner, key])
        # Schema, table and the single writable column are the only non-function targets.
        before_acl.append(
            dict(
                await conn.fetchrow("""SELECT
            (SELECT nspacl::text FROM pg_namespace WHERE nspname='control') AS schema_acl,
            (SELECT relacl::text FROM pg_class WHERE oid='control.tenant_job_schedule'::regclass) AS table_acl,
            (SELECT attacl::text FROM pg_attribute WHERE attrelid='control.tenant_job_schedule'::regclass
              AND attname='last_claimed_at') AS column_acl""")
            )
        )
    identity = dict(
        await conn.fetchrow("""SELECT current_database() AS database,
        (SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,
        (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier""")
    )
    ledger = dict(await conn.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
    return {
        "format": "salesbuddy-legacy-definer-dependencies-v1",
        "database_identity": identity,
        "migration_fingerprint": fingerprint(ledger),
        "runtime_roles": roles,
        "owners": owners,
        "functions": items,
        "checks": checks,
        "before_acl": before_acl,
        "grants": sorted(grants),
    }


async def apply_plan(conn, plan, roles, policy):
    """Apply a reviewed exact plan inside the caller's transaction."""
    if not conn.is_in_transaction():
        raise AdoptionError("Definer grants must share an explicit transaction")
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('salesbuddy-definer-dependencies:'||current_database(),0))"
    )
    if await build_plan(conn, roles, policy) != plan:
        raise AdoptionError("Definer repair plan changed; preserve it and review a fresh plan")
    for owner, repair in plan["grants"]:
        await conn.execute(REPAIRS[repair].format(owner=identifier(owner)))
    after = await build_plan(conn, roles, policy)
    if after["grants"]:
        raise AdoptionError("Legacy definer dependencies remain incomplete")
    for before_fn, after_fn in zip(plan["functions"], after["functions"], strict=True):
        if {k: v for k, v in before_fn.items() if k != "acl"} != {k: v for k, v in after_fn.items() if k != "acl"}:
            raise AdoptionError("Definer owner or definition changed during repair")
    return {
        "status": "passed",
        "plan_sha256": fingerprint(plan),
        "grants": plan["grants"],
        "verified_plan_sha256": fingerprint(after),
        "owners_preserved": True,
        "runtime_exact_acl_verified": True,
        "database_identity": plan["database_identity"],
    }
