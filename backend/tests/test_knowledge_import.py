"""Synthetic source shape and lossless legacy validation, with no Feishu access."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.knowledge_import import ImportRecord, KnowledgeImportInput, imported_contribution


def record(**changes):
    return ImportRecord(
        record_id="recSynthetic",
        original_name="合成材料原始名称",
        product_label="合成专用产品",
        category_label="客户方案",
        scope_label="仅内部",
        source_status="待复核",
        **changes,
    )


def body(**changes):
    return KnowledgeImportInput(
        workspace_id=uuid4(),
        owner_user_id=uuid4(),
        grants=[],
        base_id="baseSynthetic",
        table_id="tblSynthetic",
        source_revision=2,
        records=[record()],
        **changes,
    )


def test_legacy_missing_fields_are_preserved_not_invented():
    request = body()
    item = imported_contribution(request, request.records[0])
    assert item.content_name == "合成材料原始名称"
    assert item.selection.product_code.startswith("legacy_") and item.selection.product_code != "general"
    assert item.source.product_label == "合成专用产品"
    assert set(item.source.validation_issues) == {
        "SOURCE_REVIEW_REQUIRED",
        "MATERIAL_REFERENCE_MISSING",
        "INDUSTRY_NOT_MAPPED",
        "CUSTOMER_NOT_MAPPED",
        "REPORTING_ROLES_NOT_MAPPED",
    }
    assert "customer_id" not in item.selection.model_dump()
    assert not item.source.links and not item.source.attachments


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "https://user:secret@example.com/a",
        "ftp://example.com/a",
        "https://a.test/\nx",
        "https://?q=one",
        "https://[bad",
        "https://example.com:0/x",
        "https://example.com:65536/x",
    ],
)
def test_bad_source_links_are_rejected(link):
    with pytest.raises(ValidationError):
        record(links=[link])


def test_source_url_navigation_and_multiline_remarks_remain_valid():
    item = record(links=["https://docs.example.com:443/path?q=one#part"], remarks="line one\n\tline two")
    assert item.links[0].endswith("#part")
    with pytest.raises(ValidationError):
        record(remarks="x\x01y")


def test_unknown_category_is_not_silently_generalized():
    request = body()
    bad = request.records[0].model_copy(update={"category_label": "不认识的类别"})
    with pytest.raises(ValueError, match="UNMAPPED_ENUM"):
        imported_contribution(request, bad)


def test_duplicate_source_and_missing_explicit_acl_are_rejected():
    request = body()
    data = request.model_dump()
    with pytest.raises(ValidationError):
        KnowledgeImportInput(**{**data, "records": [request.records[0], request.records[0]]})
    data.pop("grants")
    with pytest.raises(ValidationError):
        KnowledgeImportInput(**data)


def test_normalizer_preserves_exported_markdown_and_attachment_reference_only():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_knowledge_import.py"
    spec = importlib.util.spec_from_file_location("prepare_kb_import", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    item = module.normalize_record(
        {
            "record_id": "recSynthetic",
            "产品": ["产品A"],
            "内容名称": "原始名称",
            "使用范围": ["可对客"],
            "内容类别": ["产品介绍"],
            "状态": ["可用"],
            "内容提供人": [{"id": "external-id", "name": "合成人"}],
            "内容材料": "[合成附件](https://example.com/file)",
            "备注": "保留",
            "附件": [{"file_token": "fileOpaqueReference", "name": "合成.pptx", "size": 1234}],
        }
    )
    assert item.links == ("https://example.com/file",)
    assert item.original_content_text == "[合成附件](https://example.com/file)"
    assert item.provider_name == "合成人" and item.attachments[0].source_token == "fileOpaqueReference"
    assert "file_ids" not in item.model_dump() and "retrievable" not in item.model_dump()
