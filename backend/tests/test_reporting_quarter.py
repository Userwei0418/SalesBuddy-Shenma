from datetime import UTC, datetime

import pytest

from sales_backend.domain.reporting import quarter_window


@pytest.mark.parametrize("instant,first,last", [
    ("2026-09-30T15:59:59+00:00", "2026-07-01", "2026-10-01"),
    ("2026-09-30T16:00:00+00:00", "2026-10-01", "2027-01-01"),
    ("2026-12-31T15:59:59+00:00", "2026-10-01", "2027-01-01"),
    ("2026-12-31T16:00:00+00:00", "2027-01-01", "2027-04-01"),
])
def test_shanghai_quarter_boundaries(instant, first, last):
    moment = datetime.fromisoformat(instant)
    start, end = quarter_window(moment)
    assert start.date().isoformat() == first and end.date().isoformat() == last
    assert start <= moment < end
    assert start.astimezone(UTC).hour == 16


def test_quarter_rejects_ambiguous_naive_time():
    with pytest.raises(ValueError):
        quarter_window(datetime(2026, 10, 1))
