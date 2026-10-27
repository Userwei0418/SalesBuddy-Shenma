import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from sales_backend.contracts.knowledge import (
    KnowledgeAccessScope,
    KnowledgeAllowedVersion,
    KnowledgeDocumentBinding,
    KnowledgeRetrievalRequest,
)
from sales_backend.services.knowledge_provider import (
    KnowledgeProviderClient,
    KnowledgeProviderError,
    agent_message_text,
)

NOW = datetime(2026, 10, 17, tzinfo=UTC)
COMPANY = UUID("00000000-0000-0000-0000-000000000001")
USER = UUID("00000000-0000-0000-0000-000000000002")
MATERIAL = UUID("00000000-0000-0000-0000-000000000003")
VERSION = UUID("00000000-0000-0000-0000-000000000004")


def settings(**overrides):
    values = {
        "knowledge_provider_base_url": "https://provider.example",
        "knowledge_provider_api_key": "secret",
        "knowledge_provider_auth_scheme": "bearer",
        "knowledge_provider_dataset_ids": '["dataset-a"]',
        "knowledge_provider_timeout_seconds": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def request():
    scope = KnowledgeAccessScope(
        company_id=COMPANY, user_id=USER, authorization_revision=1,
        observed_at=NOW,
        versions=(KnowledgeAllowedVersion(material_id=MATERIAL, version_id=VERSION, acl_revision=1),),
    )
    return KnowledgeRetrievalRequest(
        scope=scope, query="如何刷电调固件",
        documents=(KnowledgeDocumentBinding(
            company_id=COMPANY,
            source={"material_id": MATERIAL, "version_id": VERSION},
            provider_document_id="doc-1",
        ),),
    )


@pytest.mark.asyncio
async def test_provider_count_and_retrieval_use_confirmed_endpoints_and_auth():
    calls = []

    async def handler(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer secret"
        if req.url.path == "/api/v1/datasets":
            return httpx.Response(200, json={"code": 0, "data": [{"id": "dataset-a", "document_count": 122}]})
        assert req.url.path == "/api/v1/datasets/search"
        body = json.loads(req.content)
        assert body["dataset_ids"] == ["dataset-a"]
        assert body["doc_ids"] == ["doc-1"] and body["size"] == 20
        assert "document_ids" not in body and "page_size" not in body
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [
            {"doc_id": "doc-1", "chunk_id": "chunk-7", "content": "先断电，再校准电调。", "page": 3},
            {"doc_id": "other-doc", "chunk_id": "ignored", "content": "不得越过允许集合。"},
        ]}})

    client = KnowledgeProviderClient(
        settings(knowledge_provider_retrieval_path="/api/v1/datasets/search"),
        transport=httpx.MockTransport(handler),
    )
    count = await client.count()
    chunks = await client.retrieve(request())
    assert count.status == "available" and count.count == 122 and count.source == "knowledge.provider"
    assert len(chunks) == 1 and chunks[0].chunk_id == "chunk-7" and chunks[0].page == 3
    assert [call.url.path for call in calls] == ["/api/v1/datasets", "/api/v1/datasets/search"]


@pytest.mark.asyncio
async def test_provider_uses_official_retrieval_contract_by_default():
    calls = []

    async def handler(req):
        calls.append(req)
        assert req.url.path == "/api/v1/retrieval"
        body = json.loads(req.content)
        assert body["dataset_ids"] == ["dataset-a"]
        assert body["document_ids"] == ["doc-1"]
        assert body["page_size"] == 20 and body["top_k"] == 20
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [
            {"document_id": "doc-1", "id": "chunk-8", "content": "按授权材料执行。"},
        ]}})

    client = KnowledgeProviderClient(settings(), transport=httpx.MockTransport(handler))
    chunks = await client.retrieve(request())
    assert len(chunks) == 1 and chunks[0].chunk_id == "chunk-8"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_provider_document_lifecycle_and_agent_contracts():
    calls = []

    async def handler(req):
        calls.append(req)
        path = req.url.path
        if path.endswith("/documents") and req.method == "POST":
            assert req.url.params["type"] == "local"
            assert b"name.txt" in req.content and b"hello" in req.content
            return httpx.Response(200, json={"code": 0, "data": [{"id": "doc-new", "run": "UNSTART"}]})
        if path.endswith("/documents/doc-new") and req.method == "PUT":
            assert json.loads(req.content)["enabled"] == 0
            return httpx.Response(200, json={"code": 0, "data": {"id": "doc-new", "enabled": 0}})
        if path.endswith("/chunks") and req.method == "POST":
            assert json.loads(req.content) == {"document_ids": ["doc-new"]}
            return httpx.Response(200, json={"code": 0, "data": True})
        if path.endswith("/documents") and req.method == "DELETE":
            assert json.loads(req.content) == {"ids": ["doc-new"], "delete_all": False}
            return httpx.Response(200, json={"code": 0, "data": True})
        if path.endswith("/documents/doc-new") and req.method == "GET":
            return httpx.Response(200, content=b"provider bytes")
        if path.endswith("/agents_openai/agent-1/chat/completions"):
            body = json.loads(req.content)
            assert body["stream"] is False and body["messages"][0]["content"] == "hello"
            return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})
        raise AssertionError(f"unexpected request: {req.method} {req.url}")

    client = KnowledgeProviderClient(
        settings(knowledge_provider_agent_id="agent-1"), transport=httpx.MockTransport(handler)
    )
    uploaded = await client.upload_document("dataset-a", "name.txt", b"hello", "text/plain")
    assert uploaded[0]["id"] == "doc-new"
    assert (await client.update_document("dataset-a", "doc-new", enabled=0))["enabled"] == 0
    await client.parse_documents("dataset-a", ("doc-new",))
    assert await client.download_document("dataset-a", "doc-new") == b"provider bytes"
    await client.delete_documents("dataset-a", ("doc-new",))
    assert (await client.agent_completion("hello"))["choices"][0]["message"]["content"] == "answer"
    assert await client.generate_material("hello") == "answer"
    assert len(calls) == 7


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"choices": [{"message": {"content": "  answer  "}}]}, "answer"),
        ({"choices": [{"message": {"content": [{"text": "first"}, {"text": "second"}]}}]}, "first\nsecond"),
    ],
)
def test_agent_message_text_normalizes_documented_content_shapes(payload, expected):
    assert agent_message_text(payload) == expected


def test_agent_message_text_rejects_empty_or_untyped_provider_content():
    for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}):
        with pytest.raises(KnowledgeProviderError, match="RESPONSE_INVALID"):
            agent_message_text(payload)


@pytest.mark.asyncio
async def test_query_auth_is_preserved_when_document_upload_has_type_param():
    async def handler(req):
        assert req.url.params["type"] == "local"
        assert req.url.params["apiKey"] == "secret"
        assert "authorization" not in req.headers
        return httpx.Response(200, json={"code": 0, "data": [{"id": "doc-new"}]})

    client = KnowledgeProviderClient(
        settings(knowledge_provider_auth_scheme="query"), transport=httpx.MockTransport(handler)
    )
    assert (await client.upload_document("dataset-a", "name.txt", b"hello"))[0]["id"] == "doc-new"


@pytest.mark.asyncio
async def test_provider_count_never_treats_missing_configuration_as_zero():
    client = KnowledgeProviderClient(settings(knowledge_provider_dataset_ids=""))
    count = await client.count()
    assert count.status == "unconfigured" and count.count is None
    with pytest.raises(KnowledgeProviderError, match="UNCONFIGURED"):
        await client.datasets()


@pytest.mark.asyncio
async def test_provider_rejects_unknown_dataset_and_provider_failure():
    async def handler(req):
        return httpx.Response(200, json={"code": 0, "data": [{"id": "other", "document_count": 2}]})

    client = KnowledgeProviderClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(KnowledgeProviderError, match="DATASET_NOT_FOUND"):
        await client.count()

    async def failure(req):
        return httpx.Response(503, json={"code": 1})

    client = KnowledgeProviderClient(settings(), transport=httpx.MockTransport(failure))
    with pytest.raises(KnowledgeProviderError, match="UNAVAILABLE"):
        await client.count()
