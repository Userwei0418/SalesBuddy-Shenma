"""Exact V278 API INSERT repair; plan first, apply only the reviewed plan SHA.

Uses PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSFILE maintenance connection settings.
Never reconciles the V275 installation policy or changes migrations/credentials.
Run while the normal deployment lock and service maintenance window are held.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/installation"))
from legacy_adoption_state import atomic_json, fingerprint, locked, private_directory, read_private  # noqa: E402
from runtime_roles import COLUMN_PRIVILEGES, TABLE_PRIVILEGES, identifier, load_policy  # noqa: E402

TABLE = "activity.visit_opportunity"
OLD_POLICY = "1cf03197d32eeeb53fc1ae3b20a92f9382e2b46cea4a3257fbde3d858286d2e3"
NEW_POLICY = "47a8f60e1654df2552ff9da80318b55cf91e6565ff70e62edfdbf907760a105c"
LEDGER = "c5e8c1d2a69be556b4efece07ffc352588e35473af32e81272f0af4e534b237e"
RLS = "8f9175dc9c6f0e6e4c6e9cef05f0a068c674a0d7c0b1a4de73348e53d35f189e"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_source_policy():
    policy = load_policy()
    require(fingerprint(policy) == NEW_POLICY, "Source policy differs from reviewed INSERT-only repair")
    before = json.loads(json.dumps(policy))
    before["services"]["api"]["tables"][TABLE] = ["SELECT"]
    require(fingerprint(before) == OLD_POLICY, "Source policy changed beyond the single API INSERT")


async def inspect(conn, role):
    identifier(role)
    verify_source_policy()
    identity = dict(
        await conn.fetchrow("""SELECT current_database() AS database,
        (SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,
        (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier""")
    )
    ledger = dict(await conn.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
    schema = await conn.fetchval(
        "SELECT version FROM ops.schema_migration ORDER BY substring(version from 2)::integer DESC LIMIT 1"
    )
    require(schema == "V278" and fingerprint(ledger) == LEDGER, "Exact V278 migration ledger required")
    flags = await conn.fetchrow(
        """SELECT oid,rolcanlogin,rolsuper,rolinherit,rolcreatedb,rolcreaterole,
        rolreplication,rolbypassrls FROM pg_roles WHERE rolname=$1""",
        role,
    )
    require(
        flags is not None
        and flags["rolcanlogin"]
        and not any(flags[k] for k in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")),
        "API role missing or elevated",
    )
    require(
        not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=$1)", flags["oid"]),
        "API role must not inherit another role",
    )
    table = dict(
        await conn.fetchrow("""SELECT relowner,relrowsecurity,relforcerowsecurity,relkind::text
        FROM pg_class WHERE oid='activity.visit_opportunity'::regclass""")
    )
    require(
        table["relowner"] != flags["oid"]
        and table["relkind"] == "r"
        and table["relrowsecurity"]
        and table["relforcerowsecurity"],
        "Target must remain a non-owned FORCE RLS table",
    )
    policies = [
        dict(r)
        for r in await conn.fetch("""SELECT policyname,permissive,roles::text[],cmd,qual,with_check
        FROM pg_policies WHERE schemaname='activity' AND tablename='visit_opportunity' ORDER BY policyname""")
    ]
    require(fingerprint(policies) == RLS, "Target RLS definitions differ from reviewed V278 policies")
    effective = [
        p for p in TABLE_PRIVILEGES if await conn.fetchval("SELECT has_table_privilege($1,$2,$3)", role, TABLE, p)
    ]
    require(set(effective) in ({"SELECT"}, {"SELECT", "INSERT"}), "Unexpected effective API table ACL")
    cols = [
        dict(r)
        for r in await conn.fetch("""SELECT attname,attacl::text FROM pg_attribute
        WHERE attrelid='activity.visit_opportunity'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum""")
    ]
    for col in cols:
        for p in COLUMN_PRIVILEGES:
            allowed = await conn.fetchval("SELECT has_column_privilege($1,$2,$3,$4)", role, TABLE, col["attname"], p)
            require(allowed == (p in effective), "Unexpected effective API column ACL")
    acl = [
        dict(r)
        for r in await conn.fetch("""SELECT a.grantor::int,a.grantee::int,a.privilege_type,a.is_grantable
        FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE c.oid='activity.visit_opportunity'::regclass ORDER BY 1,2,3,4""")
    ]
    own = [a for a in acl if a["grantee"] == flags["oid"]]
    require(
        {a["privilege_type"] for a in own} == set(effective) and not any(a["is_grantable"] for a in own),
        "Expected direct non-grantable API ACL only",
    )
    # The complete security catalog fingerprint detects collateral ACL, role,
    # ownership, membership or RLS changes; only this table ACL is excluded.
    security = {}
    for key, sql in {
        "relations": "SELECT oid,relowner,relacl::text,relrowsecurity,relforcerowsecurity "
        "FROM pg_class WHERE oid<>'activity.visit_opportunity'::regclass ORDER BY oid",
        "columns": "SELECT attrelid,attnum,attacl::text FROM pg_attribute WHERE attacl IS NOT NULL ORDER BY 1,2",
        "functions": "SELECT oid,proowner,proacl::text FROM pg_proc ORDER BY oid",
        "schemas": "SELECT oid,nspowner,nspacl::text FROM pg_namespace ORDER BY oid",
        "roles": "SELECT oid,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolbypassrls,rolconfig "
        "FROM pg_roles ORDER BY oid",
        "memberships": "SELECT roleid,member,grantor,admin_option,inherit_option,set_option "
        "FROM pg_auth_members ORDER BY 1,2,3",
        "defaults": "SELECT oid,defaclrole,defaclnamespace,defaclobjtype,defaclacl::text "
        "FROM pg_default_acl ORDER BY oid",
        "policies": "SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check "
        "FROM pg_policies ORDER BY 1,2,3",
    }.items():
        security[key] = [dict(r) for r in await conn.fetch(sql)]
    return dict(
        format="salesbuddy-visit-opportunity-insert-v1",
        database_identity=identity,
        role=role,
        role_oid=flags["oid"],
        table=table,
        rls=RLS,
        ledger=LEDGER,
        source_policy=NEW_POLICY,
        columns=cols,
        acl=acl,
        effective=effective,
        unrelated_security=fingerprint(security),
    )


async def apply_plan(conn, plan):
    require(conn.is_in_transaction(), "Explicit transaction required")
    await conn.execute("SELECT pg_advisory_xact_lock($1)", 202609102101)
    await conn.execute("SET LOCAL lock_timeout='5s'")
    await conn.execute("LOCK TABLE activity.visit_opportunity IN ACCESS EXCLUSIVE MODE")
    require(await inspect(conn, plan["role"]) == plan, "Plan drift; inspect again before applying")
    require(plan["effective"] == ["SELECT"], "Plan must begin with SELECT only; do not blindly retry")
    await conn.execute(f"GRANT INSERT ON TABLE activity.visit_opportunity TO {identifier(plan['role'])}")
    after = await inspect(conn, plan["role"])
    require(
        {k: v for k, v in plan.items() if k not in ("acl", "effective")}
        == {k: v for k, v in after.items() if k not in ("acl", "effective")},
        "Collateral security change refused",
    )
    added = [a for a in after["acl"] if a not in plan["acl"]]
    require(
        all(a in after["acl"] for a in plan["acl"])
        and len(added) == 1
        and added[0]["grantee"] == plan["role_oid"]
        and added[0]["privilege_type"] == "INSERT"
        and not added[0]["is_grantable"]
        and set(after["effective"]) == {"SELECT", "INSERT"},
        "ACL delta is not one INSERT",
    )
    return dict(
        status="passed",
        plan_sha256=fingerprint(plan),
        after_sha256=fingerprint(after),
        added=added,
        schema="V278",
        source_policy_sha256=NEW_POLICY,
        database_identity=plan["database_identity"],
    )


async def execute(args):
    import asyncpg

    directory = private_directory(args.directory)
    with locked(directory):
        conn = await asyncpg.connect(command_timeout=30)
        try:
            if args.action == "plan":
                async with conn.transaction(readonly=True):
                    plan = await inspect(conn, args.role)
                require(
                    plan["effective"] == ["SELECT"], "Already applied or unexpected state; inspect existing receipt"
                )
                atomic_json(directory / "PLAN.json", plan, replace=False)
                print(
                    json.dumps({"plan_sha256": fingerprint(plan), "grant": "INSERT", "table": TABLE, "role": args.role})
                )
            elif args.action == "inspect":
                async with conn.transaction(readonly=True):
                    result = await inspect(conn, args.role)
                print(
                    json.dumps(
                        {"effective": result["effective"], "state_sha256": fingerprint(result), "schema": "V278"}
                    )
                )
            else:
                plan = read_private(directory / "PLAN.json")
                require(
                    plan["role"] == args.role and args.plan_sha256 == fingerprint(plan), "Reviewed plan SHA required"
                )
                atomic_json(
                    directory / "INTENT.json",
                    {"plan_sha256": fingerprint(plan), "outcome": "unknown_until_receipt"},
                    replace=False,
                )
                async with conn.transaction():
                    result = await apply_plan(conn, plan)
                atomic_json(directory / "RESULT.json", result, replace=False)
                print(json.dumps(result))
        finally:
            await conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("plan", "apply", "inspect"))
    p.add_argument("--role", required=True)
    p.add_argument("--directory", required=True)
    p.add_argument("--plan-sha256")
    try:
        asyncio.run(execute(p.parse_args()))
    except Exception as exc:
        # No exception detail: connection messages can contain credentials.
        print(f"Repair refused ({type(exc).__name__}); preserve plan/intent and inspect outcome", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
