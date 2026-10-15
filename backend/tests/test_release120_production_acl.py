"""Offline production ACL admission/transaction failures; no database access."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/deploy/release120_production_acl.py"
spec = importlib.util.spec_from_file_location("production_acl_under_test", SOURCE)
acl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acl)


def ref(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def write(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return ref(path)


@pytest.fixture(autouse=True)
def no_connectors(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("No database connector permitted in source tests")

    monkeypatch.setattr(asyncpg, "connect", deny)
    monkeypatch.setattr(asyncpg, "create_pool", deny)


@pytest.fixture
def bound(tmp_path):
    policy = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_bytes())
    plan = dict(
        format="salesbuddy-release120-production-acl-v1",
        mode="normal",
        roles=acl.ROLES.copy(),
        target=dict(database="sales_saas", oid=18684),
        attempt_id="synthetic-offline",
        window_id="synthetic-offline",
        expected_registered_versions=["V001", "V233"],
        policy_ref=write(tmp_path / "policy.json", policy),
        source_files={
            str(p): ref(p)["sha256"]
            for p in [SOURCE, *[ROOT / "tools/installation" / (name + ".py") for name in acl.DEPENDENCIES]]
        },
    )
    binding = write(tmp_path / "plan.json", plan)
    return plan, policy, binding, tmp_path


def test_exact_four_production_services_and_source_bindings(bound):
    plan, policy, binding, _ = bound
    assert acl.load_plan(binding) == (plan, policy)
    assert plan["roles"] == {
        "api": "sales_api_saas",
        "worker": "sales_worker_saas",
        "feishu": "sales_feishu_saas",
        "platform": "sales_platform_saas",
    }
    assert acl.dependencies()[0].validate_policy(policy) == policy


@pytest.mark.parametrize(
    "change",
    [
        "db",
        "oid",
        "three_roles",
        "local_role",
        "mode",
        "version",
        "missing_source",
        "bad_source",
        "policy",
        "policy_matrix",
        "policy_sha",
        "permission",
    ],
)
def test_unapproved_plan_or_policy_rejected(bound, change):
    plan, policy, binding, tmp = bound
    if change == "db":
        plan["target"]["database"] = "salesbuddy_local_unified"
    elif change == "oid":
        plan["target"]["oid"] = 4922457
    elif change == "three_roles":
        del plan["roles"]["platform"]
    elif change == "local_role":
        plan["roles"]["worker"] = "salesbuddy_local_unified_worker"
    elif change == "mode":
        plan["mode"] = "recovery"
    elif change == "version":
        plan["expected_registered_versions"] = ["V202"]
    elif change == "missing_source":
        del plan["source_files"][str(SOURCE)]
    elif change == "bad_source":
        plan["source_files"][str(SOURCE)] = "0" * 64
    elif change in ("policy", "policy_matrix"):
        if change == "policy":
            policy["migration_fingerprint"] = "0" * 64
        else:
            policy["services"]["platform"]["functions"] = []
        plan["policy_ref"] = write(tmp / "policy.json", policy)
    elif change == "policy_sha":
        plan["policy_ref"]["sha256"] = "0" * 64
    binding = write(tmp / "plan.json", plan)
    if change == "permission":
        Path(binding["path"]).chmod(0o644)
    with pytest.raises(ValueError):
        acl.load_plan(binding)


class Connection:
    def __init__(self):
        self.active = False
        self.readonly = False
        self.calls = []
        self.commit_error = None
        self.rollback_error = None
        self.fail_identity = False
        self.cluster_generation = 1

    def is_closed(self):
        return False

    def is_in_transaction(self):
        return self.active

    def transaction(self, **options):
        assert options["isolation"] == "repeatable_read"
        self.readonly = options.get("readonly", False)
        return self

    async def start(self):
        self.active = True
        self.calls.append("begin")

    async def commit(self):
        self.calls.append("commit")
        if self.commit_error:
            raise self.commit_error
        self.active = False

    async def rollback(self):
        self.calls.append("rollback")
        if self.rollback_error:
            raise self.rollback_error
        self.active = False

    async def execute(self, sql):
        assert sql.startswith("SELECT set_config(")

    async def fetchrow(self, sql):
        assert sql == acl.IDENTITY_SQL
        return dict(
            database="wrong" if self.fail_identity else "sales_saas",
            oid=18684,
            session_user="postgres",
            current_user="postgres",
            pid=123,
            rolsuper=True,
            read_only="on" if self.readonly else "off",
            isolation="repeatable read",
            server_version_num=160015,
        )

    async def fetchval(self, sql):
        assert sql == acl.CLUSTER_SQL
        return dict(synthetic_cluster_generation=self.cluster_generation)


@pytest.fixture
def run_context(bound, monkeypatch):
    plan, policy, binding, tmp = bound
    conn = Connection()
    events = []
    state = dict(ledger=1, fail_reconcile=False, mutate_cluster=False, mutate_ledger=False)

    async def ledger(_conn, _plan):
        return dict(synthetic=state["ledger"])

    async def verify(_conn, roles, _policy):
        assert roles == plan["roles"] and len(roles) == 4
        assert _conn.active
        events.append("verify_all_four")
        return dict(status="passed", services=sorted(roles))

    async def reconcile(_conn, roles, _policy):
        assert _conn.active and not _conn.readonly
        events.append("reconcile_all_four")
        if state["fail_reconcile"]:
            raise RuntimeError("synthetic failure")
        if state["mutate_cluster"]:
            _conn.cluster_generation += 1
        if state["mutate_ledger"]:
            state["ledger"] += 1
        return await verify(_conn, roles, _policy)

    async def persist(label, value):
        events.append(label)

    monkeypatch.setattr(acl, "ledger", ledger)
    monkeypatch.setattr(
        acl,
        "dependencies",
        lambda: (
            SimpleNamespace(validate_policy=lambda x: x, verify_service_roles=verify),
            SimpleNamespace(reconcile_service_roles=reconcile),
        ),
    )
    return conn, binding, persist, events, state


@pytest.mark.asyncio
async def test_apply_has_one_commit_after_full_four_role_verify(run_context):
    conn, binding, persist, events, _ = run_context
    result = await acl.apply_release_permissions(conn, plan_ref=binding, persist=persist)
    assert conn.calls == ["begin", "commit"] and not conn.active
    assert result["commit_attempted"] is result["commit_acknowledged"] is True
    assert result["runtime_ready"] is False
    assert events == ["ACL_BEFORE", "reconcile_all_four", "verify_all_four", "ACL_COMMIT_INTENT", "ACL_COMMITTED"]


@pytest.mark.asyncio
async def test_verify_only_never_reconciles_or_commits(run_context):
    conn, binding, _, events, _ = run_context
    result = await acl.verify_release_permissions(conn, plan_ref=binding)
    assert conn.calls == ["begin", "rollback"] and events == ["verify_all_four"]
    assert result["rolled_back"] is True and result["committed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "identity",
        "reconcile",
        "cluster",
        "ledger",
        "before_persist",
        "intent_persist",
        "commit",
        "committed_persist",
        "rollback",
    ],
)
async def test_failure_never_erases_commit_uncertainty(run_context, failure):
    conn, binding, persist, events, state = run_context
    if failure == "identity":
        conn.fail_identity = True
    if failure in ("reconcile", "rollback"):
        state["fail_reconcile"] = True
    if failure == "cluster":
        state["mutate_cluster"] = True
    if failure == "ledger":
        state["mutate_ledger"] = True
    if failure == "commit":
        conn.commit_error = RuntimeError("unknown ACK")
    if failure == "rollback":
        conn.rollback_error = RuntimeError("unknown ROLLBACK")

    async def broken(label, value):
        await persist(label, value)
        if label == {
            "before_persist": "ACL_BEFORE",
            "intent_persist": "ACL_COMMIT_INTENT",
            "committed_persist": "ACL_COMMITTED",
        }.get(failure):
            raise RuntimeError("persistence failed")

    with pytest.raises(acl.ACLExecutionFailed) as raised:
        await acl.apply_release_permissions(conn, plan_ref=binding, persist=broken)
    result = raised.value.journal
    assert result["errors"] and result["status"] == "failed_preserve_evidence"
    assert result["commit_acknowledged"] is (failure == "committed_persist")
    assert result["commit_outcome"] == (
        "unknown_no_replay"
        if failure == "commit"
        else "acknowledged"
        if failure == "committed_persist"
        else "not_attempted"
    )
    assert conn.calls.count("commit") <= 1
    if failure == "rollback":
        assert result["cleanup_errors"] and not result["rollback_acknowledged"]
    if failure == "before_persist":
        assert "reconcile_all_four" not in events


@pytest.mark.asyncio
async def test_changed_plan_after_before_persist_prevents_any_acl(run_context):
    conn, binding, persist, events, _ = run_context

    async def mutate(label, value):
        await persist(label, value)
        if label == "ACL_BEFORE":
            Path(binding["path"]).write_text("{}")

    with pytest.raises(acl.ACLExecutionFailed):
        await acl.apply_release_permissions(conn, plan_ref=binding, persist=mutate)
    assert "reconcile_all_four" not in events and conn.calls == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_existing_transaction_rejected_without_rollback_of_caller(run_context):
    conn, binding, persist, _, _ = run_context
    conn.active = True
    with pytest.raises(ValueError, match="IDLE_CONNECTION"):
        await acl.apply_release_permissions(conn, plan_ref=binding, persist=persist)
    assert conn.calls == []


@pytest.mark.asyncio
async def test_plan_change_during_commit_intent_prevents_commit(run_context):
    conn, binding, persist, events, _ = run_context

    async def mutate(label, value):
        await persist(label, value)
        if label == "ACL_COMMIT_INTENT":
            Path(binding["path"]).write_text("{}")

    with pytest.raises(acl.ACLExecutionFailed) as raised:
        await acl.apply_release_permissions(conn, plan_ref=binding, persist=mutate)
    assert raised.value.journal["commit_attempted"] is False
    assert conn.calls == ["begin", "rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "missing_historical_version", "checksum", "duplicate"])
async def test_full_ledger_preserves_historical_registration_independent_of_checksum_count(bound, change):
    plan, _, _, _ = bound
    database = ROOT / "database"
    paths = [database / "baseline/V001__current_schema.sql", *(database / "seeds").glob("S*.sql"),
             *database.glob("V*.sql"), *(database / "migrations").glob("V*.sql")]
    items = [{"key": "BASELINE_V1" if path.parent.name == "baseline" else path.stem.split("__")[0],
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
    value = dict(
        registered=[
            dict(version=v, description="synthetic history retained") for v in plan["expected_registered_versions"]
        ],
        checksums=[dict(migration_key=r["key"], checksum_sha256=r["sha256"]) for r in items],
    )
    if change == "missing_historical_version":
        value["registered"].pop(0)
    elif change == "checksum":
        value["checksums"][0]["checksum_sha256"] = "0" * 64
    elif change == "duplicate":
        value["checksums"].append(value["checksums"][0])

    async def fetch(sql):
        assert sql == acl.LEDGER_SQL
        return json.dumps(value)

    conn = SimpleNamespace(fetchval=fetch)
    if change is None:
        assert await acl.ledger(conn, plan) == value
    else:
        with pytest.raises(ValueError, match="FULL_LEDGER"):
            await acl.ledger(conn, plan)
