"""Paused routes must not parse uploads, use a database or call a provider."""
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.knowledge_availability import (
    KnowledgeTemporarilyUnavailable, is_knowledge_path, knowledge_availability_boundary,
)
from sales_backend.main import app


@pytest.mark.asyncio
async def test_all_registered_knowledge_routes_reject_before_validation_or_database():
    routes = [(method.upper(), path) for path, operations in app.openapi()["paths"].items()
              if is_knowledge_path(path) for method in operations
              if method in {"get", "head", "post", "put", "patch", "delete"}]
    assert len(routes) >= 40
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, template in routes:
            path = re.sub(r"\{[^}]+\}", "invalid-id", template)
            response = await client.request(method, path, content=b"not-valid-json-or-upload")
            assert response.status_code == 503, (method, path, response.status_code)
            body = response.json()
            assert body["code"] == "KNOWLEDGE_TEMPORARILY_UNAVAILABLE"
            assert body["message"] == "知识库暂不能使用"
            assert body["retryable"] is False and body["recovery_action"] == "contact_admin"
            assert response.headers["cache-control"] == "no-store"
            assert app.openapi()["paths"][template][method.lower()]["x-knowledge-availability"] == "temporarily_unavailable"


@pytest.mark.asyncio
async def test_pause_does_not_intercept_business_routes():
    isolated = FastAPI()
    isolated.middleware("http")(knowledge_availability_boundary)

    @isolated.get("/{path:path}")
    async def business(path):
        return {"path": path}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated), base_url="http://test") as client:
        for path in ("/api/v1/materials", "/api/v1/audio/transcribe", "/api/v1/advice",
                     "/api/v1/work-reports", "/api/v1/tasks", "/api/v1/knowledge-other"):
            response = await client.get(path)
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_knowledge_queues_pause_without_consuming_attempts_and_other_jobs_continue():
    from .test_worker_scheduling import MemoryDatabase, MemoryQueue, job
    from sales_backend.worker import Worker
    from sales_backend.repositories.feishu_sync import FeishuRepository
    from sales_backend.services.knowledge_distribution_sync import enqueue_knowledge_distribution_event

    pending, ordinary = job("knowledge.ingest"), job("business.advice")
    worker = Worker(MemoryDatabase())
    worker.jobs = MemoryQueue([pending, ordinary])
    worker._execute_claimed_job = AsyncMock(return_value=True)
    assert await worker.run_once()
    assert worker.jobs.claimed == [ordinary] and worker.jobs.pending == [pending]
    assert not await worker.run_once(job_types=("knowledge.ingest",))
    assert not await worker.run_once(job_types=("visit.import",), exclude_types=True)

    connection = SimpleNamespace(fetchrow=AsyncMock(), fetchval=AsyncMock())
    await FeishuRepository().claim(connection)
    query, *_args = connection.fetchrow.await_args.args
    assert "($3::boolean OR e.object_kind <> 'knowledge')" in query and _args[-1] is False
    assert await enqueue_knowledge_distribution_event(connection, workspace_id=None, material_id=None,
        publication_id=None, version_id=None, event_kind="published", revision=1, actor_id=None) is None
    connection.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_knowledge_adapters_reject_before_io():
    from sales_backend.services.knowledge import KnowledgeService
    from sales_backend.services.knowledge_provider import KnowledgeProviderClient
    from sales_backend.services.knowledge_document_workflow import DocumentWorkflowClient
    from sales_backend.services.knowledge_ingestion import KnowledgeIngestionService
    from sales_backend.services.feishu_sync import FeishuSyncService

    provider = KnowledgeProviderClient.__new__(KnowledgeProviderClient)
    workflow = DocumentWorkflowClient.__new__(DocumentWorkflowClient)
    knowledge = KnowledgeService.__new__(KnowledgeService)
    ingestion = KnowledgeIngestionService.__new__(KnowledgeIngestionService)
    sync = FeishuSyncService.__new__(FeishuSyncService)
    calls = [lambda: provider._request("GET", "/anything"), lambda: provider.download_document("x", "y"),
             lambda: workflow.generate("text", user="user"), lambda: workflow.download_run("x"),
             lambda: knowledge.ask(None, None), lambda: knowledge.generate_work(None, None, None),
             lambda: ingestion.handle(None, None), lambda: sync.handle(None, {"object_kind": "knowledge"}, None)]
    for call in calls:
        with pytest.raises(KnowledgeTemporarilyUnavailable):
            await call()
