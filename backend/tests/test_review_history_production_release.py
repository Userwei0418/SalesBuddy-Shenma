"""Exact Review sparse-increment admission; all host/DB/network operations stay inert."""

# Standalone deployment modules share their reviewed import directory.
# ruff: noqa: E402
import asyncio
import hashlib
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "backend/deploy"
sys.path.insert(0, str(DEPLOY))
import release120_production_acl as acl
import release_forward_bundle as forward
import review_release_contract as contract

FROZEN = json.loads((Path(__file__).with_name("fixtures") / "review_history_release.frozen.json").read_text())
MIGRATIONS = FROZEN["migration_plan"]
PRIOR = dict(
    checksums={row["key"]: row["sha256"] for row in MIGRATIONS if row["key"] != "V253"},
    registered=FROZEN["source_registered"],
    running_jobs=0,
)
FINAL = dict(
    checksums={row["key"]: row["sha256"] for row in MIGRATIONS},
    registered=sorted([*PRIOR["registered"], "V253"]),
    running_jobs=0,
)
V254_ROW = {
    "key": "V254",
    "file": "migrations/V254__review_update_activity.sql",
    "sha256": "a4f17558bffcc142b39a3ee9da74f89b5d70d761fba1635491bfacb3c7489e7e",
}
MIGRATIONS254 = [*MIGRATIONS, V254_ROW]
PRIOR254 = deepcopy(FINAL)
FINAL254 = dict(
    checksums={row["key"]: row["sha256"] for row in MIGRATIONS254},
    registered=sorted([*FINAL["registered"], "V254"]),
    running_jobs=0,
)
ZERO = json.loads((Path(__file__).with_name("fixtures") / "review_release_contract.frozen.json").read_text())[
    "zero_target_increment"
]
MIGRATIONS255 = [*MIGRATIONS254, ZERO["migration"]]
FINAL255 = dict(
    checksums={row["key"]: row["sha256"] for row in MIGRATIONS255},
    registered=sorted([*FINAL254["registered"], "V255"]),
    running_jobs=0,
)
POLICY255 = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("History admission test attempted a real DB/network operation")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(asyncpg, "create_pool", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def plan():
    return dict(
        product_version="1.2.1",
        release_scope=contract.RELEASE_SCOPE.copy(),
        source_schema="V250",
        target_schema="V253",
        pending=["V253"],
        expected_ledger=deepcopy(PRIOR),
    )


def write(path, value):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return forward.file_ref(path)


def test_exact_sparse_increment_and_final_ledger():
    assert len(PRIOR["checksums"]) == 211 and len(PRIOR["registered"]) == 242
    assert len(FINAL["checksums"]) == 212 and len(FINAL["registered"]) == 243
    assert forward.original.digest({k: PRIOR[k] for k in ("checksums", "registered")}) == (
        "536456487668c39e9f000b5302dde6bb7cfb7fe5fe4c6364e83a1a69ffd006c0"
    )
    assert forward.original.digest({k: FINAL[k] for k in ("checksums", "registered")}) == (
        "2acc3844f37deac370bf948bbe6337ec423505ee5fe285ce4047027ae4796e65"
    )
    assert forward.verify_release_ledger(plan(), MIGRATIONS, PRIOR) == ["V253"]
    assert forward.verify_release_ledger(plan(), MIGRATIONS, FINAL, final=True) == []


def test_exact_v253_to_v254_activity_increment_and_final_ledger():
    p = dict(
        product_version="1.2.1",
        release_scope=contract.RELEASE_SCOPE.copy(),
        source_schema="V253",
        target_schema="V254",
        pending=["V254"],
        expected_ledger=deepcopy(PRIOR254),
    )
    assert len(PRIOR254["checksums"]) == 212 and len(PRIOR254["registered"]) == 243
    assert len(FINAL254["checksums"]) == 213 and len(FINAL254["registered"]) == 244
    assert forward.original.digest({k: FINAL254[k] for k in ("checksums", "registered")}) == (
        "891757673141949c4cadb5a35aa8dac5e29d260d6d7934f3c9b51946443b3521"
    )
    assert forward.verify_release_ledger(p, MIGRATIONS254, PRIOR254) == ["V254"]
    assert forward.verify_release_ledger(p, MIGRATIONS254, FINAL254, final=True) == []


def zero_plan():
    return dict(
        product_version="1.2.1", release_scope=contract.RELEASE_SCOPE.copy(),
        source_schema="V254", target_schema="V255", pending=["V255"],
        expected_ledger=deepcopy(FINAL254),
    )


def test_v255_binds_exact_sql_and_preserves_deployed_v254_permissions():
    row = ZERO["migration"]
    assert hashlib.sha256((ROOT / "database" / row["file"]).read_bytes()).hexdigest() == row["sha256"]
    assert ZERO["source_policy_canonical_sha256"] == contract.STAGES["V254"]["policy"]
    policy = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())
    assert policy["schema"] == "V255"
    assert policy["migration_fingerprint"] == forward.original.digest(FINAL255["checksums"])
    assert policy["rls_fingerprint"] == ZERO["source_policy_metadata"]["rls_fingerprint"]
    assert acl.canonical(policy["services"]) == ZERO["services_canonical_sha256"]
    # Restoring only schema/ledger metadata must recreate the exact accepted
    # V254 policy hash. No service grant or unrelated policy change can pass.
    prior_policy = {**policy, **ZERO["source_policy_metadata"]}
    assert acl.canonical(prior_policy) == ZERO["source_policy_canonical_sha256"]
    assert acl.canonical(policy) == contract.STAGES["V255"]["policy"]
    assert len(FINAL255["checksums"]) == 214 and len(FINAL255["registered"]) == 245
    assert forward.original.digest({k: FINAL255[k] for k in ("checksums", "registered")}) == (
        "7215589c6043ef298952fc30e8f0edc8a063b58e08b5d4384968bdaf7a2c10cb"
    )
    assert forward.verify_release_ledger(zero_plan(), MIGRATIONS255, FINAL254) == ["V255"]
    assert forward.verify_release_ledger(zero_plan(), MIGRATIONS255, FINAL255, final=True) == []


@pytest.mark.parametrize("fault", ["replay", "downgrade", "skip_v254", "final_missing", "final_foreign"])
def test_v255_cannot_replay_downgrade_skip_or_accept_partial_final_ledger(fault):
    p, observed, final = zero_plan(), deepcopy(FINAL254), False
    if fault == "replay":
        observed = deepcopy(FINAL255)
    elif fault == "downgrade":
        p.update(source_schema="V255", target_schema="V254", expected_ledger=FINAL255, pending=[])
    elif fault == "skip_v254":
        p.update(source_schema="V253", expected_ledger=FINAL, pending=["V254", "V255"])
        observed = deepcopy(FINAL)
    else:
        observed, final = deepcopy(FINAL255), True
        if fault == "final_missing":
            observed["checksums"].pop("V255")
        else:
            observed["registered"].append("V252")
    with pytest.raises(forward.Refused):
        forward.verify_release_ledger(p, MIGRATIONS255, observed, final=final)


@pytest.fixture(params=["V253", "V255"])
def bound(tmp_path, monkeypatch, request):
    is_zero = request.param == "V255"
    final = FINAL255 if is_zero else FINAL
    migrations = MIGRATIONS255 if is_zero else MIGRATIONS
    policy = POLICY255 if is_zero else FROZEN["policy"]
    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(forward, "ROOT", tmp_path / "host")
    deploy = forward.ROOT / "releases/new/backend/deploy"
    deploy.mkdir(parents=True)
    for name in contract.GATE_FILES:
        (deploy / name).write_bytes((DEPLOY / name).read_bytes())
    paths = [
        *[DEPLOY / name for name in contract.GATE_FILES],
        *[deploy / name for name in contract.GATE_FILES],
        *[ROOT / "tools/installation" / (name + ".py") for name in acl.DEPENDENCIES],
    ]
    ap = dict(
        format="salesbuddy-release120-production-acl-v1",
        mode="normal",
        roles=acl.ROLES.copy(),
        target=dict(database="sales_saas", oid=18684),
        attempt_id="offline-history",
        window_id="offline-history",
        release_scope=contract.RELEASE_SCOPE.copy(),
        expected_registered_versions=final["registered"],
        policy_ref=write(tmp_path / "policy.json", policy),
        source_files={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
    )
    p = dict(
        **(zero_plan() if is_zero else plan()),
        expected_database={"name": "sales_saas"},
        bundle_id="offline-history",
        backend="new",
        web="new-web",
        expected_backend="c415",
        expected_web="3ef",
        migration_plan=write(tmp_path / "migrations.json", migrations),
        acl_plan=write(tmp_path / "acl.json", ap),
        acl_source=forward.file_ref(deploy / "release120_production_acl.py"),
        validation=write(
            tmp_path / "validation.json",
            dict(status="passed", tested_source_sha256="a" * 64, tested_mini_versions=["1.1.1", "1.2.0", "1.2.1"]),
        ),
        candidate={"backend": {"sha256": "a" * 64}},
        supported_mini_versions=["1.1.1", "1.2.0", "1.2.1"],
    )
    ref = write(forward.ROOT / "bundles/forward/offline-history/database/PLAN.json", p)
    return p, ap, ref, tmp_path


def test_existing_forward_and_acl_entry_accept_same_frozen_increment(bound):
    p, ap, ref, _ = bound
    is_zero = p["target_schema"] == "V255"
    assert forward.inputs(ref)[1] == (MIGRATIONS255 if is_zero else MIGRATIONS)
    assert acl.load_plan(p["acl_plan"]) == (ap, POLICY255 if is_zero else FROZEN["policy"])
    assert acl.schema_contract(ap)[1]["count"] == (214 if is_zero else 212)


@pytest.mark.parametrize(
    "fault",
    [
        "v251",
        "v252",
        "extra_pending",
        "sql",
        "sql_path",
        "old_checksum",
        "historical_registration",
        "duplicate_registration",
        "wrong_source",
        "empty_pending",
        "config",
        "agent",
        "old_policy",
        "extra_grant",
        "policy_rls",
        "acl_target",
        "missing_gate",
        "gate_bytes",
        "unverified_source",
        "missing_client",
        "same_web",
    ],
)
def test_bad_increment_refused_before_database_connect(bound, fault):
    p, ap, ref, tmp = bound
    migrations, policy = forward.document(p["migration_plan"]), forward.document(ap["policy_ref"])
    if fault in ("v251", "v252"):
        migrations.insert(-1, dict(key=fault.upper(), file=f"migrations/{fault.upper()}__foreign.sql", sha256="a" * 64))
        p["pending"].insert(0, fault.upper())
    elif fault == "extra_pending":
        p["pending"].append("V252")
    elif fault == "sql":
        migrations[-1]["sha256"] = "0" * 64
    elif fault == "sql_path":
        migrations[-1]["file"] = "migrations/other.sql"
    elif fault == "old_checksum":
        p["expected_ledger"]["checksums"]["V250"] = "0" * 64
    elif fault == "historical_registration":
        p["expected_ledger"]["registered"][0] = "V014"
    elif fault == "duplicate_registration":
        p["expected_ledger"]["registered"].append("V250")
    elif fault == "wrong_source":
        p["source_schema"] = "V246"
    elif fault == "empty_pending":
        p["pending"] = []
    elif fault == "config":
        p["prepared_configuration_delta"] = {}
    elif fault == "agent":
        p["agent_stage"] = {}
    elif fault == "old_policy":
        policy["schema"] = "V250"
    elif fault == "extra_grant":
        policy["services"]["worker"]["tables"]["ops.review_metric_snapshot"] = ["SELECT"]
    elif fault == "policy_rls":
        policy["rls_fingerprint"] = "0" * 64
    elif fault == "acl_target":
        ap["expected_registered_versions"].remove("V253")
    elif fault == "missing_gate":
        ap["source_files"] = {
            k: v for k, v in ap["source_files"].items() if not k.endswith("review_release_contract.py")
        }
    elif fault == "gate_bytes":
        (forward.ROOT / "releases/new/backend/deploy/review_release_contract.py").write_text("changed")
    elif fault == "unverified_source":
        p["candidate"]["backend"]["sha256"] = "0" * 64
    elif fault == "missing_client":
        p["supported_mini_versions"].append("unverified")
    elif fault == "same_web":
        p["web"] = p["expected_web"]
    ap["policy_ref"] = write(tmp / "policy.json", policy)
    p["acl_plan"] = write(tmp / "acl.json", ap)
    p["migration_plan"] = write(tmp / "migrations.json", migrations)
    ref = write(Path(ref["path"]), p)
    # Real entrypoint reaches its inputs guard before constructing any DB receipt
    # or connecting. The autouse guard rejects any accidental DB/network access.
    with pytest.raises((forward.Refused, acl.Refused)):
        asyncio.run(forward.database_phase(ref))
    assert not (Path(ref["path"]).parent / "DATABASE.json").exists()


def test_post_migration_ledger_cannot_be_replayed_or_downgraded():
    with pytest.raises(forward.Refused):
        forward.verify_release_ledger(plan(), MIGRATIONS, FINAL)
    p = plan()
    p.update(source_schema="V253", target_schema="V250", expected_ledger=FINAL, pending=[])
    with pytest.raises(forward.Refused):
        forward.verify_release_ledger(p, MIGRATIONS[:-1], FINAL)


def test_final_ledger_rejects_missing_sql_or_foreign_registration():
    for change in ("missing", "foreign"):
        observed = deepcopy(FINAL)
        if change == "missing":
            observed["checksums"].pop("V253")
        else:
            observed["registered"].append("V252")
        with pytest.raises(forward.Refused):
            forward.verify_release_ledger(plan(), MIGRATIONS, observed, final=True)
