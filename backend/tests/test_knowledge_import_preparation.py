"""Reject incomplete/wrong GTM exports before preparing a write request."""

import importlib.util
import json
import stat
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_knowledge_import", ROOT / "scripts/prepare_knowledge_import.py")
preparation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preparation)


def export(tmp_path):
    row = {"record_id": "recTest", "内容名称": "Synthetic material", "产品": ["通用"],
           "内容类别": ["销售支持"], "使用范围": ["仅内部"], "状态": ["待复核"],
           "内容提供人": [{"id": "ou_test", "name": "Synthetic user"}], "内容材料": None,
           "附件": [], "备注": ""}
    path = tmp_path / "records.ndjson"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {"manifest_version": "v1", "format": "ndjson", "base_token": "baseTest",
                "table_id": "tblTest", "rev": 292, "has_more": False, "records_count": 1,
                "query_context": {"record_scope": "all_records"},
                "record_file_size_bytes": path.stat().st_size,
                "columns": {k: {"field_id": v} for k, v in preparation.FIELD_IDS.items()}}
    manifest_path = tmp_path / "records.manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return path, manifest_path, manifest


def read(paths):
    return preparation.read_source(paths[0], paths[1], base_id="baseTest", table_id="tblTest", source_revision=292)


@pytest.mark.parametrize("key,value", [
    ("has_more", True), ("rev", 293), ("base_token", "otherBase"), ("table_id", "tblOther"),
    ("records_count", 2), ("records_count", True), ("record_file_size_bytes", 1),
    ("query_context", {"record_scope": "filtered"}),
])
def test_partial_or_wrong_source_is_rejected(tmp_path, key, value):
    paths = export(tmp_path)
    paths[2][key] = value
    paths[1].write_text(json.dumps(paths[2]))
    with pytest.raises(ValueError, match="KNOWLEDGE_IMPORT_SOURCE_"):
        read(paths)


def test_wrong_field_binding_rejected(tmp_path):
    paths = export(tmp_path)
    paths[2]["columns"]["状态"]["field_id"] = "wrongField"
    paths[1].write_text(json.dumps(paths[2]))
    with pytest.raises(ValueError, match="FIELDS_MISMATCH"):
        read(paths)


def test_review_record_with_no_content_is_preserved_without_leaking_summary(tmp_path):
    rows, digest = read(export(tmp_path))
    record = preparation.normalize_record(rows[0])
    summary = preparation.source_summary([record])
    assert len(digest) == 64
    assert summary["records"] == 1
    assert summary["source_status_counts"] == {"待复核": 1}
    assert summary["without_content_reference"] == 1
    assert "Synthetic user" not in json.dumps(summary)
    assert "Synthetic material" not in json.dumps(summary)


def test_payload_write_is_private_and_cannot_overwrite(tmp_path):
    rows, _ = read(export(tmp_path))
    body = preparation.KnowledgeImportInput(
        workspace_id=UUID(int=1), owner_user_id=UUID(int=2), base_id="baseTest", table_id="tblTest",
        source_revision=292, grants=[], records=[preparation.normalize_record(rows[0])],
    )
    output = tmp_path / "body.json"
    preparation.write_private_body(output, body)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text())["dry_run"] is True
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        preparation.write_private_body(output, body)
    assert output.read_bytes() == before
