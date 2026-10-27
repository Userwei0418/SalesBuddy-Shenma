"""Exact V268 to V275 knowledge admission and failure receipts, with no host/DB/network execution."""

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
import release122_knowledge_contract as contract
import release_forward_bundle as forward
import release_upgrade as upgrade

FROZEN = json.loads((Path(__file__).with_name("fixtures") / "release122_knowledge_contract.frozen.json").read_text())


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
        source_schema="V268",
        target_schema="V275",
        pending=contract.PENDING.copy(),
        expected_ledger=deepcopy(FROZEN["expected_ledger"]),
        active_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
        supported_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
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
        attempt_id="offline122knowledge",
        window_id="offline122knowledge",
        release_scope=contract.RELEASE_SCOPE.copy(),
        expected_registered_versions=target()["registered"],
        policy_ref=write(tmp_path / "policy.json", FROZEN["normal_policy"]),
        source_files={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    )
    p = dict(
        **plan(),
        expected_database={"name": "sales_saas"},
        bundle_id="offline122knowledge",
        backend="new",
        web="new-web",
        expected_backend="old",
        expected_web="old-web",
        migration_plan=write(tmp_path / "migrations.json", FROZEN["migration_plan"]),
        acl_plan=write(tmp_path / "acl.json", ap),
        acl_source=forward.file_ref(deploy / "release120_production_acl.py"),
        validation=write(
            tmp_path / "validation.json",
            dict(
                status="passed",
                tested_source_sha256="a" * 64,
                tested_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
            ),
        ),
        candidate={"backend": {"sha256": "a" * 64}},
    )
    binding = write(forward.ROOT / "bundles/forward/offline122knowledge/database/PLAN.json", p)
    return p, ap, binding, tmp_path


def test_fixed_candidate_matches_frozen_sql_policy_and_complete_production_prefix(bound):
    p, ap, binding, _ = bound
    assert forward.inputs(binding) == (p, FROZEN["migration_plan"], forward.document(p["validation"]))
    assert acl.load_plan(p["acl_plan"]) == (ap, FROZEN["normal_policy"])
    assert forward.verify_release_ledger(p, FROZEN["migration_plan"], p["expected_ledger"]) == contract.PENDING
    assert forward.verify_release_ledger(p, FROZEN["migration_plan"], target(), final=True) == []
    assert acl.schema_contract(ap) == (
        "V275",
        dict(count=234, ledger=contract.TARGET_LEDGER, registered=contract.TARGET_REGISTERED),
    )
    for row in FROZEN["migration_plan"]:
        assert hashlib.sha256((ROOT / "database" / row["file"]).read_bytes()).hexdigest() == row["sha256"]
    # Historical V275 admission stays frozen. The independently reviewed V278
    # ACL repair adds only API INSERT on the multi-opportunity visit link table.
    current = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())
    assert current["services"]["api"]["tables"]["activity.visit_opportunity"] == ["SELECT", "INSERT"]
    current["services"]["api"]["tables"]["activity.visit_opportunity"] = ["SELECT"]
    assert current == FROZEN["normal_policy"]
    contract.validate_acl(ap, FROZEN["normal_policy"], digest=acl.canonical, require=acl.require)
    import release122_contract as prior

    assert prior.TARGET_LEDGER == contract.BASE_LEDGER
    assert prior.TARGET_REGISTERED == contract.BASE_REGISTERED


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
        "stale_v274_candidate",
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
        "active_missing",
        "active_duplicate",
        "client_evidence_missing",
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
        rows.append(dict(key="V276", file="extra.sql", sha256="0" * 64))
    elif fault == "duplicate":
        rows.append(deepcopy(rows[-1]))
    elif fault == "pending_order":
        p["pending"].reverse()
    elif fault == "skip":
        p["pending"].remove("V270")
    elif fault == "stale_v274_candidate":
        rows.pop()
        p["pending"].pop()
        p["target_schema"] = "V274"
    elif fault == "old_checksum":
        p["expected_ledger"]["checksums"]["V268"] = "0" * 64
    elif fault == "missing_old":
        del p["expected_ledger"]["checksums"]["V268"]
    elif fault == "duplicate_registration":
        p["expected_ledger"]["registered"].append("V268")
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
    elif fault == "active_missing":
        p["active_mini_versions"].pop()
    elif fault == "active_duplicate":
        p["active_mini_versions"].append(p["active_mini_versions"][0])
    elif fault == "client_evidence_missing":
        value = forward.document(p["validation"])
        value["tested_mini_versions"].pop()
        p["validation"] = write(tmp / "validation.json", value)
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
            if key.endswith("release122_knowledge_contract.py"):
                del ap["source_files"][key]
    elif fault == "changed_gate":
        for key in list(ap["source_files"]):
            if key.endswith("release122_knowledge_contract.py"):
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
        final["checksums"]["V268"] = "0" * 64
    elif fault == "missing_registration":
        final["registered"].remove(contract.PENDING[-1])
    elif fault == "duplicate_registration":
        final["registered"].append("V268")
    elif fault == "foreign_registration":
        final["registered"].append("V276")
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        forward.verify_release_ledger(plan(), FROZEN["migration_plan"], final, final=True)


def test_v268_code_bundle_cannot_be_switched_onto_v275():
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
        before["checksums"]["V268"] = "0" * 64
    elif fault == "final_still255":
        after = deepcopy(before)
    elif fault == "final_extra_registered":
        after["registered"].append("V014")
    elif fault == "old_row_rewritten":
        next(row for row in after["checksum_rows"] if row["migration_key"] == "V268")["applied_at"] = "rewritten"

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
        if fault == "apply_unknown" and item.key == "V270":
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
            checksum="0" * 64
            if fault == "source_sql_changed" and key == contract.PENDING[-1]
            else contract.SQL[key][1],
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
        assert len(result["migrations"]) == 2
        assert [r["commit_outcome"] for r in result["migrations"]] == ["acknowledged", "unknown"]
        assert calls[-1] == "closed"
    else:
        assert calls == ["connected", "ledger"] + ["applied"] * len(contract.PENDING) + [
            "acl_applied",
            "acl_verified",
            "ledger",
            "closed",
        ]
        assert result["migrations"][0]["commit_outcome"] == "acknowledged"
        assert result["errors"] == [] if fault is None else bool(result["errors"])


@pytest.mark.parametrize("fault", [None, "backup", "migration_unknown", "acl", "new_health"])
def test_forward_retains_stop_backup_switch_and_no_old_restart_after_sql(bound, monkeypatch, fault):
    from tests.test_release_bundle_control import FixtureHost
    from tests.test_release_bundle_control import control as fixture_control

    p, _, binding, tmp = bound
    host = FixtureHost(tmp / "sandbox")
    host.db.update(schema="V268", ledger_sha256=contract.BASE_LEDGER)
    old_evidence = dict(
        status="passed",
        tested_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
        tested_source_sha256=host.artifacts("old", "old")["backend"]["sha256"],
    )
    old = forward.original.make_bundle(
        host, "old", "old", "old", "1.2.2", contract.ACTIVE_MINI_VERSIONS.copy(), old_evidence
    )
    host.record_current(old)
    host.clients = lambda: contract.ACTIVE_MINI_VERSIONS.copy()
    raw = deepcopy(FROZEN["expected_ledger"])
    host.database_raw = lambda: deepcopy(raw)
    candidate = host.artifacts("new", "new")
    evidence = {**old_evidence, "tested_source_sha256": candidate["backend"]["sha256"]}
    p.update(
        web="new",
        expected_web="old",
        expected_database=host.database(),
        expected_current_bundle={"bundle_id": "old"},
        active_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
        supported_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
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
            raw["checksums"]["V269"] = contract.SQL["V269"][1]
            raw["registered"].append("V269")
            host.db.update(schema="V269", ledger_sha256="partial")
            return dict(returncode=1, result=dict(success=False, closed=True, errors=["commit unknown"]))
        raw.clear()
        raw.update(target())
        host.db.update(schema="V275", ledger_sha256=contract.TARGET_LEDGER)
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
        assert host.db["schema"] != "V268"
        with pytest.raises(forward.Refused, match="Unfinished"):
            forward.forward(host, binding, database_phase=database_phase)
        assert len(peer_calls) == 1
    else:
        assert receipt["phase"] == "complete"
        assert forward.original.pointer(host.root) == forward.original.pointer(host.web) == "new"
        assert json.loads((host.state / "CURRENT.json").read_text()) == {"bundle_id": "offline122knowledge"}
        assert host.db["schema"] == "V275"
    assert host.configuration() == old["configuration"]


def test_prior_v255_v268_admission_remains_exact_and_separate():
    import release122_contract as prior

    old = json.loads((Path(__file__).with_name("fixtures") / "release122_contract.frozen.json").read_text())
    before = deepcopy(old["expected_ledger"])
    prior_plan = dict(
        product_version="1.2.2",
        release_scope=prior.RELEASE_SCOPE.copy(),
        source_schema="V255",
        target_schema="V268",
        pending=prior.PENDING.copy(),
        expected_ledger=before,
    )
    assert forward.verify_release_ledger(prior_plan, old["migration_plan"], before) == prior.PENDING
    assert forward.verify_release_ledger(prior_plan, old["migration_plan"], FROZEN["expected_ledger"], final=True) == []
    assert acl.dependency_contract(prior_plan) == prior.DEPENDENCIES
    assert acl.dependency_contract(plan()) == contract.DEPENDENCIES
    with pytest.raises(forward.Refused):
        forward.verify_release_ledger(prior_plan, FROZEN["migration_plan"], before)
    with pytest.raises(forward.Refused):
        forward.verify_release_ledger(plan(), old["migration_plan"], FROZEN["expected_ledger"])


@pytest.mark.parametrize("source", ["V255", "V267", "V269", "V270", "V271", "V272", "V273", "V274", "V275"])
def test_only_exact_v268_source_is_admitted(source):
    value = plan()
    value["source_schema"] = source
    with pytest.raises(forward.Refused):
        contract.validate(value, FROZEN["migration_plan"], digest=forward.original.digest, require=forward.require)


def test_v274_adds_only_the_two_reviewed_api_functions_to_v272_service_matrix():
    services = deepcopy(FROZEN["normal_policy"]["services"])
    added = {
        "security.knowledge_source_files(uuid)",
        "security.knowledge_archive_source_files(uuid,integer,integer,bigint,jsonb)",
    }
    assert added <= set(services["api"]["functions"])
    services["api"]["functions"] = [f for f in services["api"]["functions"] if f not in added]
    assert forward.original.digest(services) == "bd1c4d1def6485e743ca5161d15fba921e9c317d653791a9faa45267bae8c03c"
    for service in FROZEN["normal_policy"]["services"].values():
        assert "knowledge.source_file_binding" not in service["tables"]
        assert not any("knowledge_import_before_source_files" in f for f in service["functions"])


def test_v275_keeps_the_reviewed_v274_service_matrix_and_rls():
    policy = FROZEN["normal_policy"]
    assert policy["schema"] == "V275"
    assert forward.original.digest(policy["services"]) == (
        "d3243ba38f3f378bcd29351958bd499ed2bcec30ec4a145f89484e57ed00d854"
    )
    assert policy["rls_fingerprint"] == (
        "ff383aaf88e8c1f2cbca9f9e0b230fb748d04f88a1892b0cddfbdf005420ff6e"
    )
