"""Admission checks for the isolated V275 -> V276 knowledge labels release."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/deploy"))
import release122_knowledge_contract as prior
import release122_knowledge_labels_contract as contract
import release_forward_bundle as forward

FROZEN = json.loads(
    (Path(__file__).with_name("fixtures") / "release122_knowledge_contract.frozen.json").read_text()
)


def migrations():
    rows = deepcopy(FROZEN["migration_plan"])
    rows.append(
        {
            "key": "V276",
            "file": contract.SQL["V276"][0],
            "sha256": hashlib.sha256((ROOT / "database" / contract.SQL["V276"][0]).read_bytes()).hexdigest(),
        }
    )
    return rows


def plan():
    return {
        "product_version": "1.2.2",
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "source_schema": "V275",
        "target_schema": "V276",
        "pending": contract.PENDING.copy(),
        "expected_ledger": {
            "checksums": {row["key"]: row["sha256"] for row in FROZEN["migration_plan"]},
            "registered": sorted(FROZEN["expected_ledger"]["registered"] + prior.PENDING),
            "running_jobs": 0,
        },
        "active_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
        "supported_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
    }


def test_v276_admits_only_the_complete_v275_prefix_and_one_pending_migration():
    rows = migrations()
    value = plan()
    contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    assert forward.verify_release_ledger(value, rows, value["expected_ledger"]) == ["V276"]
    final = {
        "checksums": {row["key"]: row["sha256"] for row in rows},
        "registered": sorted(value["expected_ledger"]["registered"] + ["V276"]),
        "running_jobs": 0,
    }
    assert forward.verify_release_ledger(value, rows, final, final=True) == []


def test_v276_rejects_old_scope_and_unreviewed_migration_changes():
    rows = migrations()
    value = plan()
    value["release_scope"] = prior.RELEASE_SCOPE.copy()
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    value = plan()
    rows[-1]["sha256"] = "0" * 64
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)


def test_v276_policy_contract_is_exact_and_old_policy_is_not_reused():
    value = {
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "expected_registered_versions": sorted(FROZEN["expected_ledger"]["registered"] + prior.PENDING + ["V276"]),
    }
    policy = deepcopy(FROZEN["normal_policy"])
    policy["schema"] = "V276"
    policy["migration_fingerprint"] = contract.TARGET_LEDGER
    contract.validate_acl(value, policy, digest=forward.original.digest, require=forward.require)
    old = deepcopy(FROZEN["normal_policy"])
    with pytest.raises(forward.Refused):
        contract.validate_acl(value, old, digest=forward.original.digest, require=forward.require)
