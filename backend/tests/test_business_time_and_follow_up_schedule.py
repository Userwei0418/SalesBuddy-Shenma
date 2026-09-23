"""Regressions for UTC day shifts, source omissions and silent AI rescheduling."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.business_time import BUSINESS_TZ, localize_business_times
from sales_backend.domain.follow_up_schedule import (
    FollowUpDeadlineNeedsConfirmation,
    deadline_context,
    follow_up_due,
    require_resolvable_follow_up_deadlines,
    validate_today_task_dates,
)
from sales_backend.domain.model_contract import ModelContractError, contract_failure_details
from sales_backend.repositories.advice_facts import AdviceFactsRepository
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import InvalidAgentResult


@pytest.mark.asyncio
async def test_all_agent_modes_share_business_calendar_without_changing_instants():
    from sales_backend.services.agent_run.facts import AgentFactsLoader

    instant = datetime(2026, 9, 6, 16, tzinfo=UTC)
    loader = AgentFactsLoader(None)
    loader._load = AsyncMock(return_value={"visits": [{"interaction_at": instant}], "amount": 120000})
    facts = await loader.load(SimpleNamespace(mode="operating_report"))
    visit = facts["visits"][0]
    assert visit["visit_date"] == "2026-09-07"
    assert visit["interaction_at"].isoformat() == "2026-09-07T00:00:00+08:00"
    assert visit["interaction_at"].astimezone(UTC) == instant
    assert facts["amount"] == 120000
from sales_backend.services.agent_run.materialize import follow_up_task_drafts

NOW = datetime(2026, 9, 13, 11, 0, tzinfo=UTC)


def candidate(text, **overrides):
    return {
        "source_type": "visit_follow_up", "source_id": "visit-1", "next_action": text,
        "recorder_name": "刘志德", "interaction_at": datetime(2026, 9, 6, 16, tzinfo=UTC), **overrides,
    }


def test_business_visit_date_is_shanghai_calendar_day_without_changing_instant():
    original = {"interaction_at": datetime(2026, 9, 6, 16, tzinfo=UTC),
                "records": [{"due_at": datetime(2026, 9, 16, 4, tzinfo=UTC)}]}
    result = localize_business_times(original)
    assert result["visit_date"] == "2026-09-07"
    assert result["interaction_at"].isoformat() == "2026-09-07T00:00:00+08:00"
    assert result["interaction_at"] == original["interaction_at"]
    assert result["records"][0]["due_at"].isoformat() == "2026-09-16T12:00:00+08:00"
    assert "visit_date" not in original


@pytest.mark.asyncio
async def test_advice_repository_serializes_correct_date_and_stable_fingerprint():
    connection = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"id": "visit-1", "customer_id": "customer-1",
                                       "interaction_at": datetime(2026, 9, 6, 16, tzinfo=UTC)}),
        fetchval=AsyncMock(return_value={"name": "客户"}),
    )
    actor = SimpleNamespace(role=SimpleNamespace(value="sales"), user_id="sales-1")
    first = await AdviceFactsRepository().load(connection, actor, "visit", "visit-1")
    second = await AdviceFactsRepository().load(connection, actor, "visit", "visit-1")
    assert first["facts"]["subject"]["visit_date"] == "2026-09-07"
    assert first["facts"]["subject"]["interaction_at"] == "2026-09-07T00:00:00+08:00"
    assert first["facts"]["business_timezone"] == "Asia/Shanghai"
    assert first["fingerprint"] == second["fingerprint"]


def test_model_omission_does_not_discard_current_recorders_source_deadline():
    source = candidate("刘志德于2026-09-16 12:00前发送清单；沈宁于2026-09-16 15:00前提供样本；"
                       "双方于2026-09-17 10:00进行复核。")
    deadline = deadline_context(source)
    assert deadline["status"] == "explicit" and deadline["owner_explicit"]
    assert deadline["due_at"] == "2026-09-16T12:00:00+08:00"
    assert "沈宁" not in deadline["source_quote"]
    draft, = follow_up_task_drafts({"ordered_items": []}, {"visit-1": source})
    assert draft.due_at == datetime(2026, 9, 16, 4, tzinfo=UTC)


def test_current_recorder_deadline_need_not_be_the_first_or_earliest_date():
    source = candidate("王新源于2026年9月11日15:00前提交测试清单；"
                       "刘志德于9月14日10:00组织评审；陈悦于9月11日17:00前补齐规则。")
    assert follow_up_due(source, None) == datetime(2026, 9, 14, 2, tzinfo=UTC)


@pytest.mark.parametrize("text", [
    "2026-09-16 16:00召开会议", "2026年9月16日16点召开会议", "9月16日16时00分召开会议",
    "2026-09-16T08:00:00Z召开会议", "2026-09-16T08:00:00+00:00召开会议",
])
def test_supported_source_timestamp_formats_preserve_business_instant(text):
    assert follow_up_due(candidate(text), None) == datetime(2026, 9, 16, 8, tzinfo=UTC)


def test_explicit_past_deadline_stays_overdue_instead_of_becoming_tomorrow():
    source = candidate("刘志德于2026-09-10 16:00前提交清单")
    assert follow_up_due(source, None, now=NOW) == datetime(2026, 9, 10, 8, tzinfo=UTC)


def test_date_only_source_uses_configured_clock_on_source_business_day():
    source = candidate("刘志德于2026年9月16日前提交清单")
    # Legacy default 09:00 UTC is 17:00 Shanghai; company policy is not changed.
    assert follow_up_due(source, None) == datetime(2026, 9, 16, 9, tzinfo=UTC)
    custom = {"definition": {"timezone": "Asia/Shanghai", "today_at": "15:30", "next_day_at": "10:00"}}
    assert follow_up_due(source, None, custom) == datetime(2026, 9, 16, 7, 30, tzinfo=UTC)
    assert deadline_context(source, custom)["precision"] == "date"


def test_deliverable_duration_is_not_an_additional_deadline():
    source = candidate("刘志德于2026-09-16前提交两周试点计划与5条验收标准；"
                       "周玮整理限制，林悦于2026-09-16前提供20条样本，双方当天15点复核。")
    assert follow_up_due(source, None) == datetime(2026, 9, 16, 9, tzinfo=UTC)


@pytest.mark.parametrize("text", [
    "2026-09-16 10:00交资料；2026-09-17 10:00复核",  # No actor binding.
    "刘志德于2026-09-16 10:00交资料；刘志德于2026-09-17 10:00复核",  # Two own deadlines.
    "下周一提交方案", "7日内联系客户", "2026-02-30 10:00提交清单", "9月16日当天15点复核",
    "2026-09-16下午3点复核", "2026-09-16 15:00 UTC复核",
])
def test_ambiguous_relative_or_invalid_source_dates_require_confirmation(text):
    with pytest.raises(ModelContractError, match="source_deadline_needs_confirmation"):
        follow_up_due(candidate(text), None, now=NOW)


@pytest.mark.parametrize("text", [
    "三天后提交方案", "刘志德于三个工作日后提交方案", "7个工作日内确认方案",
    "7 个工作日内确认方案", "七个工作日以内确认方案", "两周之后提交方案",
    "本月25号联系客户", "25号前发送清单", "九月二十五日安排复核", "明天下午三点复核",
    "半小时后联系客户",
])
def test_common_chinese_calendar_hints_never_fall_back_to_missing_deadline(text):
    source = candidate(text)
    assert deadline_context(source)["status"] == "needs_confirmation"
    with pytest.raises(FollowUpDeadlineNeedsConfirmation):
        require_resolvable_follow_up_deadlines({"follow_up_candidates": [source]})


@pytest.mark.parametrize("text", ["发送三天培训计划", "提交7个工作日的测试记录", "整理两周试点资料"])
def test_deliverable_duration_without_relative_marker_is_not_a_deadline(text):
    assert deadline_context(candidate(text))["status"] == "missing"


def test_inherited_year_comes_from_visit_business_date_not_generation_time():
    source = candidate("1月5日10:00复核", interaction_at=datetime(2025, 12, 31, 16, tzinfo=UTC))
    assert follow_up_due(source, None) == datetime(2026, 1, 5, 2, tzinfo=UTC)


def test_only_truly_missing_deadline_uses_existing_company_default():
    source = candidate("刘志德联系客户确认试点计划")
    assert follow_up_due(source, None, now=NOW) == datetime(2026, 9, 14, 2, tzinfo=UTC)
    with pytest.raises(ModelContractError, match="due_without_source"):
        follow_up_due(source, "2026-09-17T10:00:00+08:00", now=NOW)


@pytest.mark.parametrize("due,code", [
    ("2026-09-16T16:00:00Z", "due_conflicts_with_source"),
    ("2026-09-16T16:00:00", "due_offset_or_format"),
    ("invalid", "due_offset_or_format"),
    ("2026-09-14T02:00:00Z", "due_conflicts_with_source"),
])
def test_provider_date_error_is_a_contract_failure_not_silent_repair(due, code):
    source = candidate("2026-09-16 16:00召开会议")
    run = SimpleNamespace(mode="today_tasks")
    answer = {"ordered_items": [{"source_type": "visit_follow_up", "source_id": "visit-1",
                                 "priority": "medium", "due_at": due}]}
    with pytest.raises(InvalidAgentResult) as failure:
        validate_run_result(run, answer, {"follow_up_candidates": [source]})
    assert contract_failure_details(failure.value) == {"contract_code": "today_tasks." + code}


def test_omitted_ambiguous_candidate_also_fails_before_materialization():
    with pytest.raises(InvalidAgentResult, match="source_deadline_needs_confirmation"):
        validate_run_result(SimpleNamespace(mode="today_tasks"), {"ordered_items": []}, {
            "follow_up_candidates": [candidate("2026-09-16 10:00交资料；2026-09-17 10:00复核")],
        })


def test_existing_task_dates_are_not_model_inputs_to_materialization():
    facts = {"active_tasks": [{"source_type": "management_task", "source_id": "task-1",
                               "due_at": datetime(2026, 9, 1, 8, tzinfo=UTC)}]}
    base = {"source_type": "management_task", "source_id": "task-1"}
    validate_today_task_dates([{**base, "due_at": "2026-09-01T16:00:00+08:00"}], facts)
    validate_today_task_dates([base], facts)
    validate_today_task_dates([{**base, "due_at": "2026-09-16T10:00:00+08:00"}], facts)
    validate_today_task_dates([{**base, "due_at": {"forged": "tomorrow"}}], facts)
    assert facts["active_tasks"][0]["due_at"] == datetime(2026, 9, 1, 8, tzinfo=UTC)


def test_nameless_single_deadline_can_use_context_but_not_a_missing_anchor_year():
    assert follow_up_due(candidate("2026-09-16 10:00提交清单", recorder_name=""), None) == datetime(
        2026, 9, 16, 2, tzinfo=UTC)
    with pytest.raises(ModelContractError, match="source_deadline_needs_confirmation"):
        follow_up_due(candidate("9月16日10:00提交清单", interaction_at=None), None)


def test_datetime_normalization_keeps_db_native_type():
    result = localize_business_times({"interaction_at": datetime(2026, 9, 6, 16, tzinfo=UTC)})
    assert isinstance(result["interaction_at"], datetime)
    assert result["interaction_at"].tzinfo == BUSINESS_TZ


def test_preflight_error_locates_source_without_echoing_business_content():
    source_id = "66666666-6666-4666-8666-666666666666"
    with pytest.raises(FollowUpDeadlineNeedsConfirmation) as failure:
        require_resolvable_follow_up_deadlines({"follow_up_candidates": [
            candidate("下周联系未公开项目参与人", source_id=source_id),
        ]})
    assert source_id in str(failure.value)
    assert "本次未生成任何待办" in str(failure.value)
    assert "未公开项目" not in str(failure.value)


@pytest.mark.asyncio
async def test_known_date_ambiguity_stops_whole_batch_before_any_provider_or_materialization(monkeypatch):
    from unittest.mock import Mock

    from sales_backend.services.agent_run import handler as business
    from sales_backend.services.agent_run.models import RunInput
    from tests.test_today_tasks_platform import ACTOR, configured

    settings = configured()
    monkeypatch.setattr(business, "load_runtime_configuration", AsyncMock(return_value=SimpleNamespace(
        settings=settings, prompt_overrides={},
    )))
    provider = Mock(side_effect=AssertionError("no platform call allowed"))
    direct = Mock(side_effect=AssertionError("no direct call allowed"))
    persist = AsyncMock()
    monkeypatch.setattr(business, "InferenceService", provider)
    monkeypatch.setattr(business, "SenseAudioClient", direct)
    monkeypatch.setattr(business, "AgentRunStore", lambda *a, **kw: SimpleNamespace(persist_result=persist))
    handler = business.AgentRunHandler(None, settings)
    run = RunInput("run", "conversation", "整理待办", "today_tasks", None, ACTOR)
    handler._load_and_start = AsyncMock(return_value=run)
    handler.facts_loader.load = AsyncMock(return_value={"follow_up_candidates": [
        candidate("2026-09-16 10:00提交清单"), candidate("下周提交方案", source_id="visit-2"),
    ]})
    with pytest.raises(FollowUpDeadlineNeedsConfirmation):
        await handler.handle("run", ACTOR)
    handler.facts_loader.load.assert_awaited_once()
    provider.assert_not_called()
    direct.assert_not_called()
    persist.assert_not_awaited()
