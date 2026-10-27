"""Synthetic business cases for exact signed deltas, history and team attribution."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from sales_backend.contracts.work_report import PeriodSelection
from sales_backend.contracts.work_report_meeting import MeetingResolvedScope
from sales_backend.domain.work_report import resolve_period
from sales_backend.domain.work_report_meeting import assemble, forecast_at, money, state_at

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 10, 18, tzinfo=TZ)
SINCE = datetime(2026, 9, 1, tzinfo=TZ)
A, B, TEAM, CUSTOMER, OPP = [str(UUID(int=i)) for i in range(1, 6)]
SCOPE = MeetingResolvedScope(kind="managed_team", team_id=TEAM, label="销售团队", member_ids=[A, B])
PERIOD = resolve_period(PeriodSelection(kind="week"), now=NOW)


def opportunity(ident=OPP, *, before="1000000", after="700000", owner_before=A, owner_after=A, status="open"):
    left = dict(amount=before, currency="CNY", status="open", owner_id=owner_before, team_id=TEAM)
    right = dict(amount=after, currency="CNY", status=status, owner_id=owner_after, team_id=TEAM)
    return dict(
        id=ident,
        customer_id=CUSTOMER,
        customer_name="合成客户",
        name="合成商机",
        version_no=2,
        **right,
        business_created_at=SINCE - timedelta(days=1),
        events=[
            dict(
                id=str(UUID(int=100 + UUID(ident).int)),
                after_version=2,
                before_version=1,
                occurred_at=PERIOD.start + timedelta(days=1),
                recorded_at=PERIOD.start + timedelta(days=1),
                mutation_kind="update",
                history_origin="unknown",
                complete_from_origin=False,
                source_kind="unverified_write",
                before_status="open",
                after_status=status,
                meeting_state_captured=True,
                meeting_before=left,
                meeting_after=right,
            )
        ],
        forecasts=[dict(year=2026, quarter=4, recognized_amount="20.00", collection_amount="30.00")],
        forecast_events=[],
    )


def bundle(*opportunities):
    return dict(
        history_since=SINCE,
        members=[
            dict(id=A, name="成员A", membership_state="current"),
            dict(id=B, name="成员B", membership_state="period_only"),
        ],
        opportunities=list(opportunities),
        claims=[],
        visits=[],
        tasks=[],
    )


def facts(data, *, period=PERIOD, scope=SCOPE):
    return assemble(data, period=period, scope=scope, data_as_of=NOW)[0]


def test_signed_120_to_100_team_net_and_member_deduplication():
    a = opportunity()
    b = opportunity(str(UUID(int=6)), before="200000", after="300000", owner_before=B, owner_after=B)
    value = facts(bundle(a, b))
    assert (value.amounts.acv.opening, value.amounts.acv.change, value.amounts.acv.closing) == (
        "1200000.00",
        "-200000.00",
        "1000000.00",
    )
    assert [row.amounts.acv.change for row in value.opportunity_changes] == ["-300000.00", "100000.00"]
    assert [row.amounts.acv.change for row in value.members] == ["-300000.00", "100000.00"]
    assert not value.members[0].deduplicated_team_total


def test_inside_team_transfer_zero_team_independent_negative_positive_members():
    value = facts(bundle(opportunity(before="100", after="100", owner_before=A, owner_after=B)))
    assert value.amounts.acv.change == "0.00"
    assert [m.amounts.acv.change for m in value.members] == ["-100.00", "100.00"]
    assert value.members[0].opportunity_changes[0].reasons[0].code == "scope_transfer_out"
    assert value.members[1].opportunity_changes[0].reasons[0].code == "scope_transfer_in"
    assert value.members[1].membership_state == "period_only"


def test_cross_department_transfer_leaves_selected_team_and_member_contribution():
    row = opportunity(before="100", after="100")
    row["team_id"] = str(UUID(int=900))
    row["events"][0]["meeting_after"]["team_id"] = row["team_id"]
    value = facts(bundle(row))
    assert value.amounts.acv.change == "-100.00"
    assert value.members[0].amounts.acv.change == "-100.00"
    assert value.members[0].amounts.acv.closing == "0.00"


def test_lost_excludes_expected_amount_without_changing_original_history():
    data = bundle(opportunity(status="lost"))
    original = deepcopy(data)
    value = facts(data)
    assert value.amounts.acv.closing == "0.00" and value.amounts.acv.change == "-1000000.00"
    assert value.amounts.quarter_collection.closing == "0.00"
    assert value.counts.lost_opportunities.value == 1
    assert value.lost_opportunities[0].id != UUID(OPP)
    assert value.members[0].lost_opportunity_ids == [value.lost_opportunities[0].id]
    assert data == original


def test_unknown_history_keeps_known_close_and_does_not_substitute_zero():
    data = bundle(opportunity())
    data["history_since"] = NOW - timedelta(hours=1)
    value = facts(data)
    assert value.amounts.acv.opening is None and value.amounts.acv.change is None
    assert value.amounts.acv.closing == "700000.00"
    assert value.counts.lost_opportunities.value is None
    assert value.counts.formal_visits.value == 0


def test_unknown_owner_boundary_is_not_zero_for_another_member():
    row = opportunity(owner_after=B)
    row["events"][0]["after_version"] = 10
    value = facts(bundle(row))
    assert value.members[0].amounts.acv.opening is None
    assert value.members[0].amounts.acv.change is None
    assert value.members[0].amounts.acv.closing == "0.00"
    assert value.members[1].amounts.acv.closing == "700000.00"
    assert value.counts.lost_opportunities.value is None


def test_historical_closing_reverses_future_updates_not_current_amount():
    period = resolve_period(PeriodSelection(kind="day", anchor_date=PERIOD.start.date()), now=NOW)
    value = facts(bundle(opportunity()), period=period)
    assert value.amounts.acv.opening == value.amounts.acv.closing == "1000000.00"
    assert value.amounts.acv.change == "0.00"


def test_cross_quarter_uses_same_q4_and_preserves_unknown_q4_open():
    period = resolve_period(PeriodSelection(kind="two_weeks", anchor_date=datetime(2026, 10, 2).date()), now=NOW)
    row = opportunity()
    row["events"] = []
    row["forecasts"].append(dict(year=2026, quarter=3, recognized_amount="999", collection_amount="999"))
    row["forecast_events"] = [
        dict(
            id=str(UUID(int=777)),
            year=2026,
            quarter=4,
            recorded_at=datetime(2026, 10, 1, tzinfo=TZ),
            before_state=None,
            after_state=dict(recognized_amount="20.00", collection_amount="30.00"),
        )
    ]
    value = facts(bundle(row), period=period)
    assert value.quarter.quarter == 4
    assert value.amounts.quarter_collection.opening is None
    assert value.amounts.quarter_collection.closing == "30.00" and value.amounts.quarter_collection.change is None
    reasons = [r.code for r in value.opportunity_changes[0].reasons]
    assert "forecast_change" not in reasons and "history_gap" in reasons


def test_post_period_creation_and_earlier_transfer_are_not_period_changes():
    period = resolve_period(PeriodSelection(kind="day", anchor_date=PERIOD.start.date()), now=NOW)
    created = opportunity()
    event = created["events"][0]
    event.update(
        mutation_kind="insert",
        history_origin="native_creation",
        complete_from_origin=True,
        source_kind="native_application",
        before_version=None,
        meeting_before=None,
    )
    assert facts(bundle(created), period=period).opportunity_changes == []

    transferred = opportunity()
    transferred["team_id"] = str(UUID(int=900))
    transferred["events"][0]["meeting_after"]["team_id"] = transferred["team_id"]
    transferred["events"][0]["occurred_at"] = period.start - timedelta(days=1)
    value = facts(bundle(transferred), period=period)
    assert value.opportunity_changes == [] and value.amounts.acv.change == "0.00"


@pytest.mark.parametrize("fault", ["after_state", "version_gap", "old_event"])
def test_bad_reverse_chain_returns_unknown(fault):
    row = opportunity()
    if fault == "after_state":
        row["events"][0]["meeting_after"]["amount"] = "999"
    if fault == "version_gap":
        row["events"][0]["after_version"] = 10
    if fault == "old_event":
        row["events"][0]["meeting_state_captured"] = False
    assert state_at(row, PERIOD.start, SINCE) is None


def test_deduped_claims_visits_and_registered_date_qualifier_are_frozen():
    data = bundle()
    base = dict(
        customer_id=CUSTOMER,
        customer_name="客户甲",
        name="客户甲",
        occurred_at=PERIOD.start,
        summary="审批认领生效",
        owner_id=A,
    )
    data["claims"] = [{**base, "id": str(UUID(int=11))}, {**base, "id": str(UUID(int=12)), "owner_id": B}]
    data["visits"] = [
        dict(
            id=str(UUID(int=20)),
            version_no=2,
            name="拜访记录",
            interaction_at=PERIOD.start,
            customer_id=CUSTOMER,
            opportunity_id=None,
            member_ids=[A, B],
            summary="已确认拜访",
            next_action="本月底前完成评审",
        )
    ]
    value = facts(data)
    assert value.counts.new_claimed_customers.value == 1 and len(value.new_claimed_customers[0].source_refs) == 2
    assert value.counts.formal_visits.value == 1
    assert [m.counts.formal_visits.value for m in value.members] == [1, 1]
    assert value.next_steps[0].due_at is None and value.next_steps[0].original_date_text == "本月底前完成评审"
    assert value.members[0].new_claimed_customer_ids == [value.new_claimed_customers[0].id]


def test_unknown_forecast_is_not_zero_and_exact_decimal_is_not_rounded():
    row = opportunity()
    row["forecasts"] = []
    assert forecast_at(row, PERIOD.start, SINCE, (2026, 4)) is None
    value = money([Decimal("0.12345678")], [Decimal("0.12345679")])
    assert value.change == "0.00000001" and value.closing == "0.12345679"
