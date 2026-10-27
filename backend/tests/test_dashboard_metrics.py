"""Focused monitor route/scope/arithmetic contracts; no business database I/O."""

from contextlib import asynccontextmanager
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
from sales_backend.observability import unhandled_exception_handler
from sales_backend.repositories import dashboard_scope
from sales_backend.services.dashboard_metrics import quarter_window

USER, MEMBER, TEAM, FOREIGN, WORKSPACE = (str(UUID(int=i)) for i in range(1, 6))
NOW = datetime(2026, 10, 25, 12, tzinfo=SHANGHAI)


def row(index=10, **changes):
    return dict(id=str(UUID(int=index)), version_no=1, name=f"机会{index}",
                customer_id=str(UUID(int=index + 100)), customer_name="授权客户",
                owner_id=USER, owner_name="授权姓名", team_id=TEAM, status="open", probability="70",
                amount="1000.00", currency="CNY", forecast_id=str(UUID(int=index + 200)),
                forecast_updated_at=NOW, collection_confidence="high", collection_amount="100.00",
                recognized_amount="200.00", **{}) | changes


def actual_row(index=400, **changes):
    return dict(id=str(UUID(int=index)), customer_id=str(UUID(int=110)), customer_name="授权客户",
                opportunity_id=str(UUID(int=10)), opportunity_name="已关闭机会", material_line_id=None,
                year=2026, quarter=4, kind="recognized", amount="90.00", raw_amount="90.00",
                tax_basis="exclusive", tax_unresolved=False, source="operations", status="confirmed",
                occurred_on=None, period_start=date(2026, 10, 1), period_end=date(2026, 12, 31),
                period_precision="quarter", owner_id=USER, owner_name="冻结负责人", team_id=TEAM,
                complete=True, missing_material_count=0, version_no=1) | changes


@pytest.fixture
def client(monkeypatch):
    state = SimpleNamespace(rows=[row()], actual_rows=[], company=True, assigned=False, source_allowed=True,
                            actual_allowed=True,
                            target_allowed=True, targets=True, sql=[], transactions=[], failure=None)
    actor = SimpleNamespace(user_id=USER, workspace_id=WORKSPACE)
    monkeypatch.setattr(dashboard_scope, "dashboard_team_groups", AsyncMock(return_value=[
        dict(code=f"team:{TEAM}", name="授权团队", team_id=TEAM)]))
    monkeypatch.setattr(dashboard_scope, "dashboard_members", AsyncMock(return_value=[
        dict(id=USER), dict(id=MEMBER)]))

    class Connection:
        async def fetchval(self, sql, *args):
            state.sql.append((sql, args))
            if "transaction_timestamp" in sql:
                return NOW
            if "authorization_current_grants" in sql:
                return state.assigned
            if "target_scope_read" in sql:
                return state.target_allowed
            if "authorization_subject($1,'department'" in sql:
                return state.company
            if "authorization_subject($1,$2" in sql:
                if args[0] == "actual.read":
                    return state.actual_allowed
                return state.company if args[:2] == ("dashboard.read", "department") else state.source_allowed
            raise AssertionError(sql)

        async def fetch(self, sql, *args):
            state.sql.append((sql, args))
            if "dashboard_metric_opportunities_v1" in sql:
                if state.failure:
                    raise state.failure
                return state.rows
            if "dashboard_metric_actuals_v1" in sql:
                return state.actual_rows
            if "FROM crm.sales_target" in sql:
                return [dict(id=str(UUID(int=300 + i)), version_no=1, kind=kind, value="1000.00", note=None)
                        for i, kind in enumerate(("collection", "collection_ppl", "forecast_recognized", "recognized"))
                        if state.targets]
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
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(api.router)
    app.dependency_overrides[get_database] = lambda: Database()
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
    return TestClient(app, raise_server_exceptions=False), state


def get(client, path="", **params):
    return client.get("/api/v1/dashboard/metrics" + path, params=dict(year=2026, quarter=4, **params))


def test_http_cards_and_details_share_quarter_and_money_before_paging(client):
    browser, state = client
    state.rows = [row(10, probability="29", collection_confidence="low"),
                  row(11, probability="30", collection_confidence="low"),
                  row(12, status="won", probability="70"), row(12, status="won", probability="70")]
    reply = get(browser)
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["contract_version"] == "dashboard.metrics.v1"
    assert data["values_basis"] == "current_authorized_quarter_snapshot"
    cards = {card["metric_code"]: card for card in data["metrics"]}
    assert cards["forecast_collection"]["value"] == "100.00"
    assert cards["collection_ppl"]["value"] == "200.00"
    assert cards["forecast_recognized"]["value"] == "200.00"
    assert cards["collection_ppl"]["rate"] == "0.20000000"
    assert cards["collection_ppl"]["gap"] == "800.00"
    assert all(card["day_change"] is None and card["baseline"]["status"] == "no_baseline"
               for card in cards.values())
    detail = get(browser, "/opportunities", metric_code="collection_ppl", limit=1, offset=1).json()
    assert detail["contract_version"] == "dashboard.metric_page.v1"
    assert detail["values_basis"] == data["values_basis"]
    assert detail["total"] == 2 and detail["known_total"] == 2 and not detail["has_more"]
    assert detail["items"][0]["id"] == row(12)["id"]
    assert detail["items"][0]["metric_contributions"]["collection_ppl"]["value"] == "100.00"
    assert all(tx == dict(readonly=True, isolation="repeatable_read") for tx in state.transactions)
    sql, args = next((sql, args) for sql, args in state.sql if "dashboard_metric_opportunities_v1" in sql)
    assert "LEFT JOIN crm.opportunity_forecast" in sql and "f.year=$6 AND f.quarter=$7" in sql
    assert "o.status IN ('open','won')" in sql and "c.workspace_id=o.workspace_id" in sql
    assert "security.authorization_opportunity($1::text,o.id)" in sql
    assert "security.authorization_opportunity('opportunity.read',o.id)" in sql
    assert "security.authorization_customer('customer.read',c.id)" in sql
    assert "THEN owner.display_name END" in sql
    assert args == ("dashboard.read", USER, WORKSPACE, False, None, 2026, 4)
    assert route_permission("dashboard_metrics", "metrics") == "dashboard.read"
    assert route_permission("dashboard_metrics", "opportunities") == "dashboard.read"
    assert route_permission("dashboard_metrics", "actuals") == "dashboard.read"


def test_missing_and_denied_sources_never_turn_into_zero_or_complete_total(client):
    browser, state = client
    state.rows += [row(11, collection_amount=None, forecast_id=None, collection_confidence=None)]
    state.source_allowed = False
    result = get(browser).json()
    card = next(card for card in result["metrics"] if card["metric_code"] == "collection_ppl")
    assert card["value"] is None and card["known_subtotal"] == "100.00"
    assert card["coverage"] == "partial" and card["missing_amount_count"] == 1
    assert "source_scope_not_fully_authorized" in card["missing_reasons"]
    detail = get(browser, "/opportunities", metric_code="forecast_collection").json()
    assert detail["total"] is None and detail["known_total"] == 2
    assert detail["items"][1]["metric_contributions"]["forecast_collection"]["eligible"] is None


@pytest.mark.parametrize("allowed,has_targets,status", [(True, False, "no_target"), (False, True, "permission_denied")])
def test_target_permission_independent_from_dashboard_and_no_personal_fallback(client, allowed, has_targets, status):
    browser, state = client
    state.target_allowed, state.targets = allowed, has_targets
    data = get(browser).json()
    assert all(card["target"]["status"] == status and card["target"]["value"] is None
               and card["rate"] is None and card["gap"] is None for card in data["metrics"])
    target_queries = [sql for sql, _ in state.sql if "FROM crm.sales_target" in sql]
    assert all("scope_type='department'" in sql for sql in target_queries)
    if not allowed:
        assert not target_queries


def test_restricted_authorized_scope_does_not_read_company_targets(client):
    browser, state = client
    state.company = False
    response = get(browser).json()
    assert response["selection"] == dict(personal=False, member_id=None, team_groups=[])
    assert all(card["target"]["status"] == "permission_denied" for card in response["metrics"])
    assert not any("FROM crm.sales_target" in sql for sql, _ in state.sql)


def test_existing_personal_and_team_selection_normalization(client):
    browser, state = client
    data = get(browser, personal=True).json()
    assert data["selection"] == dict(personal=True, member_id=USER, team_groups=[])
    data = get(browser, personal=True, member_id=MEMBER).json()
    assert data["selection"]["member_id"] == MEMBER
    data = get(browser, team_groups=[f"team:{TEAM}", f"team:{TEAM}"]).json()
    assert data["selection"]["team_groups"] == [f"team:{TEAM}"]
    assert data["scope"]["kind"] == "team"
    assert data["metrics"][0]["target"]["aggregation"] == "sum_distinct_subject_targets"


@pytest.mark.parametrize("params,status", [
    ({"personal": True, "member_id": FOREIGN}, 403),
    ({"team_groups": [f"team:{FOREIGN}"]}, 422),
    ({"personal": True, "team_groups": [f"team:{TEAM}"]}, 403),
    ({"member_id": MEMBER}, 422),
])
def test_outside_or_mismatched_scope_is_refused_before_fact_read(client, params, status):
    browser, state = client
    result = get(browser, **params)
    assert result.status_code == status, result.text
    assert result.json()["code"] == ("FORBIDDEN" if status == 403 else "VALIDATION_ERROR")
    assert not any("dashboard_metric_opportunities_v1" in sql for sql, _ in state.sql)


def test_invalid_quarter_and_metric_have_safe_http_contract(client):
    browser, state = client
    for path in ("?year=2026&quarter=5", "/opportunities?year=2026&quarter=4&metric_code=private_sql"):
        result = browser.get("/api/v1/dashboard/metrics" + path)
        assert result.status_code == 422 and result.json()["code"] == "VALIDATION_ERROR"
        assert "private_sql" not in result.text
    assert not state.transactions


def test_unknown_failure_is_not_reported_as_invalid_input(client):
    browser, state = client
    state.failure = RuntimeError("private SQL details")
    response = get(browser)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "private SQL" not in response.text


def test_monitor_progress_uses_today_not_selected_quarter_first_day():
    assert quarter_window(2026, 4, NOW)["time_progress"] == "0.27173913"
    assert quarter_window(2026, 3, NOW)["time_progress"] == "1.00000000"
    assert quarter_window(2027, 1, NOW)["time_progress"] == "0.00000000"


def test_actual_cards_and_leaf_details_share_facts_without_status_or_owner_reassignment(client):
    browser, state = client
    state.actual_rows = [actual_row(), actual_row(), actual_row(401, amount="10.00", raw_amount="10.00"),
                        actual_row(402, opportunity_id=None, opportunity_name=None, kind="collection",
                                   amount="20.00", raw_amount="20.00", tax_basis="not_applicable",
                                   source="entries", status="single_source", occurred_on=date(2026, 10, 2),
                                   period_precision="day")]
    data = get(browser).json()
    cards = {item["metric_code"]: item for item in data["metrics"]}
    assert len(cards) == 5
    assert cards["forecast_recognized"]["tax_basis"] == "tax_inclusive"
    assert cards["forecast_recognized"]["basis"] == "latest_sales_forecast"
    recognized = cards["actual_recognized_exclusive"]
    assert recognized["value"] == "100.00" and recognized["registered_fact_count"] == 2
    assert recognized["registered_period_count"] == 1
    assert recognized["target"]["kind"] == recognized["target_basis"] == "recognized"
    assert recognized["tax_basis"] == "tax_exclusive" and recognized["basis"] == "adopted_actuals"
    assert cards["actual_collection"]["value"] == "20.00"
    assert cards["actual_collection"]["target"]["kind"] == "collection"
    page = get(browser, "/actuals", metric_code="actual_recognized_exclusive", limit=1, offset=1).json()
    assert page["contract_version"] == "dashboard.actual_page.v1"
    assert page["basis"] == "adopted_actuals" and page["total"] == 2 and not page["has_more"]
    assert page["items"][0]["id"] == str(UUID(int=401))
    assert page["items"][0]["owner_id"] == USER
    collection = get(browser, "/actuals", metric_code="actual_collection").json()["items"][0]
    assert collection["opportunity_id"] is None and collection["period_precision"] == "day"
    assert collection["occurred_on"] == "2026-10-02"
    sql, args = next((sql, args) for sql, args in state.sql if "dashboard_metric_actuals_v1" in sql)
    assert "o.status" not in sql and "crm.opportunity_forecast" not in sql
    assert "SELECT a.owner_user_ref_id,a.owner_team_id" in sql
    assert "fact.owner_user_ref_id" in sql and "fact.owner_team_id" in sql
    assert "a.workspace_id=common.current_workspace_id() AND a.year=$6 AND a.quarter=$7" in sql
    assert "security.authorization_customer('actual.read',a.customer_id)" in sql
    assert "security.authorization_customer('customer.read',a.customer_id)" in sql
    assert "security.authorization_opportunity('actual.read',o.id)" in sql
    assert "security.authorization_opportunity('opportunity.read',o.id)" in sql
    assert "a.opportunity_id IS NULL OR" in sql
    assert args == ("dashboard.read", USER, WORKSPACE, False, None, 2026, 4)


@pytest.mark.parametrize("facts,expected,coverage", [
    ([], None, "unavailable"),
    ([actual_row(amount="0.00", raw_amount="0.00")], "0.00", "complete"),
    ([actual_row(amount=None, raw_amount=None, complete=False, status="withdrawn")], None, "unavailable"),
])
def test_actual_unregistered_zero_and_withdrawn_are_distinct(client, facts, expected, coverage):
    browser, state = client
    state.actual_rows = facts
    card = get(browser).json()["metrics"][3]
    assert card["value"] == expected and card["coverage"] == coverage
    if not facts:
        assert "actual_not_registered" in card["missing_reasons"] and card["known_subtotal"] is None
    if facts and facts[0]["status"] == "withdrawn":
        assert "actual_withdrawn" in card["missing_reasons"]


def test_partial_actuals_keep_subtotal_and_non_additive_period_counts(client):
    browser, state = client
    state.actual_rows = [actual_row(complete=False, status="partial", missing_material_count=2),
                        actual_row(401, complete=False, status="partial", amount="10.00", raw_amount="10.00",
                                   missing_material_count=2),
                        actual_row(402, opportunity_id=str(UUID(int=12)), amount=None, raw_amount="106.00",
                                   tax_basis="inclusive", tax_unresolved=True, complete=False,
                                   status="needs_confirmation")]
    card = get(browser).json()["metrics"][3]
    assert card["value"] is None and card["known_subtotal"] == "100.00" and card["coverage"] == "partial"
    assert card["registered_fact_count"] == 3 and card["registered_period_count"] == 2
    assert card["incomplete_period_count"] == 2 and card["missing_material_count"] == 2
    assert card["tax_unknown_period_count"] == card["conflict_period_count"] == 1
    assert set(card["missing_reasons"]) == {
        "actual_incomplete", "actual_source_conflict", "actual_tax_basis_unresolved",
    }


def test_actual_permission_does_not_turn_visible_subtotal_into_complete_scope(client):
    browser, state = client
    state.actual_rows = [actual_row()]
    state.actual_allowed = False
    card = get(browser).json()["metrics"][3]
    assert card["value"] is None and card["known_subtotal"] == "90.00"
    assert card["coverage"] == "partial" and "source_scope_not_fully_authorized" in card["missing_reasons"]
    page = get(browser, "/actuals", metric_code="actual_recognized_exclusive").json()
    assert page["total"] is None and page["known_total"] == 1
    for path, code in (("/actuals", "forecast_recognized"), ("/opportunities", "actual_collection")):
        result = get(browser, path, metric_code=code)
        assert result.status_code == 422 and result.json()["code"] == "VALIDATION_ERROR"
