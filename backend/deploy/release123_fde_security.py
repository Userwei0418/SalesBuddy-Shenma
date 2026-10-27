"""V280 adds one fixed definer and one API grant; preserve all other security."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from release123_contract import GATE_FILES, INSTALLATION_POLICY_SHA, validate_acl
from release_bundle_control import digest
from release_visit_index_security import QUERIES as BASE_QUERIES
from release_visit_index_security import ROLES, read_ref, require

SIGNATURE = "security.set_fde_members_with_history(uuid,integer,uuid[])"
MAINTENANCE_FUNCTION = "security.bump_fde_membership_version(uuid,integer)"
BODY_SHA = "a26d44d02f813929721abba4f6f92a98e6a665aa471a0e285b2218d479a565f7"
GRANT_SQL = "GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO sales_api_saas"
QUERIES = dict(BASE_QUERIES)
QUERIES["relations"] = BASE_QUERIES["relations"].replace("WHERE relkind NOT IN ('i','I') ", "")
QUERIES["functions"] = BASE_QUERIES["functions"].replace(
    "FROM pg_proc p",
    "FROM pg_proc p WHERE p.oid <> COALESCE(to_regprocedure('" + SIGNATURE + "'),0)",
)
INDEX_SQL = """SELECT indexrelid,indrelid,indisunique,indisprimary,indisexclusion,
    indimmediate,indisvalid,indisready,indislive,indisreplident,
    md5(pg_get_indexdef(indexrelid)) AS definition_digest FROM pg_index ORDER BY indexrelid"""
FUNCTION_SQL = """SELECT pg_get_userbyid(p.proowner) AS owner,l.lanname AS language,
    p.prokind::text AS kind,p.prosecdef AS definer,p.proleakproof AS leakproof,
    p.proisstrict AS strict,p.proretset AS returns_set,p.provolatile::text AS volatility,
    p.proparallel::text AS parallel,p.prorettype::bigint AS return_type,
    p.proargtypes::text AS argument_types,p.proargnames AS argument_names,
    p.proallargtypes::text AS all_argument_types,p.proargmodes::text AS argument_modes,
    p.pronargdefaults AS defaults,p.proconfig AS settings,
    encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') AS body_sha256
    FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure($1)"""
ACL_SQL = """SELECT pg_get_userbyid(a.grantor) AS grantor,
    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
    a.privilege_type AS privilege,a.is_grantable AS grantable
    FROM pg_proc p CROSS JOIN LATERAL
    aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
    WHERE p.oid=to_regprocedure($1) ORDER BY grantee,grantor,privilege"""


def load_plan(plan_ref):
    plan = read_ref(plan_ref)
    require(
        plan.get("format") == "salesbuddy-fde-security-plan-v1"
        and plan.get("roles") == ROLES
        and plan.get("maintenance_owner") == "postgres"
        and plan.get("target") == {"database": "sales_saas", "oid": 18684},
        "Exact production V280 security plan required",
    )
    expected = read_ref(plan["policy_ref"])
    validate_acl(plan, expected, digest=digest, require=require)
    root = Path(__file__).resolve().parents[2]
    metadata = root / "tools/installation/runtime_permissions.json"
    sources = {str(root / "backend/deploy" / name) for name in GATE_FILES}
    sources.update((str(root / "database/scripts/migrate.py"), str(metadata)))
    require(set(plan.get("source_files", {})) == sources, "Complete V280 security source closure required")
    for name, checksum in plan["source_files"].items():
        source = Path(name)
        require(
            source.is_file()
            and not any(p.is_symlink() for p in (source, *source.parents))
            and hashlib.sha256(source.read_bytes()).hexdigest() == checksum,
            "V280 security source changed",
        )
    require(
        digest(json.loads(metadata.read_bytes())) == INSTALLATION_POLICY_SHA,
        "Exact V280 installation metadata required",
    )
    return plan, expected


async def snapshot(conn):
    """Fingerprint existing catalogs; exclude only the separately checked function."""
    rows = {name: [dict(row) for row in await conn.fetch(sql)] for name, sql in QUERIES.items()}
    roles = [row for row in rows["roles"] if row["rolname"] in ROLES.values()]
    require(
        len(roles) == 4
        and all(
            row["rolcanlogin"]
            and not any(row[k] for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls"))
            for row in roles
        ),
        "Existing runtime roles are absent or privileged",
    )
    return {
        "format": "salesbuddy-live-security-v1",
        "fingerprints": {key: digest(value) for key, value in rows.items()},
        "runtime_roles_nonprivileged": True,
        "index_fingerprint": digest([dict(row) for row in await conn.fetch(INDEX_SQL)]),
    }


def validate_function(row, grants, *, owner, allow_ungranted=False):
    require(
        row == {
            "owner": owner,
            "language": "plpgsql",
            "kind": "f",
            "definer": True,
            "leakproof": False,
            "strict": False,
            "returns_set": False,
            "volatility": "v",
            "parallel": "u",
            "return_type": 23,
            "argument_types": "2950 23 2951",
            "argument_names": ["p_opportunity", "p_expected_version", "p_members"],
            "all_argument_types": None,
            "argument_modes": None,
            "defaults": 0,
            "settings": ["search_path=pg_catalog"],
            "body_sha256": BODY_SHA,
        },
        "V280 definer signature, body, owner or execution properties changed",
    )
    owner_acl = {"grantor": owner, "grantee": owner, "privilege": "EXECUTE", "grantable": False}
    api_acl = {"grantor": owner, "grantee": ROLES["api"], "privilege": "EXECUTE", "grantable": False}
    require(
        grants == [owner_acl, api_acl] or (allow_ungranted and grants == [owner_acl]),
        "V280 function must grant EXECUTE only to maintenance owner and API without grant option",
    )


async def verify_state(conn, *, plan_ref, final=False, allow_ungranted=False):
    require(conn.is_in_transaction(), "V280 security verification must be transactional")
    plan, expected = load_plan(plan_ref)
    identity = dict(await conn.fetchrow(
        """SELECT current_database() AS database,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid"""
    ))
    require(identity == plan["target"], "V280 database identity changed")
    owner = await conn.fetchval("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=to_regprocedure($1)",
                                MAINTENANCE_FUNCTION)
    require(owner == plan["maintenance_owner"], "Existing FDE maintenance owner changed")
    observed = await snapshot(conn)
    require(observed == expected, "Existing roles, ACL, RLS, indexes or function security changed")
    row = await conn.fetchrow(FUNCTION_SQL, SIGNATURE)
    if not final:
        require(row is None, "V280 function already exists before migration")
    else:
        grants = [dict(r) for r in await conn.fetch(ACL_SQL, SIGNATURE)]
        validate_function(dict(row) if row else None, grants, owner=owner, allow_ungranted=allow_ungranted)
        for role in ROLES.values():
            actual = await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, SIGNATURE)
            require(
                actual is (role == ROLES["api"] and any(r["grantee"] == role for r in grants)),
                "Unexpected effective V280 function execution privilege",
            )
    return {"security_preserved": True, "snapshot_sha256": digest(observed), "schema": "V280" if final else "V279"}


async def grant_execute(conn, *, plan_ref):
    """Called inside the same transaction as migrate.apply and its ledger writes."""
    require(conn.is_in_transaction(), "V280 grant requires the migration transaction")
    await verify_state(conn, plan_ref=plan_ref, final=True, allow_ungranted=True)
    await conn.execute(GRANT_SQL)
    return await verify_state(conn, plan_ref=plan_ref, final=True)


async def verify_release_permissions(conn, *, plan_ref, final=True):
    require(not conn.is_in_transaction(), "V280 verification needs its own readonly transaction")
    tx = conn.transaction(isolation="repeatable_read", readonly=True)
    await tx.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='10s'")
        observed = await verify_state(conn, plan_ref=plan_ref, final=final)
    finally:
        await tx.rollback()
    return {"status": "verified_readonly", "rolled_back": True, "committed": False, **observed}
