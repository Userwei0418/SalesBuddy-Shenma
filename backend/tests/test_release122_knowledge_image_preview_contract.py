"""Admission checks for the isolated V276 -> V277 image-preview release."""

# ruff: noqa: E402

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/deploy"))
import release120_production_acl as acl
import release122_knowledge_contract as distribution
import release122_knowledge_image_preview_contract as contract
import release122_knowledge_labels_contract as labels
import release_forward_bundle as forward

FROZEN = json.loads(
    (Path(__file__).with_name("fixtures") / "release122_knowledge_contract.frozen.json").read_text()
)


def migrations():
    rows = deepcopy(FROZEN["migration_plan"])
    for key, path in (
        ("V276", labels.SQL["V276"][0]),
        ("V277", contract.SQL["V277"][0]),
    ):
        rows.append(
            {
                "key": key,
                "file": path,
                "sha256": hashlib.sha256((ROOT / "database" / path).read_bytes()).hexdigest(),
            }
        )
    return rows


def plan():
    rows = migrations()
    prior = rows[:-1]
    return {
        "product_version": "1.2.2",
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "source_schema": "V276",
        "target_schema": "V277",
        "pending": contract.PENDING.copy(),
        "expected_ledger": {
            "checksums": {row["key"]: row["sha256"] for row in prior},
            "registered": sorted(FROZEN["expected_ledger"]["registered"] + distribution.PENDING + ["V276"]),
            "running_jobs": 0,
        },
        "active_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
        "supported_mini_versions": contract.ACTIVE_MINI_VERSIONS.copy(),
    }


def test_v277_admits_only_the_complete_v276_prefix_and_one_pending_migration():
    rows = migrations()
    value = plan()
    contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    assert forward.knowledge_contract_for(value) is contract
    assert acl.knowledge_contract_for(value) is contract
    assert forward.verify_release_ledger(value, rows, value["expected_ledger"]) == ["V277"]
    final = {
        "checksums": {row["key"]: row["sha256"] for row in rows},
        "registered": sorted(value["expected_ledger"]["registered"] + ["V277"]),
        "running_jobs": 0,
    }
    assert forward.verify_release_ledger(value, rows, final, final=True) == []


def test_v277_rejects_old_scope_or_any_prior_or_pending_checksum_change():
    rows = migrations()
    value = plan()
    value["release_scope"] = labels.RELEASE_SCOPE.copy()
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    value = plan()
    value["expected_ledger"]["checksums"]["V276"] = "0" * 64
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)
    value = plan()
    rows[-1]["sha256"] = "0" * 64
    with pytest.raises(forward.Refused):
        contract.validate(value, rows, digest=forward.original.digest, require=forward.require)


def test_v277_policy_is_v276_matrix_with_only_schema_and_ledger_advanced():
    value = {
        "release_scope": contract.RELEASE_SCOPE.copy(),
        "expected_registered_versions": sorted(
            FROZEN["expected_ledger"]["registered"] + distribution.PENDING + ["V276", "V277"]
        ),
    }
    policy = deepcopy(FROZEN["normal_policy"])
    policy["schema"] = "V277"
    policy["migration_fingerprint"] = contract.TARGET_LEDGER
    contract.validate_acl(value, policy, digest=forward.original.digest, require=forward.require)

    stale_v276_policy = deepcopy(FROZEN["normal_policy"])
    stale_v276_policy["schema"] = "V276"
    stale_v276_policy["migration_fingerprint"] = labels.TARGET_LEDGER
    with pytest.raises(forward.Refused):
        contract.validate_acl(value, stale_v276_policy, digest=forward.original.digest, require=forward.require)
