from datetime import date

import pytest

from sales_backend.domain.activity_window import activity_window_bounds
from sales_backend.repositories.customer_map import activity_since


@pytest.mark.parametrize("as_of,expected", [
    (date(2026, 9, 22), date(2026, 1, 1)),
    (date(2026, 8, 31), date(2026, 1, 1)),
    (date(2024, 2, 29), date(2024, 1, 1)),
    (date(2026, 1, 1), date(2026, 1, 1)),
    (date(2026, 12, 31), date(2026, 1, 1)),
    (date(2027, 1, 1), date(2027, 1, 1)),
])
def test_activity_window_starts_at_current_calendar_year(as_of, expected):
    assert activity_since(as_of) == expected


@pytest.mark.parametrize("window,expected_start", [
    ("today", date(2026, 9, 22)),
    ("day", date(2026, 9, 22)),
    ("3d", date(2026, 9, 20)),
    ("week", date(2026, 9, 21)),
    ("month", date(2026, 9, 1)),
    ("year", date(2026, 1, 1)),
])
def test_activity_window_bounds_are_inclusive_business_dates(window, expected_start):
    bounds = activity_window_bounds(date(2026, 9, 22), window)
    assert bounds.key == ("today" if window == "day" else window)
    assert bounds.start == expected_start
    assert bounds.end == date(2026, 9, 22)


def test_activity_window_defaults_to_legacy_year_for_repository_callers():
    assert activity_window_bounds(date(2026, 9, 22)).start == date(2026, 1, 1)


def test_activity_window_rejects_unknown_window():
    with pytest.raises(ValueError, match="活动时间范围"):
        activity_window_bounds(date(2026, 9, 22), "quarter")
