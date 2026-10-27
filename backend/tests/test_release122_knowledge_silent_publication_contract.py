"""Admission checks for the isolated V277 -> V278 silent-publication release."""

# ruff: noqa: E402

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tests.test_release122_knowledge_image_preview_contract import migrations as prior_migrations

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/deploy"))
import release120_production_acl as acl
import release122_knowledge_contract as distribution
import release122_knowledge_image_preview_contract as labels
import release122_knowledge_silent_publication_contract as contract
import release_forward_bundle as forward

FROZEN = json.loads((Path(__file__).with_name("fixtures") / "release122_knowledge_contract.frozen.json").read_text())


def migrations():
    rows = prior_migrations()
    key = "V278"
    path = contract.SQL[key][0]
    rows.append(
        {"key": key, "file": path, "sha256": hashlib.sha256((ROOT / "database" / path).read_bytes()).hexdigest()}
    )
    return rows


def plan():
    rows = migrations()
    prior = rows[:-1]
    return {
        "product_version": "1.2.2",
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "source_schema": "V277",
        "target_schema": "V278",
        "pending": contract.PENDING.copy(),
        "expected_ledger": {
            "checksums": {row["key"]: row["sha256"] for row in prior},
            "registered": sorted(FROZEN["expected_ledger"]["registered"] + distribution.PENDING + ["V276", "V277"]),
            "running_jobs": 0,
        },
        "active_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
        "supported_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
    }


def test_v278_admits_only_the_complete_v276_prefix_and_one_pending_migration():
    rows = migrations()
    value = plan()
    contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    assert forward.knowledge_contract_for(value) is contract
    assert acl.knowledge_contract_for(value) is contract
    assert forward.verify_release_ledger(value, rows, value["expected_ledger"]) == ["V278"]
    final = {
        "checksums": {row["key"]: row["sha256"] for row in rows},
        "registered": sorted(value["expected_ledger"]["registered"] + ["V278"]),
        "running_jobs": 0,
    }
    assert forward.verify_release_ledger(value, rows, final, final=True) == []


def test_v278_rejects_old_scope_or_any_prior_or_pending_checksum_change():
    rows = migrations()
    value = plan()
    value["release_scope"] = labels.RELEASE_SCOPE.copy()
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    value = plan()
    value["expected_ledger"]["checksums"]["V277"] = "0" * 64
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    value = plan()
    rows[-1]["sha256"] = "0" * 64
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)


def test_v278_policy_is_v276_matrix_with_only_schema_and_ledger_advanced():
    value = {
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "expected_registered_versions": sorted(
            FROZEN["expected_ledger"]["registered"] + distribution.PENDING + ["V276", "V277", "V278"]
        ),
    }
    policy = deepcopy(FROZEN["normal_policy"])
    policy["schema"] = "V278"
    policy["migration_fingerprint"] = contract.TARGET_LEDGER
    contract.validate_acl(value, policy, digest=forward.original.digest, require=forward.require)

    stale_v276_policy = deepcopy(FROZEN["normal_policy"])
    stale_v276_policy["schema"] = "V277"
    stale_v276_policy["migration_fingerprint"] = labels.TARGET_LEDGER
    with pytest.raises(forward.Refused):
        contract.validate_acl(value, stale_v276_policy, digest=forward.original.digest, require=forward.require)


def test_silent_forward_loads_real_frozen_acl_dependencies_before_sql(tmp_path):
    """Exercise the file/hash/module gate that rejected the first production attempt."""
    def ref(path, data=None):
        if data is not None:
            path.write_text(json.dumps(data))
            path.chmod(0o600)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    policy = deepcopy(FROZEN["normal_policy"])
    policy.update(schema="V278", migration_fingerprint=contract.TARGET_LEDGER)
    paths = [ROOT / "backend/deploy" / name for name in contract.GATE_FILES]
    paths += [ROOT / "tools/installation" / (name + ".py") for name in contract.DEPENDENCIES]
    value = {
        "format": "salesbuddy-release120-production-acl-v1", "mode": "normal",
        "roles": acl.ROLES.copy(), "target": {"database": "sales_saas", "oid": 18684},
        "attempt_id": "offline-silent", "window_id": "offline-silent",
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "expected_registered_versions": sorted(plan()["expected_ledger"]["registered"] + ["V278"]),
        "policy_ref": ref(tmp_path / "policy.json", policy),
        "source_files": {str(path): ref(path)["sha256"] for path in paths},
    }
    source = ROOT / "backend/deploy/release120_production_acl.py"
    loaded, actual = forward.validated_acl({
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "acl_source": ref(source), "acl_plan": ref(tmp_path / "acl.json", value),
    })
    assert actual == value
    assert loaded.dependency_contract(actual) == contract.DEPENDENCIES
