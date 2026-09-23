import csv
import io
import pytest
from fastapi import HTTPException
from sales_backend.api.exports import csv_response
from sales_backend.domain.reporting import reporting_window


def test_csv_export_preserves_unknown_and_prevents_formula_injection():
    response = csv_response(
        [{"name": "=1+1", "tokens": None}, {"name": " +cmd", "tokens": 0}],
        [("name", "姓名"), ("tokens", "Token")],
        "usage",
    )
    data = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert data == [["姓名", "Token"], ["'=1+1", ""], ["' +cmd", "0"]]
    assert response.headers["x-export-count"] == "2"


def test_export_never_silently_truncates():
    with pytest.raises(HTTPException) as exc:
        csv_response([{}] * 10001, [], "usage")
    assert exc.value.status_code == 422


def test_reporting_window_uses_local_midnight_and_bounds():
    from datetime import date, timedelta

    start, end = reporting_window("custom", date(2026, 9, 10), date(2026, 9, 10))
    assert end - start == timedelta(days=1)
    assert start.hour == 16 and start.day == 9
    with pytest.raises(ValueError):
        reporting_window("custom", date(2026, 9, 10), date(2026, 9, 9))
