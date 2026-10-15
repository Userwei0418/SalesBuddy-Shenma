"""The bridge is a pinned, derived release; old customer SQL stays immutable."""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "shenma_whole_121_manifest.json").read_text())


def test_fixed_revision_and_complete_noncolliding_mapping():
    assert MANIFEST["source_revision"] == "e40a8029d94a03fc45448e00d536e5a5b9120831"
    assert MANIFEST["expected_schema"] == "V399"
    entries = MANIFEST["migrations"]
    assert len(entries) == 92
    expected = set(range(152, 251)) - {205, 206, 207, 208, 247, 248, 249}
    assert {int(item["source_version"][1:]) for item in entries} == expected
    assert all(
        int(item["target_version"][1:]) == int(item["source_version"][1:]) + 148
        for item in entries
    )


def test_all_pinned_bytes_and_old_customer_history_match():
    for item in MANIFEST["migrations"]:
        body = (ROOT / item["target"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == item["target_sha256"]
        assert item["source_sha256"] in body.decode().splitlines()[1]
        assert not re.search(r"\breconcile_runtime_grants_v\d+\b", body.decode())
    for item in (
        MANIFEST["preserved_customer_migrations"] + MANIFEST["customer_adapters"]
    ):
        assert (
            hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
            == item["sha256"]
        )
    assert {f"V{x}" for x in range(152, 162)} <= {
        Path(item["path"]).name.split("__")[0]
        for item in MANIFEST["preserved_customer_migrations"]
    }


def test_bridge_retains_only_reviewed_overlapping_table_adaptations():
    item = next(
        item for item in MANIFEST["migrations"] if item["source_version"] == "V204"
    )
    body = (ROOT / item["target"]).read_text()
    assert "CREATE TABLE IF NOT EXISTS crm.customer_creation_request" in body
    assert "Existing customer role grants were migrated by V160" in body
    assert "DROP TABLE" not in body


def test_reviewed_service_policy_is_fixed_upstream_and_strict_request_exception():
    policy = MANIFEST["reviewed_service_policy"]
    assert policy["source_revision"] == MANIFEST["source_revision"]
    assert policy["source_path"] == "tools/installation/runtime_permissions.json"
    assert (
        policy["sha256"]
        == "31d0a6b3e525cc7ca931cb6744954c793e5321834962d54f01e79906d36a0d4d"
    )
    assert (
        hashlib.sha256((ROOT / policy["path"]).read_bytes()).hexdigest()
        == policy["sha256"]
    )
    assert set(policy["intentional_exceptions"]) == {"crm.customer_creation_request"}


def test_final_report_source_access_restores_only_reviewed_point_lookups():
    spec = importlib.util.spec_from_file_location(
        "bridge_source_access", ROOT / "scripts/build_shenma_whole_121_bridge.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    final = bridge.source_access_definition(
        (ROOT / "migrations/V399__shenma_whole_121_delivery_guards.sql").read_text()
    )
    assert final == bridge.restored_source_access()
    before = bridge.source_access_definition(
        (ROOT / "migrations/V393__internal_work_report_review_freeze.sql").read_text()
    )
    # Review provenance, explicit permissions and unknown-kind denial must not
    # change when restoring a point lookup covered by a unique primary key.
    assert (
        final[final.index("   WHEN 'review_subject'") :]
        == before[before.index("   WHEN 'review_subject'") :]
    )
    assert "LANGUAGE sql STABLE SET search_path=pg_catalog" in final
    assert "SECURITY DEFINER" not in final
    original_point = bridge.source_access_definition(
        (
            ROOT / "migrations/V386__internal_work_report_point_snapshot_access.sql"
        ).read_text()
    )
    for start, end in (("opportunity", "task"), ("meeting_customer", "meeting_state")):
        marker, stop = f"   WHEN '{start}'", f"   WHEN '{end}'"
        assert (
            final[final.index(marker) : final.index(stop)]
            == original_point[original_point.index(marker) : original_point.index(stop)]
        )
