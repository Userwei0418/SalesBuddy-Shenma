"""Real SQL/HTTP advice lifecycle; only model output is controlled in this suite."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.config import get_settings
from sales_backend.domain.advice import AdviceRequest
from sales_backend.repositories.advice_facts import AdviceFactsRepository
from sales_backend.services.advice import AdviceHandler, AdviceService
from sales_backend.services.agent_platform.inference import InferenceResult, InferenceService
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import TransactionDatabase, client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login
from tests.test_business_advice import output

pytestmark = pytest.mark.asyncio


def database(connection):
    db = TransactionDatabase(connection)
    db.settings = replace(get_settings(), access_token_secret="isolated-testing-signing-secret-not-for-production")
    return db


async def model(self, **kwargs):
    return InferenceResult(kwargs["validate"](output()), {"provider": "controlled_test_output"})


async def test_advice_native_cache_adoption_task_idempotency_and_permission(connection, monkeypatch):
    monkeypatch.setattr(InferenceService, "evaluate", model)
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    service = AdviceService(database(connection))
    req = AdviceRequest(subject_kind="opportunity", subject_id=op["id"])
    first = await service.request(who, req)
    again = await service.request(who, req)
    assert first["id"] == again["id"]
    await AdviceHandler(service.database).handle(first["id"], who)
    ready = await service.get(who, first["id"])
    assert ready["status"] == "succeeded" and len(ready["suggestions"]) == 1
    assert (await service.request(who, req))["id"] == first["id"]
    suggestion = ready["suggestions"][0]
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS002")
        assert (await client.get("/api/v1/advice/" + first["id"])).status_code == 404
        await business_login(client, "XS001")
        headers = {"Idempotency-Key": str(uuid4())}
        body = {
            "decision": "adopted",
            "version_no": suggestion["version_no"],
            "task": {
                "description": "人工修改后确认试点范围与验收标准",
                "target_position": "self",
                "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            },
        }
        url = "/api/v1/advice/suggestions/" + suggestion["id"] + "/decision"
        saved = await client.post(url, headers=headers, json=body)
        assert saved.status_code == 200, saved.text
        replay = await client.post(url, headers=headers, json=body)
        assert replay.json() == saved.json()
        task = saved.json()["task"]
        assert task["source_suggestion_id"] == suggestion["id"] and task["status"] == "pending_confirm"
        assert task["customer_id"] == op["customer_id"] and task["opportunity_id"] == op["id"]
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM workflow.task WHERE source_suggestion_id=$1::uuid", suggestion["id"]
            )
        ) == 1
        assert (
            await connection.fetchval("SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid", task["id"])
        ) == 1
        stats = await client.get("/api/v1/advice/statistics")
        assert stats.json()["adopted"] == 1 and stats.json()["adoption_rate"] == 100
    # Creating an unchanged task is the human result of this batch, not new evidence.
    assert (await service.get(who, first["id"]))["status"] == "succeeded"
    assert (await service.request(who, req))["id"] == first["id"]


@pytest.mark.parametrize("kind", ["customer", "opportunity"])
@pytest.mark.parametrize("change", ["unchanged", "edited", "accepted", "overdue", "external_task"])
async def test_same_batch_decisions_remain_actionable_until_business_facts_change(
    connection, monkeypatch, kind, change
):
    from sales_backend.api.models import TaskCreate
    from sales_backend.domain.advice import AdviceError
    from sales_backend.services.tasks import TaskService

    async def three_suggestions(self, **kwargs):
        value = output()
        value["suggestions"] = [
            {**value["suggestions"][0], "title": f"确认事项{i}", "action": f"人工核对试点事项{i}"} for i in range(1, 4)
        ]
        return InferenceResult(kwargs["validate"](value), {"provider": "controlled_test_output"})

    monkeypatch.setattr(InferenceService, "evaluate", three_suggestions)
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    service = AdviceService(database(connection))
    req = AdviceRequest(subject_kind=kind, subject_id=op["customer_id"] if kind == "customer" else op["id"])
    first = await service.request(who, req)
    await AdviceHandler(service.database).handle(first["id"], who)
    ready = await service.get(who, first["id"])
    runtime = await service.runtime(who, kind)
    task_input = TaskCreate(
        description="人工确认的试点待办",
        customer_id=op["customer_id"], opportunity_id=op["id"],
        target_position="self",
        due_at=datetime.now(UTC) + (timedelta(seconds=2) if change == "overdue" else timedelta(days=2)),
    )
    saved = await service.decide(
        connection, who, ready["suggestions"][0]["id"], "adopted", None, 1, task_input, runtime
    )
    assert (await service.get(who, first["id"]))["status"] == "succeeded"
    assert (await service.request(who, req))["id"] == first["id"]
    await service.decide(connection, who, ready["suggestions"][1]["id"], "no_task", "无需另外派发", 1, None, runtime)
    assert (await service.request(who, req))["id"] == first["id"]
    task_id = saved["task"]["id"]
    if change == "unchanged":
        another = await service.decide(
            connection, who, ready["suggestions"][2]["id"], "adopted", None, 1, task_input, runtime
        )
        assert another["task"]["id"] != task_id
        assert (await service.get(who, first["id"]))["status"] == "succeeded"
        assert (await service.request(who, req))["id"] == first["id"]
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM workflow.task t JOIN insight.business_suggestion s "
                "ON s.id=t.source_suggestion_id WHERE s.advice_id=$1::uuid",
                first["id"],
            )
            == 2
        )
        return
    if change == "external_task":
        await TaskService().create(
            connection,
            actor=who,
            description="另一条独立新事实",
            due_at=task_input.due_at,
            priority_code="medium",
            target_position="self",
            customer_id=op["customer_id"],
            opportunity_id=op["id"],
        )
    elif change == "accepted":
        await TaskService().apply_event(
            connection,
            actor=who,
            task_id=task_id,
            event_type="accept",
            note=None,
            expected_version=saved["task"]["version_no"],
        )
    elif change == "overdue":
        # Let actual time cross the deadline without touching any task field/version.
        await connection.fetchval(
            "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM due_at-clock_timestamp()),0)::double precision+0.05) "
            "FROM workflow.task WHERE id=$1::uuid",
            task_id,
        )
        assert await connection.fetchval("SELECT version_no FROM workflow.task WHERE id=$1::uuid", task_id) == 1
    else:
        await connection.execute("UPDATE workflow.task SET description='人工修改了范围' WHERE id=$1::uuid", task_id)
    assert (await service.get(who, first["id"]))["status"] == "superseded"
    with pytest.raises(AdviceError, match="资料或规则已变化"):
        await service.decide(connection, who, ready["suggestions"][2]["id"], "no_task", None, 1, None, runtime)
    assert (await service.request(who, req))["id"] != first["id"]


async def test_advice_stale_publish_and_no_task_decision(connection, monkeypatch):
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    db = database(connection)
    service = AdviceService(db)
    req = AdviceRequest(subject_kind="opportunity", subject_id=op["id"])
    first = await service.request(who, req)

    async def changed(self, **kwargs):
        async with db.transaction(who) as c:
            await c.execute("UPDATE crm.opportunity SET amount=amount+1 WHERE id=$1::uuid", op["id"])
        return await model(self, **kwargs)

    monkeypatch.setattr(InferenceService, "evaluate", changed)
    await AdviceHandler(db).handle(first["id"], who)
    stale = await service.get(who, first["id"])
    assert stale["status"] == "superseded" and stale["suggestions"] == []
    monkeypatch.setattr(InferenceService, "evaluate", model)
    second = await service.request(who, req)
    await AdviceHandler(db).handle(second["id"], who)
    ready = await service.get(who, second["id"])
    runtime = await service.runtime(who, req.subject_kind)
    before = await connection.fetchval("SELECT count(*) FROM workflow.task")
    result = await service.decide(
        connection, who, ready["suggestions"][0]["id"], "no_task", "客户暂不需要", 1, None, runtime
    )
    assert result["task"] is None
    assert await connection.fetchval("SELECT count(*) FROM workflow.task") == before
    reread = await service.get(who, second["id"])
    assert reread["suggestions"][0]["decision"] == "no_task" and reread["suggestions"][0]["version_no"] == 2


async def test_advice_facts_full_fingerprint_scope_and_bounded_context(connection):
    op = await opportunity(connection, "XS001", 100)
    other = await opportunity(connection, "XS002", 999)
    who = await actor(connection, "XS001")
    repo = AdviceFactsRepository()
    from sales_backend.repositories.visits import VisitRepository

    visits = []
    for i in range(51):
        v = await VisitRepository().create(
            connection,
            who,
            customer_id=op["customer_id"],
            fields={
                "opportunity_id": op["id"],
                "interaction_at": "2026-09-12",
                "created_date": "2026-09-13",
                "contact_name": "隔离联系人",
                "follow_up_record": "客户提出试点需求" + str(i),
                "next_action": "9月20日由销售发送试点方案",
                "_follow_up_quality_score": 85,
            },
        )
        visits.append(v)
    first = await repo.load(connection, who, "opportunity", op["id"])
    assert first["facts"]["coverage"]["visits"] == {"total": 51, "included": 50}
    included = {v["id"] for v in first["facts"]["records"]["visits"]}
    omitted = next(v for v in visits if v["id"] not in included)
    await connection.execute(
        "UPDATE activity.visit SET follow_up_record='未展示记录也发生变化' WHERE id=$1::uuid", omitted["id"]
    )
    assert (await repo.load(connection, who, "opportunity", op["id"]))["fingerprint"] != first["fingerprint"]
    single = await repo.load(connection, who, "visit", visits[0]["id"])
    assert single["facts"]["records"] == {} and single["facts"]["subject"]["id"] == visits[0]["id"]
    from sales_backend.domain.advice import AdviceError

    with pytest.raises(AdviceError):
        await repo.load(connection, who, "opportunity", other["id"])


async def test_advice_exact_facts_cycle_reuses_output_and_human_decision(connection, monkeypatch):
    monkeypatch.setattr(InferenceService, "evaluate", model)
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    service = AdviceService(database(connection))
    req = AdviceRequest(subject_kind="opportunity", subject_id=op["id"])
    a = await service.request(who, req)
    await AdviceHandler(service.database).handle(a["id"], who)
    ready = await service.get(who, a["id"])
    suggestion = ready["suggestions"][0]
    runtime = await service.runtime(who, "opportunity")
    await service.decide(connection, who, suggestion["id"], "no_task", "本周已安排", 1, None, runtime)
    amount = await connection.fetchval("SELECT amount FROM crm.opportunity WHERE id=$1::uuid", op["id"])
    await connection.execute("UPDATE crm.opportunity SET amount=amount+1 WHERE id=$1::uuid", op["id"])
    b = await service.request(who, req)
    assert b["id"] != a["id"]
    await connection.execute("UPDATE crm.opportunity SET amount=$2 WHERE id=$1::uuid", op["id"], amount)
    restored = await service.request(who, req)
    assert restored["id"] == a["id"] and restored["status"] == "succeeded"
    assert restored["suggestions"][0]["id"] == suggestion["id"]
    assert restored["suggestions"][0]["decision"] == "no_task"
    assert (await service.get(who, b["id"]))["status"] == "superseded"
    from tests.integration.test_business_activity import rows

    await actor(connection, "OPS001")
    events = await rows(connection, category="advice")
    assert events["total"] == 1
    event = events["items"][0]
    assert event["action_label"] == "确认建议无需待办" and event["actor_name"] == "XS001"
    assert "本周已安排" in str(event["details"])
    await actor(connection, "XS002")
    assert (await rows(connection, category="advice"))["total"] == 0


async def test_expired_advice_job_becomes_retryable_terminal_result(connection):
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    service = AdviceService(database(connection))
    req = AdviceRequest(subject_kind="opportunity", subject_id=op["id"])
    a = await service.request(who, req)
    await connection.execute("UPDATE insight.business_advice SET status='running' WHERE id=$1::uuid", a["id"])
    await connection.execute(
        "UPDATE ops.job SET status='running',attempts=max_attempts,"
        "locked_until=clock_timestamp()-interval '1 second' WHERE aggregate_id=$1::uuid",
        a["id"],
    )
    await connection.fetch("SELECT * FROM ops.claim_job('advice-recovery-test',30)")
    failed = await service.get(who, a["id"])
    assert failed["status"] == "failed"
    assert await connection.fetchval("SELECT status FROM ops.job WHERE aggregate_id=$1::uuid", a["id"]) == "dead_letter"
    retried = await service.request(who, req.model_copy(update={"retry": True}))
    assert retried["id"] == a["id"] and retried["status"] == "queued"
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid AND status='queued'", a["id"]
        )
        == 1
    )


async def test_native_advice_receipt_links_human_state_in_management_audit(connection, monkeypatch):
    from sales_backend.repositories.agent_audit import AgentAuditRepository
    from sales_backend.services.agent_platform.audit import InferenceAudit

    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    db = database(connection)
    operation_id = str(uuid4())

    async def audited(self, **kwargs):
        audit = InferenceAudit(db, who, operation_id, mode="opportunity_advice", run_id=None, facts={}, config={})
        await audit.start()
        await audit.finish("accepted", trace={"operation_id": operation_id, "provider": "controlled_test_output"})
        return InferenceResult(
            kwargs["validate"](output()), {"operation_id": operation_id, "provider": "controlled_test_output"}
        )

    monkeypatch.setattr(InferenceService, "evaluate", audited)
    service = AdviceService(db)
    first = await service.request(who, AdviceRequest(subject_kind="opportunity", subject_id=op["id"]))
    await AdviceHandler(db).handle(first["id"], who)
    ready = await service.get(who, first["id"])
    now = datetime.now(UTC)
    await actor(connection, "OPS001")
    rows = await AgentAuditRepository().cases(connection, start=now - timedelta(days=1), end=now + timedelta(days=1))
    item = next(row for row in rows["items"] if row["id"] == operation_id)
    assert item["case_id"] == first["id"] and item["advice_id"] == first["id"]
    assert item["business_status"] == "waiting_human" and item["customer_id"] == op["customer_id"]
    assert item["business_effects"]["suggestions"][0]["decision"] == "pending"
    assert "客户尚未确认范围" not in str(item)
    await actor(connection, "XS001")
    runtime = await service.runtime(who, "opportunity")
    await service.decide(connection, who, ready["suggestions"][0]["id"], "no_task", "已确认暂不安排", 1, None, runtime)
    await actor(connection, "OPS001")
    rows = await AgentAuditRepository().cases(connection, start=now - timedelta(days=1), end=now + timedelta(days=1))
    item = next(row for row in rows["items"] if row["id"] == operation_id)
    assert item["business_status"] == "succeeded"
    assert item["business_effects"]["suggestions"][0]["decision"] == "no_task"
    await actor(connection, "XS002")
    assert (await AgentAuditRepository().cases(connection, start=now - timedelta(days=1), end=now + timedelta(days=1)))[
        "total"
    ] == 0


@pytest.mark.parametrize("linked", [False, True])
async def test_visit_advice_daily_or_customer_adoption(connection, monkeypatch, linked):
    from sales_backend.repositories.visits import VisitRepository

    monkeypatch.setattr(InferenceService, "evaluate", model)
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    visit = await VisitRepository().create(connection, who, customer_id=op["customer_id"], fields={
        "opportunity_id": op["id"] if linked else None,
        "follow_up_record": "客户需要补充试点范围，等待销售整理资料",
        "next_action": "销售下周确认试点范围", "_follow_up_quality_score": 85,
        "interaction_at": "2026-09-21", "created_date": "2026-09-21", "contact_name": "隔离联系人",
    })
    service = AdviceService(database(connection))
    req = AdviceRequest(subject_kind="visit", subject_id=visit["id"], section="tasks")
    first = await service.request(who, req)
    assert (await service.request(who, req))["id"] == first["id"]
    await AdviceHandler(service.database).handle(first["id"], who)
    ready = await service.get(who, first["id"])
    assert ready["status"] == "succeeded"
    suggestion = ready["suggestions"][0]
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        url = "/api/v1/advice/suggestions/" + suggestion["id"] + "/decision"
        body = {"decision": "adopted", "version_no": suggestion["version_no"], "task": {
            "description": "人工确认整理试点资料并核对范围", "target_position": "self",
            "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "association_kind": "customer" if linked else "daily",
            "customer_id": op["customer_id"] if linked else None,
            "opportunity_id": op["id"] if linked else None,
        }}
        wrong = {**body, "task": {**body["task"], "association_kind": "daily" if linked else "customer"}}
        rejected = await client.post(url, headers={"Idempotency-Key": str(uuid4())}, json=wrong)
        assert rejected.status_code == 422, rejected.text
        headers = {"Idempotency-Key": str(uuid4())}
        saved = await client.post(url, headers=headers, json=body)
        assert saved.status_code == 200, saved.text
        replay = await client.post(url, headers=headers, json=body)
        assert replay.json() == saved.json()
        task_id = saved.json()["task"]["id"]
    row = await connection.fetchrow("SELECT association_kind,customer_id::text,opportunity_id::text,source_suggestion_id::text FROM workflow.task WHERE id=$1::uuid", task_id)
    assert row["association_kind"] == ("customer" if linked else "daily")
    assert row["customer_id"] == (op["customer_id"] if linked else None)
    assert row["opportunity_id"] == (op["id"] if linked else None)
    assert row["source_suggestion_id"] == suggestion["id"]
