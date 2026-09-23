from datetime import date

from sales_backend.domain.visit_dates import business_date, within_seven_days


def test_seven_calendar_days_including_today_and_cross_year():
    today = date(2026, 9, 10)
    assert within_seven_days("2026-09-04", today) is True
    assert within_seven_days("2026-09-10", today) is True
    assert within_seven_days("2026-09-03", today) is False
    assert within_seven_days("2026-09-11", today) is False
    assert within_seven_days(None, today) is None
    assert within_seven_days("2025-12-27", date(2026, 1, 2)) is True
    assert within_seven_days("2025-12-26", date(2026, 1, 2)) is False


def test_business_midnight_not_utc_midnight():
    assert business_date("2026-09-09T16:00:00Z") == date(2026, 9, 10)
    assert business_date("2026-09-09T15:59:59Z") == date(2026, 9, 9)
    assert business_date("2026-09-10 23:59") == date(2026, 9, 10)
