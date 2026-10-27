"""Focused ranking route, arithmetic and isolation regression; synthetic only."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.testclient import TestClient

from sales_backend.api import dashboard_metrics as api
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.route_permissions import route_permission
from sales_backend.domain.work_report import SHANGHAI
from sales_backend.error_contract import http_exception_handler
from sales_backend.main import business_permission_handler, request_validation_error_handler
from sales_backend.services.dashboard_performance import METRICS, material_contribution

USER, OTHER, TEAM, TEAM2, WORKSPACE, OPPORTUNITY = [str(UUID(int=i)) for i in range(1, 7)]
NOW = datetime(2026, 10, 25, 12, tzinfo=SHANGHAI)


def forecast(ident=OPPORTUNITY, **changes):
    return dict(id=ident, version_no=1, name="机会", customer_id=str(UUID(int=20)), customer_name="客户",
                owner_id=USER, team_id=TEAM, status="open", probability="70", currency="CNY",
                forecast_id=str(UUID(int=30)), collection_confidence="high", collection_amount="100.00",
                recognized_amount="999999.00") | changes


def line(**changes):
    return dict(id=str(UUID(int=40)), opportunity_id=OPPORTUNITY, is_active=True, line_no=1, usage="self_use",
                quantity="1", unit_price="113", expected_delivery_on=date(2026, 10, 20),
                catalog_snapshot=dict(recognition_rule="delivery", rule_parameters={},
                                      tax_rate="0.13", currency="CNY")) | changes


def actual(**changes):
    return dict(id=str(UUID(int=50)), customer_id=str(UUID(int=20)), opportunity_id=OPPORTUNITY,
                customer_name="客户", opportunity_name="机会", year=2026, quarter=4, kind="recognized",
                amount="88.00", raw_amount="88.00", owner_id=OTHER, team_id=TEAM2, complete=True,
                material_line_id=None, tax_unresolved=False, status="confirmed", version_no=1,
                missing_material_count=0, attribution_status="frozen", _attribution_excluded=False) | changes


@pytest.fixture
def client():
    subjects = [dict(kind=kind, id=ident, name=ident, source_complete=True, target_allowed=True, participates=True,
                     team_id=TEAM if ident == USER else TEAM2 if ident == OTHER else None, team_name=None)
                for kind, ident in (("team", TEAM), ("team", TEAM2), ("person", USER), ("person", OTHER))]
    state = SimpleNamespace(subjects=subjects, forecasts=[forecast()], actuals=[], allowed=True, targets={},
                            lines=[line()], relation="direct", rank_denied=set(), queries=[], transactions=[])

    class Connection:
        async def fetchval(self, sql, *args):
            state.queries.append((sql, args))
            if "authorization_has('dashboard.ranking')" in sql:
                return state.allowed
            if "transaction_timestamp()" in sql:
                return NOW
            raise AssertionError(sql)

        async def fetch(self, sql, *args):
            state.queries.append((sql, args))
            if "dashboard_performance_cohort_v1" in sql:
                return deepcopy(state.subjects)
            if "dashboard_performance_targets_v1" in sql:
                return [dict(scope=s["scope"], subject_id=s["id"], id=str(UUID(int=60)), kind=args[2],
                             version_no=1, note=None, value=state.targets[(s["scope"], s["id"])])
                        for s in args[0] if (s["scope"], s["id"]) in state.targets]
            if "dashboard_metric_opportunities_v1" in sql:
                return deepcopy(state.forecasts)
            if "dashboard_metric_actuals_v1" in sql:
                return deepcopy(state.actuals)
            if "dashboard_performance_ranked_facts_v1" in sql:
                return [dict(idx=row["idx"]) for row in args[0] if row["opportunity_id"] not in state.rank_denied]
            if "dashboard_performance_material_parents_v1" in sql:
                return [dict(id=ident, trade_relation=state.relation) for ident in args[0]]
            if "dashboard_performance_material_rows_v1" in sql:
                return deepcopy(state.lines)
            raise AssertionError(sql)

    class Database:
        @asynccontextmanager
        async def transaction(self, actor, **kwargs):
            state.transactions.append(kwargs)
            yield Connection()

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(PermissionError, business_permission_handler)
    app.include_router(api.router)
    app.dependency_overrides[get_database] = lambda: Database()
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(
        actor=SimpleNamespace(user_id=USER, workspace_id=WORKSPACE))
    return TestClient(app), state


def get(browser, metric="forecast_collection", **params):
    return browser.get("/api/v1/dashboard/metrics/rankings",
                       params=dict(year=2026, quarter=4, metric_code=metric, **params))


def test_same_snapshot_both_boards_targets_sorting_and_authority(client):
    browser, state = client
    state.forecasts += [forecast(str(UUID(int=7)), owner_id=OTHER, team_id=TEAM2, collection_amount="200.00")]
    state.targets = {("team", TEAM): "100.00", ("team", TEAM2): "1000.00"}
    response = get(browser, member_id=USER)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["department_sort_basis"] == "rate"
    assert [r["id"] for r in result["departments"]] == [TEAM, TEAM2]
    assert [r["id"] for r in result["people"]] == [OTHER, USER]
    assert result["departments"][0]["rate"] == "1.00000000"
    assert result["people"][0]["value"] == "200.00"
    assert state.transactions == [dict(readonly=True, isolation="repeatable_read")]
    assert route_permission("dashboard_metrics", "performance_rankings") == "dashboard.ranking"
    cohort_sql = next(sql for sql, _ in state.queries if "dashboard_performance_cohort_v1" in sql)
    for permission in ("dashboard.ranking", "dashboard.read", "customer.read", "opportunity.read", "actual.read"):
        assert permission in cohort_sql
    assert "child.parent_team_id=t.id" in cohort_sql
    facts_sql = next(sql for sql, _ in state.queries if "dashboard_metric_opportunities_v1" in sql)
    assert "common.current_workspace_id()" in facts_sql and "'customer.read'" in facts_sql
    rank_sql = next(sql for sql, _ in state.queries if "dashboard_performance_ranked_facts_v1" in sql)
    assert "authorization_opportunity('dashboard.ranking'" in rank_sql


def test_denied_rank_is_403_and_denied_target_not_mislabeled(client):
    browser, state = client
    state.allowed = False
    assert get(browser).status_code == 403
    assert not any("cohort_v1" in sql for sql, _ in state.queries)
    state.allowed = True
    state.subjects[0]["target_allowed"] = False
    state.targets[("team", TEAM)] = "9999.00"
    result = get(browser).json()
    row = next(r for r in result["departments"] if r["id"] == TEAM)
    assert row["target"]["status"] == "permission_denied" and row["target"]["value"] is None
    assert row["value"] == "100.00" and row["rate"] is None
    assert result["department_sort_basis"] == "amount" and result["department_sort_reason"] == "no_comparable_targets"


def test_unknown_amount_not_zero_and_stable_ties(client):
    browser, state = client
    state.forecasts += [forecast(str(UUID(int=7)), owner_id=OTHER, team_id=TEAM2)]
    result = get(browser).json()
    assert [r["rank"] for r in result["people"]] == [1, 1]
    assert [r["id"] for r in result["people"]] == sorted([USER, OTHER])
    state.forecasts[1]["collection_confidence"] = None
    result = get(browser).json()
    unknown = next(r for r in result["people"] if r["id"] == OTHER)
    assert unknown["value"] is None and unknown["known_subtotal"] is None and unknown["rank"] is None
    assert unknown["unknown_eligibility_count"] == 1
    assert result["people"][0]["provisional"]
    state.rank_denied.add(OPPORTUNITY)
    denied = next(r for r in get(browser).json()["people"] if r["id"] == USER)
    assert denied["value"] is None and denied["rank"] is None
    assert "source_scope_not_fully_authorized" in denied["missing_reasons"]


def test_actual_uses_effective_owner_not_current_member_and_does_not_mix_kinds(client):
    browser, state = client
    state.actuals = [actual(), actual(id=str(UUID(int=51)), amount=None, raw_amount=None, owner_id=None,
                                    team_id=None, attribution_status="unresolved", complete=False),
                     actual(id=str(UUID(int=52)), kind="collection", amount="60.00", raw_amount="60.00")]
    recognized = get(browser, "actual_recognized_exclusive").json()
    assert next(r for r in recognized["people"] if r["id"] == OTHER)["value"] == "88.00"
    assert next(r for r in recognized["people"] if r["id"] == USER)["value"] is None
    assert recognized["attribution_unknown_period_count"] == 1
    assert recognized["departments_coverage"] != "complete"
    assert all(r["provisional"] for r in recognized["departments"] + recognized["people"])
    collection = get(browser, "actual_collection").json()
    assert collection["attribution_unknown_period_count"] == 0
    assert next(r for r in collection["departments"] if r["id"] == TEAM2)["value"] == "60.00"
    state.actuals.append(actual(id=str(UUID(int=53)), owner_id=str(UUID(int=99))))
    assert get(browser, "actual_recognized_exclusive").json()["people_excluded_fact_count"] == 1


def test_net_reference_not_hand_entered_forecast_and_separate_target(client):
    browser, state = client
    result = get(browser, "forecast_recognized_exclusive").json()
    row = next(r for r in result["people"] if r["id"] == USER)
    assert result["basis"] == "material_reference" and row["value"] == "100.00"
    assert row["target"]["status"] == "not_configured" and row["rate"] is None
    assert not any("dashboard_performance_targets_v1" in sql for sql, _ in state.queries)
    state.forecasts[0]["probability"] = "30"
    assert next(r for r in get(browser, "forecast_recognized_exclusive").json()["people"]
                if r["id"] == USER)["value"] == "0.00"
    assert next(r for r in get(browser, "recognized_pipeline_exclusive").json()["people"]
                if r["id"] == USER)["value"] == "100.00"
    state.lines = [line(unit_price="0.05"), line(id=str(UUID(int=41)), unit_price="0.05")]
    assert next(r for r in get(browser, "recognized_pipeline_exclusive").json()["people"]
                if r["id"] == USER)["value"] == "0.08"  # round each line, not total / tax


@pytest.mark.parametrize("changes,expected", [
    ({"expected_delivery_on": None}, "material_schedule_missing"),
    ({"catalog_snapshot": {"recognition_rule": "manual", "tax_rate": "0.13", "currency": "CNY"}},
     "material_manual_input_required"),
    ({"catalog_snapshot": {"recognition_rule": "delivery", "currency": "CNY"}}, "material_tax_rate_missing"),
    ({"catalog_snapshot": []}, "material_reference_invalid"),
    ({"catalog_snapshot": {"recognition_rule": "delivery", "rule_parameters": ["invalid"],
                           "tax_rate": "0.13", "currency": "CNY"}}, "material_reference_invalid"),
])
def test_incomplete_or_malformed_material_does_not_crash_ranking(client, changes, expected):
    browser, state = client
    state.lines = [line(**changes)]
    result = get(browser, "forecast_recognized_exclusive").json()
    row = next(r for r in result["people"] if r["id"] == USER)
    assert row["value"] is None and row["rank"] is None and row["missing_amount_count"] == 1
    assert expected in row["missing_reasons"]


def test_excluded_resale_and_no_material_are_distinct():
    assert material_contribution(forecast(), [line(usage="resale")], "distributor",
                                 year=2026, quarter=4, threshold=70) == (False, None, None)
    assert material_contribution(forecast(), [], "direct", year=2026, quarter=4, threshold=70) == (
        True, None, "material_lines_missing")


@pytest.mark.parametrize("metric", METRICS)
def test_role_nonparticipant_keeps_department_amount_without_personal_gap_or_rank(client, metric):
    browser, state = client
    extra = str(UUID(int=71))
    state.forecasts.append(forecast(extra, owner_id=OTHER, team_id=TEAM2, collection_amount="200.00"))
    state.lines.append(line(id=str(UUID(int=72)), opportunity_id=extra, unit_price="226"))
    state.actuals = [actual(id=str(UUID(int=80 + n)), owner_id=owner, team_id=team, kind=kind,
                           amount=value, raw_amount=value)
                    for n, (owner, team, kind, value) in enumerate([
                        (USER, TEAM, "recognized", "100.00"), (OTHER, TEAM2, "recognized", "200.00"),
                        (USER, TEAM, "collection", "100.00"), (OTHER, TEAM2, "collection", "200.00")])]
    state.targets = {(s["kind"], s["id"]): "500.00" for s in state.subjects}
    before = get(browser, metric).json()
    assert before["people"][0]["id"] == OTHER

    next(s for s in state.subjects if s["kind"] == "person" and s["id"] == OTHER)["participates"] = False
    state.queries.clear()
    after = get(browser, metric).json()
    assert after["departments"] == before["departments"]
    assert after["departments_coverage"] == before["departments_coverage"] == "complete"
    assert [(p["id"], p["rank"], p["provisional"]) for p in after["people"]] == [(USER, 1, False)]
    assert after["cohort"] == dict(scope="current_authorized_ranking_scope", departments_count=2, people_count=1)
    assert after["people_coverage"] == "complete"
    assert after["people_excluded_fact_count"] == after["attribution_unknown_period_count"] == 0
    for sql, args in state.queries:
        if "dashboard_performance_targets_v1" in sql:
            assert dict(scope="person", id=OTHER) not in args[0]
