"""FDE recording authorization and review provenance; no database or model calls."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sales_backend.api import business
from sales_backend.contracts.models import VisitCreate
from sales_backend.contracts.visit_flow import archival_snapshot
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.domain.company_rules import VisitAdmissionPolicy
from sales_backend.domain.visit_contract import PROMPT_VERSION
from sales_backend.services import visit_access, visit_archive, visit_review_gate

pytestmark = pytest.mark.asyncio


def person(role=RoleCode.FDE):
    return ActorContext(
        workspace_id=str(uuid4()),
        user_id=str(uuid4()),
        role=role,
        data_scope=DataScope.TEAM if role == RoleCode.FDE_LEAD else DataScope.SELF,
        team_ids=(str(uuid4()),),
    )


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
@pytest.mark.parametrize("missing", ["customer", "opportunity"])
async def test_recording_requires_nonempty_customer_and_personally_assigned_opportunity(role, missing):
    connection = AsyncMock()
    ids = {"customer": str(uuid4()), "opportunity": str(uuid4()), missing: None}
    with pytest.raises(PermissionError, match="本人参与"):
        await visit_access.require_visit_recording_scope(connection, person(role), ids["customer"], ids["opportunity"])
    connection.fetchrow.assert_not_awaited()


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
async def test_customer_panorama_or_team_scope_never_substitutes_for_direct_recording_assignment(role):
    connection = AsyncMock()
    connection.fetchrow.return_value = None
    connection.fetchval.return_value = True  # Broader customer READ may still be permitted.
    customer_id, opportunity_id = str(uuid4()), str(uuid4())
    with pytest.raises(PermissionError, match="名单"):
        await visit_access.require_visit_recording_scope(connection, person(role), customer_id, opportunity_id)
    connection.fetchval.assert_not_awaited()
    connection.fetchrow.return_value = {"id": opportunity_id, "customer_id": customer_id}
    await visit_access.require_visit_recording_scope(connection, person(role), customer_id, opportunity_id)


async def test_sales_retains_optional_post_extraction_association():
    connection = AsyncMock()
    await visit_access.require_visit_recording_scope(connection, person(RoleCode.SALES), str(uuid4()), None)
    connection.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(
    "changed", ["created_by_user_ref_id", "recorder_user_ref_id", "confirmed_by_user_ref_id", "status"]
)
async def test_supplement_requires_self_creation_recording_and_confirmation(monkeypatch, changed):
    actor = person()
    row = {
        "customer_id": str(uuid4()),
        "opportunity_id": str(uuid4()),
        "status": "archived",
        "created_by_user_ref_id": actor.user_id,
        "recorder_user_ref_id": actor.user_id,
        "confirmed_by_user_ref_id": actor.user_id,
    }
    row[changed] = "draft" if changed == "status" else str(uuid4())
    monkeypatch.setattr(visit_access, "recording_visit", AsyncMock(return_value=row))
    scope = AsyncMock()
    monkeypatch.setattr(visit_access, "require_visit_recording_scope", scope)
    with pytest.raises(PermissionError, match="本人创建"):
        await visit_access.require_visit_supplement_scope(AsyncMock(), actor, str(uuid4()))
    scope.assert_not_awaited()


async def test_supplement_rechecks_current_assignment_even_for_own_archived_record(monkeypatch):
    actor = person()
    row = {
        "customer_id": str(uuid4()),
        "opportunity_id": str(uuid4()),
        "status": "archived",
        "created_by_user_ref_id": actor.user_id,
        "recorder_user_ref_id": actor.user_id,
        "confirmed_by_user_ref_id": actor.user_id,
    }
    monkeypatch.setattr(visit_access, "recording_visit", AsyncMock(return_value=row))
    scope = AsyncMock(side_effect=PermissionError("已移出商机"))
    monkeypatch.setattr(visit_access, "require_visit_recording_scope", scope)
    with pytest.raises(PermissionError, match="已移出"):
        await visit_access.require_visit_supplement_scope(AsyncMock(), actor, str(uuid4()))
    assert scope.await_args.args[2:] == (row["customer_id"], row["opportunity_id"])


def reviewed():
    customer_id, opportunity_id = str(uuid4()), str(uuid4())
    fields = {
        "opportunity_id": opportunity_id,
        "follow_up_record": "已核对试点验收范围",
        "next_action": "9月15日提交验收方案",
        "_quality_review_run_id": str(uuid4()),
    }
    row = {
        "id": str(uuid4()),
        "status": "pending_confirm",
        "text_content": "技术测试原文",
        "business_context": {"mode": "visit_entry", "customer_id": customer_id, "opportunity_id": opportunity_id},
        "identity_context": {"permission_version": "current-assignment"},
        "payload": {
            "prompt_version": PROMPT_VERSION,
            "fields": fields,
            "quality_review": {"follow_up_score": 85, "next_action": {"passed": True}},
        },
    }
    return customer_id, fields, row


@pytest.mark.parametrize("changed", ["customer", "opportunity", "missing_snapshot", "revoked_snapshot"])
async def test_review_cannot_move_to_other_project_or_outlive_its_authorization(monkeypatch, changed):
    customer_id, fields, row = reviewed()
    repository = SimpleNamespace(lock_review=AsyncMock(return_value=row), confirm=AsyncMock())
    monkeypatch.setattr(visit_review_gate, "VisitReviewRepository", lambda: repository)
    gate = AsyncMock(side_effect=PermissionError("权限已变化") if changed == "revoked_snapshot" else None)
    monkeypatch.setattr(visit_review_gate, "require_agent_access", gate)
    if changed == "customer":
        customer_id = str(uuid4())
    elif changed == "opportunity":
        fields = {**fields, "opportunity_id": str(uuid4())}
    elif changed == "missing_snapshot":
        row["identity_context"] = {}
    with pytest.raises((PermissionError, ValueError)):
        await visit_review_gate.consume_review(AsyncMock(), person(), customer_id, fields)
    repository.confirm.assert_not_awaited()
    if changed != "revoked_snapshot":
        gate.assert_not_awaited()


async def test_review_uses_stored_score_and_passes_selected_project_to_live_scope_guard(monkeypatch):
    customer_id, fields, row = reviewed()
    fields = {**fields, "_follow_up_quality_score": 100}
    policy = {"id": "current", "definition": VisitAdmissionPolicy().model_dump()}
    row["business_context"]["visit_request"] = {**archival_snapshot(fields), "stage": "quality"}
    row["payload"].update(visit_stage="quality", company_policy=policy)
    monkeypatch.setattr(
        "sales_backend.repositories.company_rules.CompanyRulesRepository.active", AsyncMock(return_value=policy)
    )
    repository = SimpleNamespace(lock_review=AsyncMock(return_value=row), confirm=AsyncMock())
    monkeypatch.setattr(visit_review_gate, "VisitReviewRepository", lambda: repository)
    gate = AsyncMock()
    monkeypatch.setattr(visit_review_gate, "require_agent_access", gate)
    saved, artifact_id = await visit_review_gate.consume_review(AsyncMock(), person(), customer_id, fields)
    assert saved["_follow_up_quality_score"] == 85 and artifact_id == row["id"]
    assert gate.await_args.kwargs == {
        "opportunity_id": fields["opportunity_id"],
        "permission_version": "current-assignment",
    }
    repository.confirm.assert_awaited_once()


@pytest.mark.parametrize("operation", ["archive", "supplement"])
async def test_revoked_recording_scope_is_checked_before_http_idempotency_replay(monkeypatch, operation):
    connection = AsyncMock()

    class Database:
        @asynccontextmanager
        async def transaction(self, actor):
            yield connection

    actor = person()
    monkeypatch.setattr(business, "require_capability", AsyncMock())
    receipt = AsyncMock(return_value={"status": "archived", "sensitive_previous_response": True})
    monkeypatch.setattr(business, "execute_mutation", receipt)
    denied = AsyncMock(side_effect=PermissionError("已移出商机"))
    monkeypatch.setattr(business, "require_visit_recording_scope", denied)
    monkeypatch.setattr(business, "require_visit_supplement_scope", denied)
    with pytest.raises(HTTPException) as error:
        if operation == "archive":
            await business.create_visit(
                VisitCreate(customer_id=str(uuid4()), fields={"opportunity_id": str(uuid4())}),
                SimpleNamespace(actor=actor),
                Database(),
                uuid4(),
            )
        else:
            await business.supplement_visit(
                str(uuid4()),
                {"contact_name": "测试联系人"},
                SimpleNamespace(actor=actor),
                Database(),
                uuid4(),
            )
    assert error.value.status_code == 403
    denied.assert_awaited_once()
    receipt.assert_not_awaited()


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.SALES])
async def test_archive_returns_final_stored_version_after_binding_without_losing_receipt_fields(monkeypatch, role):
    actor = person(role)
    customer_id, fields, reviewed_row = reviewed()
    events = []
    visit_id = str(uuid4())
    receipt = {
        "id": visit_id,
        "customer_id": customer_id,
        "status": "archived",
        "fields": {"follow_up_record": fields["follow_up_record"]},
        "completed_count": 6,
        "total_count": 12,
    }

    async def bind(*args):
        events.append("review_bound")

    async def enqueue(*args, **kwargs):
        events.append("review_enqueued")

    async def detail(*args):
        assert events == ["review_bound", "review_enqueued", "risk_enqueued"]
        return {"id": visit_id, "customer_id": customer_id, "status": "archived", "version_no": 2}

    visits = SimpleNamespace(create=AsyncMock(return_value=receipt), detail=AsyncMock(side_effect=detail))
    reviews = SimpleNamespace(
        lock_previous_archive=AsyncMock(return_value=None), bind_archive=AsyncMock(side_effect=bind)
    )
    monkeypatch.setattr(visit_archive, "VisitRepository", lambda: visits)
    monkeypatch.setattr(visit_archive, "VisitReviewRepository", lambda: reviews)
    monkeypatch.setattr(visit_archive, "require_capability", AsyncMock())
    monkeypatch.setattr(visit_archive, "require_visit_recording_scope", AsyncMock())
    monkeypatch.setattr(visit_archive, "consume_review", AsyncMock(return_value=(fields, reviewed_row["id"])))
    monkeypatch.setattr(visit_archive, "archive_fde_collaboration", AsyncMock())
    monkeypatch.setattr(visit_archive, "enqueue_battle_map_review", AsyncMock(side_effect=enqueue))
    risk_enqueue = AsyncMock(side_effect=lambda *args, **kwargs: events.append("risk_enqueued"))
    monkeypatch.setattr(visit_archive, "enqueue_customer_risk_review", risk_enqueue)
    monkeypatch.setattr(
        visit_archive, "ProfileRepository", lambda: SimpleNamespace(ensure_daily_competency_review=AsyncMock())
    )
    response = await visit_archive.archive_visit(AsyncMock(), actor, customer_id=customer_id, fields=fields)
    risk_enqueue.assert_awaited_once()
    assert risk_enqueue.call_args.kwargs["customer_id"] == customer_id
    assert risk_enqueue.call_args.kwargs["trigger_id"] == visit_id
    assert response["version_no"] == 2
    assert response["fields"] == receipt["fields"]
    assert response["completed_count"] == 6 and response["total_count"] == 12
    visits.detail.assert_awaited_once()
