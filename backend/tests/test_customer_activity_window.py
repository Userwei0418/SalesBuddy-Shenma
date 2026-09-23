from datetime import date

import pytest

from sales_backend.repositories.customer_map import activity_since


@pytest.mark.parametrize("as_of,expected", [
    (date(2026, 9, 22), date(2026, 3, 22)),
    (date(2026, 8, 31), date(2026, 2, 28)),
    (date(2024, 8, 31), date(2024, 2, 29)),
    (date(2026, 1, 31), date(2025, 7, 31)),
])
def test_six_calendar_months_clamps_end_of_month(as_of, expected):
    assert activity_since(as_of) == expected
