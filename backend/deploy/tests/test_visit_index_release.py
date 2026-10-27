"""Offline V279 admission, readonly security drift, and migration-boundary tests."""

import asyncio
import hashlib
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend/deploy"))
import release122_knowledge_contract as prior  # noqa: E402
import release122_visit_index_contract as contract  # noqa: E402
import release_forward_bundle as forward  # noqa: E402
import release_visit_index_security as security  # noqa: E402

FROZEN = json.loads((ROOT / "backend/tests/fixtures/release122_knowledge_contract.frozen.json").read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline test reached a real host or database")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(forward.subprocess, "run", forbidden)


def migrations():
    rows = deepcopy(FROZEN["migration_plan"])
    for version in ("V276", "V277", "V278"):
        file = next((ROOT / "database/migrations").glob(version + "__*.sql"))
        rows.append(
            {"key": version, "file": "migrations/" + file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
        )
    name, checksum = contract.SQL["V279"]
    return rows + [{"key": "V279", "file": name, "sha256": checksum}]


def base_plan():
    rows = migrations()
    registered = sorted(FROZEN["expected_ledger"]["registered"] + prior.PENDING + ["V276", "V277", "V278"])
    return {
        "product_version": "1.2.2",
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "source_schema": "V278",
        "target_schema": "V279",
        "pending": ["V279"],
        "expected_ledger": {
            "registered": registered,
            "checksums": {r["key"]: r["sha256"] for r in rows[:-1]},
            "running_jobs": 0,
        },
        "active_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
        "supported_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
    }


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return forward.file_ref(path)


def security_snapshot():
    return {
        "format": "salesbuddy-live-security-v1",
        "runtime_roles_nonprivileged": True,
        "fingerprints": {key: "a" * 64 for key in contract.SECURITY_SECTIONS},
    }


@pytest.fixture
def bound(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "host"
    monkeypatch.setattr(forward, "ROOT", root)
    release = root / "releases/new"
    deploy = release / "backend/deploy"
    deploy.mkdir(parents=True)
    sources = {}
    for name in contract.GATE_FILES:
        dest = deploy / name
        dest.write_bytes((ROOT / "backend/deploy" / name).read_bytes())
        sources[str(dest)] = hashlib.sha256(dest.read_bytes()).hexdigest()
    migrator = release / "database/scripts/migrate.py"
    migrator.parent.mkdir(parents=True)
    migrator.write_bytes((ROOT / "database/scripts/migrate.py").read_bytes())
    sources[str(migrator)] = hashlib.sha256(migrator.read_bytes()).hexdigest()
    metadata = release / "tools/installation/runtime_permissions.json"
    metadata.parent.mkdir(parents=True)
    policy = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())
    policy.update(schema="V279", migration_fingerprint=contract.TARGET_LEDGER)
    # Reconstruct the historical policy before V280's single added function.
    policy["services"]["api"]["functions"] = [
        value for value in policy["services"]["api"]["functions"]
        if value != "security.set_fde_members_with_history(uuid,integer,uuid[])"
        and not value.startswith(("security.company_customer_directory_candidates(",
                                  "security.company_customer_directory_page_items("))
    ]
    metadata.write_text(json.dumps(policy))
    sources[str(metadata)] = hashlib.sha256(metadata.read_bytes()).hexdigest()
    p = base_plan()
    acl_plan = {
        "format": "salesbuddy-index-security-plan-v1",
        "mode": "preserve",
        "roles": security.ROLES.copy(),
        "target": {"database": "sales_saas", "oid": 18684},
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "expected_registered_versions": sorted(p["expected_ledger"]["registered"] + ["V279"]),
        "policy_ref": write(tmp_path / "security.json", security_snapshot()),
        "source_files": sources,
    }
    p.update(
        bundle_id="index279",
        backend="new",
        web="new-web",
        expected_backend="old",
        expected_web="old-web",
        expected_database={"name": "sales_saas"},
        candidate={"backend": {"sha256": "b" * 64}},
        migration_plan=write(tmp_path / "migrations.json", migrations()),
        acl_plan=write(tmp_path / "security-plan.json", acl_plan),
        acl_source=forward.file_ref(deploy / "release_visit_index_security.py"),
        validation=write(
            tmp_path / "validation.json",
            {
                "status": "passed",
                "tested_source_sha256": "b" * 64,
                "tested_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
            },
        ),
    )
    ref = write(root / "bundles/forward/index279/database/PLAN.json", p)
    return p, acl_plan, ref, release


def test_exact_v279_admission_binds_all_sources_and_preserves_acl(bound):
    p, _, ref, _ = bound
    assert forward.inputs(ref)[0] == p
    helper, _ = forward.validated_acl(p)
    assert not hasattr(helper, "apply_release_permissions")
    assert forward.verify_release_ledger(p, migrations(), p["expected_ledger"]) == ["V279"]
    final = {
        "registered": sorted(p["expected_ledger"]["registered"] + ["V279"]),
        "checksums": {r["key"]: r["sha256"] for r in migrations()},
        "running_jobs": 0,
    }
    assert forward.verify_release_ledger(p, migrations(), final, final=True) == []


@pytest.mark.parametrize("fault", ["sql", "old_checksum", "registration", "client", "scope", "configuration", "jobs"])
def test_unreviewed_transition_rejected_before_connection(fault):
    p, rows = base_plan(), migrations()
    if fault == "sql":
        rows[-1]["sha256"] = "0" * 64
    if fault == "old_checksum":
        p["expected_ledger"]["checksums"]["BASELINE_V1"] = "0" * 64
    if fault == "registration":
        p["expected_ledger"]["registered"].pop()
    if fault == "client":
        p["active_mini_versions"].pop()
    if fault == "scope":
        p["release_scope"] = ["release_1.2.2_knowledge_silent_publication"]
    if fault == "configuration":
        p["agent_stage"] = {}
    if fault == "jobs":
        p["expected_ledger"]["running_jobs"] = 1
    with pytest.raises(forward.Refused):
        contract.validate(p, rows, digest=forward.original.digest, require=forward.require)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_catalog",
        "elevated_role",
        "acl_apply",
        "source_changed",
        "wrong_helper",
        "installation_policy",
        "missing_policy_binding",
    ],
)
def test_security_gate_is_complete_and_cannot_replay_policy(bound, fault):
    p, acl_plan, ref, release = bound
    snapshot = security_snapshot()
    if fault == "missing_catalog":
        snapshot["fingerprints"].pop("functions")
    if fault == "elevated_role":
        snapshot["runtime_roles_nonprivileged"] = False
    if fault == "acl_apply":
        acl_plan["mode"] = "normal"
    if fault == "source_changed":
        (release / "backend/deploy/release_visit_index_security.py").write_text("# changed")
    if fault == "wrong_helper":
        p["acl_source"] = forward.file_ref(ROOT / "backend/deploy/release120_production_acl.py")
    metadata = release / "tools/installation/runtime_permissions.json"
    if fault == "installation_policy":
        value = json.loads(metadata.read_text())
        value["schema"] = "V278"
        metadata.write_text(json.dumps(value))
        acl_plan["source_files"][str(metadata)] = hashlib.sha256(metadata.read_bytes()).hexdigest()
    if fault == "missing_policy_binding":
        acl_plan["source_files"].pop(str(metadata))
    acl_plan["policy_ref"] = write(Path(acl_plan["policy_ref"]["path"]), snapshot)
    p["acl_plan"] = write(Path(p["acl_plan"]["path"]), acl_plan)
    ref = write(Path(ref["path"]), p)
    with pytest.raises(forward.Refused):
        forward.inputs(ref)


@pytest.mark.parametrize("drift", [None, *sorted(contract.SECURITY_SECTIONS), "index"])
def test_security_verification_rejects_catalog_or_index_drift_and_always_rolls_back(monkeypatch, drift):
    expected = security_snapshot()
    observed = deepcopy(expected)
    if drift in contract.SECURITY_SECTIONS:
        observed["fingerprints"][drift] = "c" * 64
    monkeypatch.setattr(security, "load_plan", lambda _: ({}, expected))

    async def snapshot(_):
        return observed

    monkeypatch.setattr(security, "snapshot", snapshot)
    events = []

    class Transaction:
        async def start(self):
            events.append("start")

        async def rollback(self):
            events.append("rollback")

    class Connection:
        def is_in_transaction(self):
            return False

        def transaction(self, **kwargs):
            assert kwargs == {"readonly": True, "isolation": "repeatable_read"}
            return Transaction()

        async def execute(self, sql):
            assert sql == "SET LOCAL statement_timeout='10s'"

        async def fetch(self, sql):
            assert sql == security.INDEX_SQL
            return [
                {
                    "name": "idx_visit_field_value_workspace_field",
                    "definition": security.NEW_INDEX,
                    "valid": drift != "index",
                    "ready": True,
                    "unique": False,
                },
                {
                    "name": "visit_field_value_pkey",
                    "definition": security.PRIMARY_INDEX,
                    "valid": True,
                    "ready": True,
                    "unique": True,
                },
            ]

    async def exercise():
        if drift:
            with pytest.raises(forward.Refused):
                await security.verify_preserved_permissions(Connection(), plan_ref={}, final=True)
        else:
            result = await security.verify_preserved_permissions(Connection(), plan_ref={}, final=True)
            assert result["security_unchanged"] and result["rolled_back"] and not result["committed"]

    asyncio.run(exercise())
    assert events == ["start", "rollback"]


@pytest.mark.parametrize("fault", [None, "before_security", "migration", "after_security"])
def test_database_phase_never_applies_acl_and_preserves_uncertain_migration_receipt(bound, monkeypatch, fault):
    p, acl_plan, ref, release = bound
    calls = []
    state = {"migrated": False, "closed": False}
    monkeypatch.setattr(forward, "__file__", str(release / "backend/deploy/release_forward_bundle.py"))
    monkeypatch.setattr(forward.os, "getuid", lambda: 12345)
    monkeypatch.setattr(forward.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=12345))
    monkeypatch.setattr(forward, "inputs", lambda _: (p, migrations(), {}))

    async def verify(conn, *, plan_ref, final=False):
        calls.append("security_after" if final else "security_before")
        if fault == ("after_security" if final else "before_security"):
            raise forward.Refused("security drift")
        return {"status": "verified_readonly", "rolled_back": True, "committed": False}

    helper = SimpleNamespace(verify_preserved_permissions=verify)
    monkeypatch.setattr(forward, "validated_acl", lambda _: (helper, acl_plan))
    name, checksum = contract.SQL["V279"]
    item = SimpleNamespace(key="V279", checksum=checksum, path=release / "database" / name)

    async def apply(conn, migration, root):
        calls.append("migration")
        if fault == "migration":
            raise RuntimeError("commit outcome not known")
        state["migrated"] = True
        return "applied"

    monkeypatch.setattr(
        forward, "load", lambda *_: SimpleNamespace(discover=lambda _: [item], apply=apply, DEPLOY_LOCK=202609102101)
    )

    class Connection:
        async def fetchrow(self, sql):
            return {"database": "sales_saas", "oid": 18684, "session_user": "postgres", "current_user": "postgres"}

        async def fetchval(self, sql, *args):
            return "pg_try_advisory_lock" in sql

        def is_in_transaction(self):
            return False

        async def close(self):
            state["closed"] = True

        def is_closed(self):
            return state["closed"]

    async def connect(**kwargs):
        calls.append("connected")
        return Connection()

    monkeypatch.setattr(asyncpg, "connect", connect)

    async def ledger(_):
        value = deepcopy(p["expected_ledger"])
        if state["migrated"]:
            value["registered"] = sorted(value["registered"] + ["V279"])
            value["checksums"]["V279"] = checksum
        value["checksum_rows"] = [
            {"migration_key": k, "checksum_sha256": v, "provenance": "executed"} for k, v in value["checksums"].items()
        ]
        value["registered_rows"] = [{"version": v} for v in value["registered"]]
        return value

    monkeypatch.setattr(forward, "ledger", ledger)
    result = asyncio.run(forward.database_phase(ref))
    assert result["closed"]
    assert result["success"] == (fault is None)
    assert "acl_applying" != result["phase"]
    if fault == "before_security":
        assert calls == ["connected", "security_before"] and result["migrations"] == []
    elif fault == "migration":
        assert result["migrations"][0]["commit_outcome"] == "unknown"
    else:
        assert calls == ["connected", "security_before", "migration", "security_after"]
        assert result["acl_commit_outcome"] == "not_attempted"
