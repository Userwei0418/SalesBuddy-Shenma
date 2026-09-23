"""Real RLS, task materialization and lifecycle boundaries; synthetic provider HTTP."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from sales_backend.db import Database, set_request_context
from sales_backend.domain.agent import AgentMode
from sales_backend.domain.business_time import BUSINESS_TZ
from sales_backend.domain.company_rules import TaskSchedulePolicy
from sales_backend.domain.follow_up_schedule import FollowUpDeadlineNeedsConfirmation
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.assistant import AssistantRepository, MessageReplayConflict
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.jobs import ClaimedJob
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.agent_run import handler as business
from sales_backend.services.agent_run.today_scope import TodaySourceChanged
from sales_backend.services.tasks import TaskService
from sales_backend.worker import Worker
from tests.integration.test_operations_claims_sql import actor as select_actor
from tests.test_fde_facts_runtime import Chunks, frame
from tests.test_today_tasks_platform import ANSWER, configured

from .provision import create_owned_customer, create_owned_opportunity

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("scenario", [
    "normal", "company_schedule", "admin_direct", "admin_budget", "off", "blocked", "timeout",
    "foreign_visit", "duplicates", "bad_priority",
    "omitted_ranking", "past_date", "past_source", "relative_deadline", "source_changed", "completed", "both_failed",
])
async def test_today_tasks_only_materializes_own_followups_and_preserves_management(
    connection, sales_actor, monkeypatch, scenario,
):
    async def actor(code):
        return (await IdentityRepository().find_actor_by_account(
            connection, workspace_external_id="demo-sales-workspace", account_code=code,
        )).context

    other, supervisor = await actor("XS002"), await actor("ZJ001")

    source_due = datetime(2000 if scenario == "past_source" else 2099, 9, 15, 10, tzinfo=BUSINESS_TZ)
    next_action = (f"{source_due.date().isoformat()}前销售提交试点方案" if scenario == "company_schedule"
                   else f"{source_due.date().isoformat()} 10:00前销售提交试点方案")

    async def owned_visit(owner, *, relative=False):
        customer = await create_owned_customer(connection, owner, data={
            "name": "今日待办隔离测试" + uuid4().hex, "industry": "软件", "customer_type": "潜在客户",
            "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
            "contact_name": "合成人员", "contact_title": "经理", "contact_role": "决策者",
        })
        opportunity_id = await create_owned_opportunity(connection, owner, customer["id"])
        visit = await VisitRepository().create(connection, owner, customer_id=customer["id"], fields={
            "interaction_at": date.today().isoformat(), "created_date": date.today().isoformat(),
            "contact_name": "合成人员", "follow_up_record": "客户已要求补充试点方案，下周确认。",
            "opportunity_id": opportunity_id,
            "next_action": "下周一销售提交试点方案" if relative else next_action,
            "_follow_up_quality_score": 85,
        })
        return customer, visit

    # An inaccessible ambiguous source must neither block this actor's valid
    # batch nor appear in the needs-confirmation response or provider facts.
    _, foreign_visit = await owned_visit(other, relative=True)
    customer, visit = await owned_visit(sales_actor, relative=scenario == "relative_deadline")
    await set_request_context(connection, supervisor)
    management = await TaskService().create(
        connection, actor=supervisor, description="管理任务保持原截止时间和优先级",
        assignee_account_code="XS001", due_at=datetime.now(UTC) + timedelta(days=3), priority_code="medium",
    )
    await TaskService().create(
        connection, actor=supervisor, description="其他销售的任务不可进入模型",
        assignee_account_code="XS002", due_at=datetime.now(UTC) + timedelta(days=2), priority_code="normal",
    )
    await set_request_context(connection, sales_actor)
    config = configured(enabled=scenario != "off", blocked=scenario in {"blocked", "both_failed"})
    raw = next(iter(json.loads(config.agent_fde_pilot_json).values()))
    raw["capabilities"]["today_tasks"]["user_ids"] = [sales_actor.user_id]
    bindings = next(iter(json.loads(config.agent_platform_bindings_json).values()))
    config = replace(config, agent_fde_pilot_json=json.dumps({sales_actor.workspace_id: raw}),
                     agent_platform_bindings_json=json.dumps({sales_actor.workspace_id: bindings}),
                     senseaudio_api_key="synthetic-original-key", senseaudio_base_url="https://original.invalid",
                     agent_inference_platform_seconds=.1 if scenario == "timeout" else 2,
                     agent_inference_total_seconds=5)

    if scenario in {"admin_direct", "admin_budget"}:
        await select_actor(connection, "ADMIN001")
        repository = CompanyRulesRepository()
        base_execution = await repository.active(connection, "agent_execution.today_tasks")
        changed = {"strategy": "direct_only", "override_budget": True, "platform_seconds": 1,
                   "total_seconds": 16} if scenario == "admin_direct" else {
            "override_budget": True, "platform_seconds": 1, "total_seconds": 16,
        }
        execution_id = await repository.save(connection, "agent_execution.today_tasks", {
            "base_id": base_execution["id"], "reason": "隔离运行控制验证",
            "definition": {**base_execution["definition"], **changed},
        })
        await repository.publish(connection, execution_id, 1)
        await set_request_context(connection, sales_actor)

    class Pool:
        # A production pool gives each borrower exclusive access. This fixture
        # keeps one rollback connection, so cancellation audit and fallback
        # must serialize their leases instead of sharing a busy connection.
        lock = asyncio.Lock()

        @asynccontextmanager
        async def acquire(self):
            async with self.lock:
                yield connection

    database = Database(config, pool=Pool())
    answer = deepcopy(ANSWER)
    answer["ordered_items"][0]["source_id"] = visit["id"]
    answer["ordered_items"][0]["due_at"] = source_due.isoformat()
    answer["ordered_items"][1]["source_id"] = management["id"]
    # Both providers may repeat wrong read-only fields. They must never affect
    # the existing task or block this actor's independently valid new candidate.
    answer["ordered_items"][1].update(
        due_at="2000-01-01T00:00:00Z", priority={"forged": "urgent"}, title="模型伪造旧任务",
        status="completed", owner_user_ref_id=other.user_id,
    )
    if scenario == "omitted_ranking":
        answer["ordered_items"] = []
    selected_policy = None
    if scenario == "company_schedule":
        await select_actor(connection, "ADMIN001")
        repository = CompanyRulesRepository()
        base = await repository.active(connection, "task_schedule")
        selected_policy = TaskSchedulePolicy(timezone="Asia/Shanghai", today_at="13:07", next_day_at="07:11")
        identifier = await repository.save(connection, "task_schedule", {
            "base_id": base["id"], "reason": "隔离待办时间验证", "definition": selected_policy.model_dump(),
        })
        await repository.publish(connection, identifier, 1)
        await set_request_context(connection, sales_actor)
        answer["ordered_items"][0]["due_at"] = ""
    payload = deepcopy(answer)
    if scenario == "past_date":
        # A conflicting provider date now fails the contract; the fallback
        # returns the actual source date instead of silently rescheduling it.
        payload["ordered_items"][0]["due_at"] = "2000-01-01T00:00:00Z"
    elif scenario == "foreign_visit":
        payload["ordered_items"][0]["source_id"] = foreign_visit["id"]
    elif scenario == "duplicates":
        payload["ordered_items"].append(deepcopy(payload["ordered_items"][0]))
    elif scenario == "bad_priority":
        payload["ordered_items"][0]["priority"] = "supreme"
    for item in payload["ordered_items"]:
        item.update(owner_user_ref_id=other.user_id, customer_id=str(uuid4()), task_id=str(uuid4()),
                    opportunity_id=str(uuid4()), status="completed", description="模型不能改任务内容")
    calls, requests = [], []

    async def platform_wire(request):
        calls.append("agent_platform")
        supplied = json.loads(json.loads(request.content)["query"])
        requests.append(supplied)
        assert supplied["mode"] == "today_tasks"
        assert supplied["facts"]["scope"]["user_id"] == sales_actor.user_id
        assert foreign_visit["id"] not in json.dumps(supplied)
        assert "其他销售的任务不可进入模型" not in json.dumps(supplied)
        if scenario == "source_changed":
            await connection.execute(
                "UPDATE activity.visit SET next_action='2099年9月16日10:00提供修订计划' WHERE id=$1::uuid",
                visit["id"],
            )
        stream = Chunks([], stall=True) if scenario == "timeout" else Chunks([
            frame("message", answer=json.dumps(payload)), frame("message_end"),
        ])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def original_wire(request):
        calls.append("senseaudio")
        assert foreign_visit["id"] not in request.content.decode()
        if scenario == "both_failed":
            return httpx.Response(503, json={"error": "synthetic failure"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    def runtime_factory(db, settings, **kwargs):
        runtime = filtered_facts_runtime(db, settings, **kwargs)
        runtime.client_factory = lambda c: FdeClient(c, transport=httpx.MockTransport(platform_wire))
        return runtime

    async def snapshot():
        return {
            table: await connection.fetch(f"SELECT * FROM {table} ORDER BY id")  # noqa: S608 - fixed table list
            for table in ("crm.customer", "crm.opportunity", "activity.visit", "workflow.notification")
        }

    before = await snapshot()
    management_before = await connection.fetchrow("SELECT * FROM workflow.task WHERE id=$1::uuid", management["id"])
    before_count = await connection.fetchval("SELECT count(*) FROM workflow.task")
    assistant = AssistantRepository()
    conversation = await assistant.create_conversation(
        connection, sales_actor, mode=AgentMode.TODAY_TASKS, customer_id=None,
    )
    message = dict(conversation_id=conversation["id"], text="整理今日待办",
                   client_message_id=str(uuid4()), input_source="text")
    run_id = await assistant.enqueue_message(connection, sales_actor, **message)
    assert await assistant.enqueue_message(connection, sales_actor, **message) == run_id
    with pytest.raises(MessageReplayConflict):
        await assistant.enqueue_message(connection, sales_actor, **{**message, "text": "不同正文"})
    async with httpx.AsyncClient(base_url="https://original.invalid",
                                transport=httpx.MockTransport(original_wire)) as http:
        def direct_factory(settings, **kwargs):
            return SenseAudioClient(settings, client=http, **kwargs)

        monkeypatch.setattr(business, "filtered_facts_runtime", runtime_factory)
        monkeypatch.setattr(business, "InferenceService", lambda db, settings, **kw: InferenceService(
            db, settings, direct_factory=direct_factory, **kw,
        ))
        monkeypatch.setattr(business, "SenseAudioClient", direct_factory)
        handler = business.AgentRunHandler(database, config)
        if scenario == "source_changed":
            assert not config.agent_today_tasks_acceptance_path and not config.agent_today_tasks_acceptance_target
            with pytest.raises(TodaySourceChanged) as error:
                await handler.handle(run_id, sales_actor)
            assert error.value.retryable is False
            assert calls == ["agent_platform"]
            assert await connection.fetchval("SELECT count(*) FROM workflow.task") == before_count
            assert await connection.fetchval(
                "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id,
            ) == 0
            assert await connection.fetchval(
                "SELECT next_action FROM activity.visit WHERE id=$1::uuid", visit["id"],
            ) == "2099年9月16日10:00提供修订计划"
            after = await snapshot()
            assert all(after[table] == before[table] for table in before if table != "activity.visit")
            return
        if scenario == "relative_deadline":
            # Give this exact queued job a real lease; other derived jobs are
            # untouched. Exercise Worker terminal persistence on the same RLS
            # connection after the real facts loader rejects the source.
            row = await connection.fetchrow(
                """UPDATE ops.job SET status='running',attempts=1,lease_token=gen_random_uuid(),
                   locked_by='date-preflight-test',locked_until=clock_timestamp()+interval '1 minute'
                   WHERE job_type='agent.run' AND aggregate_id=$1::uuid RETURNING *""", run_id,
            )
            job = ClaimedJob(
                id=str(row["id"]), workspace_id=str(row["workspace_id"]), job_type=row["job_type"],
                aggregate_id=str(row["aggregate_id"]), payload=row["payload"], attempts=row["attempts"],
                max_attempts=row["max_attempts"], lease_token=str(row["lease_token"]),
            )
            with pytest.raises(FollowUpDeadlineNeedsConfirmation) as error:
                await handler.handle(run_id, sales_actor)
            assert error.value.source_ids == (visit["id"],)
            assert foreign_visit["id"] not in str(error.value)
            await Worker(database)._finish_failed(job, error.value)
            public = await assistant.get_run(connection, run_id=run_id)
            assert public["status"] == "failed"
            assert public["error_code"] == "FollowUpDeadlineNeedsConfirmation"
            assert "请人工确认日期" in public["error_detail"]
            terminal = await connection.fetchrow("SELECT * FROM ops.job WHERE id=$1::uuid", job.id)
            assert terminal["status"] == "dead_letter" and terminal["attempts"] < terminal["max_attempts"]
            assert terminal["completed_at"] is not None and terminal["lease_token"] is None
            assert calls == []
            assert await connection.fetchval(
                "SELECT count(*) FROM agent.inference_operation WHERE run_id=$1::uuid", run_id,
            ) == 0
            assert await connection.fetchval(
                "SELECT count(*) FROM agent.model_invocation WHERE run_id=$1::uuid", run_id,
            ) == 0
            assert await connection.fetchval("SELECT count(*) FROM workflow.task") == before_count
            assert await connection.fetchval(
                "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id,
            ) == 0
            assert await snapshot() == before
            await set_request_context(connection, other)
            assert await assistant.get_run(connection, run_id=run_id) is None
            return
        if scenario == "both_failed":
            with pytest.raises(SenseAudioError) as error:
                await handler.handle(run_id, sales_actor)
            assert error.value.retryable is False
            assert await connection.fetchval("SELECT count(*) FROM workflow.task") == before_count
            assert await connection.fetchval(
                "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id,
            ) == 0
            assert await snapshot() == before
            return

        await handler.handle(run_id, sales_actor)
        public = await assistant.get_run(connection, run_id=run_id)
        assert public["status"] == "succeeded" and "inference_route" not in str(public)
        assert public["result"]["artifact_id"] is None
        rows = public["result"]["rows"]
        assert len(rows) == 2  # One pre-existing management task plus one follow-up.
        management_card = next(item for item in rows if item["task_id"] == management["id"])
        assert management_card["title"] == management_before["title"]
        assert datetime.fromisoformat(management_card["due_at"]) == management_before["due_at"]
        assert management_card["tone"] == "中"  # The real task priority is medium.
        saved = await connection.fetchrow(
            "SELECT * FROM workflow.task WHERE source_visit_id=$1::uuid", visit["id"],
        )
        assert saved["status"] == "pending_execution" and saved["task_type"] == "visit_follow_up"
        assert str(saved["creator_user_ref_id"]) == sales_actor.user_id
        assert str(saved["customer_id"]) == customer["id"] and str(saved["opportunity_id"]) == str(visit["opportunity_id"])
        assert saved["description"] == next_action
        assert saved["priority_code"] == ("medium" if scenario == "omitted_ranking" else "high")
        if selected_policy:
            assert saved["due_at"] == source_due.replace(hour=13, minute=7)
            event_policy = await connection.fetchval(
                "SELECT payload->'company_policy' FROM workflow.task_event WHERE task_id=$1::uuid", saved["id"],
            )
            assert event_policy["id"] == identifier
            assert event_policy["definition"] == selected_policy.model_dump()
        else:
            assert saved["due_at"] == source_due
        if scenario == "past_source":
            assert saved["due_at"] < datetime.now(UTC)  # Never silently shifted to tomorrow.

        owner = await connection.fetchrow(
            "SELECT * FROM workflow.task_assignee WHERE task_id=$1::uuid", saved["id"],
        )
        assert str(owner["assignee_user_ref_id"]) == sales_actor.user_id and owner["responsibility"] == "owner"
        assert await connection.fetchval(
            "SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid", saved["id"],
        ) == 1
        assert await connection.fetchrow(
            "SELECT * FROM workflow.task WHERE id=$1::uuid", management["id"],
        ) == management_before
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == before_count + 1
        assert await connection.fetchval(
            "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id,
        ) == 1
        expected = [] if scenario in {"off", "blocked", "admin_direct"} else ["agent_platform"]
        if scenario in {"off", "blocked", "admin_direct", "timeout", "foreign_visit", "duplicates", "bad_priority", "past_date"}:
            expected += ["senseaudio"]
        assert calls == expected
        context = await connection.fetchval("SELECT business_context FROM agent.run WHERE id=$1::uuid", run_id)
        if scenario in {"admin_direct", "admin_budget"}:
            operation = await connection.fetchrow(
                "SELECT configuration,trace FROM agent.inference_operation WHERE run_id=$1::uuid", run_id,
            )
            assert operation["configuration"]["execution_policy"]["id"] == execution_id
            if scenario == "admin_direct":
                assert operation["trace"]["provider"] == "senseaudio"
                assert not requests
                assert operation["configuration"]["platform_seconds"] == 0
                assert operation["configuration"]["total_seconds"] == 16
            else:
                assert operation["configuration"]["platform_seconds"] == 1
                assert operation["configuration"]["total_seconds"] == 16
        if scenario != "off":
            trace = context["inference_route"]
            attempts = await connection.fetch(
                "SELECT provider_code,operation_id::text FROM agent.model_invocation WHERE run_id=$1::uuid "
                "AND record_kind='provider_attempt' ORDER BY started_at", run_id,
            )
            assert all(row["operation_id"] == trace["operation_id"] for row in attempts)
            assert attempts[-1]["provider_code"] == calls[-1]
        assert await snapshot() == before
        await set_request_context(connection, other)
        assert await assistant.get_run(connection, run_id=run_id) is None
        assert await connection.fetchval("SELECT id FROM workflow.task WHERE id=$1::uuid", saved["id"]) is None
        await set_request_context(connection, sales_actor)

        if scenario not in {"normal", "completed", "omitted_ranking", "company_schedule"}:
            return
        if scenario == "company_schedule":
            await select_actor(connection, "ADMIN001")
            restored = await repository.save(connection, "task_schedule", {
                "base_id": identifier, "reason": "恢复旧待办基线", "definition": base["definition"],
            }, restored=base["id"])
            await repository.publish(connection, restored, 1)
            await set_request_context(connection, sales_actor)
        if scenario == "completed":
            await TaskService().complete(
                connection, actor=sales_actor, task_id=str(saved["id"]), note="隔离验证完成",
            )
            answer["ordered_items"] = [answer["ordered_items"][1]]
            payload["ordered_items"] = deepcopy(answer["ordered_items"])
        elif scenario == "normal":
            # On the next run the same visit_follow_up ID belongs to active_tasks,
            # not follow_up_candidates. Its official fields are now read-only too.
            answer["ordered_items"][0].update(due_at={"forged": "tomorrow"}, priority=["urgent"])
            payload["ordered_items"] = deepcopy(answer["ordered_items"])
        second = await assistant.enqueue_message(
            connection, sales_actor, **{**message, "client_message_id": str(uuid4())},
        )
        await handler.handle(second, sales_actor)
        assert requests[-1]["facts"]["follow_up_candidates"] == []
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == before_count + 1
        again = await connection.fetchrow("SELECT * FROM workflow.task WHERE id=$1::uuid", saved["id"])
        assert again["status"] == ("completed" if scenario == "completed" else "pending_execution")
        # A fresh run cannot alter the already materialized follow-up's timing.
        assert again["due_at"] == saved["due_at"] and again["priority_code"] == saved["priority_code"]
        assert await snapshot() == before
