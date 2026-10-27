"""Read-only live ACL/RLS/role/function verification for the V279 index change.

No GRANT, REVOKE, role creation, policy application or business rows. Index
relations are excluded from the security fingerprint because only the reviewed
index replacement may change; their exact shape is checked separately.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from release122_visit_index_contract import GATE_FILES, INSTALLATION_POLICY_SHA, SECURITY_SECTIONS, validate_acl
from release_bundle_control import Refused, digest

ROLES = {
    "api": "sales_api_saas",
    "worker": "sales_worker_saas",
    "feishu": "sales_feishu_saas",
    "platform": "sales_platform_saas",
}
QUERIES = {
    "relations": """SELECT oid,relkind::text,relowner,relacl::text,relrowsecurity,relforcerowsecurity
        FROM pg_class WHERE relkind NOT IN ('i','I') ORDER BY oid""",
    "columns": """SELECT attrelid,attnum,attacl::text FROM pg_attribute
        WHERE attacl IS NOT NULL ORDER BY attrelid,attnum""",
    "functions": """SELECT p.oid,p.proowner,p.proacl::text,p.prosecdef,p.proleakproof,p.prokind::text,
        p.proargtypes::text,p.prorettype,
        md5(COALESCE(p.proconfig::text,'')) AS settings_digest,
        md5(CASE WHEN p.prokind IN ('f','p') THEN pg_get_functiondef(p.oid) ELSE p.prosrc END) AS definition_digest
        FROM pg_proc p
        ORDER BY p.oid""",
    "schemas": "SELECT oid,nspowner,nspacl::text FROM pg_namespace ORDER BY oid",
    "roles": """SELECT oid,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,
        rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil::text,
        md5(COALESCE(rolconfig::text,'')) AS settings_digest FROM pg_roles ORDER BY oid""",
    "memberships": """SELECT roleid,member,grantor,admin_option,inherit_option,set_option
        FROM pg_auth_members ORDER BY roleid,member,grantor""",
    "defaults": """SELECT oid,defaclrole,defaclnamespace,defaclobjtype::text,defaclacl::text
        FROM pg_default_acl ORDER BY oid""",
    "policies": """SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,
        md5(COALESCE(qual,'')) AS using_digest,md5(COALESCE(with_check,'')) AS check_digest
        FROM pg_policies ORDER BY schemaname,tablename,policyname""",
    "database": """SELECT oid,datname,datdba,datacl::text FROM pg_database
        WHERE datname=current_database()""",
    "database_role_settings": """SELECT setdatabase,setrole,md5(setconfig::text) AS settings_digest
        FROM pg_db_role_setting ORDER BY setdatabase,setrole""",
}
INDEX_SQL = """SELECT c.relname AS name,pg_get_indexdef(i.indexrelid) AS definition,
    i.indisvalid AS valid,i.indisready AS ready,i.indisunique AS unique
    FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
    WHERE i.indrelid='activity.visit_field_value'::regclass ORDER BY c.relname"""
PRIMARY_INDEX = (
    "CREATE UNIQUE INDEX visit_field_value_pkey ON activity.visit_field_value "
    "USING btree (visit_id, field_definition_id)"
)
OLD_INDEX = (
    "CREATE INDEX idx_visit_field_value_text ON activity.visit_field_value "
    "USING btree (workspace_id, field_definition_id, value_text)"
)
NEW_INDEX = (
    "CREATE INDEX idx_visit_field_value_workspace_field ON activity.visit_field_value "
    "USING btree (workspace_id, field_definition_id)"
)


def require(condition, message):
    if not condition:
        raise Refused(message)


def read_ref(ref):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "Exact security reference required")
    path = Path(ref["path"])
    require(
        path.is_absolute()
        and path.is_file()
        and not any(p.is_symlink() for p in (path, *path.parents))
        and stat.S_IMODE(path.stat().st_mode) == 0o600,
        "Private regular security reference required",
    )
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == ref["sha256"], "Security reference changed")
    return json.loads(data)


def load_plan(plan_ref):
    plan = read_ref(plan_ref)
    require(
        plan.get("format") == "salesbuddy-index-security-plan-v1"
        and plan.get("roles") == ROLES
        and plan.get("target") == {"database": "sales_saas", "oid": 18684},
        "Exact production index security plan required",
    )
    snapshot_value = read_ref(plan["policy_ref"])
    validate_acl(plan, snapshot_value, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]
    expected = {str(root / "backend/deploy" / name) for name in GATE_FILES}
    expected.add(str(root / "database/scripts/migrate.py"))
    metadata = root / "tools/installation/runtime_permissions.json"
    expected.add(str(metadata))
    require(set(plan.get("source_files", {})) == expected, "Complete index security source closure required")
    for name, checksum in plan["source_files"].items():
        source = Path(name)
        require(
            source.is_file()
            and not source.is_symlink()
            and hashlib.sha256(source.read_bytes()).hexdigest() == checksum,
            "Index security source changed",
        )
    require(
        digest(json.loads(metadata.read_bytes())) == INSTALLATION_POLICY_SHA,
        "V279 installation schema, ledger, service matrix or RLS metadata differs",
    )
    return plan, snapshot_value


async def snapshot(conn):
    """Return catalog fingerprints only, never function bodies or role settings."""
    rows = {name: [dict(row) for row in await conn.fetch(sql)] for name, sql in QUERIES.items()}
    roles = [row for row in rows["roles"] if row["rolname"] in ROLES.values()]
    safe = len(roles) == 4 and all(
        row["rolcanlogin"]
        and not any(row[k] for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls"))
        for row in roles
    )
    require(safe, "Existing runtime roles are absent or privileged")
    require(set(rows) == SECURITY_SECTIONS, "Incomplete security catalog")
    return {
        "format": "salesbuddy-live-security-v1",
        "fingerprints": {key: digest(value) for key, value in rows.items()},
        "runtime_roles_nonprivileged": True,
    }


async def verify_preserved_permissions(conn, *, plan_ref, final=False):
    require(not conn.is_in_transaction(), "Security verification needs its own readonly transaction")
    plan, expected = load_plan(plan_ref)
    tx = conn.transaction(isolation="repeatable_read", readonly=True)
    await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='10s'")
        observed = await snapshot(conn)
        require(observed == expected, "Live roles, ACL, RLS or function security changed")
        indexes = [dict(r) for r in await conn.fetch(INDEX_SQL)]
        secondary = (
            ("idx_visit_field_value_workspace_field", NEW_INDEX) if final else ("idx_visit_field_value_text", OLD_INDEX)
        )
        expected_indexes = [
            {"name": secondary[0], "definition": secondary[1], "valid": True, "ready": True, "unique": False},
            {
                "name": "visit_field_value_pkey",
                "definition": PRIMARY_INDEX,
                "valid": True,
                "ready": True,
                "unique": True,
            },
        ]
        require(indexes == expected_indexes, "Exact visit field index shape differs")
    finally:
        await tx.rollback()
    return {
        "status": "verified_readonly",
        "rolled_back": True,
        "committed": False,
        "security_unchanged": True,
        "snapshot_sha256": digest(observed),
        "index_schema": "V279" if final else "V278",
    }
