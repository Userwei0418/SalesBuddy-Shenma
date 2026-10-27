"""Adapter negative fixtures only; no real provider/network calls."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from sales_backend.services.knowledge import KnowledgeService
from sales_backend.services.knowledge_provider import KnowledgeProviderClient, KnowledgeProviderError

from .test_knowledge_provider import request, settings


def provider(handler, **config):
    return KnowledgeProviderClient(settings(**config), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,code",
    [
        ([{"id": "different", "run": "3", "chunk_count": 2}], "KNOWLEDGE_PROVIDER_DOCUMENT_NOT_FOUND"),
        ([{"id": "doc-1", "progress": 1, "chunk_count": 2}], "KNOWLEDGE_PROVIDER_PARSE_STATE_UNKNOWN"),
        ([{"id": "doc-1", "run": "3", "chunk_count": True}], "KNOWLEDGE_PROVIDER_RESPONSE_INVALID"),
        ([{"id": "doc-1", "run": "3", "chunk_count": -1}], "KNOWLEDGE_PROVIDER_RESPONSE_INVALID"),
        ([{"id": "doc-1", "run": "3", "dataset_id": "wrong", "chunk_count": 2}], "KNOWLEDGE_PROVIDER_RESPONSE_INVALID"),
        ([{"id": "doc-1", "run": "3", "chunk_count": 2}] * 2, "KNOWLEDGE_PROVIDER_RESPONSE_INVALID"),
    ],
)
async def test_status_requires_exact_document_and_explicit_done(rows, code):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"code": 0, "data": {"docs": rows}})

    with pytest.raises(KnowledgeProviderError, match=code):
        await provider(handler).document_status("dataset-a", "doc-1")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["../x", "a/b", "a?x=y", "a#x", "%2F", "x" * 301])
async def test_status_cannot_turn_document_identifier_into_a_path(bad):
    calls = []
    with pytest.raises(KnowledgeProviderError, match="DOCUMENT_INVALID"):
        await provider(lambda req: calls.append(req)).document_status("dataset-a", bad)
    assert not calls


@pytest.mark.asyncio
async def test_paginated_exact_status_and_run_mapping():
    calls = []

    def handler(req):
        assert req.url.path == "/api/v1/datasets/dataset-a/documents"
        calls.append(req.url.params["page"])
        rows = (
            [{"id": f"other-{i}"} for i in range(100)]
            if len(calls) == 1
            else [{"id": "doc-1", "run": "DONE", "chunk_num": 4}]
        )
        return httpx.Response(200, json={"code": 0, "data": {"docs": rows}})

    assert await provider(handler).document_status("dataset-a", "doc-1") == {
        "document_id": "doc-1",
        "state": "done",
        "chunk_count": 4,
    }
    assert calls == ["1", "2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [[], [{"id": "a"}, {"id": "b"}], [{"id": "a"}, None], {"id": "a"}])
async def test_single_file_upload_requires_exactly_one_receipt(data):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"code": 0, "data": data})

    with pytest.raises(KnowledgeProviderError, match="RESPONSE_INVALID"):
        await provider(handler).upload_document("dataset-a", "x.txt", b"hello", "text/plain")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_multiple_documents_same_source_use_only_bound_dataset():
    base = request()
    doc = base.documents[0].model_copy(update={"provider_dataset_id": "dataset-a"})
    req = type(base)(
        scope=base.scope, query=base.query, documents=(doc, doc.model_copy(update={"provider_document_id": "doc-2"}))
    )

    def handler(r):
        body = json.loads(r.content)
        assert body["dataset_ids"] == ["dataset-a"]
        assert body["document_ids"] == ["doc-1", "doc-2"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "chunks": [
                        {"document_id": d, "id": d + "-chunk", "content": "authorized"} for d in body["document_ids"]
                    ]
                },
            },
        )

    chunks = await provider(handler, knowledge_provider_dataset_ids='["dataset-a","other-company"]').retrieve(req)
    assert len(chunks) == 2 and all(c.source == doc.source for c in chunks)
    with pytest.raises(ValidationError):
        type(base)(scope=base.scope, query=base.query, documents=(doc, doc))


@pytest.mark.asyncio
async def test_multiple_configured_datasets_require_explicit_binding_no_network():
    calls = []
    with pytest.raises(KnowledgeProviderError, match="DATASET_NOT_FOUND"):
        await provider(lambda r: calls.append(r), knowledge_provider_dataset_ids='["dataset-a","other"]').retrieve(
            request()
        )
    assert not calls


@pytest.mark.asyncio
async def test_response_memory_bound_and_auth_error_do_not_leak_provider_text():
    client = provider(lambda r: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1)))
    with pytest.raises(KnowledgeProviderError, match="RESPONSE_INVALID"):
        await client.document_status("dataset-a", "doc-1")
    client = provider(lambda r: httpx.Response(401, text="private upstream message"))
    with pytest.raises(KnowledgeProviderError, match="UNAVAILABLE") as error:
        await client.document_status("dataset-a", "doc-1")
    assert "private" not in str(error.value) and error.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, {"workspace_id": "other"}, {"user_id": "other"},
                                   {"permission_version": None}, {"permission_version": ""},
                                   {"permission_version": "rbac-v1:broken"}])
async def test_authorization_snapshot_invalid_identity_or_version_never_falls_back(change):
    actor = SimpleNamespace(workspace_id="00000000-0000-0000-0000-000000000001",
                            user_id="00000000-0000-0000-0000-000000000002")
    snapshot = None if change is None else {
        "workspace_id": actor.workspace_id, "user_id": actor.user_id,
        "permission_version": "rbac-v1:" + "a" * 32, **change,
    }
    calls = []

    class Connection:
        async def fetchval(self, query):
            calls.append(query)
            assert query == "SELECT security.authorization_snapshot(NULL)"
            return snapshot

    with pytest.raises(PermissionError, match="KNOWLEDGE_ACCESS_DENIED"):
        await KnowledgeService.__new__(KnowledgeService)._knowledge_actor(Connection(), actor)
    assert len(calls) == 1
