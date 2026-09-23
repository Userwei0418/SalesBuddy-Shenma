from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.contracts.visit_flow import archival_snapshot, canonical_fields, validate_stage_result
from sales_backend.domain.agent import RoleCode
from sales_backend.domain.company_rules import VisitAdmissionPolicy
from sales_backend.services.visit_review_gate import consume_review

FIELDS = canonical_fields(
    {
        "customer_name": "演示客户",
        "customer_type": "客户",
        "follow_up_record": "客户确认试点结果\n仍需补样本。",
        "next_action": "9月18日销售提交补充方案",
        "interaction_at": "2026-09-15",
        "created_date": "2026-09-15",
        "contact_name": "陈经理",
    }
)
POLICY = {"id": "current", "definition": VisitAdmissionPolicy().model_dump()}
QUALITY = {
    "follow_up_score": 81,
    "suggestions": [],
    "next_action": {"passed": True, "time_found": True, "goal_or_plan_found": True, "suggestions": []},
}
FACTS = {"visit_stage": "quality", "fields": FIELDS, "summary": "客户确认试点结果", "company_policy": POLICY}
RESULT = {"fields": FIELDS, "summary": FACTS["summary"], "quality_review": QUALITY}


def test_structure_has_no_score_and_uses_bound_identity():
    facts = {
        "visit_stage": "structure",
        "server_fields": {"customer_name": "选中客户", "customer_type": "客户", "created_date": "2026-09-15"},
        "is_first_visit": False,
    }
    result = validate_stage_result({"fields": FIELDS, "summary": "已整理"}, facts)
    assert result["fields"]["customer_name"] == "选中客户"
    assert "quality_review" not in result
    with pytest.raises(ValueError):
        validate_stage_result(RESULT, facts)


@pytest.mark.parametrize(
    "key,value",
    [
        ("contact_name", "其他人"),
        ("next_action", "下周提交"),
        ("customer_type", "商机客户"),
        ("follow_up_record", FIELDS["follow_up_record"] + " "),
    ],
)
def test_quality_never_rewrites_human_fields(key, value):
    result = deepcopy(RESULT)
    result["fields"][key] = value
    with pytest.raises(ValueError, match="改写"):
        validate_stage_result(result, FACTS)


@pytest.mark.parametrize("score", [True, "80", 80.5, -1, 101])
def test_quality_rejects_non_integer_score(score):
    result = deepcopy(RESULT)
    result["quality_review"]["follow_up_score"] = score
    with pytest.raises(ValueError):
        validate_stage_result(result, FACTS)


def test_quality_rejects_contradictory_action_and_uses_trusted_policy():
    result = deepcopy(RESULT)
    result["quality_review"]["next_action"]["time_found"] = False
    with pytest.raises(ValueError):
        validate_stage_result(result, FACTS)
    assert validate_stage_result(RESULT, FACTS)["company_policy"] == POLICY


@pytest.fixture
def gate(monkeypatch):
    fields = {**FIELDS, "_quality_review_run_id": "11111111-1111-1111-1111-111111111111"}
    request = {**archival_snapshot(fields), "stage": "quality"}
    row = {
        "id": "artifact",
        "status": "pending_confirm",
        "payload": validate_stage_result(RESULT, FACTS),
        "business_context": {"customer_id": "customer", "visit_request": request},
    }
    db = SimpleNamespace(fetchrow=AsyncMock(return_value=row), execute=AsyncMock())
    actor = SimpleNamespace(workspace_id="workspace", user_id="user", role=RoleCode.SALES)
    monkeypatch.setattr(
        "sales_backend.repositories.company_rules.CompanyRulesRepository.active", AsyncMock(return_value=POLICY)
    )
    return db, actor, fields, row


@pytest.mark.asyncio
async def test_archive_uses_stored_quality(gate):
    db, actor, fields, row = gate
    trusted, _ = await consume_review(db, actor, "customer", {**fields, "_follow_up_quality_score": 100})
    assert trusted["_follow_up_quality_score"] == 81
    assert db.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"contact_name": "新人"},
        {"source_import_id": "other"},
        {"opportunity_id": "other"},
        {"collaborator_ids": ["other"]},
        {"_fde_participant_ids": ["other"]},
    ],
)
async def test_archive_invalidates_changed_snapshot(gate, change):
    db, actor, fields, row = gate
    with pytest.raises(ValueError, match="修改"):
        await consume_review(db, actor, "customer", {**fields, **change})
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,score,passed", [("structure", 90, True), ("quality", 60, True), ("quality", 90, False)])
async def test_archive_cannot_skip_quality_or_rejections(gate, stage, score, passed):
    db, actor, fields, row = gate
    row = deepcopy(row)
    db.fetchrow.return_value = row
    row["business_context"]["visit_request"]["stage"] = stage
    row["payload"]["quality_review"]["follow_up_score"] = score
    row["payload"]["quality_review"]["next_action"]["passed"] = passed
    with pytest.raises(ValueError):
        await consume_review(db, actor, "customer", fields)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_change_requires_new_review(gate):
    db, actor, fields, row = gate
    row = deepcopy(row)
    db.fetchrow.return_value = row
    row["payload"]["company_policy"]["id"] = "old"
    with pytest.raises(ValueError, match="规则已更新"):
        await consume_review(db, actor, "customer", fields)


@pytest.mark.asyncio
async def test_quality_facts_keep_human_date_strings(monkeypatch):
    from sales_backend.services.agent_run.facts import AgentFactsLoader

    loader = AgentFactsLoader(None)
    monkeypatch.setattr(
        loader, "_load", AsyncMock(return_value={**deepcopy(FACTS), "data_as_of": "2026-09-15T00:00:00+00:00"})
    )
    facts = await loader.load(SimpleNamespace(mode="visit_entry"))
    assert facts["fields"] == FIELDS
    assert facts["fields"]["interaction_at"] == "2026-09-15"
