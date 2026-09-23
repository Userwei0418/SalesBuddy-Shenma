"""Actual RLS facts, candidate persistence and explicit confirmation; synthetic HTTP."""

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from uuid import uuid4

import httpx
import pytest

from sales_backend.contracts.models import OpportunityCreate
from sales_backend.db import Database, set_request_context
from sales_backend.domain.agent import AgentMode
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.assistant import AssistantRepository, MessageReplayConflict
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_run import handler as business
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.opportunities import save_opportunity
from tests.test_fde_facts_runtime import Chunks, frame
from tests.test_opportunity_platform import ANSWER, configured

from .provision import create_owned_customer

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("scenario", ["create", "update", "none", "blocked", "off", "foreign_id"])
async def test_agent_only_saves_candidate_and_confirmation_retains_business_boundaries(
    connection, sales_actor, monkeypatch, scenario,
):
    customer = await create_owned_customer(connection, sales_actor, data={
        "name": "商机Agent隔离测试" + uuid4().hex, "industry": "软件", "customer_type": "潜在客户",
        "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "合成人员", "contact_title": "经理", "contact_role": "决策者",
    })
    original = await save_opportunity(connection, sales_actor, customer_id=customer["id"], data={
        "name": "已有项目", "probability": 30, "amount": 90000, "expected_close_date": date(2026, 12, 1),
        "quarterly_forecasts": [dict(year=2026,quarter=4,recognized_amount=0,collection_amount=0)],
    })
    config = configured(enabled=scenario != "off", blocked=scenario == "blocked")
    raw = next(iter(json.loads(config.agent_fde_pilot_json).values()))
    raw["capabilities"]["opportunity_draft"]["user_ids"] = [sales_actor.user_id]
    bindings = next(iter(json.loads(config.agent_platform_bindings_json).values()))
    config = replace(
        config, agent_fde_pilot_json=json.dumps({sales_actor.workspace_id: raw}),
        agent_platform_bindings_json=json.dumps({sales_actor.workspace_id: bindings}),
        senseaudio_api_key="synthetic-original-key", senseaudio_base_url="https://original.invalid",
        agent_inference_platform_seconds=2, agent_inference_total_seconds=5,
    )

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    database = Database(config, pool=Pool())
    candidate = dict(ANSWER)
    if scenario == "update":
        candidate = {"action": "update", "opportunity_id": original["id"], "amount": 1200}
    if scenario == "none":
        candidate = {"action": "none", "name": "必须清空", "amount": 80000}
    if scenario == "foreign_id":
        candidate = {"action": "update", "opportunity_id": str(uuid4())}
    calls = []

    def platform_wire(request):
        calls.append("agent_platform")
        supplied = json.loads(json.loads(request.content)["query"])
        assert supplied["mode"] == "opportunity_draft"
        assert supplied["facts"]["customer"]["id"] == customer["id"]
        assert [row["id"] for row in supplied["facts"]["current_opportunities"]] == [original["id"]]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks([
            frame("message", answer=json.dumps(candidate)), frame("message_end"),
        ]))

    def original_wire(request):
        calls.append("senseaudio")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(ANSWER)}}]})

    def runtime_factory(db, settings, **kwargs):
        runtime = filtered_facts_runtime(db, settings, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(platform_wire))
        return runtime

    repo = AssistantRepository()
    conversation = await repo.create_conversation(
        connection, sales_actor, mode=AgentMode.OPPORTUNITY_DRAFT, customer_id=customer["id"],
    )
    message = dict(conversation_id=conversation["id"], text="合成候选原文", client_message_id=str(uuid4()),
                   input_source="text")
    run_id = await repo.enqueue_message(connection, sales_actor, **message)
    assert await repo.enqueue_message(connection, sales_actor, **message) == run_id
    with pytest.raises(MessageReplayConflict):
        await repo.enqueue_message(connection, sales_actor, **{**message, "text": "不同原文"})
    before = await connection.fetch("SELECT * FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"])
    notifications_before = await connection.fetchval("SELECT count(*) FROM workflow.notification")
    async with httpx.AsyncClient(base_url="https://original.invalid",
                                transport=httpx.MockTransport(original_wire)) as http:
        def direct_factory(settings, **kwargs):
            return SenseAudioClient(settings, client=http, **kwargs)

        monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
        monkeypatch.setattr(business, "InferenceService", lambda db, settings, **kw: InferenceService(
            db, settings, direct_factory=direct_factory, **kw,
        ))
        monkeypatch.setattr(business, "SenseAudioClient", direct_factory)
        await business.AgentRunHandler(database, config).handle(run_id, sales_actor)
    assert await connection.fetch(
        "SELECT * FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
    ) == before
    assert await connection.fetchval("SELECT count(*) FROM workflow.notification") == notifications_before
    assert await connection.fetchval("SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid", run_id) == 1
    assert await connection.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 1
    assert await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id) == 1
    public = await repo.get_run(connection, run_id=run_id)
    assert public["status"] == "waiting_human"
    result = public["result"]
    assert "inference_route" not in str(public) and "ControlledPlatformBlock" not in str(public)
    artifact = await connection.fetchrow(
        "SELECT status,payload,model_ref FROM agent.artifact WHERE run_id=$1::uuid", run_id,
    )
    assert artifact["status"] == "pending_confirm"
    assert artifact["payload"]["action"] == result["action"]
    if scenario == "none":
        assert result["action"] == "none" and result["amount"] is None and result["name"] == ""
    elif scenario == "update":
        assert result["opportunity_id"] == original["id"] and result["amount"] == 1200
        assert result["probability"] is None
    else:
        assert result["action"] == "create" and result["amount"] == 80000
    expected_calls = ["agent_platform"] if scenario in {"create", "update", "none", "foreign_id"} else []
    if scenario in {"off", "blocked", "foreign_id"}:
        expected_calls.append("senseaudio")
    assert calls == expected_calls
    context = await connection.fetchval("SELECT business_context FROM agent.run WHERE id=$1::uuid", run_id)
    if scenario != "off":
        trace = context["inference_route"]
        assert artifact["model_ref"] == trace["model_ref"]
        attempts = await connection.fetch(
            "SELECT provider_code,operation_id::text FROM agent.model_invocation WHERE run_id=$1::uuid "
            "ORDER BY started_at", run_id,
        )
        assert all(item["operation_id"] == trace["operation_id"] for item in attempts)
        assert attempts[-1]["provider_code"] == calls[-1]
        if scenario in {"blocked", "foreign_id"}:
            assert len(attempts) == 2
            assert trace["fallback_reason"] == (
                "ControlledPlatformBlock" if scenario == "blocked" else "InvalidAgentResult"
            )

    # A separate explicit human save, not candidate generation, changes CRM.
    if scenario in {"create", "update"}:
        confirmation = OpportunityCreate.model_validate({
            "quarterly_forecasts": [dict(year=2026,quarter=4,recognized_amount=0,collection_amount=0)],
            "action": result["action"], "opportunity_id": result["opportunity_id"] or None,
            "name": result["name"] or original["name"], "amount": result["amount"],
            "probability": result["probability"] or original["probability"], "status": "open",
            "expected_close_date": result["expected_close_date"] or original["expected_close_date"],
            "version_no": original["version_no"] if scenario == "update" else None,
        }).model_dump()
        key = uuid4()

        async def confirm():
            return await execute_mutation(
                connection, sales_actor, key, f"opportunities.save:{customer['id']}", confirmation,
                lambda: save_opportunity(connection, sales_actor, customer_id=customer["id"], data=confirmation),
            )

        saved = await confirm()
        assert await confirm() == saved
        assert saved["operation"] == ("created" if scenario == "create" else "updated")
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
        ) == (2 if scenario == "create" else 1)
        assert await connection.fetchval("SELECT count(*) FROM workflow.notification") == notifications_before + 1

    other = await IdentityRepository().find_actor_by_account(
        connection, workspace_external_id="demo-sales-workspace", account_code="XS002",
    )
    await set_request_context(connection, other.context)
    assert await repo.get_run(connection, run_id=run_id) is None
    assert await connection.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 0
