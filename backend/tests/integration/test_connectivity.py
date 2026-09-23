from uuid import uuid4

import pytest

from sales_backend.services.connectivity import ConnectivityService
from tests.integration.test_operations_api import client_for, sign_in, TransactionDatabase
from sales_backend.config import get_settings
from sales_backend.worker import Worker
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_authenticated_probe_receipts_idempotency_limits_and_unchanged_bindings(connection, monkeypatch):
    calls = []

    async def fixed_probe(self, person, target, result):
        calls.append(target)
        result.update(status="passed", connection_status="passed", inference_status="passed", message="固定测试成功")

    monkeypatch.setattr(ConnectivityService, "direct", fixed_probe)
    await actor(connection, "ADMIN001")
    before = await connection.fetchval("SELECT count(*) FROM config.model_api_release")
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        key = str(uuid4())
        url = "/api/v1/admin/ai-connectivity/direct/text/tests"
        first = await client.post(url, json={"request_id": key})
        assert first.status_code == 200 and first.json()["status"] == "passed"
        second = await client.post(url, json={"request_id": key})
        assert second.status_code == 200 and second.json() == first.json() and calls == ["text"]
        assert (await client.get("/api/v1/admin/ai-connectivity/tests/" + key)).json() == first.json()
        assert (await client.post(url.replace("text", "asr"), json={"request_id": key})).status_code == 409
        assert (await client.post(url, json={"request_id": key, "api_key": "must-not-echo"})).status_code == 422
        for _ in range(4):
            assert (await client.post(url, json={"request_id": str(uuid4())})).status_code == 200
        assert (await client.post(url, json={"request_id": str(uuid4())})).status_code == 429
        assert await connection.fetchval("SELECT count(*) FROM config.model_api_release") == before
        await sign_in(client, "OPS001")
        assert (await client.post(url, json={"request_id": str(uuid4())})).status_code == 403
        assert (await client.get("/api/v1/admin/ai-connectivity/tests/" + key)).status_code == 403


async def test_agent_probe_is_executed_once_by_real_worker_and_receipt_is_polled(connection, monkeypatch):
    calls = []

    async def probe(self, person, target, result):
        calls.append(target)
        result.update(status="passed", connection_status="passed", inference_status="passed", message="测试回执")

    monkeypatch.setattr(ConnectivityService, "agent", probe)
    database = TransactionDatabase(connection)
    database.settings = get_settings()
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        key = str(uuid4())
        url = "/api/v1/admin/ai-connectivity/agent/customer_advice/tests"
        first = await client.post(url, json={"request_id": key})
        assert first.status_code == 200 and first.json()["status"] == "running" and not calls
        assert (await client.post(url, json={"request_id": key})).json() == first.json()
        queued = await connection.fetchrow("SELECT id,max_attempts FROM ops.job WHERE aggregate_id=$1::uuid", key)
        assert queued["max_attempts"] == 1
        assert await Worker(database).run_once(job_types=("ai.connectivity",))
        final = (await client.get("/api/v1/admin/ai-connectivity/tests/" + key)).json()
        assert final["status"] == "passed" and final["execution_process"] == "worker"
        assert calls == ["customer_advice"]
        assert (await client.post(url, json={"request_id": key})).json() == final
        assert not await Worker(database).run_once(job_types=("ai.connectivity",))
        assert await connection.fetchval("SELECT status FROM ops.job WHERE id=$1", queued["id"]) == "succeeded"
        assert await connection.fetchval("SELECT count(*) FROM ops.job_effect WHERE job_id=$1", queued["id"]) == 1
