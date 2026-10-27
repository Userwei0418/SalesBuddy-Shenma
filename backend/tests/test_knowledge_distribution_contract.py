"""Public headers and explicitly gated writes are exported from the actual routes."""

import pytest
from pydantic import ValidationError

from sales_backend.contracts.knowledge_distribution import PublicationView, PublicationWrite, Questionnaire
from sales_backend.main import app


def test_distribution_openapi_public_tokens_are_headers_not_urls():
    paths = app.openapi()["paths"]
    public = {p: methods for p, methods in paths.items() if p.startswith("/api/v1/public/knowledge/")}
    assert len(public) == 5
    for path, methods in public.items():
        assert "{token}" not in path
        for operation in methods.values():
            headers = {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "header"}
            assert headers["X-Knowledge-Share"]["required"]
            assert "Authorization" not in headers
    assert next(
        p for p in public["/api/v1/public/knowledge/responses"]["post"]["parameters"] if p["name"] == "Idempotency-Key"
    )["required"]
    for path, methods in paths.items():
        if not path.startswith("/api/v1/knowledge/distribution/"):
            continue
        for method, operation in methods.items():
            if method in {"post", "put"}:
                assert next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")["required"]


def test_questionnaire_has_exact_fields_and_disabled_required_is_invalid():
    with pytest.raises(ValidationError):
        Questionnaire(fields=[])
    with pytest.raises(ValidationError):
        Questionnaire(fields=[{"name": "name", "enabled": False, "required": True}] * 4)
    with pytest.raises(ValidationError):
        PublicationWrite(
            expected_revision=0,
            version_id="00000000-0000-0000-0000-000000000001",
            allow_preview=False,
            allow_download=False,
        )


def test_publication_view_keeps_authenticated_source_links_but_public_share_has_no_field():
    publication = PublicationView(
        material_id="00000000-0000-0000-0000-000000000001",
        content_name="来源资料",
        source_links=("https://materials.example.test/plan",),
    )
    assert publication.source_links == ("https://materials.example.test/plan",)


def test_publication_notification_choice_is_explicit_and_strict():
    fields = dict(expected_revision=0, version_id="00000000-0000-0000-0000-000000000001")
    assert PublicationWrite(**fields).notify is True
    assert PublicationWrite(**fields, notify=False).notify is False
    with pytest.raises(ValidationError):
        PublicationWrite(**fields, notify="false")
