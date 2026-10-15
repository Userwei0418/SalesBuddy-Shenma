"""Report snapshot v2 selector and frozen facts are distinct from legacy scope."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.review_scope import WorkReportScopeSelectionV2
from sales_backend.contracts.work_report import SnapshotSelection
from sales_backend.contracts.work_report_review import ReviewFacts, ReviewFactsV2, validate_review_facts

A, B = UUID(int=1), UUID(int=2)


def test_report_scope_v2_canonicalizes_raw_ids_and_adapts_to_resolver():
    selected = WorkReportScopeSelectionV2(
        kind="team", team_ids=[B, A, B], expected_scope_revision="a" * 64
    )
    assert selected.team_ids == [A, B]
    adapted = selected.review_selection()
    assert adapted.scope == "team" and adapted.team_ids == [A, B]
    assert adapted.expected_scope_revision == "a" * 64


@pytest.mark.parametrize(
    "payload",
    [
        dict(kind="self", member_ids=[A], expected_scope_revision="a" * 64),
        dict(kind="team", team_ids=[A], member_ids=[B], expected_scope_revision="a" * 64),
        dict(kind="member", member_ids=[], expected_scope_revision="a" * 64),
        dict(kind="team", team_ids=[A], expected_scope_revision="bad"),
    ],
)
def test_report_scope_v2_rejects_ambiguous_or_unfrozen_selection(payload):
    with pytest.raises(ValidationError):
        WorkReportScopeSelectionV2.model_validate(payload)


@pytest.mark.parametrize("period", ["day", "week", "two_weeks", "month", "quarter"])
def test_snapshot_scope_v2_is_independent_of_review_mode(period):
    scope = dict(
        contract_version="work_report.scope_selection.v2",
        kind="team",
        team_ids=[str(A)],
        expected_scope_revision="a" * 64,
    )
    selected = SnapshotSelection(scope=scope, period={"kind": period})
    assert selected.scope.kind == "team" and selected.review_mode is None
    with pytest.raises(ValidationError):
        SnapshotSelection(scope=scope, period={"kind": period}, sources=[])
    if period != "week":
        with pytest.raises(ValidationError):
            SnapshotSelection(scope=scope, period={"kind": period}, review_mode="weekly")
        with pytest.raises(ValidationError):
            SnapshotSelection(scope=scope, period={"kind": period}, facts_contract="work_report.sales_week_facts.v1")
    else:
        assert SnapshotSelection(scope=scope, facts_contract="work_report.sales_week_facts.v1").facts_contract
        assert SnapshotSelection(scope=scope, review_mode="weekly", sources=[]).review_mode == "weekly"


def test_review_facts_v2_uses_only_the_frozen_v2_scope():
    period = dict(
        kind="day",
        start="2026-10-14T00:00:00+08:00",
        end_exclusive="2026-10-15T00:00:00+08:00",
        cutoff="2026-10-14T12:00:00+08:00",
        label="2026-10-14",
    )
    scope = dict(
        contract_version="review.scope.v2",
        kind="team",
        label="甲团队、乙团队",
        subject_ids=[str(A), str(B)],
        subjects=[
            {"scope_type": "team", "id": str(A), "label": "甲团队"},
            {"scope_type": "team", "id": str(B), "label": "乙团队"},
        ],
        resolved_member_ids=[str(A), str(B)],
        scope_key="review-scope:v2:" + "a" * 64,
        scope_revision="b" * 64,
    )
    facts = ReviewFactsV2(
        period=period,
        scope=scope,
        permission_version="rbac:1",
        rules_version="work_report.v1",
        data_as_of="2026-10-14T12:00:00+08:00",
        evidence=[],
        measures=[],
        groups=[],
    )
    assert isinstance(validate_review_facts(facts.model_dump(mode="python")), ReviewFactsV2)
    with pytest.raises(ValidationError):
        ReviewFacts.model_validate(facts.model_dump(mode="python"))


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["work_report.read", "work_report.write", "work_report.share", "agent.work_report", "report.export"])
async def test_empty_contributing_subject_still_requires_action_permission(permission):
    from sales_backend.services.work_report_access import can_act_on_frozen_subjects

    scope = dict(
        contract_version="review.scope.v2", kind="team", label="A,B",
        subject_ids=[str(A), str(B)],
        subjects=[dict(scope_type="team", id=str(uid), label=str(uid)) for uid in (A, B)],
        resolved_member_ids=[str(A), str(B)],
        scope_key="review-scope:v2:" + "a" * 64, scope_revision="b" * 64,
    )
    calls = []

    class Connection:
        async def fetchval(self, query, action, kind, person, team):
            calls.append((action, team))
            return team == str(A)

    assert not await can_act_on_frozen_subjects(Connection(), scope, permission)
    assert calls == [(permission, str(A)), (permission, str(B))]
