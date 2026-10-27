"""Fixed reviewed normal permissions for the four existing sales_saas services.

SOURCE only until the caller supplies a reviewed plan. The caller owns the
managed deployment lock, stopped writers, backup, migration and full database
protection. This helper owns its ACL transaction, records COMMIT uncertainty and
never retries, creates roles, reads credentials or changes runtime pointers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import stat
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLES = {
    "api": "sales_api_saas",
    "worker": "sales_worker_saas",
    "feishu": "sales_feishu_saas",
    "platform": "sales_platform_saas",
}
SERVICES_SHA = "a442f1ea126b1d2ffe5ca61e6911af0eca9f7a56e2c648f56fe75f34c476a722"
MIGRATION_SHA = "4838fdac5ab18e821bd51e794b63b179f9ef467e3ea2d47e961426603bf417a4"
SCHEMA_CONTRACTS = {
    'V233': {'count': 197, 'ledger': MIGRATION_SHA,
             'registered': '51c8d2b81183bc70d6b3a28a172d44f036dc2dc5005d66fd7d0ac118bf596b6e'},
    'V234': {'count': 198, 'ledger': '43b782d87d8b4d181ccc1861c5bcfa504a54ad575a42c17a8186926b7a67e2ca',
             'registered': '0ee26c2eeec26bb3c6dbfc6127b05fe0c6bd62e9e1c65966aac0852f3a81208f'},
}

# This V238 contract intentionally excludes the unrelated V234/V239 migrations.
from report_release_contract import (
    SERVICES_SHA as REPORT_SERVICES_SHA,
    TARGET_LEDGER as REPORT_LEDGER,
    TARGET_REGISTERED as REPORT_REGISTERED,
)

SCHEMA_CONTRACTS['V238'] = {
    'count': 201, 'ledger': REPORT_LEDGER, 'registered': REPORT_REGISTERED,
}
DEPENDENCIES = {
    "runtime_roles": "1ff578315160d76e485d9bc9bc76690990cf9bc8684774d8f88e72414226515c",
    "upgrade_permissions": "dd8b8bb053ab3d38ae5b2c23f8fb65e547e6abcdf4ed413143abe2b248406d2f",
}
IDENTITY_SQL = """SELECT current_database() AS database,
 (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid,
 session_user::text AS session_user,current_user::text AS current_user,
 pg_backend_pid() AS pid,current_setting('transaction_read_only') AS read_only,
 current_setting('transaction_isolation') AS isolation,
 current_setting('server_version_num')::integer AS server_version_num,
 r.rolsuper FROM pg_roles r WHERE r.rolname=current_user"""
# Full row values, including original timestamps/provenance, stay in memory.
# Only their digests are persisted; neither query contains credentials.
LEDGER_SQL = """SELECT jsonb_build_object(
 'registered',COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY version) FROM ops.schema_migration s),'[]'::jsonb),
 'checksums',COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY migration_key)
 FROM ops.migration_checksum c),'[]'::jsonb))"""
CLUSTER_SQL = """SELECT jsonb_build_object(
 'roles',(SELECT jsonb_agg(jsonb_build_object('oid',oid,'name',rolname,'login',rolcanlogin,
  'super',rolsuper,'bypass',rolbypassrls,'create_role',rolcreaterole,'create_db',rolcreatedb,
  'replication',rolreplication,'inherit',rolinherit,'limit',rolconnlimit,'validuntil',rolvaliduntil)
  ORDER BY oid) FROM pg_roles),
 'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY roleid,member,grantor) FROM pg_auth_members m))"""


class Refused(ValueError):
    pass


class ACLExecutionFailed(RuntimeError):
    def __init__(self, journal):
        super().__init__("ACL failed; inspect preserved journal; never blindly retry")
        self.journal = deepcopy(journal)


def require(condition, message):
    if not condition:
        raise Refused(message)


def canonical(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def read_ref(ref):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "EXACT_REF_REQUIRED")
    path = Path(ref["path"])
    require(
        path.is_absolute() and path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
        "REGULAR_ABSOLUTE_REF",
    )
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, "PRIVATE_0600_REF")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref["sha256"], "REF_CHANGED")
    return json.loads(raw)


def knowledge_contract_for(plan):
    scope = plan.get('release_scope')
    if scope == ['release_1.2.2_knowledge_distribution']:
        import release122_knowledge_contract as contract
        return contract
    if scope == ['release_1.2.2_knowledge_labels']:
        import release122_knowledge_labels_contract as contract
        return contract
    if scope == ['release_1.2.2_knowledge_image_preview']:
        import release122_knowledge_image_preview_contract as contract
        return contract
    if scope == ['release_1.2.2_knowledge_silent_publication']:
        import release122_knowledge_silent_publication_contract as contract
        return contract
    return None


def schema_contract(plan):
    knowledge_contract = knowledge_contract_for(plan)
    if knowledge_contract:
        return knowledge_contract.schema_contract(plan, digest=canonical, require=require)
    if plan.get('release_scope') == ['release_1.2.2']:
        from release122_contract import schema_contract as release122_schema
        return release122_schema(plan, digest=canonical, require=require)
    if plan.get('release_scope') == ['review_okr_1.2.1']:
        from review_release_contract import schema_contract as review_schema
        schema, stage = review_schema(plan, digest=canonical, require=require)
        return schema, {key: stage[key] for key in ('count', 'ledger', 'registered')}
    versions = plan['expected_registered_versions']
    require(isinstance(versions, list) and bool(versions)
            and all(isinstance(v, str) and v.startswith('V') and v[1:].isdigit() for v in versions)
            and versions == sorted(set(versions)), 'EXACT_REGISTERED_VERSIONS_REQUIRED')
    schema = max(versions, key=lambda version: int(version[1:]))
    contract = SCHEMA_CONTRACTS.get(schema)
    if plan.get('release_scope') == ['release_1.2.1']:
        from release121_contract import TARGET_LEDGER, TARGET_REGISTERED
        require(schema == 'V238', 'RELEASE121_SCHEMA_REQUIRED')
        contract = {'count': 202, 'ledger': TARGET_LEDGER, 'registered': TARGET_REGISTERED}
    if plan.get('release_scope') == ['enterprise_identity_1.2.1']:
        from identity_release_contract import TARGET_LEDGER, TARGET_REGISTERED
        require(schema == 'V239', 'IDENTITY_SCHEMA_REQUIRED')
        contract = {'count': 203, 'ledger': TARGET_LEDGER, 'registered': TARGET_REGISTERED}
    require(contract is not None and canonical(versions) == contract['registered'],
            'EXACT_REGISTERED_VERSIONS_REQUIRED')
    return schema, contract


def load_plan(plan_ref):
    plan = read_ref(plan_ref)
    require(
        plan["format"] == "salesbuddy-release120-production-acl-v1"
        and plan["mode"] == "normal"
        and plan["roles"] == ROLES
        and set(plan["target"]) == {"database", "oid"}
        and plan["target"]["database"] == "sales_saas"
        and type(plan["target"]["oid"]) is int
        and plan["target"]["oid"] == 18684
        and all(isinstance(plan[k], str) and plan[k] for k in ("attempt_id", "window_id")),
        "EXACT_PRODUCTION_PLAN",
    )
    schema, contract = schema_contract(plan)
    release121 = plan.get('release_scope') == ['release_1.2.1']
    identity239 = plan.get('release_scope') == ['enterprise_identity_1.2.1']
    review_okr = plan.get('release_scope') == ['review_okr_1.2.1']
    release122 = plan.get('release_scope') == ['release_1.2.2']
    knowledge122 = plan.get('release_scope') == ['release_1.2.2_knowledge_distribution']
    knowledge_labels = plan.get('release_scope') == ['release_1.2.2_knowledge_labels']
    knowledge_image_preview = plan.get('release_scope') in (
        ['release_1.2.2_knowledge_image_preview'], ['release_1.2.2_knowledge_silent_publication'])
    if schema == 'V238' and not release121:
        require(plan.get('release_scope') == ['work_report'], 'REPORT_ONLY_SCOPE_REQUIRED')
    policy = read_ref(plan["policy_ref"])
    if plan.get('release_scope') == ['work_report']:
        from report_release_contract import validate_acl
        validate_acl(plan, policy, digest=canonical, require=require)
    if release121:
        from release121_contract import validate_acl
        validate_acl(plan, policy, digest=canonical, require=require)
    if identity239:
        from identity_release_contract import SERVICES_SHA as IDENTITY_SERVICES_SHA, validate_acl
        validate_acl(plan, policy, digest=canonical, require=require)
    if review_okr:
        from review_release_contract import frozen_stage, validate_acl
        validate_acl(plan, policy, digest=canonical, require=require)
    if release122:
        from release122_contract import SERVICES_SHA as RELEASE122_SERVICES_SHA
        from release122_contract import validate_acl
        validate_acl(plan, policy, digest=canonical, require=require)
    if knowledge122 or knowledge_labels or knowledge_image_preview:
        knowledge_contract = knowledge_contract_for(plan)
        knowledge_contract.validate_acl(plan, policy, digest=canonical, require=require)
    service_digest = (knowledge_contract_for(plan).SERVICES_SHA
                      if knowledge122 or knowledge_labels or knowledge_image_preview else
                      RELEASE122_SERVICES_SHA if release122 else
                      frozen_stage(schema, require=require)['services'] if review_okr else
                      IDENTITY_SERVICES_SHA if identity239 else
                      REPORT_SERVICES_SHA if schema == 'V238' else SERVICES_SHA)
    require(
        policy["schema"] == schema
        and policy["migration_fingerprint"] == contract['ledger']
        and canonical(policy["services"]) == service_digest,
        "FIXED_NORMAL_SERVICE_MATRIX",
    )
    required = {str(Path(__file__).resolve())}
    if schema == 'V238':
        required.add(str(ROOT / 'backend/deploy/report_release_contract.py'))
    if release121:
        required |= {str(ROOT / 'backend/deploy' / name) for name in
                     ('release121_contract.py', 'release_upgrade.py', 'release_forward_bundle.py')}
    if identity239:
        required |= {str(ROOT / 'backend/deploy' / name) for name in
                     ('identity_release_contract.py', 'release121_contract.py', 'release_upgrade.py',
                      'release_forward_bundle.py')}
    if review_okr:
        from review_release_contract import GATE_FILES
        required |= {str(ROOT / 'backend/deploy' / name) for name in GATE_FILES}
    if release122:
        from release122_contract import GATE_FILES
        required |= {str(ROOT / 'backend/deploy' / name) for name in GATE_FILES}
    if knowledge122 or knowledge_labels or knowledge_image_preview:
        required |= {str(ROOT / 'backend/deploy' / name)
                     for name in knowledge_contract_for(plan).GATE_FILES}
    required |= {str(ROOT / "tools/installation" / (name + ".py")) for name in DEPENDENCIES}
    require(required <= set(plan["source_files"]), "SOURCE_CLOSURE_REQUIRED")
    for name, digest in plan["source_files"].items():
        path = Path(name)
        require(
            path.is_absolute()
            and path.is_file()
            and not any(p.is_symlink() for p in (path, *path.parents))
            and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
            "SOURCE_CHANGED",
        )
    for name, digest in dependency_contract(plan).items():
        require(
            plan["source_files"][str(ROOT / "tools/installation" / (name + ".py"))] == digest, "FIXED_SHARED_SOURCE"
        )
    return plan, policy


def dependency_contract(plan=None):
    if plan and plan.get('release_scope') in (
        ['release_1.2.2_knowledge_distribution'], ['release_1.2.2_knowledge_labels'],
        ['release_1.2.2_knowledge_image_preview'], ['release_1.2.2_knowledge_silent_publication']):
        return knowledge_contract_for(plan).DEPENDENCIES
    if plan and plan.get('release_scope') == ['release_1.2.2']:
        from release122_contract import DEPENDENCIES as release122_dependencies
        return release122_dependencies
    return DEPENDENCIES


def dependencies(plan=None):
    sys.path.insert(0, str(ROOT / "tools/installation"))
    modules = []
    for name, expected in dependency_contract(plan).items():
        path = ROOT / "tools/installation" / (name + ".py")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "SHARED_SOURCE_CHANGED")
        module = importlib.import_module(name)
        require(Path(module.__file__).resolve() == path, "SHARED_MODULE_ORIGIN")
        modules.append(module)
    return tuple(modules)


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


async def identity(conn, plan, *, readonly):
    value = dict(await conn.fetchrow(IDENTITY_SQL))
    require(
        {k: value[k] for k in ("database", "oid")} == plan["target"]
        and value["session_user"] == value["current_user"] == "postgres"
        and value["rolsuper"] is True
        and value["read_only"] == ("on" if readonly else "off")
        and value["isolation"] == "repeatable read"
        and 160000 <= value["server_version_num"] < 170000,
        "ACTUAL_PRODUCTION_MAINTENANCE_IDENTITY",
    )
    return value


async def ledger(conn, plan):
    _, contract = schema_contract(plan)
    value = decoded(await conn.fetchval(LEDGER_SQL))
    rows = value["checksums"]
    require(
        len({r["migration_key"] for r in rows}) == len(rows) == contract['count']
        and canonical({r["migration_key"]: r["checksum_sha256"] for r in rows}) == contract['ledger']
        and [r["version"] for r in value["registered"]] == plan["expected_registered_versions"],
        "LIVE_FULL_LEDGER_NOT_TARGET",
    )
    return value


async def verify_release_permissions(conn, *, plan_ref):
    """Borrow an idle connection; begin RO/RR, verify, always ROLLBACK."""
    plan, policy = load_plan(plan_ref)
    roles, _ = (dependencies(plan) if plan.get('release_scope') in
                (['release_1.2.2'], ['release_1.2.2_knowledge_distribution'],
                 ['release_1.2.2_knowledge_labels'], ['release_1.2.2_knowledge_image_preview'], ['release_1.2.2_knowledge_silent_publication']) else dependencies())
    roles.validate_policy(policy)
    require(not conn.is_closed() and not conn.is_in_transaction(), "IDLE_CONNECTION_REQUIRED")
    tx = conn.transaction(isolation="repeatable_read", readonly=True)
    started = False
    try:
        await tx.start()
        started = True
        await conn.execute(
            "SELECT set_config('statement_timeout','45000ms',true),set_config('lock_timeout','5000ms',true)"
        )
        who = await identity(conn, plan, readonly=True)
        before = await ledger(conn, plan)
        result = await roles.verify_service_roles(conn, ROLES, policy)
        require(await ledger(conn, plan) == before, "LEDGER_CHANGED")
        require(
            await identity(conn, plan, readonly=True) == who and load_plan(plan_ref) == (plan, policy),
            "VERIFY_BINDING_CHANGED",
        )
    finally:
        if started or conn.is_in_transaction():
            await tx.rollback()
    require(not conn.is_in_transaction(), "VERIFY_ROLLBACK_NOT_ACKNOWLEDGED")
    return dict(
        status="verified_readonly",
        identity=who,
        verification=result,
        ledger_sha256=canonical(before),
        policy_ref=plan["policy_ref"],
        rolled_back=True,
        committed=False,
        runtime_ready=False,
    )


async def apply_release_permissions(conn, *, plan_ref, persist):
    """Own one ACL transaction. persist is async (label, safe_json) -> any.

    The host holds its deployment lock and has stopped writers before this call.
    A commit exception remains unknown even if cleanup ROLLBACK succeeds. The
    caller must inspect it on a new connection; there is no automatic retry.
    """
    plan, policy = load_plan(plan_ref)
    roles, upgrade = (dependencies(plan) if plan.get('release_scope') in
                      (['release_1.2.2'], ['release_1.2.2_knowledge_distribution'],
                       ['release_1.2.2_knowledge_labels'],
                       ['release_1.2.2_knowledge_image_preview'], ['release_1.2.2_knowledge_silent_publication']) else dependencies())
    roles.validate_policy(policy)
    require(not conn.is_closed() and not conn.is_in_transaction(), "IDLE_CONNECTION_REQUIRED")
    journal = dict(
        format="salesbuddy-release120-production-acl-result-v1",
        plan_ref=deepcopy(plan_ref),
        target=plan["target"],
        roles=ROLES,
        attempt_id=plan["attempt_id"],
        window_id=plan["window_id"],
        commit_attempted=False,
        commit_acknowledged=False,
        rollback_acknowledged=False,
        errors=[],
        cleanup_errors=[],
        runtime_ready=False,
    )
    tx = conn.transaction(isolation="repeatable_read")
    started = False
    try:
        await tx.start()
        started = True
        await conn.execute(
            "SELECT set_config('statement_timeout','45000ms',true),set_config('lock_timeout','5000ms',true)"
        )
        journal["identity"] = await identity(conn, plan, readonly=False)
        before = await ledger(conn, plan)
        cluster = decoded(await conn.fetchval(CLUSTER_SQL))
        journal.update(ledger_before_sha256=canonical(before), cluster_before_sha256=canonical(cluster))
        await persist("ACL_BEFORE", deepcopy(journal))
        require(load_plan(plan_ref) == (plan, policy), "PLAN_CHANGED_BEFORE_ACL")
        journal["verification"] = await upgrade.reconcile_service_roles(conn, ROLES, policy)
        require(await ledger(conn, plan) == before, "ACL_CHANGED_LEDGER")
        require(decoded(await conn.fetchval(CLUSTER_SQL)) == cluster, "ACL_CHANGED_CLUSTER_IDENTITIES")
        require(await identity(conn, plan, readonly=False) == journal["identity"], "ACL_IDENTITY_CHANGED")
        require(load_plan(plan_ref) == (plan, policy), "PLAN_CHANGED_BEFORE_COMMIT")
        journal["ledger_after_sha256"] = canonical(before)
        await persist("ACL_COMMIT_INTENT", deepcopy(journal))
        require(load_plan(plan_ref) == (plan, policy), "PLAN_CHANGED_AFTER_INTENT")
        require(await identity(conn, plan, readonly=False) == journal["identity"], "IDENTITY_CHANGED_AFTER_INTENT")
        journal["commit_attempted"] = True
        await tx.commit()
        journal["commit_acknowledged"] = True
        require(not conn.is_in_transaction(), "COMMIT_LEFT_TRANSACTION_OPEN")
        journal["status"] = "committed_pending_independent_readback"
        await persist("ACL_COMMITTED", deepcopy(journal))
    except BaseException as error:
        journal["errors"].append(
            dict(type=type(error).__name__, phase="commit_or_after" if journal["commit_attempted"] else "before_commit")
        )
    finally:
        if conn.is_in_transaction():
            try:
                await tx.rollback()
                journal["rollback_acknowledged"] = not conn.is_in_transaction()
            except BaseException as error:
                journal["cleanup_errors"].append(dict(type=type(error).__name__, phase="rollback"))
        journal["commit_outcome"] = (
            "acknowledged"
            if journal["commit_acknowledged"]
            else "unknown_no_replay"
            if journal["commit_attempted"]
            else "not_attempted"
        )
        if journal["errors"] or journal["cleanup_errors"]:
            journal["status"] = "failed_preserve_evidence"
            try:
                await persist("ACL_FAILED", deepcopy(journal))
            except BaseException as error:
                journal["cleanup_errors"].append(dict(type=type(error).__name__, phase="failure_persistence"))
    if journal["errors"] or journal["cleanup_errors"] or not started or not journal["commit_acknowledged"]:
        raise ACLExecutionFailed(journal)
    return journal


def exclusive_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def cli(args):
    plan_ref = dict(path=str(Path(args.plan).resolve()), sha256=args.plan_sha256)
    load_plan(plan_ref)  # Validate all public inputs before opening any connector.
    output = Path(args.output_dir)
    require(output.is_absolute() and not output.exists(), "FRESH_OUTPUT_DIRECTORY_REQUIRED")
    output.mkdir(mode=0o700)
    import asyncpg

    conn = None
    result = dict(status="not_started", connection_closed=False)
    try:
        conn = await asyncpg.connect(
            database="sales_saas",
            user="postgres",
            host="/tmp",  # noqa: S108 -- existing PostgreSQL peer socket
            port=5432,
            timeout=10,
            command_timeout=60,
        )

        async def persist(label, value):
            exclusive_json(output / (label + ".json"), value)

        if args.apply:
            # The standalone CLI refuses to race the managed root caller. A root
            # holding this same lock imports the function instead of spawning CLI.
            import fcntl

            with open("/www/wwwroot/salegent/.deploy.lock", "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = await apply_release_permissions(conn, plan_ref=plan_ref, persist=persist)
        else:
            result = await verify_release_permissions(conn, plan_ref=plan_ref)
    except ACLExecutionFailed as error:
        result = error.journal
    except BaseException as error:
        result = dict(status="failed", error_type=type(error).__name__)
    finally:
        if conn is not None:
            try:
                await conn.close(timeout=5)
                result["connection_closed"] = conn.is_closed()
            except BaseException as error:
                result["close_error_type"] = type(error).__name__
                result["connection_closed"] = False
                conn.terminate()
        exclusive_json(output / "ACL_RECEIPT.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result.get("connection_closed")
        and result.get("status") in ("verified_readonly", "committed_pending_independent_readback")
        else 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--output-dir", required=True)
    return asyncio.run(cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
