from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.work_report import SHANGHAI
from sales_backend.services.review_history import attach_baseline, with_baseline
from sales_backend.services.review_overview import availability, mark_day_updates, metric_cards


def fact(**changes):
    return dict(id='a', version_no=1, status='open', currency='CNY', probability='70',
                forecast_id='f', collection_confidence='high', collection_amount='100.01',
                recognized_amount='200.02', **changes)


def baseline(rows):
    return dict(status='available', snapshot_id='snap', captured_at='2026-10-15T23:50:12+08:00',
                snapshot_date='2026-10-15', year=2026, quarter=4, definition_version=1, rows=rows)


def compare(current, old):
    return attach_baseline(metric_cards(current, {}, True, year=2026, quarter=4), old,
                           year=2026, quarter=4, today=date(2026, 10, 16))


def test_actual_snapshot_time_null_zero_and_exact_decimal_change():
    prior = baseline([fact()])
    current = [{**fact(), 'collection_amount': '150.03'}]
    result = compare(current, prior)
    assert result[0]['day_change'] == '50.02'
    assert result[0]['baseline']['captured_at'] == '2026-10-15T23:50:12+08:00'
    assert result[0]['baseline']['basis'] == 'previous_day_scheduled_snapshot'
    assert result[2]['day_change'] == '0.00'
    assert result[2]['baseline']['value'] == '200.02'


@pytest.mark.parametrize('field,value,code', [('collection_amount', None, 'baseline_amount_incomplete'),
                                             ('collection_confidence', None, 'baseline_amount_incomplete')])
def test_partial_old_value_cannot_use_known_subtotal(field, value, code):
    rows = [fact(), {**fact(), 'id': 'missing', field: value}]
    result = compare([fact()], baseline(rows))
    assert result[0]['day_change'] is None
    assert result[0]['baseline']['status'] == 'incomparable'
    assert result[0]['baseline']['reason_code'] == code
    assert result[0]['baseline']['known_subtotal'] == '100.01'


def test_current_missing_keeps_baseline_zero_but_does_not_fabricate_difference():
    result = compare([{**fact(), 'collection_amount': None}], baseline([]))
    assert result[0]['value'] is None and result[0]['day_change'] is None
    assert result[0]['baseline']['value'] == '0.00'
    assert result[0]['baseline']['reason_code'] == 'current_amount_incomplete'


@pytest.mark.parametrize('delta', [dict(snapshot_date='2026-10-14'), dict(quarter=3), dict(definition_version=2),
                                  dict(captured_at=None), dict(captured_at='2026-10-15T23:50:12'),
                                  dict(captured_at='2026-10-14T23:50:12+08:00'), dict(snapshot_id=None)])
def test_no_reuse_after_missed_day_period_or_definition_change(delta):
    result = compare([fact()], {**baseline([]), **delta})
    assert all(c['day_change'] is None for c in result)
    assert result[0]['baseline']['reason_code'] == 'baseline_identity_mismatch'


def test_duplicate_same_subject_rows_do_not_double_count_and_conflicts_reject():
    result = compare([fact()], baseline([fact(), fact()]))
    assert result[0]['day_change'] == '0.00'
    with pytest.raises(ValueError, match='季度事实不一致'):
        compare([fact()], baseline([fact(), {**fact(), 'collection_amount': '1000'}]))


def test_eligibility_confidence_and_lost_changes_use_same_metric_rules():
    prior = baseline([{**fact(), 'collection_confidence': 'low', 'probability': '30'}])
    result = compare([fact()], prior)
    assert [c['day_change'] for c in result] == ['100.01', '0.00', '200.02']
    assert all(c['day_change'] == '0.00' for c in compare([{**fact(), 'status': 'lost'}], baseline([])))


@pytest.mark.asyncio
async def test_historical_anchor_does_not_compare_current_values_to_an_older_day(monkeypatch):
    reader = AsyncMock()
    monkeypatch.setattr('sales_backend.services.review_history.repository.metric_baseline', reader)
    cards = metric_cards([fact()], {}, True, year=2026, quarter=4)
    result = await with_baseline(None, cards, [], quarter={'year': 2026, 'quarter': 4},
        selection=SimpleNamespace(anchor_date=date(2026, 10, 14)), now=datetime(2026, 10, 16, 9, tzinfo=SHANGHAI))
    reader.assert_not_called()
    assert result[0]['day_change'] is None
    assert result[0]['baseline']['reason_code'] == 'historical_anchor_current_values'


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [('complete', False), ('partial', None), ('unavailable', None)])
async def test_no_event_means_untouched_only_after_proven_complete_capture(monkeypatch, status, expected):
    coverage = dict(status=status, complete_from='2026-10-15T12:00:00+08:00',
                    reason_codes=[] if status == 'complete' else ['before_complete_capture'], message='coverage')
    monkeypatch.setattr('sales_backend.services.review_overview.history_repository.day_updates',
                        AsyncMock(return_value={'updated_ids': ['changed'], 'history_coverage': coverage}))
    rows = [dict(id='changed'), dict(id='untouched')]
    filters = availability()
    envelope = {'selection': SimpleNamespace(anchor_date=date(2026, 10, 16)), 'scope': object()}
    for row in rows:
        row.update(status='open', last_follow_up_at=None, business_created_on=None)
    await mark_day_updates(None, envelope, rows, filters, datetime(2026, 10, 16, 10, tzinfo=SHANGHAI))
    assert rows[0]['updated_in_selected_day'] is True
    assert rows[1]['updated_in_selected_day'] is expected
    assert filters['not_updated']['available'] is (status == 'complete')
