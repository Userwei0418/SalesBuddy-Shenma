"""Customer risk lifecycle on PostgreSQL/RLS; only inference and runtime are synthetic."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.db import Database, set_request_context
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.repositories.customer_risk import (
    current_clear_assessment,
    enqueue_customer_risk_review,
    load_customer_risk_facts,
)
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.detail_overviews import DetailOverviewRepository
from sales_backend.repositories.risks import RiskRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services import customer_risk as business
from sales_backend.services.agent_platform.inference import InferenceService
from sales_backend.services.runtime_config import RuntimeConfiguration
from tests.integration.provision import create_owned_customer
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio

VISIT_BODY = "客户预算尚未获批，需要财务确认后才可推进。"


async def customer_fixture(connection, person, *, with_visit=True):
    customer = await create_owned_customer(connection, person, data={
        "name": "客户风险生命周期" + uuid4().hex, "industry": "软件", "customer_type": "潜在客户",
        "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "测试联系人", "contact_title": "经理", "contact_role": "决策者",
    })
    visit = await add_visit(connection, person, customer["id"]) if with_visit else None
    return customer, visit


async def add_visit(connection, person, customer_id):
    await set_request_context(connection, person)
    return await VisitRepository().create(connection, person, customer_id=customer_id, fields={
        "interaction_at": date.today().isoformat(), "created_date": date.today().isoformat(),
        "contact_name": "测试联系人", "follow_up_record": VISIT_BODY,
        "next_action": "下周一销售与财务确认预算进度", "_follow_up_quality_score": 85,
    })


async def enqueue(connection, person, customer_id, *, event=None):
    await set_request_context(connection, person)
    return await enqueue_customer_risk_review(connection, person, customer_id=customer_id,
                                             trigger_type="visit.archived", trigger_id=event or str(uuid4()))


def deterministic_handler(connection, monkeypatch, *, outcome="no_risk_identified", mock_inference=True):
    settings = replace(get_settings(), database_url="", senseaudio_api_key="", agent_fde_pilot_json="{}",
                       agent_fde_pilot_path="", agent_platform_bindings_json="{}", agent_execution_policy={})

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    handler = business.CustomerRiskReviewHandler(Database(settings, pool=Pool()), settings)
    provider = SimpleNamespace(calls=[], outcome=outcome, error=None, during_inference=None,
                               runtime=RuntimeConfiguration(settings, {}))

    async def runtime(person):
        return provider.runtime

    async def evaluate(_service, **request):
        provider.calls.append(deepcopy(request["facts"]))
        if provider.during_inference:
            await provider.during_inference(request["facts"])
        if provider.error:
            raise provider.error
        facts = request["facts"]
        result = {"outcome": provider.outcome, "reviewed_visit_ids": [v["id"] for v in facts["visits"]],
                  "risks": [], "reason": "已逐条评估本次全部授权事实"}
        if provider.outcome == "risk_found":
            result["risks"] = [{
                "source_visit_id": facts["visits"][0]["id"], "risk_type": "budget_risk",
                "title": "预算待批", "description": VISIT_BODY, "severity": "high",
                "suggested_action": "与财务确认预算", "evidence_detail": "客户预算尚未获批", "due_at": None,
            }]
        # Exercise the production semantic validator, rather than bypassing the
        # contract when replacing network inference with a deterministic answer.
        return SimpleNamespace(payload=request["validate"](result), trace={"model_ref": "synthetic-risk-test"})

    monkeypatch.setattr(handler, "runtime", runtime)
    if mock_inference:
        monkeypatch.setattr(business.InferenceService, "evaluate", evaluate)
    return handler, provider


async def receipt(connection, assessment_id):
    return await connection.fetchrow("SELECT * FROM insight.customer_risk_assessment WHERE id=$1::uuid", assessment_id)


async def risk_rows(connection, customer_id):
    return await connection.fetch("SELECT * FROM insight.risk WHERE customer_id=$1::uuid ORDER BY id", customer_id)


async def revoke_or_transfer_owner(connection, sales_actor, customer_id, change):
    if change == "role_revoked":
        await actor(connection, "ADMIN001")
        await connection.execute(
            "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid "
            "AND role_code='sales'", sales_actor.user_id,
        )
    else:
        await actor(connection, "OPS001")
        version = await connection.fetchval(
            "SELECT version_no FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer_id,
        )
        await connection.fetchval(
            "SELECT security.release_customer($1::uuid,$2,'测试人员交接')", customer_id, version,
        )
        await actor(connection, "XS002")
        claim = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer_id)
        await actor(connection, "OPS001")
        await connection.fetchval(
            "SELECT security.review_customer_claim($1::uuid,'approved','测试新负责人')", claim["request_id"],
        )


async def test_same_event_replay_is_one_job_but_changed_facts_enqueue_a_new_assessment(connection, sales_actor):
    customer, visit = await customer_fixture(connection, sales_actor)
    first = await enqueue(connection, sales_actor, customer["id"], event=visit["id"])
    assert await enqueue(connection, sales_actor, customer["id"], event=visit["id"]) == first
    await connection.execute(
        "UPDATE crm.customer SET demand_summary='新增客户需求',version_no=version_no+1 WHERE id=$1::uuid",
        customer["id"],
    )
    second = await enqueue(connection, sales_actor, customer["id"], event=visit["id"])
    assert second != first
    assert await enqueue(connection, sales_actor, customer["id"], event=visit["id"]) == second
    rows = await connection.fetch(
        "SELECT a.id,a.actor_user_ref_id,a.initiated_by_user_ref_id,a.status,j.job_type,j.payload "
        "FROM insight.customer_risk_assessment a JOIN ops.job j ON j.id=a.job_id WHERE a.customer_id=$1::uuid",
        customer["id"],
    )
    assert len(rows) == 2
    assert all(str(row["actor_user_ref_id"]) == str(row["initiated_by_user_ref_id"]) == sales_actor.user_id
               for row in rows)
    assert all(row["status"] == "queued" and row["job_type"] == "customer.risk.review" for row in rows)
    assert all(row["payload"]["customer_id"] == customer["id"] for row in rows)


async def test_complete_no_risk_receipt_drives_both_profiles_and_gets_never_enqueue(
    connection, sales_actor, monkeypatch,
):
    customer, visit = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch)
    assert not await current_clear_assessment(connection, customer["id"])
    await handler.handle(assessment_id, sales_actor)
    saved = await receipt(connection, assessment_id)
    assert saved["status"] == "succeeded" and saved["outcome"] == "no_risk_identified"
    assert saved["coverage"]["complete"] is True and saved["risk_count"] == 0
    assert saved["result"]["reviewed_visit_ids"] == [visit["id"]]
    assert saved["facts_fingerprint"] == provider.calls[0]["fingerprint"]
    assert await current_clear_assessment(connection, customer["id"])
    jobs_before = await connection.fetchval("SELECT count(*) FROM ops.job")
    old = await CustomerRepository().detail(connection, customer_id=customer["id"])
    overview = await DetailOverviewRepository().customer(connection, customer["id"])
    assert old["profile"] == overview["profile"]
    assert overview["profile"]["dimensions"][-1]["value"] == 100
    assert "风险评估" in overview["profile"]["dimensions"][-1]["basis"]
    assert "risk_assessment_clear" not in json.dumps(overview, default=str)
    assert await receipt(connection, assessment_id) == saved
    assert await connection.fetchval("SELECT count(*) FROM ops.job") == jobs_before
    assert await risk_rows(connection, customer["id"]) == []
    await handler.handle(assessment_id, sales_actor)
    assert len(provider.calls) == 1 and await receipt(connection, assessment_id) == saved
    await add_visit(connection, sales_actor, customer["id"])
    assert not await current_clear_assessment(connection, customer["id"])
    assert (await DetailOverviewRepository().customer(connection, customer["id"]))["profile"]["dimensions"][-1][
        "value"
    ] is None


async def test_latest_provider_failure_hides_old_clear_and_queue_projects_terminal_failure(
    connection, sales_actor, monkeypatch,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    first = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch)
    await handler.handle(first, sales_actor)
    assert await current_clear_assessment(connection, customer["id"])
    second = await enqueue(connection, sales_actor, customer["id"])
    assert second != first and not await current_clear_assessment(connection, customer["id"])
    provider.error = RuntimeError("synthetic provider unavailable")
    with pytest.raises(RuntimeError, match="synthetic provider unavailable"):
        await handler.handle(second, sales_actor)
    failed = await receipt(connection, second)
    assert failed["status"] == "running" and failed["outcome"] == "unknown"
    # Exercise the real queue-to-receipt terminal projection after handler failure.
    await connection.execute(
        "UPDATE ops.job SET status='dead_letter',attempts=max_attempts,last_error_code='SYNTHETIC_PROVIDER_FAILED' "
        "WHERE id=$1", failed["job_id"],
    )
    failed = await receipt(connection, second)
    assert failed["status"] == "failed" and failed["error_code"] == "SYNTHETIC_PROVIDER_FAILED"
    assert (await receipt(connection, first))["status"] == "succeeded"
    assert not await current_clear_assessment(connection, customer["id"])
    assert await risk_rows(connection, customer["id"]) == []


@pytest.mark.parametrize("closed_status", ["resolved", "accepted"])
async def test_detected_risk_is_pending_and_reanalysis_cannot_revive_human_closed_risk(
    connection, sales_actor, monkeypatch, closed_status,
):
    customer, visit = await customer_fixture(connection, sales_actor)
    handler, provider = deterministic_handler(connection, monkeypatch, outcome="risk_found")
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    await handler.handle(assessment_id, sales_actor)
    rows = await risk_rows(connection, customer["id"])
    assert len(rows) == 1
    risk = rows[0]
    assert risk["status"] == "pending" and str(risk["owner_user_ref_id"]) == sales_actor.user_id
    assert str(risk["source_visit_id"]) == visit["id"] and risk["source_code"] == "customer_risk_agent"
    assert risk["input_snapshot"]["assessment_id"] == assessment_id
    assert risk["evidence"] == [{"type": "visit", "visit_id": visit["id"], "detail": "客户预算尚未获批"}]
    assert not await current_clear_assessment(connection, customer["id"])
    await handler.handle(assessment_id, sales_actor)
    assert len(provider.calls) == 1 and await risk_rows(connection, customer["id"]) == rows
    if closed_status == "resolved":
        await RiskRepository().resolve(connection, actor=sales_actor, risk_id=str(risk["id"]),
                                       resolution_note="人工确认预算已获批", expected_version=risk["version_no"])
    else:
        await connection.execute("UPDATE insight.risk SET status='accepted' WHERE id=$1", risk["id"])
    closed = await risk_rows(connection, customer["id"])
    events = await connection.fetchval("SELECT count(*) FROM insight.risk_event WHERE risk_id=$1", risk["id"])
    await handler.handle(await enqueue(connection, sales_actor, customer["id"]), sales_actor)
    assert len(provider.calls) == 2 and await risk_rows(connection, customer["id"]) == closed
    assert await connection.fetchval("SELECT count(*) FROM insight.risk_event WHERE risk_id=$1", risk["id"]) == events
    assert closed[0]["status"] == closed_status


@pytest.mark.parametrize("change", ["facts", "newer_assessment", "configuration"])
async def test_changed_context_during_provider_call_supersedes_without_risk_write(
    connection, sales_actor, monkeypatch, change,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch, outcome="risk_found")

    async def change_context(_facts):
        if change == "facts":
            await add_visit(connection, sales_actor, customer["id"])
        elif change == "newer_assessment":
            assert await enqueue(connection, sales_actor, customer["id"]) != assessment_id
        else:
            provider.runtime = RuntimeConfiguration(handler.settings, {"personal_risks": "新的业务补充指引"})

    provider.during_inference = change_context
    await handler.handle(assessment_id, sales_actor)
    saved = await receipt(connection, assessment_id)
    assert saved["status"] == "superseded" and saved["outcome"] == "unknown"
    assert saved["error_code"] == "facts_or_scope_changed"
    assert await risk_rows(connection, customer["id"]) == []
    assert not await current_clear_assessment(connection, customer["id"])


@pytest.mark.parametrize("change", ["role_revoked", "owner_transferred"])
@pytest.mark.parametrize("timing", ["before_provider", "during_provider"])
async def test_changed_authority_never_adopts_the_old_owner_result(
    connection, sales_actor, monkeypatch, change, timing,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch, outcome="risk_found")

    async def change_authority(_facts=None):
        await revoke_or_transfer_owner(connection, sales_actor, customer["id"], change)

    if timing == "before_provider":
        await change_authority()
    else:
        provider.during_inference = change_authority
    with pytest.raises(PermissionError):
        await handler.handle(assessment_id, sales_actor)
    assert len(provider.calls) == (0 if timing == "before_provider" else 1)
    await actor(connection, "ZJL001")
    saved = await receipt(connection, assessment_id)
    assert saved is not None and saved["status"] != "succeeded"
    assert await risk_rows(connection, customer["id"]) == []
    assert not await current_clear_assessment(connection, customer["id"])


@pytest.mark.parametrize("change", ["role_revoked", "owner_transferred"])
async def test_successful_clear_receipt_loses_health_when_owner_authority_changes(
    connection, sales_actor, monkeypatch, change,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, _ = deterministic_handler(connection, monkeypatch)
    await handler.handle(assessment_id, sales_actor)
    assert await current_clear_assessment(connection, customer["id"])
    await revoke_or_transfer_owner(connection, sales_actor, customer["id"], change)
    await actor(connection, "ZJL001")
    assert (await receipt(connection, assessment_id))["status"] == "succeeded"
    assert not await current_clear_assessment(connection, customer["id"])
    profile = (await DetailOverviewRepository().customer(connection, customer["id"]))["profile"]
    assert profile["dimensions"][-1]["value"] is None


async def test_no_visits_records_insufficient_evidence_without_calling_provider(connection, sales_actor, monkeypatch):
    customer, _ = await customer_fixture(connection, sales_actor, with_visit=False)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch)
    await handler.handle(assessment_id, sales_actor)
    saved = await receipt(connection, assessment_id)
    assert provider.calls == []
    assert saved["status"] == "succeeded" and saved["outcome"] == "insufficient_evidence"
    assert saved["result"]["reviewed_visit_ids"] == [] and saved["risk_count"] == 0
    assert not await current_clear_assessment(connection, customer["id"])
    assert (await DetailOverviewRepository().customer(connection, customer["id"]))["profile"]["dimensions"][-1][
        "value"
    ] is None


async def test_other_sales_cannot_read_receipt_facts_health_or_execute_the_assessment(
    connection, sales_actor, monkeypatch,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch)
    await handler.handle(assessment_id, sales_actor)
    assert await current_clear_assessment(connection, customer["id"])
    other = await actor(connection, "XS002")
    assert await receipt(connection, assessment_id) is None
    assert not await current_clear_assessment(connection, customer["id"])
    assert await enqueue(connection, other, customer["id"]) is None
    with pytest.raises(PermissionError):
        await load_customer_risk_facts(connection, customer["id"])
    with pytest.raises(PermissionError):
        await handler.handle(assessment_id, other)
    assert len(provider.calls) == 1
    assert await CustomerRepository().detail(connection, customer_id=customer["id"]) is None
    assert await DetailOverviewRepository().customer(connection, customer["id"]) is None


@pytest.mark.parametrize("outcome", ["no_risk_identified", "risk_found"])
async def test_http_provider_flows_through_real_inference_validation_audit_and_risk_persistence(
    connection, sales_actor, monkeypatch, outcome,
):
    customer, visit = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    handler, provider = deterministic_handler(connection, monkeypatch, mock_inference=False)
    settings = replace(handler.settings, senseaudio_api_key="synthetic-original-key",
                       senseaudio_base_url="https://customer-risk.invalid", max_retries=0,
                       agent_inference_total_seconds=10, agent_inference_platform_seconds=1)
    provider.runtime = RuntimeConfiguration(settings, {})
    calls = []
    answer = {"outcome": outcome, "reviewed_visit_ids": [visit["id"]], "risks": [],
              "reason": "已逐条评估本次全部授权事实"}
    if outcome == "risk_found":
        answer["risks"] = [{
            "source_visit_id": visit["id"], "risk_type": "budget_risk", "title": "预算待批",
            "description": VISIT_BODY, "severity": "high", "suggested_action": "与财务确认预算",
            "evidence_detail": "客户预算尚未获批", "due_at": None,
        }]

    def wire(request):
        body = json.loads(request.content)
        calls.append(body)
        facts = json.loads(body["messages"][-1]["content"])
        assert facts["customer"]["id"] == customer["id"]
        assert [item["id"] for item in facts["visits"]] == [visit["id"]]
        assert facts["visits"][0]["follow_up_record"] == VISIT_BODY
        assert facts["coverage"]["complete"] is True
        assert "no_risk_identified" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    async with httpx.AsyncClient(base_url=settings.senseaudio_base_url, transport=httpx.MockTransport(wire)) as http:
        def direct_factory(config, **kwargs):
            return SenseAudioClient(config, client=http, **kwargs)

        monkeypatch.setattr(business, "InferenceService", lambda db, config, **kw: InferenceService(
            db, config, direct_factory=direct_factory, **kw,
        ))
        await handler.handle(assessment_id, sales_actor)
    assert len(calls) == 1 and provider.calls == []
    saved = await receipt(connection, assessment_id)
    assert saved["status"] == "succeeded" and saved["outcome"] == outcome
    assert saved["inference_operation_id"] is not None
    attempts = await connection.fetch(
        "SELECT provider_code,operation_id FROM agent.model_invocation WHERE operation_id=$1",
        saved["inference_operation_id"],
    )
    assert len(attempts) == 1 and attempts[0]["provider_code"] == "senseaudio"
    risks = await risk_rows(connection, customer["id"])
    assert len(risks) == (1 if outcome == "risk_found" else 0)
    if risks:
        assert risks[0]["status"] == "pending"
        assert risks[0]["input_snapshot"]["assessment_id"] == assessment_id
    assert await current_clear_assessment(connection, customer["id"]) is (outcome == "no_risk_identified")


@pytest.mark.parametrize("audit_case", ["matching", "older_attempt", "other_job", "other_capability"])
async def test_failed_assessment_links_only_its_current_inference_receipt(
    connection, sales_actor, monkeypatch, audit_case,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    assessment = await receipt(connection, assessment_id)
    handler, provider = deterministic_handler(connection, monkeypatch)
    provider.error = RuntimeError("synthetic provider failure")
    operation_id = str(uuid4())
    job_id = assessment["job_id"]
    if audit_case == "other_job":
        other_customer, _ = await customer_fixture(connection, sales_actor)
        other = await enqueue(connection, sales_actor, other_customer["id"])
        job_id = (await receipt(connection, other))["job_id"]

    async def write_failed_audit(_facts):
        await connection.execute(
            "INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,"
            "capability,job_id,status,scope_snapshot,configuration,input_summary,started_at) VALUES("
            "$1::uuid,$2::uuid,$3::uuid,$4,$5,$6::uuid,'running','{}','{}','{}',"
            "clock_timestamp()-make_interval(secs=>$7))",
            operation_id,sales_actor.workspace_id,sales_actor.user_id,sales_actor.role.value,
            "personal_risks" if audit_case != "other_capability" else "visit_quality",job_id,
            60 if audit_case == "older_attempt" else 0,
        )
        await connection.execute(
            "UPDATE agent.inference_operation SET status='failed',error_code='synthetic_failure' WHERE id=$1::uuid",
            operation_id,
        )

    provider.during_inference = write_failed_audit
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        await handler.handle(assessment_id, sales_actor)
    saved = await receipt(connection, assessment_id)
    assert saved["status"] == "running" and saved["outcome"] == "unknown"
    assert saved["result"] == {} and saved["risk_count"] == 0
    assert await risk_rows(connection, customer["id"]) == []
    assert (str(saved["inference_operation_id"]) if saved["inference_operation_id"] else None) == (
        operation_id if audit_case == "matching" else None
    )


async def test_retried_assessment_links_new_failed_operation_and_preserves_previous_audit(
    connection, sales_actor, monkeypatch,
):
    customer, _ = await customer_fixture(connection, sales_actor)
    assessment_id = await enqueue(connection, sales_actor, customer["id"])
    job_id = (await receipt(connection, assessment_id))["job_id"]
    handler, provider = deterministic_handler(connection, monkeypatch)
    provider.error = ConnectionError("synthetic retryable provider failure")
    operation_ids = []

    async def write_failed_audit(_facts):
        # Each attempt starts without claiming the preceding attempt's operation.
        assert (await receipt(connection, assessment_id))["inference_operation_id"] is None
        operation_id = str(uuid4())
        operation_ids.append(operation_id)
        await connection.execute(
            "INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,"
            "capability,job_id,status,scope_snapshot,configuration,input_summary) VALUES("
            "$1::uuid,$2::uuid,$3::uuid,$4,'personal_risks',$5::uuid,'running','{}','{}','{}')",
            operation_id, sales_actor.workspace_id, sales_actor.user_id, sales_actor.role.value, job_id,
        )
        await connection.execute(
            "UPDATE agent.inference_operation SET status='failed',error_code='synthetic_failure' WHERE id=$1::uuid",
            operation_id,
        )

    provider.during_inference = write_failed_audit
    for _ in range(2):
        with pytest.raises(ConnectionError, match="synthetic retryable provider failure"):
            await handler.handle(assessment_id, sales_actor)
        saved = await receipt(connection, assessment_id)
        assert str(saved["inference_operation_id"]) == operation_ids[-1]
        assert saved["status"] == "running" and saved["outcome"] == "unknown"
        assert saved["result"] == {} and saved["risk_count"] == 0
        await connection.execute(
            "UPDATE insight.customer_risk_assessment SET status='queued' WHERE id=$1::uuid", assessment_id,
        )
    assert len(operation_ids) == 2 and operation_ids[0] != operation_ids[1]
    audits = await connection.fetch(
        "SELECT id::text,status FROM agent.inference_operation WHERE job_id=$1::uuid", job_id,
    )
    assert {row["id"] for row in audits} == set(operation_ids)
    assert all(row["status"] == "failed" for row in audits)
    assert await risk_rows(connection, customer["id"]) == []
