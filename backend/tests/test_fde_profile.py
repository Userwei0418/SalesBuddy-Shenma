"""Factual ratios, trusted AI surface and stale-result rejection without providers."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.contracts.fde_profile import FdeProfileResponse
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.domain.fde_coaching import coaching_inputs, model_facts
from sales_backend.domain.fde_profile import PORTRAIT_PROMPT_VERSION, TZ, portrait
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.services import fde_profile
from sales_backend.services.agent_run.contract import ensure_instant_summary_contract
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.agent_run.prompts import AgentPromptBuilder

NOW = datetime(2026, 9, 13, 12, tzinfo=TZ)


def person(role=RoleCode.FDE):
    return ActorContext(
        workspace_id=str(uuid4()),
        user_id=str(uuid4()),
        role=role,
        data_scope=DataScope.TEAM if role == RoleCode.FDE_LEAD else DataScope.SELF,
    )


def facts(actor=None, **kwargs):
    values = dict(days=30, now=NOW, permission_version="permission-1", visits=[], projects=[], tasks=[])
    values.update(kwargs)
    rows = [{**v, "communication": "已确认项目验证范围", "next_action": "按约定提交方案"}
            for v in values["visits"] if v["detail_visible"]]
    values.setdefault("coaching", coaching_inputs(rows, []))
    return portrait(actor or person(), **values)


def visit(id="v1", project="p1", customer="c1", quality=80, plan=True, readable=True):
    return {
        "id": id,
        "opportunity_id": project,
        "customer_id": customer,
        "interaction_at": NOW,
        "detail_visible": readable,
        "follow_up_score": quality,
        "has_next_action": plan,
        "version_no": 2,
    }


def task(id, status, *, due=None, completed=None):
    return {"id": id, "status": status, "due_at": due, "completed_at": completed, "version_no": 1}


def test_empty_denominators_stay_null_without_fabricated_performance():
    result = FdeProfileResponse.model_validate(fde_profile.presentation(facts()))
    assert result.sample_count == 0 and result.review_status == "empty"
    assert result.latest.overall_score is None and result.latest.advice == []
    assert len(result.framework.dimensions) == 6
    assert all(d.score is None and d.denominator == 0 for d in result.latest.dimensions)
    assert "归档0条记录" in result.latest.summary


def test_summary_uses_database_counts_even_when_model_invents_perfect_scores():
    loaded = facts(
        visits=[visit(quality=90)],
        projects=[
            {"id": "p1", "customer_id": "c1"},
            {"id": "p2", "customer_id": "c1"},
        ],
    )
    run = {
        "id": "run",
        "status": "succeeded",
        "completed_at": NOW,
        "result": {
            "summary": "evidence_count=1，各项均为满分，能力优秀。",
            "action_plan": [{"title": "跟进未覆盖项目", "detail": "约定下一次技术验证的时间和交付内容。"}],
        },
    }
    rendered = fde_profile.presentation(loaded, run)
    assert rendered["latest"]["summary"] == (
        "近30天，本人归档1条记录；当前协助2个项目，涉及1家客户。记录较少，建议随跟进持续补充。"
    )
    assert rendered["latest"]["summary"] == fde_profile.presentation(loaded)["latest"]["summary"]
    assert rendered["latest"]["dimensions"][0]["score"] == 90
    assert rendered["latest"]["advice"][0]["content"] == run["result"]["action_plan"][0]["detail"]


@pytest.mark.asyncio
async def test_prompt_v3_is_part_of_native_facts_key_and_invalidates_v2():
    actor = person()
    connection = AsyncMock()
    connection.fetchval.side_effect = [NOW, "permission-1"]
    connection.fetchrow.side_effect = [None, None, None]
    connection.fetch.side_effect = [[], [], [], [], []]
    loaded = await fde_profile.fde_profile.profile_facts(connection, actor, 30)
    assert PORTRAIT_PROMPT_VERSION == "fde.profile.coaching.v3"
    assert loaded["configuration"]["portrait_prompt_version"] == PORTRAIT_PROMPT_VERSION
    previous = facts(
        actor, configuration={**loaded["configuration"], "portrait_prompt_version": "fde.profile.coaching.v2"}
    )
    assert loaded["facts_fingerprint"] != previous["facts_fingerprint"]


@pytest.mark.asyncio
async def test_published_business_guidance_invalidates_cached_coaching_without_changing_facts():
    actor = person()

    async def load(version):
        connection = AsyncMock()
        connection.fetchval.side_effect = [NOW, "permission-1"]
        connection.fetchrow.side_effect = [
            None, None, {"id": "policy-" + str(version), "version_no": version, "revision_no": 2},
        ]
        connection.fetch.side_effect = [[], [], [], [], []]
        result = await fde_profile.fde_profile.profile_facts(connection, actor, 30)
        assert "agent_business.operating_report" in connection.fetchrow.await_args_list[-1].args[0]
        return result

    old, new = await load(1), await load(2)
    assert old["dimensions"] == new["dimensions"]
    assert old["facts_fingerprint"] != new["facts_fingerprint"]
    assert new["configuration"]["business_policy"]["version_no"] == 2


def test_six_metrics_have_independent_deduplicated_denominators():
    day = NOW - timedelta(days=1)
    result = facts(
        visits=[
            visit(),
            visit("v2", quality=90, plan=False),
            visit("v3", project="past", customer="old", readable=False, quality=None, plan=False),
        ],
        projects=[
            {"id": "p1", "customer_id": "c1"},
            {"id": "p2", "customer_id": "c1"},
            {"id": "p3", "customer_id": "c2"},
        ],
        tasks=[
            task("t1", "completed", due=NOW, completed=day),
            task("t2", "completed", due=day, completed=NOW),
            task("t3", "in_progress", due=day),
            task("t4", "cancelled", due=day),
            task("t5", "pending_execution", due=NOW + timedelta(days=2)),
        ],
    )
    metrics = {d["code"]: d for d in result["dimensions"]}
    assert [d["score"] for d in result["dimensions"]] == [85, 50, 33.3, 50, 66.7, 50]
    assert result["sample_count"] == 3 and metrics["record_quality"]["evidence_count"] == 2
    assert result["evidence_coverage"]["readable_archived_visits"] == 2
    assert metrics["task_completion"]["denominator"] == 3


def test_fingerprint_stable_on_clock_progress_but_not_data_identity_or_configuration_change():
    actor = person()
    base = dict(visits=[visit()], configuration={"prompt_digest": "one"})
    current = facts(actor, **base)
    assert current["facts_fingerprint"] == facts(actor, **base, now=NOW + timedelta(seconds=3))["facts_fingerprint"]
    for changed in [
        dict(permission_version="new"),
        dict(configuration={"prompt_digest": "two"}),
        dict(visits=[{**visit(), "version_no": 3}]),
        dict(days=7),
        dict(now=NOW + timedelta(days=1)),
    ]:
        assert facts(actor, **{**base, **changed})["facts_fingerprint"] != current["facts_fingerprint"]
    assert facts(person(), **base)["facts_fingerprint"] != current["facts_fingerprint"]


@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER, RoleCode.OPERATIONS])
def test_portrait_rejects_sales_and_management_roles(role):
    with pytest.raises(PermissionError):
        fde_profile.require_fde(person(role))


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
async def test_stale_facts_or_permission_block_both_load_and_late_persistence(monkeypatch, role):
    actor = person(role)
    loaded = facts(actor, visits=[visit()])
    run = RunInput(
        "r",
        "c",
        "本人",
        "operating_report",
        None,
        actor,
        permission_version="permission-1",
        surface="fde_profile",
        profile_days=30,
        facts_fingerprint=loaded["facts_fingerprint"],
    )
    fresh = AsyncMock(return_value=loaded)
    monkeypatch.setattr(fde_profile.fde_profile, "profile_facts", fresh)
    assert (await fde_profile.current_run_facts(AsyncMock(), run))["scope"]["user_id"] == actor.user_id
    connection = AsyncMock()

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection

    db = SimpleNamespace(transaction=transaction)
    fresh.return_value = {**loaded, "facts_fingerprint": "changed"}
    with pytest.raises(PermissionError, match="事实已变化"):
        await AgentFactsLoader(db).load(run)
    import sales_backend.services.agent_run.persist as persist_module

    monkeypatch.setattr(persist_module, "require_agent_access", AsyncMock())
    with pytest.raises(PermissionError, match="事实已变化"):
        await AgentRunStore(db, SimpleNamespace()).persist_result(run, {"summary": "obsolete"}, loaded)
    connection.execute.assert_not_awaited()
    fresh.return_value = {**loaded, "permission_version": "revoked"}
    with pytest.raises(PermissionError, match="权限已变化"):
        await fde_profile.current_run_facts(connection, run)


@pytest.mark.asyncio
async def test_same_success_is_reused_and_empty_profile_does_not_enqueue(monkeypatch):
    actor, connection = person(), AsyncMock()
    loaded = facts(actor, visits=[visit()])
    monkeypatch.setattr(fde_profile, "require_agent_access", AsyncMock())
    monkeypatch.setattr(fde_profile.fde_profile, "lock_profile", AsyncMock())
    fresh = AsyncMock(return_value=loaded)
    monkeypatch.setattr(fde_profile.fde_profile, "profile_facts", fresh)
    existing = AsyncMock(
        return_value={
            "id": "old",
            "status": "succeeded",
            "completed_at": NOW,
            "result": {"summary": "真实已保存建议", "action_plan": [{"title": "计划", "detail": "补充验证步骤"}]},
        }
    )
    monkeypatch.setattr(fde_profile.fde_profile, "matching_run", existing)
    repository = AsyncMock()
    monkeypatch.setattr(fde_profile, "AssistantRepository", lambda: repository)
    result = await fde_profile.request_review(connection, actor, 30)
    assert result["review_run_id"] == "old" and result["latest"]["advice"][0]["content"] == "补充验证步骤"
    repository.create_conversation.assert_not_awaited()
    fresh.return_value, existing.return_value = facts(actor), None
    assert (await fde_profile.request_review(connection, actor, 30))["review_status"] == "empty"
    repository.enqueue_message.assert_not_awaited()


@pytest.mark.parametrize("role", [RoleCode.FDE, RoleCode.FDE_LEAD])
def test_profile_prompt_and_contract_keep_own_coaching_after_runtime_override(role):
    actor = person(role)
    run = RunInput("r", "c", "本人", "operating_report", None, actor, surface="fde_profile", profile_days=30)
    loaded = facts(actor, visits=[visit()])
    prompt = AgentPromptBuilder().build(run, model_facts(loaded), "公司已调优的提示词")[0].content
    assert prompt.index("公司已调优") < prompt.index("允许零至三条")
    assert "负责人在本页面也只分析本人" in prompt
    assert "返回空action_plan" in prompt
    assert "source_refs" in prompt
    payload = {
        "summary": "本人覆盖仍需改进",
        "safe_customers": [],
        "attention_customers": [],
        "team_comparison": [],
        "action_plan": [{"title": "跟进", "detail": "核对材料中待确认的安排", "source_refs": ["visit:v1"]}],
        "dimensions": [{"score": 100}],
        "overall_score": 100,
        "operations": ["write"],
    }
    result = ensure_instant_summary_contract(run, payload, model_facts(loaded))
    assert result["scope"] == "本人" and result["period"] == "近30天"
    assert not ({"overall_score", "dimensions", "operations"} & result.keys())
    with pytest.raises(SenseAudioError):
        ensure_instant_summary_contract(
            run, {**payload, "action_plan": payload["action_plan"] * 4}, model_facts(loaded)
        )
    ordinary = replace(run, surface=None)
    assert "即时总结" in ensure_instant_summary_contract(ordinary, payload, facts(actor))["title"]
