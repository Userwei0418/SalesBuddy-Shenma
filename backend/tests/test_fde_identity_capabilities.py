from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.auth.models import PasswordLogin
from sales_backend.contracts.models import TaskEventCreate
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.domain.capabilities import permission_version, role_capabilities
from sales_backend.domain.company_rules import FdeCapabilitiesPolicy
from sales_backend.repositories.identity import ActorRecord
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.auth import AuthService
from sales_backend.services.capabilities import capability_snapshot, require_capability
from sales_backend.services.tasks import TaskService


def fde(role=RoleCode.FDE):
    return ActorContext(
        workspace_id=str(uuid4()),
        user_id=str(uuid4()),
        role=role,
        data_scope=DataScope.TEAM if role == RoleCode.FDE_LEAD else DataScope.SELF,
        team_ids=(str(uuid4()),),
    )


@pytest.mark.parametrize("role", ["fde", "fde_lead"])
def test_fde_role_defaults_deny_commercial_and_recording_writes(role):
    caps = role_capabilities(role)
    assert caps["task.create"] and caps["task.respond"] and caps["customer.read"] and caps["opportunity.read"]
    assert caps["advice.decide"]  # Human decisions on FDE-scoped advice remain available.
    for key in [
        "customer.create",
        "customer.edit",
        "customer.claim",
        "opportunity.edit",
        "actual.manage",
        "risk.resolve",
        "console.access",
        "visit.create",
    ]:
        assert caps[key] is False, key
    enabled = role_capabilities(role, visit_entry_enabled=True)
    assert {key for key in caps if enabled[key] != caps[key]} == {"visit.create", "visit.supplement"}
    assert caps["team.view"] == (role == "fde_lead")
    assert caps["fde.members.manage"] == (role == "fde_lead")


def test_permission_version_changes_when_scope_or_relation_changes():
    actor = fde()
    caps = role_capabilities("fde")
    first = permission_version(actor, caps, "relation-1")
    assert first == permission_version(actor, dict(reversed(list(caps.items()))), "relation-1")
    assert first != permission_version(actor, caps, "relation-2")
    assert first != permission_version(actor.model_copy(update={"team_ids": ()}), caps, "relation-1")


@pytest.mark.parametrize("role", ["sales", "supervisor", "manager"])
def test_all_sales_roles_have_the_same_personal_home_actions(role):
    caps = role_capabilities(role)
    assert all(caps[key] for key in ("customer.claim", "visit.create", "visit.supplement", "task.create"))
    assert not caps["customer.create"] and not caps["console.access"]


def test_policy_precedence_is_user_then_role_then_global():
    uid = str(uuid4())
    policy = FdeCapabilitiesPolicy(visit_entry_enabled=False, role_overrides={"fde": True}, user_overrides={uid: False})
    assert policy.enabled("fde")
    assert not policy.enabled("fde", uid)
    assert not policy.enabled("fde_lead")
    assert not policy.enabled("administrator")
    for payload in [
        {"user_overrides": {"not-a-user": True}},
        {"role_overrides": {"administrator": True}},
        {"visit_entry_enabled": "true"},
        {"commercial_write": True},
    ]:
        with pytest.raises(ValidationError):
            FdeCapabilitiesPolicy(**payload)


@pytest.mark.asyncio
async def test_live_policy_changes_capabilities_without_reissuing_token():
    actor = fde()
    connection = AsyncMock()
    connection.fetchrow.return_value = {"visit_entry_enabled": False, "permission_version": "one"}
    disabled = await capability_snapshot(connection, actor)
    with pytest.raises(PermissionError, match="未开启"):
        await require_capability(connection, actor, "visit.create")
    connection.fetchrow.return_value = {"visit_entry_enabled": True, "permission_version": "two"}
    enabled = await capability_snapshot(connection, actor)
    assert enabled["capabilities"]["visit.create"]
    assert enabled["permission_version"] != disabled["permission_version"]
    await require_capability(connection, actor, "visit.create")
    with pytest.raises(PermissionError):
        await require_capability(connection, actor, "opportunity.edit")


@pytest.mark.asyncio
@pytest.mark.parametrize(("role", "scope"), [("fde", "self"), ("fde_lead", "team")])
async def test_operations_role_binding_gives_fde_narrow_scope(role, scope):
    connection = AsyncMock()
    actor = fde(RoleCode.FDE_LEAD)
    await OperationsAccountRepository().roles_and_team(connection, actor, actor.user_id, [role], actor.team_ids[0])
    bindings = [
        call.args for call in connection.execute.call_args_list if "INSERT INTO platform.role_binding" in call.args[0]
    ]
    assert len(bindings) == 1 and bindings[0][3:5] == (role, scope)


def test_fde_actor_response_and_explicit_login_role():
    actor = fde(RoleCode.FDE_LEAD)
    result = AuthService.actor_response(ActorRecord(actor, "FDE001", "负责人", ("FDE部门",)))
    assert result.role_name == "FDE主管" and result.data_scope == "team"
    assert result.capabilities["team.view"] and not result.capabilities["console.access"]
    assert result.permission_version
    assert PasswordLogin(account_code="FDE001", password="Test-only1234567", role="fde_lead").role == "fde_lead"


def test_task_handover_requires_version_reason_and_replacement():
    for data in [
        {"event_type": "cancel"},
        {"event_type": "cancel", "version_no": 2, "note": "  "},
        {"event_type": "reassign", "version_no": 2, "note": "交接"},
        {"event_type": "complete", "assignee_account_code": "FDE002"},
    ]:
        with pytest.raises(ValidationError):
            TaskEventCreate(**data)
    assert TaskEventCreate(event_type="cancel", version_no=2, note="已失去资格").event_type == "cancel"
    assert (
        TaskEventCreate(
            event_type="reassign", version_no=2, note="人员交接", assignee_account_code="FDE002"
        ).assignee_account_code
        == "FDE002"
    )


@pytest.mark.asyncio
async def test_removed_fde_owner_cannot_continue_task_even_if_task_is_readable():
    connection = AsyncMock()
    connection.fetchval.return_value = False
    with pytest.raises(Exception, match="待交接"):
        await TaskService._require_current_owner_eligibility(connection, fde(), str(uuid4()))


@pytest.mark.asyncio
async def test_fde_agent_modes_block_sales_materialization_and_changed_snapshots():
    from sales_backend.services.agent_access import require_agent_access

    connection = AsyncMock()
    actor = fde()
    connection.fetchrow.return_value = {"visit_entry_enabled": False, "permission_version": "current"}
    for mode in ["today_tasks", "personal_risks", "opportunity_draft", "customer_create", "management_task"]:
        with pytest.raises(PermissionError):
            await require_agent_access(connection, actor, mode)
    for mode in ["chatbi", "customer_chatbi", "operating_report"]:
        await require_agent_access(connection, actor, mode, str(uuid4()) if mode == "customer_chatbi" else None, permission_version="current")
    with pytest.raises(PermissionError, match="权限已变化"):
        await require_agent_access(connection, actor, "chatbi", permission_version="old")
    with pytest.raises(PermissionError, match="未开启"):
        await require_agent_access(connection, actor, "visit_entry", str(uuid4()), opportunity_id=str(uuid4()), permission_version="current")
    connection.fetchrow.return_value["visit_entry_enabled"] = True
    await require_agent_access(connection, actor, "visit_entry", str(uuid4()), opportunity_id=str(uuid4()), permission_version="current")


def test_advice_cache_key_includes_fde_permission_version():
    from sales_backend.services.advice import cache_key

    actor = fde()
    before = cache_key(actor, "opportunity", "id", "progress", "same-facts", {}, "permission-1")
    assert before == cache_key(actor, "opportunity", "id", "progress", "same-facts", {}, "permission-1")
    assert before != cache_key(actor, "opportunity", "id", "progress", "same-facts", {}, "permission-2")


@pytest.mark.asyncio
async def test_analysis_snapshot_uses_database_version_separately_from_ui_version():
    from sales_backend.repositories.capabilities import CapabilityRepository

    connection = AsyncMock()
    connection.fetchrow.return_value = {"visit_entry_enabled": False, "permission_version": "db-raw"}
    actor = fde()
    snapshot = await CapabilityRepository().analysis_identity(connection, actor)
    assert snapshot["permission_version"] == "db-raw"
    assert (await capability_snapshot(connection, actor))["permission_version"] != "db-raw"


def test_fde_activity_and_task_handover_have_business_names():
    from sales_backend.domain.business_activity import present_activity
    from tests.test_business_activity import record

    for code, label in [
        ("opportunity.fde_members", "调整FDE协助成员"),
        ("visit.fde_participants", "登记实际协同人员"),
        ("company_rule.change", "调整公司规则"),
        ("task.cancel", "取消任务"),
        ("task.reassign", "转交任务"),
    ]:
        view = present_activity(record(code, {"note": "事务中保存的业务说明"}))
        assert view["action_label"] == label


@pytest.mark.asyncio
async def test_audio_visit_permission_is_live_while_fde_manual_task_audio_remains_allowed():
    from fastapi import HTTPException

    from sales_backend.api.audio import require_audio_purpose

    actor = fde()
    connection = AsyncMock()
    connection.fetchrow.return_value = {"visit_entry_enabled": False, "permission_version": "one"}
    with pytest.raises(HTTPException) as error:
        await require_audio_purpose(connection, actor, "visit_entry")
    assert error.value.status_code == 403
    await require_audio_purpose(connection, actor, "management_task")
    connection.fetchrow.return_value["visit_entry_enabled"] = True
    await require_audio_purpose(connection, actor, "visit_entry")
    with pytest.raises(HTTPException):
        await require_audio_purpose(connection, actor, "customer_create")


@pytest.mark.asyncio
async def test_file_processing_rechecks_policy_before_persisting_text(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from sales_backend.services import visit_import

    source = tmp_path / "record.md"
    source.write_text("本次拜访的真实流程测试文本")
    enabled = True
    connection = AsyncMock()

    async def fetchrow(sql, *args):
        if "fde_visit_entry_enabled" in sql:
            return {"visit_entry_enabled": enabled, "permission_version": str(enabled)}
        return {"filename": "record.md", "file_path": str(source), "file_size": source.stat().st_size, "status": "queued"}

    connection.fetchrow.side_effect = fetchrow

    class Database:
        @asynccontextmanager
        async def transaction(self, actor):
            yield connection

    def extract(filename, content):
        nonlocal enabled
        enabled = False
        return content.decode()

    monkeypatch.setattr(visit_import, "IMPORT_ROOT", tmp_path)
    monkeypatch.setenv("VISIT_IMPORT_ROOT", str(tmp_path))
    monkeypatch.setattr(visit_import, "document_text", extract)
    with pytest.raises(PermissionError, match="未开启"):
        await visit_import.VisitImportHandler(Database()).handle(str(uuid4()), fde())
    assert not any("status='succeeded'" in call.args[0] for call in connection.execute.call_args_list)


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
def test_fde_report_has_native_prompt_and_output_contract(role):
    from sales_backend.services.agent_run.contract import INSTANT_SUMMARY_CONTRACT, ensure_instant_summary_contract
    from sales_backend.services.agent_run.models import RunInput
    from sales_backend.services.agent_run.prompts import AgentPromptBuilder

    run = RunInput("run", "conversation", "总结协作情况", "operating_report", None, fde(role))
    facts = {"scope": {"scope_label": "协作范围"}, "summary": {"period_visits": 2}}
    messages = AgentPromptBuilder().build(run, facts)
    assert "FDE" in messages[0].content
    assert "参与商机" in messages[0].content and "独占" in messages[0].content
    title, keys = INSTANT_SUMMARY_CONTRACT[role]
    result = ensure_instant_summary_contract(run, {"summary": "按实际参与统计", **dict.fromkeys(keys, [])}, facts)
    assert result["title"] == title and result["scope"] == "协作范围"


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
def test_sales_profile_routes_reject_fde_before_database_queries(role):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sales_backend.api.dependencies import get_database, get_identity
    from sales_backend.api.profile import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=fde(role))
    # A missing DB cannot turn a denied role into a SQL 500.
    app.dependency_overrides[get_database] = lambda: None
    with TestClient(app) as client:
        for path in ["evaluation", "performance", "sales-growth", "sales-growth/scoped", "team-members/XS001/sales-growth"]:
            response = client.get("/api/v1/profile/" + path)
            assert response.status_code == 403, path
            assert response.json()["detail"] == "SALES_PROFILE_UNAVAILABLE"
        assert client.post("/api/v1/profile/sales-growth/review").status_code == 403
        assert client.post("/api/v1/profile/sales-targets", json={}).status_code == 403


@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.FDE, RoleCode.FDE_LEAD])
def test_customer_question_rejects_missing_subject_at_http_boundary(role):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sales_backend.api.assistant import router
    from sales_backend.api.dependencies import get_database, get_identity

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=fde(role))
    app.dependency_overrides[get_database] = lambda: None
    with TestClient(app) as client:
        for customer_id in [None, "", " "]:
            response = client.post("/api/v1/conversations", json={"mode": "customer_chatbi", "customer_id": customer_id})
            assert response.status_code == 422
