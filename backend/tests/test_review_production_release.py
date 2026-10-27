"""Offline admission using frozen policy/ledger evidence and synthetic host plans."""
# Deployment modules are standalone scripts; add their directory before imports.
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
import identity_release_contract as identity
import release120_production_acl as acl
import release_forward_bundle as forward
import release_upgrade as upgrade
import review_release_contract as contract

BASE = json.loads((Path(__file__).with_name("fixtures") / "release121_contract.synthetic.json").read_text())
FROZEN = json.loads((Path(__file__).with_name("fixtures") / "review_release_contract.frozen.json").read_text())
MIGRATIONS = [
    *BASE["migration_plan"],
    {"key": "V239", "file": identity.SQL["V239"][0], "sha256": identity.SQL["V239"][1]},
]
PRIOR = {
    "checksums": {row["key"]: row["sha256"] for row in MIGRATIONS},
    "registered": sorted([*BASE["expected_ledger"]["registered"], "V234", "V239"]),
    "running_jobs": 0,
}


def test_frozen_v240_uses_actual_sql_ledger_and_exact_api_policy():
    stage = contract.STAGES["V240"]
    path, expected_sha = stage["sql"]
    actual_sha = hashlib.sha256((ROOT / "database" / path).read_bytes()).hexdigest()
    assert actual_sha == expected_sha
    migrations = [*MIGRATIONS, dict(key="V240", file=path, sha256=actual_sha)]
    registered = sorted([*PRIOR["registered"], "V240"])
    p = plan(ledgers={"V239": PRIOR})
    assert forward.verify_release_ledger(p, migrations, PRIOR) == ["V240"]
    final = dict(checksums={r["key"]: r["sha256"] for r in migrations}, registered=registered)
    assert forward.verify_release_ledger(p, migrations, final, final=True) == []

    policy = deepcopy(FROZEN["policies"]["V240"])
    ap = dict(release_scope=contract.RELEASE_SCOPE.copy(), expected_registered_versions=registered)
    contract.validate_acl(ap, policy, digest=acl.canonical, require=acl.require)
    assert policy["rls_fingerprint"] == "55839e43cb6aab54d61fcb848f3a5780734d0658eb8d215bfb09f813fa91d57a"
    prior = deepcopy(policy)
    prior.update(
        schema="V239",
        migration_fingerprint=identity.TARGET_LEDGER,
        rls_fingerprint="7c76778c9b7290293abbb9df03b6d31ddc0a6568b405faf8d9e316be09cf8686",
    )
    api = prior["services"]["api"]
    assert api["tables"].pop("crm.review_metric") == ["SELECT"]
    for signature in ("security.save_review_metric(text,jsonb,integer)", "security.review_target_money_json(jsonb)"):
        api["functions"].remove(signature)
    assert acl.canonical(prior) == identity.POLICY_SHA
    identity.validate_acl(
        dict(release_scope=identity.RELEASE_SCOPE.copy(), expected_registered_versions=PRIOR["registered"]),
        prior,
        digest=acl.canonical,
        require=acl.require,
    )


@pytest.mark.parametrize(
    ("schema", "filename", "expected_sha", "count", "registered_count"),
    [
        (
            "V245",
            "migrations/V245__work_report_review_freeze.sql",
            "36cab1055618b765dc822bdd69fa15f8b73e262316d31481969728adea06d19b",
            209,
            240,
        ),
        (
            "V246",
            "migrations/V246__review_worker_fact_reads.sql",
            "947f157f37bf721bef5b0e8bbae5e28cb223a4a82b07fb6db81c3a3f07611d33",
            210,
            241,
        ),
    ],
)
def test_candidate_stages_are_frozen(schema, filename, expected_sha, count, registered_count):
    stage = contract.frozen_stage(schema, require=forward.require)
    assert stage["count"] == count
    assert stage["registered_count"] == registered_count
    assert stage["sql"] == (filename, expected_sha)
    assert hashlib.sha256((ROOT / "database" / filename).read_bytes()).hexdigest() == expected_sha


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline admission test attempted network/DB access")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(asyncpg, "create_pool", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def actual():
    plans, ledgers = {}, {"V239": deepcopy(PRIOR)}
    migrations = deepcopy(MIGRATIONS)
    for row in FROZEN["migration_plan"]:
        schema = row["key"]
        migrations.append(deepcopy(row))
        plans[schema] = deepcopy(migrations)
        ledgers[schema] = dict(
            checksums={item["key"]: item["sha256"] for item in migrations},
            registered=sorted([*PRIOR["registered"], *plans.keys()]),
            running_jobs=0,
        )
    return plans, deepcopy(FROZEN["policies"]), ledgers


@pytest.mark.parametrize("schema", ["V240", "V241", "V242", "V243", "V244"])
def test_actual_stage_sql_policy_and_preserved_production_history(actual, schema):
    plans, policies, ledgers = actual
    row = plans[schema][-1]
    assert hashlib.sha256((ROOT / "database" / row["file"]).read_bytes()).hexdigest() == row["sha256"]
    registered = ledgers[schema]["registered"]
    assert len(FROZEN["historical_registrations"]) == 33
    if schema in FROZEN["isolated_registered"]:
        assert set(registered) - set(FROZEN["isolated_registered"][schema]) == set(FROZEN["historical_registrations"])
    else:
        # D03 recorded complete checksums, but not isolated registrations.
        evidence = FROZEN["registration_evidence"][schema]
        assert registered == evidence["registered"]
        assert evidence["observed_schema"] == "V243" and evidence["v244_observed"] is False
    ap = dict(release_scope=contract.RELEASE_SCOPE.copy(), expected_registered_versions=registered)
    contract.validate_acl(ap, policies[schema], digest=acl.canonical, require=acl.require)
    p = plan(target=schema, ledgers=ledgers)
    assert forward.verify_release_ledger(p, plans[schema], ledgers["V239"]) == p["pending"]
    assert forward.verify_release_ledger(p, plans[schema], ledgers[schema], final=True) == []


def reviewed_v250_policy():
    """Apply the exact V245/V246 grant delta to the frozen V244 test baseline."""
    policy = deepcopy(FROZEN["policies"]["V244"])
    policy["migration_fingerprint"] = "e705b47e56df842e0f13cb4d4e819b7e69cc6f5bb307fd8a8be842182650d3bf"
    policy["schema"] = "V250"

    api = policy["services"]["api"]
    api["functions"] = sorted({*api["functions"], "security.work_report_review_access(jsonb)"})

    worker = policy["services"]["worker"]
    worker["functions"] = sorted(
        {
            *worker["functions"],
            "security.work_report_review_access(jsonb)",
            "security.target_scope_read(text,uuid,uuid,text)",
            "security.target_scope_write(text,uuid,uuid,text)",
            "security.review_objective_readable(uuid)",
            "security.task_alignment_read(uuid)",
        }
    )
    worker["tables"].update(
        {
            table: ["SELECT"]
            for table in (
                "crm.sales_target",
                "crm.review_metric",
                "crm.review_objective",
                "crm.review_key_result",
                "crm.review_key_result_progress",
            )
        }
    )
    worker["tables"] = dict(sorted(worker["tables"].items()))
    return policy


def test_product_policy_is_exact_reviewed_source_stage():
    raw = (ROOT / "tools/installation/runtime_permissions.json").read_bytes()
    policy = json.loads(raw)
    if policy["schema"] in ("V254", "V255"):
        stage = contract.STAGES[policy["schema"]]
        assert policy["migration_fingerprint"] == stage["ledger"]
        assert acl.canonical(policy["services"]) == stage["services"]
        assert acl.canonical(policy) == stage["policy"]
        activity = "security.review_update_activity(text,uuid[],timestamp with time zone,timestamp with time zone)"
        assert activity in policy["services"]["api"]["functions"]
        assert activity in policy["services"]["worker"]["functions"]
        return
    if policy["schema"] == "V253":
        history = json.loads((Path(__file__).with_name("fixtures") / "review_history_release.frozen.json").read_text())
        assert policy == history["policy"]
        assert acl.canonical(policy) == contract.STAGES["V253"]["policy"]
        return
    expected = reviewed_v250_policy()
    assert policy == expected
    assert policy["migration_fingerprint"] == expected["migration_fingerprint"]
    assert policy["schema"] == "V250"
    assert acl.canonical(policy["services"]) == contract.STAGES["V246"]["services"]
    v246 = {**policy, "schema": "V246", "migration_fingerprint": contract.STAGES["V246"]["ledger"]}
    assert acl.canonical(v246) == contract.STAGES["V246"]["policy"]


def test_product_policy_binds_complete_source_ledger():
    database = ROOT / "database"
    files = [database / "baseline/V001__current_schema.sql", *sorted((database / "seeds").glob("S*.sql")),
             *database.glob("V*.sql"), *(database / "migrations").glob("V*.sql")]
    ledger = {("BASELINE_V1" if p.parent.name == "baseline" else p.stem.split("__")[0]):
              hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    policy = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())
    assert policy["schema"] in ("V250", "V253", "V254", "V255")
    expected_count = {"V250": 211, "V253": 212, "V254": 213, "V255": 214}[policy["schema"]]
    assert len(ledger) == len(files) == expected_count
    assert policy["migration_fingerprint"] == acl.canonical(ledger)
    assert ledger["V250"] == "10f954e4d1193dd8c1c6b5875a97b2338fb2f8b623b062d85593085bb371135b"
    if policy["schema"] == "V253":
        assert not {"V251", "V252"} & ledger.keys()
        assert ledger["V253"] == contract.STAGES["V253"]["sql"][1]
    if policy["schema"] in ("V254", "V255"):
        assert not {"V251", "V252"} & ledger.keys()
        assert ledger["V253"] == contract.STAGES["V253"]["sql"][1]
        assert ledger["V254"] == contract.STAGES["V254"]["sql"][1]
    if policy["schema"] == "V255":
        assert ledger["V255"] == contract.STAGES["V255"]["sql"][1]


def test_v244_registration_expectation_preserves_complete_live_v243_prefix(actual):
    _, _, ledgers = actual
    expected = FROZEN["registration_evidence"]["V244"]
    assert "V244" not in FROZEN["isolated_registered"]
    assert expected["registered"] == sorted([*ledgers["V243"]["registered"], "V244"])
    assert len(expected["registered"]) == 239
    assert len(ledgers["V244"]["checksums"]) == 208


@pytest.mark.parametrize("source,target", [("V243", "V240"), ("V244", "V243")])
def test_actual_core_downgrade_refused(actual, source, target):
    plans, _, ledgers = actual
    with pytest.raises(forward.Refused, match="downgrade"):
        contract.validate_migrations(
            plans[target], ledgers[source]["checksums"], ledgers[source]["registered"],
            source, target, [], digest=acl.canonical, require=forward.require,
        )


def write(path, value):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return forward.file_ref(path)


@pytest.fixture
def frozen(monkeypatch):
    # Simulated reviewed stages test the guards; these do not freeze product SQL.
    stages, plans, policies, ledgers = {}, {}, {}, {"V239": deepcopy(PRIOR)}
    migrations = deepcopy(MIGRATIONS)
    policy = deepcopy(BASE["normal_policy"])
    for number in (240, 241, 242, 243):
        schema = f"V{number}"
        row = dict(key=schema, file=f"migrations/{schema}__synthetic.sql", sha256=str(number)[-1] * 64)
        migrations.append(row)
        checksums = {item["key"]: item["sha256"] for item in migrations}
        registered = sorted([*PRIOR["registered"], *[f"V{n}" for n in range(240, number + 1)]])
        policy.update(
            schema=schema,
            migration_fingerprint=forward.original.digest(checksums),
            rls_fingerprint=str(number)[-1] * 64,
        )
        plans[schema], policies[schema] = deepcopy(migrations), deepcopy(policy)
        ledgers[schema] = dict(checksums=checksums, registered=registered, running_jobs=0)
        stages[schema] = dict(
            count=len(checksums),
            ledger=forward.original.digest(checksums),
            registered_count=len(registered),
            registered=forward.original.digest(registered),
            sql=(row["file"], row["sha256"]),
            services=acl.canonical(policy["services"]),
            policy=acl.canonical(policy),
        )
    monkeypatch.setattr(contract, "STAGES", stages)
    return plans, policies, ledgers


def plan(source="V239", target="V240", *, ledgers):
    return dict(
        product_version="1.2.1",
        release_scope=contract.RELEASE_SCOPE.copy(),
        source_schema=source,
        target_schema=target,
        pending=[f"V{n}" for n in range(int(source[1:]) + 1, int(target[1:]) + 1)],
        expected_ledger=deepcopy(ledgers[source]),
    )


@pytest.mark.parametrize("source,target", [
    ("V239", "V240"), ("V240", "V241"), ("V241", "V242"), ("V242", "V243"),
    ("V239", "V243"), ("V240", "V243"),
    ("V239", "V244"), ("V243", "V244"),
])
def test_only_frozen_complete_increment_and_final_ledger(actual, source, target):
    plans, _, ledgers = actual
    p = plan(source, target, ledgers=ledgers)
    assert forward.verify_release_ledger(p, plans[target], ledgers[source]) == p["pending"]
    assert forward.verify_release_ledger(p, plans[target], ledgers[target], final=True) == []


def test_unfrozen_or_incomplete_prefix_refused(frozen, monkeypatch):
    plans, _, ledgers = frozen
    monkeypatch.setitem(contract.STAGES, "V241", None)
    for target in ("V241", "V242", "V243"):
        with pytest.raises(forward.Refused, match="not frozen|not completely frozen"):
            forward.verify_release_ledger(plan(target=target, ledgers=ledgers), plans[target], PRIOR)


@pytest.fixture(params=[("V239", "V240"), ("V240", "V243"), ("V243", "V244")])
def bound(tmp_path, monkeypatch, actual, request):
    plans, policies, ledgers = actual
    source, target = request.param
    monkeypatch.setattr(forward, "ROOT", tmp_path / "host")
    deploy = forward.ROOT / "releases/new/backend/deploy"
    deploy.mkdir(parents=True)
    names = [*contract.GATE_FILES, "report_release_contract.py"]
    for name in names:
        (deploy / name).write_bytes((DEPLOY / name).read_bytes())
    paths = [
        *[DEPLOY / name for name in names],
        *[deploy / name for name in names],
        *[ROOT / "tools/installation" / (name + ".py") for name in acl.DEPENDENCIES],
    ]
    ap = dict(
        format="salesbuddy-release120-production-acl-v1",
        mode="normal",
        roles=acl.ROLES.copy(),
        target=dict(database="sales_saas", oid=18684),
        attempt_id="offline-review",
        window_id="offline-review",
        release_scope=contract.RELEASE_SCOPE.copy(),
        expected_registered_versions=ledgers[target]["registered"],
        policy_ref=write(tmp_path / "policy.json", policies[target]),
        source_files={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    )
    p = dict(
        **plan(source, target, ledgers=ledgers),
        expected_database={"name": "sales_saas"},
        bundle_id="offline-review",
        backend="new",
        web="new-web",
        expected_backend="bd",
        expected_web="e33",
        migration_plan=write(tmp_path / "migration.json", plans[target]),
        acl_plan=write(tmp_path / "acl.json", ap),
        acl_source=forward.file_ref(deploy / names[-2]),
        validation=write(
            tmp_path / "validation.json",
            dict(status="passed", tested_source_sha256="a" * 64, tested_mini_versions=["1.2.0", "1.2.1"]),
        ),
        candidate={"backend": {"sha256": "a" * 64}},
        supported_mini_versions=["1.2.0", "1.2.1"],
    )
    binding = write(forward.ROOT / "bundles/forward/offline-review/database/PLAN.json", p)
    return p, ap, binding, tmp_path


def test_existing_forward_and_acl_entries_share_exact_contract(bound, actual):
    p, ap, binding, _ = bound
    plans, policies, _ = actual
    target = p["target_schema"]
    assert forward.inputs(binding)[1] == plans[target]
    assert acl.load_plan(p["acl_plan"]) == (ap, policies[target])
    assert acl.schema_contract(ap)[1]["count"] == len(plans[target])


@pytest.mark.parametrize(
    "change",
    [
        "sql",
        "sql_path",
        "old_checksum",
        "extra_migration",
        "extra_pending",
        "scope",
        "product",
        "config",
        "agent",
        "source",
        "target",
        "duplicate_registration",
        "wrong_registration_count",
        "isolated_registration",
        "historical_registration_substitution",
        "old_policy",
        "mixed_stage_policy",
        "worker_grant",
        "report_grant",
        "direct_relation_grant",
        "report_target",
        "rls",
        "missing_gate",
        "gate_bytes",
        "compatibility",
        "unverified_source",
        "acl_target",
    ],
)
@pytest.mark.parametrize("entry", ["inputs", "database_phase"])
def test_bad_review_inputs_refused_before_db(bound, actual, monkeypatch, change, entry):
    p, ap, binding, tmp = bound
    plans, policies, ledgers = actual
    target = p["target_schema"]
    migrations, policy = deepcopy(plans[target]), deepcopy(policies[target])
    if change == "sql":
        migrations[-1]["sha256"] = "a" * 64
    elif change == "sql_path":
        migrations[-1]["file"] = "migrations/other.sql"
    elif change == "old_checksum":
        p["expected_ledger"]["checksums"]["V239"] = "a" * 64
    elif change == "extra_migration":
        migrations.append(dict(key="V999", file="other.sql", sha256="a" * 64))
    elif change == "extra_pending":
        p["pending"].append("V241")
    elif change == "scope":
        p["release_scope"] = ["enterprise_identity_1.2.1"]
    elif change == "product":
        p["product_version"] = "1.2.0"
    elif change == "config":
        p["prepared_configuration_delta"] = {}
    elif change == "agent":
        p["agent_stage"] = {}
    elif change == "source":
        p["source_schema"] = "V238"
    elif change == "target":
        p["target_schema"] = "V999"
    elif change == "duplicate_registration":
        p["expected_ledger"]["registered"].append("V239")
    elif change == "wrong_registration_count":
        p["expected_ledger"]["registered"] = p["expected_ledger"]["registered"][1:]
    elif change == "isolated_registration":
        # Deliberately remove production-only history; this is a negative input,
        # not a claim that D03 recorded isolated registration rows.
        ap["expected_registered_versions"] = [
            version for version in ledgers[target]["registered"]
            if version not in FROZEN["historical_registrations"]
        ]
    elif change == "historical_registration_substitution":
        versions = p["expected_ledger"]["registered"]
        versions.remove("V001")
        versions.append("V014")
        versions.sort()
    elif change == "old_policy":
        policy = deepcopy(BASE["normal_policy"])
    elif change == "mixed_stage_policy":
        policy = deepcopy(policies["V241"])
    elif change == "worker_grant":
        policy["services"]["worker"]["functions"].append("security.save_review_metric(text,jsonb,integer)")
    elif change == "report_grant":
        policy["services"]["worker"]["functions"].append("security.work_report_review_access(jsonb)")
    elif change == "direct_relation_grant":
        policy["services"]["api"]["tables"]["workflow.task_target_alignment"] = ["SELECT"]
    elif change == "report_target":
        p["target_schema"] = "V245"
    elif change == "rls":
        policy["rls_fingerprint"] = "a" * 64
    elif change == "missing_gate":
        ap["source_files"] = {
            k: v for k, v in ap["source_files"].items() if not k.endswith("review_release_contract.py")
        }
    elif change == "gate_bytes":
        (forward.ROOT / "releases/new/backend/deploy/review_release_contract.py").write_text("changed")
    elif change == "compatibility":
        p["supported_mini_versions"].append("1.2.1+mini." + "b" * 40)
    elif change == "unverified_source":
        p["candidate"]["backend"]["sha256"] = "b" * 64
    elif change == "acl_target":
        ap["expected_registered_versions"] = ledgers["V241"]["registered"]
        policy = deepcopy(policies["V241"])
    ap["policy_ref"] = write(tmp / "policy.json", policy)
    p["acl_plan"] = write(tmp / "acl.json", ap)
    p["migration_plan"] = write(tmp / "migration.json", migrations)
    binding = write(Path(binding["path"]), p)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rejected input reached module loading")

    monkeypatch.setattr(forward, "load", forbidden)
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        if entry == "inputs":
            forward.inputs(binding)
        else:
            asyncio.run(forward.database_phase(binding))


@pytest.mark.parametrize("change", ["checksum", "missing_checksum", "registered", "duplicate_registered"])
@pytest.mark.parametrize("target", ["V240", "V243", "V244"])
def test_final_live_ledger_drift_rejected(actual, change, target):
    plans, _, ledgers = actual
    observed = deepcopy(ledgers[target])
    if change == "checksum":
        observed["checksums"]["V239"] = "a" * 64
    elif change == "missing_checksum":
        del observed["checksums"][target]
    elif change == "registered":
        observed["registered"].remove(target)
    else:
        observed["registered"].append(target)
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        forward.verify_release_ledger(plan(target=target, ledgers=ledgers), plans[target], observed, final=True)
