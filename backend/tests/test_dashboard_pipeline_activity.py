"""Focused stage arithmetic, date boundaries and scoped read contracts."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.dashboard_pipeline import DashboardActivity, DashboardActivityPage, DashboardPipeline
from sales_backend.domain.route_permissions import route_permission
from sales_backend.domain.work_report import SHANGHAI
from sales_backend.repositories import dashboard_activity as activity_repository
from sales_backend.repositories.dashboard_scope import DashboardSelection
from sales_backend.services import dashboard_activity
from sales_backend.services.dashboard_metrics import quarter_window
from sales_backend.services.dashboard_pipeline import stage_summary

NOW = datetime(2026, 10, 25, 12, tzinfo=SHANGHAI)


@asynccontextmanager
async def savepoint():
    yield


def opportunity(**overrides):
    return dict(status="open", probability="70", currency="CNY", forecast_id=str(UUID(int=1)),
                collection_confidence="high", collection_amount="12.34", recognized_amount="56.78") | overrides


def stage(result, value):
    return next(row for row in result["stages"] if row["code"] == str(value))


def test_ppl_counts_independent_of_money_and_amount_qualification():
    rows = [opportunity(probability="30", collection_confidence="low"),
            opportunity(collection_amount=None, recognized_amount=None),
            opportunity(status="won", probability="100", collection_amount="0", recognized_amount="0"),
            opportunity(probability="10"), opportunity(status="lost"), opportunity(probability=None)]
    result = stage_summary(rows, scope_complete=True)
    assert result["eligible_count"] == sum(s["count"] for s in result["stages"]) == 3
    assert result["excluded_count"] == 2 and result["unknown_eligibility_count"] == 1
    low = stage(result, 30)["metrics"]
    assert low["forecast_collection"]["eligible_count"] == 0
    assert low["forecast_recognized"]["excluded_count"] == 1
    assert low["collection_ppl"]["value"] == "12.34"
    assert stage(result, 70)["metrics"]["forecast_recognized"]["value"] is None
    assert stage(result, 100)["metrics"]["forecast_recognized"]["value"] == "0.00"


def test_partial_money_and_currency_remain_unknown_not_zero():
    result = stage_summary([opportunity(), opportunity(currency="USD"), opportunity(collection_amount=None)],
                           scope_complete=True)
    value = stage(result, 70)["metrics"]["forecast_collection"]
    assert value["value"] is None and value["known_subtotal"] == "12.34"
    assert value["known_amount_count"] == 1 and value["missing_amount_count"] == 2
    assert set(value["missing_reasons"]) == {"currency_unavailable", "missing_forecast_amount"}
    empty = stage(stage_summary([], scope_complete=True), 70)["metrics"]["forecast_collection"]
    assert empty["value"] == "0.00" and empty["eligible_count"] == 0


def test_permission_partial_and_nonstandard_stage_are_not_complete():
    result = stage_summary([opportunity(probability="40"), opportunity()], scope_complete=False)
    assert result["eligible_count"] == 2 and stage(result, "unknown")["count"] == 1
    assert result["coverage"] == "partial"
    assert stage(result, 70)["metrics"]["forecast_recognized"]["value"] is None
    assert stage(result, 70)["metrics"]["forecast_recognized"]["known_subtotal"] == "56.78"


def test_pipeline_contract_and_existing_read_permission():
    result = DashboardPipeline.model_validate(dict(data_as_of=NOW.isoformat(),
        selection=dict(personal=False, member_id=None, team_groups=[]),
        scope=dict(kind="authorized", label="当前授权范围"), quarter=quarter_window(2026, 4, NOW),
        **stage_summary([opportunity()], scope_complete=True)))
    assert result.contract_version == "dashboard.pipeline.v1"
    assert all(route_permission("dashboard_metrics", name) == "dashboard.read"
               for name in ("pipeline", "activity", "activity_opportunities"))


def test_seven_shanghai_days_future_and_unreadable_history():
    window = dashboard_activity.activity_window(NOW)
    assert window["start"] == "2026-10-19T00:00:00+08:00"
    state = dashboard_activity.follow_up_state
    assert state(datetime(2026, 10, 18, 16, tzinfo=UTC), now=NOW)[0] == "recent"
    assert state(datetime(2026, 10, 18, 23, 59, tzinfo=SHANGHAI), now=NOW) == ("overdue", 7)
    assert state(NOW + timedelta(seconds=1), now=NOW)[0] == "unknown"
    assert state(None, now=NOW, readable=False)[0] == "unknown"
    assert state(NOW - timedelta(days=8), now=NOW, readable=False)[0] == "unknown"
    assert state(NOW - timedelta(days=1), now=NOW, readable=False)[0] == "recent"
    assert state(None, now=NOW) == ("never", None)


@pytest.mark.asyncio
async def test_summary_and_all_pages_share_cohort_never_and_visibility(monkeypatch):
    envelope = dict(data_as_of=NOW.isoformat(), selection=dict(personal=False, member_id=None, team_groups=[]),
                    scope=dict(kind="authorized", label="当前授权范围"), quarter=quarter_window(2026, 4, NOW))
    raw = []
    for index, (last, readable) in enumerate(((None, True), (NOW - timedelta(days=8), True),
                                            (NOW - timedelta(days=1), True), (None, False)), 1):
        raw.append(dict(id=str(UUID(int=index)), name=f"机会{index}", customer_id=str(UUID(int=100)),
                        customer_name="客户", owner_id=None, owner_name=None, team_id=None,
                        stage_code="qualified", probability="30", status="lost" if index == 1 else "open",
                        currency="CNY", collection_amount="0.00", recognized_amount="12.34",
                        last_follow_up_at=last, follow_up_complete=readable))
    material = (envelope, dashboard_activity.classify(raw, now=NOW), True, dashboard_activity.activity_window(NOW))
    monkeypatch.setattr(dashboard_activity, "material", AsyncMock(return_value=material))
    result = DashboardActivity.model_validate(await dashboard_activity.overview(None, None))
    assert (result.active_count, result.recent_count, result.overdue_count, result.unknown_count) == (4, 1, 2, 1)
    assert result.never_count == 1 and result.coverage == "partial"
    pages = [DashboardActivityPage.model_validate(await dashboard_activity.opportunities(None, None, limit=1, offset=i))
             for i in range(2)]
    assert all(page.total is None and page.known_total == 2 for page in pages)
    assert [page.items[0].follow_up_status for page in pages] == ["never", "overdue"]
    assert pages[0].items[0].days_since_follow_up is None and pages[0].items[0].status == "lost"
    assert pages[1].items[0].days_since_follow_up == 8
    assert pages[0].has_more and not pages[1].has_more
    assert pages[0].items[0].collection_amount == "0.00"


@pytest.mark.asyncio
async def test_activity_query_tenant_object_and_visit_intersection():
    actor = SimpleNamespace(user_id=str(UUID(int=1)), workspace_id=str(UUID(int=2)))
    object_id, customer_id = str(UUID(int=10)), str(UUID(int=100))
    connection = SimpleNamespace(fetch=AsyncMock(side_effect=[[
        dict(id=object_id, customer_id=customer_id)],
        [dict(id=object_id, last_follow_up_at=NOW, follow_up_complete=True)]]),
        fetchval=AsyncMock(return_value="dashboard.read"), execute=AsyncMock(), transaction=savepoint)
    selected = DashboardSelection(True, actor.user_id)
    await activity_repository.activity_rows(connection, actor, selected, year=2026, quarter=4, now=NOW)
    sql, *args = connection.fetch.call_args_list[0].args
    assert args[1:4] == [actor.user_id, actor.workspace_id, True]
    assert args[-2:] == [2026, 4]
    assert "f.collection_amount>0 OR f.recognized_amount>0" in sql and "o.status IN" not in sql
    assert "security.authorization_customer('customer.read',c.id)" in sql
    assert "security.authorization_opportunity('opportunity.read',o.id)" in sql
    dates, *args = connection.fetch.call_args_list[1].args
    assert args == [[object_id], [customer_id], NOW]
    assert "security.authorization_visit('visit.read',v.id)" in dates and "v.recorder_user_ref_id" not in dates
    assert "v.interaction_at<=$3::timestamptz" in dates and "UNION" in dates
    assert "follow_up_record" not in dates and "LIMIT" not in dates
    assert connection.execute.call_args_list[-1].args[-1] == "dashboard.read"


@pytest.mark.asyncio
async def test_empty_scope_never_reads_visits_and_failure_restores_feature():
    actor = SimpleNamespace(user_id=str(UUID(int=1)), workspace_id=str(UUID(int=2)))
    selected = DashboardSelection(True, actor.user_id)
    connection = SimpleNamespace(fetch=AsyncMock(return_value=[]), fetchval=AsyncMock(),
                                 execute=AsyncMock(), transaction=savepoint)
    assert await activity_repository.activity_rows(connection, actor, selected, year=2026, quarter=4, now=NOW) == []
    assert connection.fetch.await_count == 1 and not connection.execute.called
    connection.fetch = AsyncMock(side_effect=[
        [dict(id=str(UUID(int=10)), customer_id=str(UUID(int=100)))], RuntimeError("failed")])
    connection.fetchval.return_value = "original.feature"
    with pytest.raises(RuntimeError):
        await activity_repository.activity_rows(connection, actor, selected, year=2026, quarter=4, now=NOW)
    assert connection.execute.call_args_list[-1].args[-1] == "original.feature"
