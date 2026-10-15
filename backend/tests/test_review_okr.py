"""Narrow contract regressions for O/KR; database proofs live in the opt-in script."""

from datetime import UTC
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.review_okr import (
    KeyResultCreate,
    KeyResultUpdate,
    ManualProgress,
    ObjectiveCreate,
    ObjectiveUpdate,
    OkrMention,
)
from sales_backend.services.review_okr import save_objective


def objective(**changes):
    owner = uuid4()
    return {
        "title": "季度支持",
        "owner_user_id": owner,
        "scope_type": "person",
        "user_id": owner,
        "anchor_date": "2026-10-14",
        **changes,
    }


def test_collaborators_deduplicate_and_owner_is_unique():
    owner, collaborator = uuid4(), uuid4()
    value = ObjectiveCreate(**objective(owner_user_id=owner, collaborator_user_ids=[collaborator, owner, collaborator]))
    assert value.collaborator_user_ids == [collaborator]


@pytest.mark.parametrize(
    "changes",
    [
        {"scope_type": "team"},
        {"scope_type": "person", "team_id": uuid4()},
        {"department_code": "fde"},
        {"scope_type": "department"},
        {"title": "  "},
        {"workspace_id": str(uuid4())},
        {"amount": "1000"},
    ],
)
def test_invalid_scope_and_financial_or_tenant_injection_rejected(changes):
    with pytest.raises(ValidationError):
        ObjectiveCreate(**objective(**changes))


def test_department_does_not_require_synthetic_person():
    result = ObjectiveCreate(**objective(scope_type="department", user_id=None, department_code="fde"))
    assert result.user_id is None


def test_updates_require_read_version_and_freeze_identity():
    base = {"title": "changed", "owner_user_id": uuid4()}
    with pytest.raises(ValidationError):
        ObjectiveUpdate(**base)
    with pytest.raises(ValidationError):
        ObjectiveUpdate(**base, version_no=1, period_type="year")
    with pytest.raises(ValidationError):
        KeyResultUpdate(**base, version_no=1, metric_code="recognized")


def test_key_result_only_references_target():
    base = {"title": "储备", "owner_user_id": uuid4(), "objective_id": uuid4(), "metric_code": "collection_ppl"}
    assert KeyResultCreate(**base).target_id is None
    with pytest.raises(ValidationError):
        KeyResultCreate(**base, target_amount="1000")


def test_manual_zero_and_evidence_are_explicit():
    result = ManualProgress(version_no=1, value="0", evidence="核对后尚未完成", confirmed=True)
    assert result.value == Decimal("0")
    for changes in ({"confirmed": False}, {"evidence": " "}, {"value": "NaN"}, {"value": "1.001"}):
        with pytest.raises(ValidationError):
            ManualProgress(**{"version_no": 1, "value": "1", "evidence": "记录", "confirmed": True, **changes})


def test_mentions_do_not_accept_permission_or_owner_mutations():
    user = uuid4()
    assert OkrMention(version_no=2, recipient_user_ids=[user, user]).recipient_user_ids == [user]
    with pytest.raises(ValidationError):
        OkrMention(version_no=2, recipient_user_ids=[user], owner_user_id=user)


@pytest.mark.asyncio
async def test_objective_uses_same_natural_target_period():
    connection = AsyncMock()
    connection.fetchval.return_value = {"id": str(uuid4())}
    body = ObjectiveCreate(**objective())
    await save_objective(connection, body)
    payload = connection.fetchval.call_args.args[2]
    assert payload["period_start"] == "2026-10-01"
    assert payload["period_end"] == "2026-12-31"
    assert "anchor_date" not in payload and "amount" not in payload


def test_actual_projection_keeps_tax_conflict_and_date_precision_missing():
    from sales_backend.services.review_okr import actual_progress

    fact = {
        "id": "a",
        "opportunity_id": "o",
        "kind": "recognized",
        "year": 2026,
        "quarter": 4,
        "amount": "0",
        "complete": True,
        "period_start": "2026-10-01",
        "period_end": "2026-12-31",
        "occurred_on": None,
    }
    metric = {"code": "recognized", "definition_version": 1, "unit": "元"}
    value = actual_progress([fact], metric=metric, period={"start": "2026-10-01", "end": "2026-12-31"}, as_of="now")
    assert Decimal(value["value"]) == 0 and value["complete"] and value["tax_basis"] == "tax_exclusive"
    partial = actual_progress([fact], metric=metric, period={"start": "2026-10-01", "end": "2026-10-31"}, as_of="now")
    assert partial["value"] is None and "period_actual_not_allocated_to_selected_dates" in partial["missing_reasons"]
    unknown = actual_progress(
        [fact | {"amount": None, "complete": False, "tax_unresolved": True}],
        metric=metric,
        period={"start": "2026-10-01", "end": "2026-12-31"},
        as_of="now",
    )
    assert unknown["value"] is None and "tax_basis_unresolved" in unknown["missing_reasons"]
    empty = actual_progress([], metric=metric, period={"start": "2026-10-01", "end": "2026-12-31"}, as_of="now")
    assert empty["value"] is None and "no_actual_evidence" in empty["missing_reasons"]


@pytest.mark.asyncio
async def test_forecast_kr_reuses_card_values_and_caches_scope_period(monkeypatch):
    from copy import deepcopy
    from datetime import datetime

    from sales_backend.services import review_okr as service

    team, target = str(uuid4()), str(uuid4())
    common = {
        "progress_mode": "automatic",
        "scope": {"scope_type": "team", "team_id": team, "user_id": None},
        "period": {"type": "quarter", "start": "2026-10-01", "end": "2026-12-31"},
        "effective_target": {"id": target, "value": "1000.00"},
        "progress": {},
    }
    codes = ["forecast_collection", "collection_ppl", "forecast_recognized"]
    rows = [deepcopy(common) | {"metric": {"code": code, "definition_version": 1, "unit": "元"}} for code in codes]
    card_base = {
        "coverage": "partial",
        "value": None,
        "known_subtotal": "12.34",
        "missing_reasons": ["missing_forecast_amount"],
        "source_refs": [{"opportunity_id": "source"}],
        "target": {"id": target, "value": "1000.00"},
        "status": "incomplete",
        "gap": None,
        "rate": None,
    }
    projection = AsyncMock(
        return_value={"metrics": [card_base | {"metric_code": code} for code in codes], "data_as_of": "now"}
    )
    monkeypatch.setattr(service, "read_metric_projection", projection)
    monkeypatch.setattr(service.ReviewOkrRepository, "key_results", AsyncMock(return_value={"items": rows}))
    connection = AsyncMock()
    connection.fetchval.return_value = datetime(2026, 10, 14, tzinfo=UTC)
    result = await service.read_key_results(connection, object())
    assert projection.await_count == 1
    for row in result["items"]:
        assert row["progress"]["value"] is None and row["progress"]["known_subtotal"] == "12.34"
        assert row["progress"]["coverage"] == "partial" and row["progress"]["source_refs"] == card_base["source_refs"]
        assert "target" not in row["progress"]


@pytest.mark.asyncio
async def test_forecast_scope_denial_is_missing_not_zero(monkeypatch):
    from datetime import datetime

    from sales_backend.services import review_okr as service

    row = {
        "progress_mode": "automatic",
        "scope": {"scope_type": "person", "user_id": str(uuid4()), "team_id": None},
        "period": {"type": "quarter", "start": "2026-10-01", "end": "2026-12-31"},
        "metric": {"code": "forecast_collection", "definition_version": 1, "unit": "元"},
    }
    monkeypatch.setattr(service, "read_metric_projection", AsyncMock(side_effect=PermissionError("scope denied")))
    monkeypatch.setattr(service.ReviewOkrRepository, "key_results", AsyncMock(return_value={"items": [row]}))
    connection = AsyncMock()
    connection.fetchval.return_value = datetime(2026, 10, 14, tzinfo=UTC)
    result = await service.read_key_results(connection, object())
    assert result["items"][0]["progress"]["value"] is None
    assert result["items"][0]["progress"]["reason"] == "forecast_source_permission_denied"


def count_projection(code, facts, *, complete=True, scope=None, as_of="2026-10-14T12:00:00+08:00", **period):
    from sales_backend.services.review_okr import count_progress

    return count_progress(
        {"items": facts, "scope_complete": complete},
        metric={"code": code, "name": "冻结计数定义", "definition": "Data 定义", "definition_version": 2, "unit": "个"},
        scope=scope or {"scope_type": "team", "team_id": "team"},
        period={"start": "2026-10-01", "end": "2026-10-31", **period},
        as_of=as_of,
    )


def count_opportunity(identity="opportunity", **changes):
    return {
        "id": identity,
        "version_no": 1,
        "status": "lost",
        "user_id": "original",
        "team_id": "team",
        "creation_date": {"historical": True, "source_created": "2026-10-01", "created_at": "2026-10-14"},
        "native_creation": False,
        **changes,
    }


def count_visit(identity="visit", **changes):
    return {
        "id": identity,
        "version_no": 1,
        "status": "confirmed",
        "user_id": "recorder",
        "team_id": "team",
        "interaction_at": "2026-10-01T00:00:00+08:00",
        **changes,
    }


def test_count_current_owner_transfer_lost_and_history_date_priority():
    fact = count_opportunity()
    before = count_projection("opportunity_count", [fact], scope={"scope_type": "person", "user_id": "original"})
    moved = fact | {"user_id": "next", "team_id": "next_team", "version_no": 2}
    after = count_projection("opportunity_count", [moved], scope={"scope_type": "person", "user_id": "original"})
    new = count_projection("opportunity_count", [moved], scope={"scope_type": "team", "team_id": "next_team"})
    assert before["value"] == new["value"] == 1 and after["value"] == 0
    assert before["attribution_basis"] == "current_opportunity_owner_and_team"
    unknown = count_opportunity(
        "invalid",
        creation_date={
            "historical": True,
            "source_created": "invalid",
            "legacy_created": "2026-10-02",
            "created_at": "2026-10-03",
        },
    )
    fallback = count_opportunity(
        "fallback", creation_date={"historical": True, "source_created": " ", "legacy_created": "2026/10/02"}
    )
    unproven = count_opportunity("unproven", creation_date={"historical": False, "created_at": "2026-10-03"})
    result = count_projection("opportunity_count", [fact, fact, unknown, fallback, unproven])
    assert result["value"] is None and result["known_subtotal"] == 2 and result["unknown_date_count"] == 2
    assert {"unknown_creation_date", "unproven_import_creation_provenance"} <= set(result["missing_reasons"])
    # Import or update day cannot pull a source-created opportunity into a new period.
    assert count_projection("opportunity_count", [fact], start="2026-10-02")["value"] == 0


def test_count_visit_recorder_dedup_status_and_shanghai_as_of():
    visit = count_visit()
    facts = [
        visit,
        visit,
        count_visit("archived", status="archived"),
        count_visit("draft", status="draft"),
        count_visit("pending", status="pending_confirm"),
        count_visit("prior", interaction_at="2026-09-30T15:59:59Z"),
        count_visit("boundary", interaction_at="2026-09-30T16:00:00Z"),
        count_visit("future", interaction_at="2026-10-14T12:00:01+08:00"),
        count_visit("other-recorder", user_id="participant"),
    ]
    team = count_projection("visit_count", facts)
    person = count_projection("visit_count", facts, scope={"scope_type": "person", "user_id": "recorder"})
    assert team["value"] == 4 and person["value"] == 3
    assert team["display_name"] == "团队记录的正式跟进数"
    assert person["attribution_basis"] == "recorder_user_ref_id"
    assert len(person["source_refs"]) == 3


def test_count_missing_dates_attribution_permissions_and_empty_are_distinct():
    missing = count_visit("unknown-date", interaction_at=None)
    unknown_team = count_visit("unknown-team", team_id=None)
    result = count_projection("visit_count", [count_visit(), missing, unknown_team])
    assert result["coverage"] == "partial" and result["value"] is None and result["known_subtotal"] == 1
    assert result["unknown_date_count"] == result["unknown_attribution_count"] == 1
    assert set(result["missing_reasons"]) == {"unknown_interaction_at", "unknown_recorded_team_where_needed"}
    empty = count_projection("visit_count", [])
    denied = count_projection("visit_count", [], complete=False)
    partial = count_projection("visit_count", [count_visit()], complete=False)
    assert empty["coverage"] == "complete" and empty["value"] == 0
    assert denied["coverage"] == "unavailable" and denied["value"] is None
    assert partial["coverage"] == "partial" and partial["value"] is None and partial["known_subtotal"] == 1
    assert partial["authorization_coverage"] == "authorized_subset"


@pytest.mark.asyncio
async def test_actual_requires_full_finance_customer_opportunity_subject_before_fetch():
    from sales_backend.repositories.review_okr import ReviewOkrRepository

    connection = AsyncMock()
    connection.fetchval.return_value = False
    result = await ReviewOkrRepository().actual_facts(
        connection, scope={"scope_type": "team", "team_id": uuid4()}, as_of="2026-10-14"
    )
    assert result is None and not connection.fetch.called
    query = connection.fetchval.call_args.args[0]
    assert all(code in query for code in ("actual.read", "customer.read", "opportunity.read"))


def test_count_history_timestamp_future_intraday_and_unreadable_provenance():
    future = count_opportunity(creation_date={"historical": True, "source_created": "2026-10-14T04:00:01Z"})
    assert count_projection("opportunity_count", [future])["value"] == 0
    now = count_opportunity(creation_date={"historical": True, "source_created": "2026-10-14T04:00:00Z"})
    assert count_projection("opportunity_count", [now])["value"] == 1
    denied = count_opportunity(
        creation_date={"historical": False, "created_at": "2026-10-01"}, creation_history_readable=False
    )
    result = count_projection("opportunity_count", [denied])
    assert result["value"] is None and result["missing_reasons"] == ["creation_provenance_permission_denied"]
