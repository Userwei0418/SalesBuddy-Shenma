from datetime import date
from decimal import Decimal

import pytest

from sales_backend.services.profile_performance import quarter_period, ratio_summary


def test_quarter_boundaries_include_leap_day_and_year_end():
    assert quarter_period(2028, 1) == {
        'type': 'quarter', 'year': 2028, 'quarter': 1,
        'start': date(2028, 1, 1), 'end': date(2028, 3, 31),
    }
    assert quarter_period(2026, 4)['end'] == date(2026, 12, 31)


@pytest.mark.parametrize('numerator,total,evaluated,state,rate,coverage', [
    (0, 0, 0, 'empty', None, None),
    (0, 4, 0, 'pending', None, 0),
    (1, 4, 2, 'partial', 25, 50),
    (0, 4, 4, 'ready', 0, 100),
    (9, 10, 10, 'ready', 90, 100),
])
def test_ratio_keeps_unknown_in_denominator_without_inventing_a_zero_score(
        numerator, total, evaluated, state, rate, coverage):
    result = ratio_summary(numerator, total, evaluated)
    assert result['status'] == state
    assert result['rate'] == rate
    assert result['coverage'] == coverage
    assert result['pending_count'] == total-evaluated


def test_ratio_aggregates_counts_not_member_percentage_means():
    left, right = ratio_summary(1, 2, 2), ratio_summary(8, 8, 8)
    team = ratio_summary(left['numerator']+right['numerator'], left['denominator']+right['denominator'], 10)
    assert team['rate'] == Decimal(90)
    assert team['rate'] != (left['rate']+right['rate'])/2
