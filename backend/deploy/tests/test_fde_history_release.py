"""Exact V280 admission and atomic DDL/permission/ledger failure boundaries."""

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
import release123_contract as contract  # noqa: E402
import release123_fde_security as security  # noqa: E402
import release_forward_bundle as forward  # noqa: E402
import test_visit_index_release as old  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline test reached a real host or database")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(forward.subprocess, "run", forbidden)


def migrations():
    name, checksum = contract.SQL["V280"]
    return old.migrations() + [{"key": "V280", "file": name, "sha256": checksum}]


def base_plan():
    p = old.base_plan()
    p.update(
        product_version="1.2.3", release_scope=contract.RELEASE_SCOPE.copy(),
        source_schema="V279", target_schema="V280", pending=["V280"],
        active_mini_versions=contract.ACTIVE_MINI_VERSIONS.copy(),
        supported_mini_versions=sorted(contract.ACTIVE_MINI_VERSIONS + ["1.2.3", "1.2.3+mini." + "a" * 40]),
        candidate={"backend": {"revision": "a" * 40, "sha256": "b" * 64}},
        expected_database={"name": "sales_saas", "schema": "V279", "ledger_sha256": contract.BASE_COMPLETE_LEDGER},
    )
    p["expected_ledger"]["registered"] = sorted(p["expected_ledger"]["registered"] + ["V279"])
    p["expected_ledger"]["checksums"] = {r["key"]: r["sha256"] for r in old.migrations()}
    return p


def security_snapshot():
    return {**old.security_snapshot(), "index_fingerprint": "a" * 64}


@pytest.fixture
def bound(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "host"
    monkeypatch.setattr(forward, "ROOT", root)
    release = root / "releases/new"
    sources = {}
    for relative in [
        *("backend/deploy/" + name for name in contract.GATE_FILES),
        "database/scripts/migrate.py", "tools/installation/runtime_permissions.json",
    ]:
        dest = release / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / relative).read_bytes())
        sources[str(dest)] = hashlib.sha256(dest.read_bytes()).hexdigest()
    metadata = release / "tools/installation/runtime_permissions.json"
    policy = json.loads(metadata.read_text())
    policy.update(schema="V280", migration_fingerprint=contract.TARGET_LEDGER)
    policy["services"]["api"]["functions"] = [v for v in policy["services"]["api"]["functions"]
        if not v.startswith(("security.company_customer_directory_candidates(",
                             "security.company_customer_directory_page_items("))]
    metadata.write_text(json.dumps(policy))
    sources[str(metadata)] = hashlib.sha256(metadata.read_bytes()).hexdigest()
    p = base_plan()
    acl = {
        "format": "salesbuddy-fde-security-plan-v1", "mode": "fde_history_api_execute_only",
        "roles": security.ROLES, "maintenance_owner": "postgres",
        "target": {"database": "sales_saas", "oid": 18684},
        "release_scope": contract.RELEASE_SCOPE,
        "expected_registered_versions": sorted(p["expected_ledger"]["registered"] + ["V280"]),
        "policy_ref": old.write(tmp_path / "security.json", security_snapshot()), "source_files": sources,
    }
    p.update(
        bundle_id="fde280", backend="new", web="new-web", expected_backend="old", expected_web="old-web",
        migration_plan=old.write(tmp_path / "migrations.json", migrations()),
        acl_plan=old.write(tmp_path / "security-plan.json", acl),
        acl_source=forward.file_ref(release / "backend/deploy/release123_fde_security.py"),
        validation=old.write(tmp_path / "validation.json", {
            "status": "passed", "tested_source_sha256": "b" * 64,
            "tested_mini_versions": p["supported_mini_versions"],
        }),
    )
    ref = old.write(root / "bundles/forward/fde280/database/PLAN.json", p)
    return p, acl, ref, release


def test_exact_v280_admission(bound):
    p, _, ref, _ = bound
    assert contract.ACTIVE_MINI_VERSIONS == sorted(contract.ACTIVE_MINI_VERSIONS)
    assert forward.inputs(ref)[0] == p
    assert forward.verify_release_ledger(p, migrations(), p["expected_ledger"]) == ["V280"]
    assert forward.is_fde_history(p)
    helper, _ = forward.validated_acl(p)
    assert not hasattr(helper, "apply_release_permissions")


@pytest.mark.parametrize("fault", ["sql", "old_checksum", "registration", "client", "scope", "configuration", "jobs"])
def test_unreviewed_transition_rejected(fault):
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
        p["release_scope"] = ["release_1.2.2_visit_field_prefix_index"]
    if fault == "configuration":
        p["agent_stage"] = {}
    if fault == "jobs":
        p["expected_ledger"]["running_jobs"] = 1
    with pytest.raises(forward.Refused):
        contract.validate(p, rows, digest=forward.original.digest, require=forward.require)


@pytest.mark.parametrize("fault", ["helper", "metadata", "missing_source", "index_snapshot", "mode", "owner"])
def test_security_closure_fail_closed(bound, fault):
    p, acl, ref, release = bound
    if fault == "helper":
        (release / "backend/deploy/release123_fde_security.py").write_text("# changed")
    if fault == "metadata":
        file = release / "tools/installation/runtime_permissions.json"
        v = json.loads(file.read_text())
        v["services"]["worker"]["functions"].append(security.SIGNATURE)
        file.write_text(json.dumps(v))
        acl["source_files"][str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    if fault == "missing_source":
        acl["source_files"].pop(str(release / "database/scripts/migrate.py"))
    if fault == "index_snapshot":
        v = security_snapshot()
        del v["index_fingerprint"]
        acl["policy_ref"] = old.write(Path(acl["policy_ref"]["path"]), v)
    if fault == "mode":
        acl["mode"] = "normal"
    if fault == "owner":
        acl["maintenance_owner"] = "sales_api_saas"
    p["acl_plan"] = old.write(Path(p["acl_plan"]["path"]), acl)
    ref = old.write(Path(ref["path"]), p)
    with pytest.raises(forward.Refused):
        forward.inputs(ref)


def function_row():
    return {
        "owner": "postgres", "language": "plpgsql", "kind": "f", "definer": True, "leakproof": False,
        "strict": False, "returns_set": False, "volatility": "v", "parallel": "u", "return_type": 23,
        "argument_types": "2950 23 2951", "argument_names": ["p_opportunity", "p_expected_version", "p_members"],
        "all_argument_types": None, "argument_modes": None, "defaults": 0,
        "settings": ["search_path=pg_catalog"], "body_sha256": security.BODY_SHA,
    }


@pytest.mark.parametrize("fault", [None, "body", "owner", "public", "worker", "grant_option", "search_path", "definer"])
def test_new_function_is_exact_and_api_only(fault):
    row = function_row()
    grants = [
        {"grantor": "postgres", "grantee": role, "privilege": "EXECUTE", "grantable": False}
        for role in ["postgres", "sales_api_saas"]
    ]
    if fault in {"public", "worker"}:
        grants.append({**grants[1], "grantee": "PUBLIC" if fault == "public" else "sales_worker_saas"})
    if fault == "body":
        row["body_sha256"] = "0" * 64
    if fault == "owner":
        row["owner"] = "sales_api_saas"
    if fault == "grant_option":
        grants[1]["grantable"] = True
    if fault == "search_path":
        row["settings"] = ["search_path=public"]
    if fault == "definer":
        row["definer"] = False
    if fault:
        with pytest.raises(forward.Refused):
            security.validate_function(row, grants, owner="postgres")
    else:
        security.validate_function(row, grants, owner="postgres")
        security.validate_function(row, grants[:1], owner="postgres", allow_ungranted=True)
        with pytest.raises(forward.Refused):
            security.validate_function(row, grants[:1], owner="postgres")


@pytest.mark.parametrize("drift", [None, *sorted(old.contract.SECURITY_SECTIONS), "index_fingerprint"])
def test_existing_security_drift_stops_before_any_grant(monkeypatch, drift):
    expected = security_snapshot()
    observed = deepcopy(expected)
    if drift == "index_fingerprint":
        observed[drift] = "b" * 64
    elif drift:
        observed["fingerprints"][drift] = "b" * 64
    monkeypatch.setattr(security, "load_plan", lambda _: (
        {"target": {"database": "sales_saas", "oid": 18684}, "maintenance_owner": "postgres"}, expected,
    ))

    async def snapshot(_):
        return observed

    monkeypatch.setattr(security, "snapshot", snapshot)

    class Conn:
        def is_in_transaction(self):
            return True

        async def fetchrow(self, sql, *args):
            return None if sql == security.FUNCTION_SQL else {"database": "sales_saas", "oid": 18684}

        async def fetchval(self, sql, *args):
            return "postgres"

        async def execute(self, *args):
            raise AssertionError("Readonly check tried to write")

    if drift:
        with pytest.raises(forward.Refused):
            asyncio.run(security.verify_state(Conn(), plan_ref={}))
    else:
        assert asyncio.run(security.verify_state(Conn(), plan_ref={}))["security_preserved"]


def test_only_fixed_execute_grant_runs_inside_transaction(monkeypatch):
    events = []

    async def verify(conn, *, plan_ref, final=False, allow_ungranted=False):
        events.append(("verify", final, allow_ungranted))
        return {"security_preserved": True}

    monkeypatch.setattr(security, "verify_state", verify)

    class Conn:
        def is_in_transaction(self):
            return True

        async def execute(self, sql):
            events.append(sql)

    asyncio.run(security.grant_execute(Conn(), plan_ref={}))
    assert events == [("verify", True, True), security.GRANT_SQL, ("verify", True, False)]


@pytest.mark.parametrize("fault", [None, "before", "migration", "grant", "ledger", "commit", "post"])
def test_atomic_ddl_grant_and_ledger_commit_boundary(monkeypatch, fault):
    events, state = [], {"tx": False, "migrated": False}
    p, rows = base_plan(), migrations()
    p["acl_plan"] = {}

    class Tx:
        async def start(self):
            events.append("begin")
            state["tx"] = True

        async def rollback(self):
            events.append("rollback")
            state.update(tx=False, migrated=False)

        async def commit(self):
            events.append("commit")
            state["tx"] = False
            if fault == "commit":
                raise ConnectionError("lost commit acknowledgement")

    class Conn:
        def is_in_transaction(self):
            return state["tx"]

        def transaction(self, **kwargs):
            assert kwargs == {"isolation": "repeatable_read"}
            return Tx()

        async def execute(self, sql):
            assert sql.startswith("SET LOCAL ")

    async def ledger(_):
        v = deepcopy(p["expected_ledger"])
        if state["migrated"]:
            v["registered"] = sorted(v["registered"] + ["V280"])
            v["checksums"]["V280"] = rows[-1]["sha256"]
        v["checksum_rows"] = [
            {"migration_key": k, "checksum_sha256": c, "provenance": "executed"} for k, c in v["checksums"].items()
        ]
        v["registered_rows"] = [{"version": v} for v in v["registered"]]
        if state["migrated"] and fault == "ledger":
            v["checksum_rows"][0]["provenance"] = "changed"
        return v

    monkeypatch.setattr(forward, "ledger", ledger)

    async def before(*args, **kwargs):
        events.append("security_before")
        assert state["tx"]
        if fault == "before":
            raise forward.Refused("security drift")

    async def migrate(*args, **kwargs):
        events.append("migration")
        assert state["tx"]
        state["migrated"] = True
        if fault == "migration":
            raise forward.Refused("migration failure")
        return "applied"

    async def grant(*args, **kwargs):
        events.append("grant_and_security_after")
        assert state["tx"]
        if fault == "grant":
            raise forward.Refused("unauthorized ACL")
        return {"security_preserved": True}

    async def verify(*args, **kwargs):
        events.append("postcommit_readonly")
        assert not state["tx"]
        if fault == "post":
            raise forward.Refused("postcommit drift")
        return {"status": "verified_readonly", "rolled_back": True, "committed": False}

    result = {"migrations": [], "errors": []}
    saves = []

    async def run():
        await forward.apply_fde_history(
            Conn(), p, rows, await ledger(None), SimpleNamespace(apply=migrate),
            SimpleNamespace(key="V280", checksum=rows[-1]["sha256"]), ROOT / "database",
            SimpleNamespace(verify_state=before, grant_execute=grant, verify_release_permissions=verify),
            result, lambda: saves.append(deepcopy(result)),
        )

    if fault:
        with pytest.raises((forward.Refused, ConnectionError)):
            asyncio.run(run())
    else:
        asyncio.run(run())
    if fault in {"before", "migration", "grant", "ledger"}:
        assert "commit" not in events and events[-1] == "rollback" and not state["migrated"]
        assert result["migrations"][0]["commit_outcome"] == "not_attempted"
    else:
        assert events.index("commit") > events.index("grant_and_security_after")
        assert any(r.get("phase") == "v280_commit" and r["acl_commit_outcome"] == "unknown" for r in saves)
        assert result["acl_commit_outcome"] == ("unknown" if fault == "commit" else "acknowledged")
    assert not state["tx"]
