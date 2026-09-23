from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from sales_backend.repositories.agent_audit import AgentAuditRepository
from sales_backend.services.agent_platform.audit import InferenceAudit
from tests.integration.test_operations_api import TransactionDatabase, client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def receipt(connection, sales, *, status="failed"):
    operation_id = str(uuid4())
    audit = InferenceAudit(
        TransactionDatabase(connection),
        sales,
        operation_id,
        mode="operating_report",
        run_id=None,
        facts={"visits": [{"private": "客户正文"}]},
        config={"platform_seconds": 12, "total_seconds": 45, "direct_model": "test-model"},
    )
    await audit.start()
    audit.event("route_failed", "agent_platform", error_code="TimeoutError")
    audit.event("fallback_requested", "agent_platform", reason="TimeoutError")
    audit.event("route_failed", "senseaudio", error_code="InvalidAgentResult")
    await audit.finish(status, trace={"operation_id": operation_id}, error_code="AgentUnavailable")
    return operation_id


async def test_failed_receipt_visible_to_management_not_other_sales_and_without_business_success(connection):
    sales = await actor(connection, "XS001")
    operation_id = await receipt(connection, sales)
    row = await connection.fetchrow("SELECT * FROM agent.inference_operation WHERE id=$1::uuid", operation_id)
    assert row["status"] == "failed"
    assert (
        await connection.execute("DELETE FROM agent.inference_operation WHERE id=$1::uuid", operation_id) == "DELETE 0"
    )
    assert (
        await connection.execute(
            "UPDATE agent.inference_operation SET status='accepted' WHERE id=$1::uuid", operation_id
        )
        == "UPDATE 0"
    )
    assert row["input_summary"]["record_counts"]["visits"] == 1
    assert "客户正文" not in str(row["input_summary"])
    await actor(connection, "XS002")
    assert (
        await connection.fetchval("SELECT count(*) FROM agent.inference_operation WHERE id=$1::uuid", operation_id) == 0
    )
    now = datetime.now(UTC)
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM security.agent_operation_rows($1,$2)",
            now - timedelta(days=1),
            now + timedelta(days=1),
        )
        == 0
    )
    await actor(connection, "OPS001")
    result = await AgentAuditRepository().cases(connection, start=now - timedelta(days=1), end=now + timedelta(days=1))
    item = next(r for r in result["items"] if r["id"] == operation_id)
    assert item["inference_status"] == "failed" and item["business_status"] is None
    assert item["events"][-1]["error_code"] == "InvalidAgentResult"
    assert item["assistant_results"] == 0 and not item["job_effect_recorded"]
    assert item["configuration"]["platform_seconds"] == 12


async def test_audit_http_filter_export_and_missing_auth(connection):
    sales = await actor(connection, "XS001")
    operation_id = await receipt(connection, sales)
    async with await client_for(connection) as client:
        assert (await client.get("/api/v1/console/ai/runs")).status_code == 401
        await sign_in(client)
        url = "/api/v1/console/ai/runs"
        result = await client.get(url, params={"outcome": "failed", "capability": "operating_report"})
        assert result.status_code == 200, result.text
        assert any(r["id"] == operation_id for r in result.json()["items"])
        assert "客户正文" not in result.text
        assert (await client.get(url, params={"capability": "visit_entry"})).json()["total"] == 0
        exported = await client.get(url + "/export", params={"outcome": "failed"})
        assert exported.status_code == 200 and operation_id in exported.content.decode("utf-8-sig")
        assert (await client.get(url, params={"limit": 101})).status_code == 422


async def test_audit_statistics_use_business_receipts_not_http_and_exclude_fault_tests(connection):
    from sales_backend.services.model_calls import DatabaseModelObserver

    sales = await actor(connection, "XS001")
    database = TransactionDatabase(connection)
    operation_ids = []
    for final, provider, fallback, test_fault in [
        ("accepted", "agent_platform", False, False),
        ("failed", "agent_platform", False, False),
        ("accepted", "senseaudio", True, False),
        ("accepted", "senseaudio", True, True),
        ("running", None, False, False),
    ]:
        operation_id = str(uuid4())
        operation_ids.append(operation_id)
        audit = InferenceAudit(database, sales, operation_id, mode="operating_report", run_id=None, facts={}, config={})
        await audit.start()
        if final == "running":
            await connection.execute(
                "UPDATE agent.inference_operation SET started_at=now()-interval '20 minutes' WHERE id=$1::uuid",
                operation_id,
            )
            continue
        observer = DatabaseModelObserver(
            database, sales, "operating_report", operation_id=operation_id, provider="agent_platform"
        )
        invocation = await observer.start(
            "chat-messages",
            "fixture",
            {
                "test_fault_injected": test_fault,
                "network_dispatch_suppressed": test_fault,
            },
            1,
        )
        # HTTP 200 alone must not qualify the failed contract above.
        await observer.finish(invocation, httpx.Response(200, json={}), None)
        if fallback:
            audit.event("fallback_requested", "agent_platform", reason="TimeoutError")
        await audit.finish(final, trace={"provider": provider, "fallback_reason": "TimeoutError" if fallback else None})
    now = datetime.now(UTC)
    await actor(connection, "OPS001")
    repo = AgentAuditRepository()
    args = {"start": now - timedelta(days=1), "end": now + timedelta(days=1), "capability": "operating_report"}
    result = await repo.cases(connection, **args)
    assert result["total"] == 5
    assert result["statistics"] == {
        "eligible": 4,
        "excluded_fault_tests": 1,
        "platform_attempted": 3,
        "platform_accepted": 1,
        "fallback_requested": 1,
        "fallback_accepted": 1,
        "rule_fallback_accepted": 0,
        "direct_accepted": 0,
        "business_succeeded": 0,
        "waiting_human": 0,
        "unknown": 1,
    }
    stale = next(item for item in result["items"] if item["id"] == operation_ids[-1])
    assert stale["inference_status"] == "unknown" and "15分钟" in stale["receipt_warning"]
    assert not stale["job_effect_recorded"] and stale["business_status"] is None
    # An out-of-range page retains the total and the full-filter statistics.
    empty_page = await repo.cases(connection, **args, offset=100)
    assert empty_page["items"] == [] and empty_page["total"] == 5
    assert empty_page["statistics"] == result["statistics"]
    tests = await repo.cases(connection, **args, test_only=True)
    assert tests["total"] == 1 and tests["statistics"]["eligible"] == 0
    assert tests["statistics"]["platform_attempted"] == 0
    assert tests["statistics"]["fallback_requested"] == 0
    # A different business filter cannot leak these aggregates.
    empty = await repo.cases(connection, start=args["start"], end=args["end"], capability="visit_entry")
    assert empty["total"] == 0 and all(v == 0 for v in empty["statistics"].values())
