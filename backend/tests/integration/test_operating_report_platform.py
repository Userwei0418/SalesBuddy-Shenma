"""Real identity/facts/RLS/persistence with synthetic HTTP on each provider."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from sales_backend.db import Database, set_request_context
from sales_backend.domain.agent import AgentMode, RoleCode
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.assistant import AssistantRepository, MessageReplayConflict
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.contract import INSTANT_SUMMARY_CONTRACT
from sales_backend.services.tasks import TaskService
from tests.test_fde_facts_runtime import Chunks, frame
from tests.test_operating_report_platform import answer, configured

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
@pytest.mark.parametrize("scenario", ["normal", "off", "blocked", "timeout", "bad_sections", "both_failed"])
async def test_report_role_scoped_facts_and_one_card_without_business_writes(
    connection, actor_factory, monkeypatch, role, scenario
):
    actor = await actor_factory(role)
    supervisor = await actor_factory(RoleCode.SUPERVISOR)
    await set_request_context(connection, supervisor)
    task = await TaskService().create(
        connection,
        actor=supervisor,
        description="即时总结只建议不执行",
        assignee_account_code="XS001",
        due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="medium",
    )
    await set_request_context(connection, actor)
    settings = configured(actor, enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    settings = replace(
        settings,
        senseaudio_api_key="synthetic-original-key",
        senseaudio_base_url="https://original.invalid",
        agent_inference_platform_seconds=0.1 if scenario == "timeout" else 2,
        agent_inference_total_seconds=5,
    )

    class Pool:
        lock = asyncio.Lock()

        @asynccontextmanager
        async def acquire(self):
            async with self.lock:
                yield connection

    database = Database(settings, pool=Pool())
    valid = answer(role)
    valid["action_plan"] = [
        {"title": "按已有任务推进", "detail": "只是建议", "task_id": task["id"], "status": "completed"}
    ]
    payload = {**valid, "inference_route": {"provider": "forged"}}
    if scenario == "bad_sections":
        payload = {"summary": "缺少角色段落"}
    calls, supplied = [], []

    def platform_wire(request):
        calls.append("agent_platform")
        query = json.loads(json.loads(request.content)["query"])
        supplied.append(query)
        assert query["mode"] == "operating_report" and query["role"] == actor.role.value
        assert query["facts"]["scope"]["user_id"] == actor.user_id
        assert set(query["facts"]) >= {"tasks", "visits", "opportunities", "risks"}
        if actor.role is RoleCode.SALES:
            assert all(t["assignee_user_ref_id"] == actor.user_id for t in query["facts"]["tasks"])
        stream = (
            Chunks([], stall=True)
            if scenario == "timeout"
            else Chunks([frame("message", answer=json.dumps(payload)), frame("message_end")])
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def original_wire(request):
        calls.append("senseaudio")
        if scenario == "both_failed":
            return httpx.Response(503, json={"error": "synthetic failure"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid)}}]})

    def runtime_factory(db, config, **kwargs):
        runtime = filtered_facts_runtime(db, config, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(platform_wire))
        return runtime

    async def snapshot():
        return {
            table: await connection.fetch(
                f"SELECT * FROM {table} ORDER BY "  # noqa: S608 - fixed table list
                + ("task_id,assignee_user_ref_id,responsibility" if table == "workflow.task_assignee" else "id")
            )  # noqa: S608
            for table in (
                "crm.customer",
                "crm.opportunity",
                "activity.visit",
                "workflow.task",
                "workflow.task_assignee",
                "workflow.notification",
                "insight.risk",
            )
        }

    before = await snapshot()
    repo = AssistantRepository()
    conversation = await repo.create_conversation(connection, actor, mode=AgentMode.OPERATING_REPORT, customer_id=None)
    body = dict(
        conversation_id=conversation["id"],
        text="生成当前经营即时总结",
        client_message_id=str(uuid4()),
        input_source="text",
    )
    run_id = await repo.enqueue_message(connection, actor, **body)
    assert await repo.enqueue_message(connection, actor, **body) == run_id
    with pytest.raises(MessageReplayConflict):
        await repo.enqueue_message(connection, actor, **{**body, "text": "different"})
    async with httpx.AsyncClient(
        base_url="https://original.invalid", transport=httpx.MockTransport(original_wire)
    ) as http:

        def direct_factory(config, **kwargs):
            return SenseAudioClient(config, client=http, **kwargs)

        monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
        monkeypatch.setattr(
            business,
            "InferenceService",
            lambda db, config, **kw: InferenceService(db, config, direct_factory=direct_factory, **kw),
        )
        monkeypatch.setattr(business, "SenseAudioClient", direct_factory)
        handler = business.AgentRunHandler(database, settings)
        if scenario == "both_failed":
            with pytest.raises(SenseAudioError) as exc:
                await handler.handle(run_id, actor)
            assert not exc.value.retryable
            assert (
                await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id)
                == 0
            )
            assert await snapshot() == before
            return
        await handler.handle(run_id, actor)
    public = await repo.get_run(connection, run_id=run_id)
    assert public["status"] == "succeeded" and public["result"]["artifact_id"] is None
    assert public["result"]["title"] == INSTANT_SUMMARY_CONTRACT[role][0]
    assert public["result"]["action_plan"] == [{"title": "按已有任务推进", "detail": "只是建议"}]
    assert "inference_route" not in public["result"]
    assert await snapshot() == before
    assert await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id) == 1
    assert await connection.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 0
    attempts = await connection.fetch(
        "SELECT provider_code,status,http_status,operation_id FROM agent.model_invocation "
        "WHERE run_id=$1::uuid AND record_kind='provider_attempt' ORDER BY started_at",
        run_id,
    )
    assert attempts[-1]["status"] == "succeeded" and attempts[-1]["http_status"] == 200
    assert len({r["operation_id"] for r in attempts}) == 1
    if scenario == "normal":
        assert calls == ["agent_platform"]
    elif scenario in {"off", "blocked"}:
        assert calls == ["senseaudio"] and not supplied
    else:
        assert calls == ["agent_platform", "senseaudio"]
    other = (
        await IdentityRepository().find_actor_by_account(
            connection, workspace_external_id="demo-sales-workspace", account_code="XS002"
        )
    ).context
    await set_request_context(connection, other)
    assert await repo.get_run(connection, run_id=run_id) is None
    assert await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id) == 0
