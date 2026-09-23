"""RLS facts, real risk persistence and human resolution; synthetic HTTP only."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date
from uuid import uuid4

import httpx
import pytest

from sales_backend.db import Database, set_request_context
from sales_backend.domain.agent import AgentMode
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.assistant import AssistantRepository, MessageReplayConflict
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.risks import RiskNotFound, RiskRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_run import handler as business
from tests.test_fde_facts_runtime import Chunks, frame
from tests.test_personal_risk_platform import ANSWER, configured

from .provision import create_owned_customer

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("scenario", [
    "normal", "empty", "blocked", "off", "foreign_visit", "duplicates", "both_failed", "accepted",
])
async def test_risk_business_keeps_owner_pending_and_human_resolution_boundaries(
    connection, sales_actor, monkeypatch, scenario,
):
    other = (await IdentityRepository().find_actor_by_account(
        connection, workspace_external_id="demo-sales-workspace", account_code="XS002",
    )).context

    async def owned_visit(actor):
        customer = await create_owned_customer(connection, actor, data={
            "name": "个人风险隔离测试" + uuid4().hex, "industry": "软件", "customer_type": "潜在客户",
            "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
            "contact_name": "合成人员", "contact_title": "经理", "contact_role": "决策者",
        })
        visit = await VisitRepository().create(connection, actor, customer_id=customer["id"], fields={
            "interaction_at": date.today().isoformat(), "created_date": date.today().isoformat(),
            "contact_name": "合成人员", "follow_up_record": "客户预算尚未获批，需要财务确认后才可推进。",
            "next_action": "下周一销售与财务确认预算进度", "_follow_up_quality_score": 85,
        })
        return customer, visit

    _, foreign_visit = await owned_visit(other)
    customer, visit = await owned_visit(sales_actor)
    config = configured(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    raw = next(iter(json.loads(config.agent_fde_pilot_json).values()))
    raw["capabilities"]["personal_risks"]["user_ids"] = [sales_actor.user_id]
    bindings = next(iter(json.loads(config.agent_platform_bindings_json).values()))
    config = replace(config, agent_fde_pilot_json=json.dumps({sales_actor.workspace_id: raw}),
                     agent_platform_bindings_json=json.dumps({sales_actor.workspace_id: bindings}),
                     senseaudio_api_key="synthetic-original-key", senseaudio_base_url="https://original.invalid",
                     agent_inference_platform_seconds=2, agent_inference_total_seconds=5)

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    database = Database(config, pool=Pool())
    answer = deepcopy(ANSWER)
    answer["risks"][0]["source_visit_id"] = visit["id"]
    payload = deepcopy(answer)
    if scenario == "empty":
        payload["risks"] = []
    elif scenario == "foreign_visit":
        payload["risks"][0]["source_visit_id"] = foreign_visit["id"]
    elif scenario == "duplicates":
        payload["risks"] *= 2
    # Model-supplied ownership, object IDs and status never become write authority.
    if payload["risks"]:
        payload["risks"][0].update(owner_user_ref_id=other.user_id, customer_id=str(uuid4()), status="resolved")
    calls = []

    def platform_wire(request):
        calls.append("agent_platform")
        supplied = json.loads(json.loads(request.content)["query"])
        assert supplied["mode"] == "personal_risks"
        assert [v["source_visit_id"] for v in supplied["facts"]["visits"]] == [visit["id"]]
        assert supplied["facts"]["visits"][0]["customer_id"] == customer["id"]
        assert foreign_visit["id"] not in json.dumps(supplied)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Chunks([
            frame("message", answer=json.dumps(payload)), frame("message_end"),
        ]))

    def original_wire(request):
        calls.append("senseaudio")
        if scenario == "both_failed":
            return httpx.Response(503, json={"error": "synthetic failure"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    def runtime_factory(db, settings, **kwargs):
        runtime = filtered_facts_runtime(db, settings, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(platform_wire))
        return runtime

    assistant, risks = AssistantRepository(), RiskRepository()
    before_customer = await connection.fetchrow("SELECT * FROM crm.customer WHERE id=$1::uuid", customer["id"])
    before_opps = await connection.fetch("SELECT * FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"])
    before_notifications = await connection.fetchval("SELECT count(*) FROM workflow.notification")

    async def run_analysis():
        conversation = await assistant.create_conversation(
            connection, sales_actor, mode=AgentMode.PERSONAL_RISKS, customer_id=None,
        )
        message = dict(conversation_id=conversation["id"], text="查看个人风险", client_message_id=str(uuid4()),
                       input_source="text")
        run_id = await assistant.enqueue_message(connection, sales_actor, **message)
        assert await assistant.enqueue_message(connection, sales_actor, **message) == run_id
        with pytest.raises(MessageReplayConflict):
            await assistant.enqueue_message(connection, sales_actor, **{**message, "text": "不同内容"})
        await business.AgentRunHandler(database, config).handle(run_id, sales_actor)
        return run_id, await assistant.get_run(connection, run_id=run_id)

    async with httpx.AsyncClient(base_url="https://original.invalid",
                                transport=httpx.MockTransport(original_wire)) as http:
        def direct_factory(settings, **kwargs):
            return SenseAudioClient(settings, client=http, **kwargs)

        monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
        monkeypatch.setattr(business, "InferenceService", lambda db, settings, **kw: InferenceService(
            db, settings, direct_factory=direct_factory, **kw,
        ))
        monkeypatch.setattr(business, "SenseAudioClient", direct_factory)
        if scenario == "both_failed":
            with pytest.raises(SenseAudioError) as error:
                await run_analysis()
            assert error.value.retryable is False
            assert await connection.fetchval("SELECT count(*) FROM insight.risk") == 0
            assert await connection.fetchval("SELECT count(*) FROM agent.message WHERE sender_type='assistant'") == 0
            return
        run_id, public = await run_analysis()
        assert public["status"] == "succeeded" and public["result"]["artifact_id"] is None
        assert "inference_route" not in str(public)
        assert await connection.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 0
        assert await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id) == 1
        rows = await connection.fetch("SELECT * FROM insight.risk WHERE source_visit_id=$1::uuid", visit["id"])
        assert len(rows) == (0 if scenario == "empty" else 1)
        assert not await connection.fetchval(
            "SELECT count(*) FROM insight.risk WHERE source_visit_id=$1::uuid", foreign_visit["id"],
        )
        platform_cases = {"normal", "empty", "foreign_visit", "duplicates", "accepted"}
        expected = ["agent_platform"] if scenario in platform_cases else []
        if scenario in {"off", "blocked", "foreign_visit", "duplicates"}:
            expected.append("senseaudio")
        assert calls == expected
        context = await connection.fetchval("SELECT business_context FROM agent.run WHERE id=$1::uuid", run_id)
        if scenario != "off":
            trace = context["inference_route"]
            attempts = await connection.fetch(
                "SELECT provider_code,operation_id::text FROM agent.model_invocation WHERE run_id=$1::uuid "
                "ORDER BY started_at", run_id,
            )
            assert all(row["operation_id"] == trace["operation_id"] for row in attempts)
            assert attempts[-1]["provider_code"] == calls[-1]
        if rows:
            risk = rows[0]
            rid = str(risk["id"])
            assert risk["status"] == "pending" and str(risk["owner_user_ref_id"]) == sales_actor.user_id
            assert str(risk["customer_id"]) == customer["id"] and risk["opportunity_id"] is None
            expected_model = context["inference_route"]["model_ref"] if scenario != "off" else config.llm_model
            assert risk["model_ref"] == expected_model
            assert await connection.fetchval("SELECT count(*) FROM insight.risk_event WHERE risk_id=$1::uuid", rid) == 1
            if scenario in {"normal", "accepted"}:
                # A new request reanalyzes one logical risk, without duplicate rows.
                _, repeat = await run_analysis()
                risk = await risks.detail(connection, risk_id=rid)
                assert risk["version_no"] == 2 and len(repeat["result"]["rows"]) == 1
                if scenario == "normal":
                    # Only this separate human action resolves the risk.
                    await risks.resolve(connection, actor=sales_actor, risk_id=rid,
                                        resolution_note="人工确认预算已获批", expected_version=2)
                    closed_status = "resolved"
                else:
                    await connection.execute("UPDATE insight.risk SET status='accepted' WHERE id=$1::uuid", rid)
                    closed_status = "accepted"
                closed = await connection.fetchrow("SELECT * FROM insight.risk WHERE id=$1::uuid", rid)
                _, after = await run_analysis()
                assert after["result"]["rows"] == []
                assert await connection.fetchrow("SELECT * FROM insight.risk WHERE id=$1::uuid", rid) == closed
                assert closed["status"] == closed_status
            await set_request_context(connection, other)
            assert await risks.detail(connection, risk_id=rid) is None
            with pytest.raises(RiskNotFound):
                await risks.resolve(connection, actor=other, risk_id=rid, resolution_note="无权解除")
            assert await assistant.get_run(connection, run_id=run_id) is None
            await set_request_context(connection, sales_actor)
    assert await connection.fetchrow("SELECT * FROM crm.customer WHERE id=$1::uuid", customer["id"]) == before_customer
    assert await connection.fetch(
        "SELECT * FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
    ) == before_opps
    assert await connection.fetchval("SELECT count(*) FROM workflow.notification") == before_notifications
