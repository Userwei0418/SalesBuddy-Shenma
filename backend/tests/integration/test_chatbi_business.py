"""Current schema regressions for ChatBI retries, full totals and private traces."""

from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.config import get_settings
from sales_backend.domain.agent import AgentMode
from sales_backend.repositories.assistant import AssistantRepository, MessageReplayConflict
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.persist import AgentRunStore

from .provision import create_owned_customer

pytestmark = pytest.mark.asyncio


async def test_message_retry_returns_same_run_and_conflicting_text_rejected(connection, sales_actor):
    repo = AssistantRepository()
    conversation = await repo.create_conversation(connection, sales_actor, mode=AgentMode.CHATBI, customer_id=None)
    args = dict(
        conversation_id=conversation["id"], text="合成问数", client_message_id=str(uuid4()), input_source="text"
    )
    first = await repo.enqueue_message(connection, sales_actor, **args)
    assert await repo.enqueue_message(connection, sales_actor, **args) == first
    with pytest.raises(MessageReplayConflict):
        await repo.enqueue_message(connection, sales_actor, **{**args, "text": "不同内容"})
    with pytest.raises(MessageReplayConflict):
        await repo.enqueue_message(connection, sales_actor, **{**args, "input_source": "audio_transcript"})
    assert (
        await connection.fetchval("SELECT count(*) FROM agent.run WHERE conversation_id=$1::uuid", conversation["id"])
        == 1
    )
    assert await connection.fetchval("SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid", first) == 1


async def test_customer_totals_include_rows_beyond_detail_limit(connection, sales_actor):
    customer = await create_owned_customer(
        connection,
        sales_actor,
        data={
            "name": "问数合成回归" + uuid4().hex,
            "industry": "软件",
            "customer_type": "潜在客户",
            "level_code": "Tier-2",
            "source": "销售线索",
            "target_team": "南区",
            "partner_name": "",
            "contact_name": "合成联系人",
            "contact_title": "经理",
            "contact_role": "决策者",
        },
    )
    for index in range(27):
        await connection.execute(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,status,
                       owner_user_ref_id,created_by_user_ref_id,owner_team_id)
                 VALUES($1::uuid,$2::uuid,$3,100.01,$4,$5::uuid,$5::uuid,$6::uuid)""",
            sales_actor.workspace_id,
            customer["id"],
            f"合成商机{index}",
            "open" if index < 25 else "lost",
            sales_actor.user_id,
            sales_actor.team_ids[0],
        )
    task_opportunity = await connection.fetchval("SELECT id::text FROM crm.opportunity WHERE customer_id=$1::uuid LIMIT 1", customer["id"])
    for index in range(32):
        await connection.execute(
            """INSERT INTO workflow.task(workspace_id,customer_id,title,description,creator_user_ref_id,status,due_at,opportunity_id)
                 VALUES($1::uuid,$2::uuid,'合成任务','问数数量回归',$3::uuid,$4,clock_timestamp()+interval '1 day',$5::uuid)""",
            sales_actor.workspace_id,
            customer["id"],
            sales_actor.user_id,
            "pending_execution" if index < 25 else "completed",
            task_opportunity,
        )
    run = RunInput(str(uuid4()), str(uuid4()), "合成问数", "customer_chatbi", customer["id"], sales_actor)
    facts = await AgentFactsLoader(None)._load_customer_chatbi_facts(connection, run)
    assert facts["summary"]["open_opportunities"] == 25
    assert facts["summary"]["open_pipeline_amount_cny"] == Decimal("2500.25")
    assert facts["summary"]["open_tasks"] == 25
    assert facts["summary"]["completed_tasks"] == 7
    assert facts["detail_coverage"]["opportunities"] == {"total": 27, "returned": 20, "truncated": True}
    assert facts["detail_coverage"]["tasks"] == {"total": 32, "returned": 20, "truncated": True}


async def test_route_metadata_persists_without_changing_public_result(connection, sales_actor):
    class Database:
        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert actor == sales_actor
            yield connection

    repo = AssistantRepository()
    conversation = await repo.create_conversation(connection, sales_actor, mode=AgentMode.CHATBI, customer_id=None)
    run_id = await repo.enqueue_message(
        connection,
        sales_actor,
        conversation_id=conversation["id"],
        text="合成问数",
        client_message_id=str(uuid4()),
        input_source="text",
    )
    from sales_backend.repositories.capabilities import CapabilityRepository
    snapshot = await CapabilityRepository().analysis_identity(connection, sales_actor)
    run = RunInput(run_id, conversation["id"], "合成问数", "chatbi", None, sales_actor,
                   permission_version=snapshot["permission_version"])
    trace = {"provider": "senseaudio", "fallback_reason": "ControlledPlatformBlock", "model_ref": "synthetic"}
    result = {"summary": "合成结果", "metrics": [], "rows": []}
    await AgentRunStore(Database(), get_settings()).persist_result(
        run,
        result,
        {"data_as_of": "2026-09-12T00:00:00Z"},
        inference_trace=trace,
    )
    stored = await connection.fetchval("SELECT business_context FROM agent.run WHERE id=$1::uuid", run_id)
    assert stored["inference_route"] == trace
    public = await repo.get_run(connection, run_id=run_id)
    assert public["status"] == "succeeded"
    assert "inference_route" not in str(public)
    assert "ControlledPlatformBlock" not in str(public)
    assert await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id) == 1


async def test_transport_diagnostics_survive_failed_invocation_without_mutating_input(connection, sales_actor):
    import httpx

    from sales_backend.integrations.model_observer import response_usage
    from sales_backend.repositories.model_calls import ModelCallRepository

    repo = ModelCallRepository()
    invocation_id = await repo.start(
        connection,
        sales_actor,
        operation="chatbi",
        operation_id=str(uuid4()),
        run_id=None,
        endpoint="agent-chat-messages",
        model="agent:synthetic",
        metadata={"input_bytes": 17},
        attempt=1,
        request_id=None,
        provider="agent_platform",
    )
    diagnostic = {"transport": {"http_status": 200, "headers_received_ms": 2, "first_body_byte_ms": None}}
    await repo.finish(
        connection,
        invocation_id,
        response_usage(httpx.Response(200)),
        "cancelled",
        "CancelledError",
        response_metadata=diagnostic,
    )
    row = await connection.fetchrow(
        "SELECT request_snapshot,response_snapshot,http_status,status,input_tokens,output_tokens "
        "FROM agent.model_invocation WHERE id=$1::uuid",
        invocation_id,
    )
    assert row["request_snapshot"] == {"input_bytes": 17}
    assert row["response_snapshot"] == diagnostic
    assert row["status"] == "cancelled" and row["http_status"] == 200
    assert row["input_tokens"] is row["output_tokens"] is None
