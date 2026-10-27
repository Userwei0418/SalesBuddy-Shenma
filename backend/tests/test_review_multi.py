"""Multi-subject facts are unioned before ratios, coverage and pagination."""

from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.review_multi import ReviewOverviewV2, ReviewPageV2, ReviewSelectionV2
from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.contracts.work_report_overview import OverviewResolvedScope
from sales_backend.domain.review_changes import history_coverage
from sales_backend.services import review_multi as service

A, B, M, N, OPP, T = [str(UUID(int=i)) for i in range(1, 7)]
NOW = datetime(2026, 10, 14, 4, tzinfo=UTC)
QUARTER = dict(year=2026, quarter=4, time_progress="0.1")


def row(ident=OPP, value="100"):
    return dict(id=ident, version_no=1, name="Opportunity " + ident, customer_id=M, customer_name="Customer",
                owner_id=M, owner_name="Owner", team_id=A, status="open", stage_code="proposal",
                probability="70", amount="1000", currency="CNY", updated_at=NOW, forecast_id=T,
                forecast_updated_at=NOW, collection_amount=value, recognized_amount=value,
                collection_confidence="high", last_follow_up_at=None, business_created_on=date(2026, 10, 1),
                high_risk=False, updated_in_selected_day=None, stale_days=13, stale_basis="business_created_on")


def subject(ident, amount, *, allowed=True):
    targets = {kind: dict(id=str(UUID(int=int(UUID(ident)) * 10 + index)), version_no=1, value=str(amount))
               for index, kind in enumerate(("collection", "collection_ppl", "forecast_recognized"))}
    return dict(scope_type="team", subject_id=ident, allowed=allowed, targets=targets)


def material():
    return ({code: [] for code in service.core.ACTIVITIES},
            {code: dict(value=0, known_count=0, coverage="complete", missing_reasons=[])
             for code in service.core.ACTIVITIES}, [])


def event(ident, before, after, at="2026-10-14T01:00:00+00:00"):
    return dict(event_id=ident, id=ident, occurred_at=at, actor_status="not_recorded",
                changes=[dict(field="sales_channel", before=before, after=after)], source_refs=[dict(id=ident)])


def test_money_union_uses_independent_400_denominator_not_average_ratio():
    rows = service.union_items([[row()], [row(), row(B, "0")]])
    cards = service.union_metric_cards(rows, [subject(A, 100), subject(B, 300)], quarter=QUARTER, complete=True)
    assert len(rows) == 2 and len(cards) == 3
    for card in cards:
        assert card["value"] == "100.00" and card["target"]["value"] == "400.00"
        assert card["rate"] == "0.25000000" and card["gap"] == "300.00"
        assert len(card["target"]["components"]) == 2


@pytest.mark.parametrize("condition", ["missing", "denied", "partial_source"])
def test_unknown_targets_or_source_scope_never_produce_comparable_total(condition):
    subjects = [subject(A, 100), subject(B, 300)]
    if condition == "missing":
        subjects[1]["targets"] = {}
    if condition == "denied":
        subjects[1]["allowed"] = False
    cards = service.union_metric_cards([row()], subjects, quarter=QUARTER, complete=condition != "partial_source")
    for card in cards:
        if condition == "partial_source":
            assert card["value"] is None and card["known_subtotal"] == "100.00"
            assert card["target"]["value"] == "400.00"
            assert card["rate"] == "0.25000000" and card["gap"] == "300.00"
            assert card["rate_basis"] == "known_subtotals"
        else:
            assert card["target"]["value"] is None and card["target"]["known_subtotal"] == "100.00"
            if condition == "missing":
                assert card["rate"] == "1.00000000" and card["gap"] == "0.00"
                assert card["rate_basis"] == "known_effective_targets"
            else:
                assert card["rate"] is None and card["gap"] is None
        if condition == "denied":
            component = card["target"]["components"][1]
            assert component["status"] == "permission_denied"
            assert component["target_id"] is None and component["value"] is None and component["version_no"] is None


def test_activity_union_counts_deterministic_updates_even_when_detail_history_is_partial():
    first, second = material(), material()
    forward, backward = event("change:1", "direct", "partner"), event("change:2", "partner", "direct")
    for value, events, member in ((first, [forward], M), (second, [forward, backward], N)):
        value[0]["updated_opportunities"] = [dict(id=OPP, opportunity_id=OPP, name="Changed", events=events,
                                                 event_count=len(events), occurred_at=NOW)]
        value[1]["updated_opportunities"].update(value=1, known_count=1, coverage="complete",
                                                missing_reasons=["history_field_coverage_unverified"],
                                                history_coverage=history_coverage(has_records=True))
        value[0]["pending_tasks"] = [dict(id=T, task_id=T, name="Shared", member_ids=[member],
                                         owners=[dict(member_id=member, name=member)], due_at=NOW)]
        value[0]["formal_visits"] = [dict(id=OPP, member_ids=[member], occurred_at=NOW)]
    groups, counts, _ = service.union_activities([first, second])
    changed = groups["updated_opportunities"][0]
    assert changed["event_count"] == 2 and [e["event_id"] for e in changed["events"]] == ["change:1", "change:2"]
    assert counts["updated_opportunities"]["value"] == 1
    assert counts["updated_opportunities"]["known_count"] == 1
    assert counts["updated_opportunities"]["history_coverage"]["status"] == "partial"
    assert counts["pending_tasks"]["value"] == 1 and groups["pending_tasks"][0]["member_ids"] == [M, N]
    assert len(groups["pending_tasks"][0]["owners"]) == 2
    assert counts["formal_visits"]["value"] == 1


def test_conflicting_same_event_or_snapshot_is_refused_instead_of_arbitrary_choice():
    with pytest.raises(ValueError):
        service.union_items([[row()], [row(value="200")]])
    with pytest.raises(ValueError):
        service.union_items([[dict(id=OPP, events=[event("same", "A", "B")])],
                             [dict(id=OPP, events=[event("same", "B", "A")])]])


@pytest.fixture
def environment(monkeypatch):
    selection = ReviewSelectionV2(scope="team", team_ids=[B, A], anchor_date=date(2026, 10, 14))
    scope = ReviewResolvedScopeV2(kind="team", label="A、B", subject_ids=[A, B],
        subjects=[dict(scope_type="team", id=A, label="A"), dict(scope_type="team", id=B, label="B")],
        resolved_member_ids=[M, N], scope_key="review-scope:v2:" + "a" * 64, scope_revision="b" * 64)
    resolved = SimpleNamespace(scope=scope, data_as_of=NOW, legacy_scopes=tuple(
        OverviewResolvedScope(kind="team", team_id=ident, label=ident, member_ids=[M, N]) for ident in (A, B)))
    resolver = AsyncMock(return_value=resolved)
    monkeypatch.setattr(service, "resolve_review_scope", resolver)
    monkeypatch.setattr("sales_backend.services.review_history.repository.metric_baseline",
                        AsyncMock(return_value={"status": "no_baseline",
                                                "reason_code": "previous_day_snapshot_missing"}))
    monkeypatch.setattr(service.AuthorizationRepository, "effective", AsyncMock(
        return_value=SimpleNamespace(allows=lambda permission: True)))
    monkeypatch.setattr(service.core, "metric_scope_complete", AsyncMock(return_value=True))
    monkeypatch.setattr(service.core, "source_scope_allowed", AsyncMock(return_value=True))

    async def rows(connection, actor, single, **kwargs):
        return [row()] if str(single.team_id) == A else [row(), row(B, "0")]

    async def targets(connection, single, **kwargs):
        ident = str(single.team_id)
        return subject(ident, 100 if ident == A else 300)["targets"], True

    async def mark(connection, envelope, rows, filters, now):
        filters["not_updated"] = dict(available=False, reason="unverified", history_coverage=history_coverage(
            has_records=True))

    monkeypatch.setattr(service.repository, "opportunity_rows", rows)
    monkeypatch.setattr(service.repository, "targets", targets)
    monkeypatch.setattr(service.core, "mark_day_updates", mark)
    monkeypatch.setattr(service.core, "activity_material", AsyncMock(side_effect=lambda *args: material()))
    return selection, resolver


@pytest.mark.asyncio
async def test_full_projection_and_pages_echo_same_scope_and_paginate_after_union(environment):
    selection, resolver = environment
    overview = ReviewOverviewV2.model_validate(await service.read_overview(None, None, selection))
    page = ReviewPageV2.model_validate(await service.read_opportunities(
        None, None, selection, metric_code="collection_ppl", limit=1, offset=1))
    assert overview.scope == page.scope and page.known_total == 2 and len(page.items) == 1
    assert not page.has_more and page.total == 2
    assert overview.metrics[0].rate == "0.25000000" and len(overview.metrics) == 3
    assert overview.generation == page.generation == service.GENERATION
    assert overview.pool_counts["not_updated"] is None
    unavailable = await service.read_opportunities(None, None, selection, filter="not_updated")
    assert unavailable["items"] == [] and unavailable["total"] is None and unavailable["coverage"] == "unavailable"
    assert not unavailable["available"] and resolver.await_count == 3


@pytest.mark.asyncio
async def test_filters_and_search_apply_to_union_and_source_denial_propagates(environment, monkeypatch):
    selection, _ = environment
    result = await service.read_opportunities(None, None, selection, q=B, sort="updated_desc")
    assert result["total"] == 1 and result["items"][0]["id"] == B
    monkeypatch.setattr(service.core, "metric_scope_complete", AsyncMock(side_effect=[True, False]))
    partial = await service.read_overview(None, None, selection)
    assert partial["metrics"][0]["value"] is None and partial["metrics"][0]["known_subtotal"] == "100.00"
    assert partial["pool_counts"]["all"] is None and partial["filter_availability"]["all"]["coverage"] == "partial"


@pytest.mark.asyncio
async def test_task_union_is_before_activity_paging(environment, monkeypatch):
    selection, _ = environment
    one, two = material(), material()
    one[0]["pending_tasks"] = [dict(id=T, task_id=T, member_ids=[M], due_at=NOW)]
    two[0]["pending_tasks"] = [dict(id=T, task_id=T, member_ids=[N], due_at=NOW),
                               dict(id=OPP, task_id=OPP, member_ids=[N], due_at=NOW)]
    monkeypatch.setattr(service.core, "activity_material", AsyncMock(side_effect=[deepcopy(one), deepcopy(two)]))
    result = ReviewPageV2.model_validate(await service.read_activities(
        None, None, selection, activity="pending_tasks", limit=1, offset=1))
    assert result.total == result.known_total == 2 and not result.has_more and len(result.items) == 1
    assert result.items[0]["member_ids"] == [M, N]


@pytest.mark.asyncio
async def test_partial_visit_scope_does_not_infer_stale_days_from_missing_rows(environment, monkeypatch):
    selection, _ = environment
    monkeypatch.setattr(service.core, "source_scope_allowed", AsyncMock(
        side_effect=lambda connection, scope, permission: permission != "visit.read"))
    result = await service.read_opportunities(None, None, selection)
    assert result["total"] == 2  # Independent opportunity/customer coverage stays complete.
    assert not result["filter_availability"]["stale"]["available"]
    assert all(item["stale_days"] is None and item["stale_basis"] == "unknown" for item in result["items"])
