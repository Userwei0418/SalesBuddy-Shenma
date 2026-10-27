"""Source archive protocol shape, route registration and bounded input."""

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.api.knowledge_source_files import router
from sales_backend.contracts.knowledge_source_files import SourceArchiveInput
from sales_backend.domain.route_permissions import route_permission


def body():
    return {
        "expected_material_revision": 1,
        "expected_acl_revision": 1,
        "expected_source_revision": 294,
        "attachments": [
            {"source_token": "sourceToken", "name": "原文件.pdf", "size_bytes": 52428800, "file_id": str(uuid4())}
        ],
    }


def test_new_routes_mounted_and_explicitly_classified():
    assert {(r.path, tuple(sorted(r.methods))) for r in router.routes} == {
        ("/api/v1/knowledge/materials/{material_id}/source-files", ("GET",)),
        ("/api/v1/knowledge/materials/{material_id}/source-files", ("POST",)),
    }
    for name in ["source_files", "archive_source_files"]:
        assert route_permission("knowledge_source_files", name) == "knowledge.catalog_manage"
    source = Path(__file__).parents[1] / "src/sales_backend/main.py"
    tree = ast.parse(source.read_text())
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "include_router"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "knowledge_source_files_router"
        for n in ast.walk(tree)
    )


@pytest.mark.parametrize("size", [0, -1, 52428801, "42", True])
def test_invalid_archive_size_rejected(size):
    data = body()
    data["attachments"][0]["size_bytes"] = size
    with pytest.raises(ValidationError):
        SourceArchiveInput.model_validate(data)


def test_exact_50_mib_allowed_and_no_caller_hash_or_unknown_input():
    data = body()
    assert SourceArchiveInput.model_validate(data).attachments[0].size_bytes == 52428800
    data["attachments"][0]["sha256"] = "a" * 64
    with pytest.raises(ValidationError):
        SourceArchiveInput.model_validate(data)


@pytest.mark.parametrize("field", ["source_token", "file_id"])
def test_duplicate_attachment_or_uploaded_file_is_rejected(field):
    data = body()
    second = {**data["attachments"][0], "source_token": "anotherToken", "file_id": str(uuid4())}
    second[field] = data["attachments"][0][field]
    data["attachments"].append(second)
    with pytest.raises(ValidationError):
        SourceArchiveInput.model_validate(data)
