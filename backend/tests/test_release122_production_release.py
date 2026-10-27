"""Frozen 1.2.2 admission and failure receipts, with no host/DB/network execution."""

# ruff: noqa: E402
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

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "backend/deploy"
sys.path.insert(0, str(DEPLOY))
import release120_production_acl as acl
import release122_contract as contract
import release_forward_bundle as forward
import release_upgrade as upgrade

FROZEN = json.loads((Path(__file__).with_name("fixtures") / "release122_contract.frozen.json").read_text())


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline test reached a real host or database")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(asyncpg, "create_pool", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(forward.subprocess, "run", forbidden)


def plan():
    return dict(
        product_version="1.2.2",
        release_scope=contract.RELEASE_SCOPE.copy(),
        source_schema="V255",
        target_schema="V268",
        pending=contract.PENDING.copy(),
        expected_ledger=deepcopy(FROZEN["expected_ledger"]),
    )


def target():
    return dict(
        checksums={r["key"]: r["sha256"] for r in FROZEN["migration_plan"]},
        registered=sorted(FROZEN["expected_ledger"]["registered"] + contract.PENDING),
        running_jobs=0,
    )


def write(path, value):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return forward.file_ref(path)


@pytest.fixture
def bound(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(forward, "ROOT", tmp_path / "host")
    deploy = forward.ROOT / "releases/new/backend/deploy"
    deploy.mkdir(parents=True)
    for name in contract.GATE_FILES:
        (deploy / name).write_bytes((DEPLOY / name).read_bytes())
    sources = [
        *[DEPLOY / name for name in contract.GATE_FILES],
        *[deploy / name for name in contract.GATE_FILES],
        *[ROOT / "tools/installation" / (name + ".py") for name in acl.DEPENDENCIES],
    ]
    ap = dict(
        format="salesbuddy-release120-production-acl-v1",
        mode="normal",
        roles=acl.ROLES.copy(),
        target=dict(database="sales_saas", oid=18684),
        attempt_id="offline122",
        window_id="offline122",
        release_scope=contract.RELEASE_SCOPE.copy(),
        expected_registered_versions=target()["registered"],
        policy_ref=write(tmp_path / "policy.json", FROZEN["normal_policy"]),
        source_files={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    )
    p = dict(
        **plan(),
        expected_database={"name": "sales_saas"},
        bundle_id="offline122",
        backend="new",
        web="new-web",
        expected_backend="old",
        expected_web="old-web",
        migration_plan=write(tmp_path / "migrations.json", FROZEN["migration_plan"]),
        acl_plan=write(tmp_path / "acl.json", ap),
        acl_source=forward.file_ref(deploy / "release120_production_acl.py"),
        validation=write(
            tmp_path / "validation.json",
            dict(status="passed", tested_source_sha256="a" * 64, tested_mini_versions=["1.2.1", "1.2.2"]),
        ),
        candidate={"backend": {"sha256": "a" * 64}},
        supported_mini_versions=["1.2.1", "1.2.2"],
    )
    binding = write(forward.ROOT / "bundles/forward/offline122/database/PLAN.json", p)
    return p, ap, binding, tmp_path


def test_fixed_candidate_matches_frozen_sql_policy_and_complete_production_prefix(bound):
    p, ap, binding, _ = bound
    assert forward.inputs(binding) == (p, FROZEN["migration_plan"], forward.document(p["validation"]))
    assert acl.load_plan(p["acl_plan"]) == (ap, FROZEN["normal_policy"])
    assert forward.verify_release_ledger(p, FROZEN["migration_plan"], p["expected_ledger"]) == contract.PENDING
    assert forward.verify_release_ledger(p, FROZEN["migration_plan"], target(), final=True) == []
    assert acl.schema_contract(ap) == (
        "V268",
        dict(count=227, ledger=contract.TARGET_LEDGER, registered=contract.TARGET_REGISTERED),
    )
    for row in FROZEN["migration_plan"]:
        assert hashlib.sha256((ROOT / "database" / row["file"]).read_bytes()).hexdigest() == row["sha256"]
    # The historical admission remains pinned even after a newer policy ships.
    contract.validate_acl(ap, FROZEN["normal_policy"], digest=acl.canonical, require=acl.require)
    current_policy = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())
    assert current_policy != FROZEN["normal_policy"]
    with pytest.raises(acl.Refused, match="exact reviewed V268 policy"):
        contract.validate_acl(ap, current_policy, digest=acl.canonical, require=acl.require)
    from review_release_contract import STAGES

    assert STAGES["V255"]["ledger"] == contract.BASE_LEDGER
    assert STAGES["V255"]["registered"] == contract.BASE_REGISTERED


@pytest.mark.parametrize(
    "fault",
    [
        "sql",
        "old_sql",
        "path",
        "extra",
        "duplicate",
        "pending_order",
        "skip",
        "old_checksum",
        "missing_old",
        "duplicate_registration",
        "source",
        "target",
        "scope",
        "product",
        "config",
        "agent",
        "running_jobs",
        "clients",
        "source_evidence",
    ],
)
@pytest.mark.parametrize("entry", ["inputs", "database_phase"])
def test_bad_transition_refuses_before_connection_or_candidate_loading(bound, monkeypatch, fault, entry):
    p, ap, binding, tmp = bound
    rows = deepcopy(FROZEN["migration_plan"])
    if fault == "sql":
        rows[-1]["sha256"] = "0" * 64
    elif fault == "old_sql":
        rows[0]["sha256"] = "0" * 64
    elif fault == "path":
        rows[-1]["file"] = "migrations/elsewhere.sql"
    elif fault == "extra":
        rows.append(dict(key="V264", file="extra.sql", sha256="0" * 64))
    elif fault == "duplicate":
        rows.append(deepcopy(rows[-1]))
    elif fault == "pending_order":
        p["pending"].reverse()
    elif fault == "skip":
        p["pending"].remove("V259")
    elif fault == "old_checksum":
        p["expected_ledger"]["checksums"]["V255"] = "0" * 64
    elif fault == "missing_old":
        del p["expected_ledger"]["checksums"]["V255"]
    elif fault == "duplicate_registration":
        p["expected_ledger"]["registered"].append("V255")
    elif fault == "source":
        p["source_schema"] = "V254"
    elif fault == "target":
        p["target_schema"] = "V261"
    elif fault == "scope":
        p["release_scope"] = ["anything"]
    elif fault == "product":
        p["product_version"] = "1.2.1"
    elif fault == "config":
        p["prepared_configuration_delta"] = {}
    elif fault == "agent":
        p["agent_stage"] = {}
    elif fault == "running_jobs":
        p["expected_ledger"]["running_jobs"] = 1
    elif fault == "clients":
        p["supported_mini_versions"].append("1.0.0")
    elif fault == "source_evidence":
        p["candidate"]["backend"]["sha256"] = "b" * 64
    p["migration_plan"] = write(tmp / "migrations.json", rows)
    binding = write(Path(binding["path"]), p)

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid source reached module loading")

    monkeypatch.setattr(forward, "load", forbidden)
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        if entry == "inputs":
            forward.inputs(binding)
        else:
            asyncio.run(forward.database_phase(binding))


@pytest.mark.parametrize(
    "fault", ["policy", "matrix", "missing_function", "rls", "registration", "missing_gate", "changed_gate"]
)
@pytest.mark.parametrize("entry", ["inputs", "database_phase", "acl"])
def test_exact_acl_and_source_closure_cannot_be_relaxed(bound, monkeypatch, fault, entry):
    p, ap, binding, tmp = bound
    policy = deepcopy(FROZEN["normal_policy"])
    if fault == "policy":
        policy["migration_fingerprint"] = contract.BASE_LEDGER
    elif fault == "matrix":
        policy["services"]["worker"]["functions"].append(
            "security.knowledge_acl_candidates(uuid,text,text,integer,integer)"
        )
    elif fault == "missing_function":
        policy["services"]["api"]["functions"].remove(
            "security.knowledge_acl_candidates(uuid,text,text,integer,integer)"
        )
    elif fault == "rls":
        policy["rls_fingerprint"] = "0" * 64
    elif fault == "registration":
        ap["expected_registered_versions"].remove(contract.PENDING[-1])
    elif fault == "missing_gate":
        for key in list(ap["source_files"]):
            if key.endswith("release122_contract.py"):
                del ap["source_files"][key]
    elif fault == "changed_gate":
        for key in list(ap["source_files"]):
            if key.endswith("release122_contract.py"):
                ap["source_files"][key] = "0" * 64
    ap["policy_ref"] = write(tmp / "policy.json", policy)
    p["acl_plan"] = write(tmp / "acl.json", ap)
    binding = write(Path(binding["path"]), p)

    def forbidden(*args, **kwargs):
        raise AssertionError("Bad ACL reached module loading")

    monkeypatch.setattr(forward, "load", forbidden)
    with pytest.raises((forward.Refused, acl.Refused)):
        if entry == "inputs":
            forward.inputs(binding)
        elif entry == "acl":
            acl.load_plan(p["acl_plan"])
        else:
            asyncio.run(forward.database_phase(binding))


@pytest.mark.parametrize(
    "fault", ["partial", "old_checksum", "missing_registration", "duplicate_registration", "foreign_registration"]
)
def test_incomplete_or_rewritten_final_state_is_not_accepted(fault):
    final = target()
    if fault == "partial":
        del final["checksums"][contract.PENDING[-1]]
    elif fault == "old_checksum":
        final["checksums"]["V255"] = "0" * 64
    elif fault == "missing_registration":
        final["registered"].remove(contract.PENDING[-1])
    elif fault == "duplicate_registration":
        final["registered"].append("V255")
    elif fault == "foreign_registration":
        final["registered"].append("V264")
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        forward.verify_release_ledger(plan(), FROZEN["migration_plan"], final, final=True)


def test_v255_code_bundle_cannot_be_switched_onto_v262():
    old = dict(database={"ledger_sha256": contract.BASE_LEDGER}, configuration={}, supported_mini_versions=["1.2.1"])
    live = dict(database={"ledger_sha256": contract.TARGET_LEDGER}, configuration={})
    with pytest.raises(forward.original.Refused, match="DATABASE_MISMATCH"):
        forward.original.guard(old, live, ["1.2.1"])


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "source_sql_changed",
        "live_prefix_changed",
        "apply_unknown",
        "final_still255",
        "final_extra_registered",
        "old_row_rewritten",
    ],
)
def test_peer_phase_preserves_pre_sql_guards_and_uncertain_commit_receipts(bound, monkeypatch, fault):
    """Exercise the real peer orchestration with inert in-memory collaborators."""
    p, _, binding, _ = bound
    release = forward.ROOT / "releases/new"
    monkeypatch.setattr(forward, "__file__", str(release / "backend/deploy/release_forward_bundle.py"))
    monkeypatch.setattr(forward.os, "getuid", lambda: 12345)
    monkeypatch.setattr(forward.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=12345))
    calls = []
    before, after = deepcopy(p["expected_ledger"]), target()
    for state in (before, after):
        state["checksum_rows"] = [
            dict(
                migration_key=k,
                checksum_sha256=v,
                provenance="executed" if k in contract.PENDING else "adopted",
                applied_at="synthetic-original",
            )
            for k, v in state["checksums"].items()
        ]
        state["registered_rows"] = [dict(version=k, description="synthetic-original") for k in state["registered"]]
    if fault == "live_prefix_changed":
        before["checksums"]["V255"] = "0" * 64
    elif fault == "final_still255":
        after = deepcopy(before)
    elif fault == "final_extra_registered":
        after["registered"].append("V014")
    elif fault == "old_row_rewritten":
        next(row for row in after["checksum_rows"] if row["migration_key"] == "V255")["applied_at"] = "rewritten"

    class Connection:
        closed = False

        def is_in_transaction(self):
            return False

        def is_closed(self):
            return self.closed

        async def close(self):
            self.closed = True
            calls.append("closed")

        async def fetchrow(self, sql):
            return dict(database="sales_saas", oid=18684, session_user="postgres", current_user="postgres")

        async def fetchval(self, sql, *args):
            return sql.startswith("SELECT pg_try_advisory_lock")

    conn = Connection()

    async def connect(**kwargs):
        calls.append("connected")
        return conn

    async def observed_ledger(connection):
        calls.append("ledger")
        return deepcopy(after if "applied" in calls else before)

    async def apply(connection, item, root):
        # A durable unknown receipt must exist before the migration collaborator.
        receipt = json.loads((Path(binding["path"]).parent / "DATABASE.json").read_text())
        assert receipt["migrations"][-1] == dict(
            key=item.key, sha256=contract.SQL[item.key][1], commit_outcome="unknown"
        )
        assert all(r["commit_outcome"] == "acknowledged" for r in receipt["migrations"][:-1])
        calls.append("applied")
        if fault == "apply_unknown" and item.key == "V259":
            raise ConnectionError("Synthetic lost commit acknowledgement")
        return "applied"

    async def apply_acl(connection, **kwargs):
        calls.append("acl_applied")
        return dict(commit_attempted=True, commit_acknowledged=True, errors=[], cleanup_errors=[])

    async def verify_acl(connection, **kwargs):
        calls.append("acl_verified")
        return dict(status="verified_readonly", rolled_back=True, committed=False)

    items = [
        SimpleNamespace(
            key=key,
            checksum=("0" * 64 if fault == "source_sql_changed" and key == contract.PENDING[-1]
                      else contract.SQL[key][1]),
            path=release / "database" / contract.SQL[key][0],
        )
        for key in contract.PENDING
    ]
    migrator = SimpleNamespace(discover=lambda root: items, DEPLOY_LOCK=202609102101, apply=apply)
    mock_acl = SimpleNamespace(
        apply_release_permissions=apply_acl,
        verify_release_permissions=verify_acl,
        load_plan=acl.load_plan,
        dependencies=acl.dependencies,
    )

    def load(name, path):
        return migrator if name == "_forward_candidate_migrate" else mock_acl

    monkeypatch.setattr(forward, "load", load)
    monkeypatch.setattr(forward, "ledger", observed_ledger)
    monkeypatch.setattr(asyncpg, "connect", connect)
    result = asyncio.run(forward.database_phase(binding))
    assert result["closed"] is True
    assert result["success"] is (fault is None)
    if fault == "source_sql_changed":
        assert calls == [] and result["migrations"] == []
        assert result["errors"][0]["code"] == "Exact pending SQL source changed"
    elif fault == "live_prefix_changed":
        assert "applied" not in calls and result["migrations"] == []
        assert calls[-1] == "closed"
    elif fault == "apply_unknown":
        assert "acl_applied" not in calls
        assert len(result["migrations"]) == 4
        assert [r["commit_outcome"] for r in result["migrations"]] == ["acknowledged"] * 3 + ["unknown"]
        assert calls[-1] == "closed"
    else:
        assert calls == (["connected", "ledger"] + ["applied"] * len(contract.PENDING)
                         + ["acl_applied", "acl_verified", "ledger", "closed"])
        assert result["migrations"][0]["commit_outcome"] == "acknowledged"
        assert result["errors"] == [] if fault is None else bool(result["errors"])


@pytest.mark.parametrize("fault", [None, "backup", "migration_unknown", "acl", "new_health"])
def test_forward_retains_stop_backup_switch_and_no_old_restart_after_sql(bound, monkeypatch, fault):
    from tests.test_release_bundle_control import FixtureHost
    from tests.test_release_bundle_control import control as fixture_control

    p, _, binding, tmp = bound
    host = FixtureHost(tmp / "sandbox")
    host.db.update(schema="V255", ledger_sha256=contract.BASE_LEDGER)
    old_evidence = dict(
        status="passed",
        tested_mini_versions=["1.1.1"],
        tested_source_sha256=host.artifacts("old", "old")["backend"]["sha256"],
    )
    old = forward.original.make_bundle(host, "old", "old", "old", "1.2.1", ["1.1.1"], old_evidence)
    host.record_current(old)
    raw = deepcopy(FROZEN["expected_ledger"])
    host.database_raw = lambda: deepcopy(raw)
    candidate = host.artifacts("new", "new")
    evidence = {**old_evidence, "tested_source_sha256": candidate["backend"]["sha256"]}
    p.update(
        web="new",
        expected_web="old",
        expected_database=host.database(),
        expected_current_bundle={"bundle_id": "old"},
        active_mini_versions=["1.1.1"],
        supported_mini_versions=["1.1.1"],
        candidate=candidate,
        validation=write(tmp / "host-validation.json", evidence),
    )
    binding = write(Path(binding["path"]), p)
    # Only host subprocess/network edges are inert; use the real admission and orchestrator.
    monkeypatch.setattr(forward, "source_plan", lambda *_: None)
    monkeypatch.setattr(forward, "peer_access", lambda *_: None)
    host.fail_backup = fault == "backup"
    host.fail_health = fault == "new_health"
    peer_calls = []

    def database_phase(host, ref, folder):
        peer_calls.append("called")
        assert host.events[-2:] == ["stop", "backup"]
        assert json.loads((host.state / "TRANSACTION.json").read_text())["migration_attempted"] is True
        if fault == "migration_unknown":
            raw["checksums"]["V256"] = contract.SQL["V256"][1]
            raw["registered"].append("V256")
            host.db.update(schema="V256", ledger_sha256="partial")
            return dict(returncode=1, result=dict(success=False, closed=True, errors=["commit unknown"]))
        raw.clear()
        raw.update(target())
        host.db.update(schema="V268", ledger_sha256=contract.TARGET_LEDGER)
        return dict(
            returncode=1 if fault == "acl" else 0,
            result=dict(success=fault != "acl", closed=True, errors=["ACL failed"] if fault == "acl" else []),
        )

    if fault:
        with pytest.raises((forward.Refused, fixture_control.Refused)):
            forward.forward(host, binding, database_phase=database_phase)
    else:
        assert forward.forward(host, binding, database_phase=database_phase)["success"] is True
    receipt = json.loads((host.state / "TRANSACTION.json").read_text())
    if fault == "backup":
        assert peer_calls == [] and receipt["phase"] == "rolled_back"
        assert host.events[-2:] == ["start", "health-old"]
    elif fault:
        assert receipt["phase"] == "maintenance_required" and host.events[-1] == "stop"
        after_backup = host.events[host.events.index("backup") + 1 :]
        assert "health-old" not in after_backup
        assert host.db["schema"] != "V255"
        with pytest.raises(forward.Refused, match="Unfinished"):
            forward.forward(host, binding, database_phase=database_phase)
        assert len(peer_calls) == 1
    else:
        assert receipt["phase"] == "complete"
        assert forward.original.pointer(host.root) == forward.original.pointer(host.web) == "new"
        assert json.loads((host.state / "CURRENT.json").read_text()) == {"bundle_id": "offline122"}
        assert host.db["schema"] == "V268"
    assert host.configuration() == old["configuration"]


def test_prior_v255_admission_and_dependency_contract_remain_separate():
    before = deepcopy(FROZEN["expected_ledger"])
    del before["checksums"]["V255"]
    before["registered"].remove("V255")
    prior_plan = dict(
        product_version="1.2.1",
        release_scope=["review_okr_1.2.1"],
        source_schema="V254",
        target_schema="V255",
        pending=["V255"],
        expected_ledger=before,
    )
    rows = [row for row in FROZEN["migration_plan"] if row["key"] not in contract.PENDING]
    assert forward.verify_release_ledger(prior_plan, rows, before) == ["V255"]
    assert forward.verify_release_ledger(prior_plan, rows, FROZEN["expected_ledger"], final=True) == []
    assert acl.dependency_contract(prior_plan) == acl.DEPENDENCIES
    assert acl.dependency_contract(plan()) == contract.DEPENDENCIES
    assert acl.DEPENDENCIES["runtime_roles"] != contract.DEPENDENCIES["runtime_roles"]
