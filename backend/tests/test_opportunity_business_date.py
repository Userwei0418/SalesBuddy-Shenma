"""Creation-date statistics must not turn historical imports into new business."""

from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.repositories.opportunity_business_date import (
    business_created_on,
    creation_date_projection,
)
from sales_backend.repositories.opportunity_overview import opportunity_overview


@pytest.mark.parametrize("source", [
    "2026-03-31", "2026/03/31", " 2026-03-31 ",
    "2026-03-31T23:59:00", "2026-03-31 23:59:00",
    date(2026, 3, 31), datetime(2026, 3, 31, 23, 59),
])
def test_historical_source_day_takes_precedence_over_legacy_and_import_day(source):
    fact = {
        "historical": True, "source_created": source,
        "legacy_created": "2025-12-31", "created_at": "2026-09-22T12:00:00Z",
    }
    original = deepcopy(fact)
    assert business_created_on(fact) == date(2026, 3, 31)
    assert fact == original


@pytest.mark.parametrize("source,expected", [
    ("2026-03-31T15:59:59Z", date(2026, 3, 31)),
    ("2026-03-31T16:00:00Z", date(2026, 4, 1)),
    ("2025-12-31T16:00:00+00:00", date(2026, 1, 1)),
    ("2026-04-01T00:30:00+09:00", date(2026, 3, 31)),
    (datetime(2026, 3, 31, 16, tzinfo=UTC), date(2026, 4, 1)),
])
def test_source_timestamps_use_shanghai_business_day_at_quarter_and_year_boundaries(source, expected):
    assert business_created_on({"historical": True, "source_created": source}) == expected


@pytest.mark.parametrize("source", [None, "", " \t\n"])
def test_only_missing_or_blank_new_source_can_use_legacy_raw_date(source):
    assert business_created_on({
        "historical": True, "source_created": source,
        "legacy_created": "2025/12/31", "created_at": "2026-09-22T00:00:00Z",
    }) == date(2025, 12, 31)


@pytest.mark.parametrize("source", [
    "not a date", "2026-02-30", "2026", "2026 Q1", "2026-03",
    "2026年3月31日", "03/31/2026", 1774915200000, True, [], {},
])
def test_present_invalid_historical_source_stays_unknown_even_with_valid_legacy(source):
    assert business_created_on({
        "historical": True, "source_created": source,
        "legacy_created": "2025-12-31", "created_at": "2026-09-22T00:00:00Z",
    }) is None


@pytest.mark.parametrize("legacy", [None, "", "unknown", "2026-02-30", []])
def test_unknown_history_never_falls_back_to_import_timestamp(legacy):
    assert business_created_on({
        "historical": True, "legacy_created": legacy,
        "created_at": "2026-09-22T00:00:00Z",
    }) is None


@pytest.mark.parametrize("historical", [False, None])
def test_native_row_uses_created_at_in_shanghai_and_ignores_unrelated_source_fields(historical):
    assert business_created_on({
        "historical": historical, "created_at": "2025-12-31T16:00:00Z",
        "source_created": "2025-01-01", "legacy_created": "2024-01-01",
    }) == date(2026, 1, 1)


def test_native_row_with_missing_created_at_is_unknown():
    assert business_created_on({"historical": False, "source_created": "2026-01-01"}) is None


def test_source_date_is_not_clamped_to_today_or_import_day():
    # Interpretation remains faithful to the source; no invented future-date rule.
    assert business_created_on({
        "historical": True, "source_created": "2030-06-30", "created_at": "2026-09-22T00:00:00Z",
    }) == date(2030, 6, 30)


@pytest.mark.parametrize("source", ["9999-12-31T23:00:00Z", "0001-01-01T00:00:00+14:00"])
def test_unrepresentable_local_date_is_unknown_instead_of_failing_statistics(source):
    assert business_created_on({"historical": True, "source_created": source}) is None


def test_sql_projection_retains_only_audit_date_and_two_approved_source_date_paths():
    sql = creation_date_projection()
    assert "'created_at',o.created_at" in sql
    assert "o.import_meta->>'import_type'='crm_history'" in sql
    assert "o.import_meta->'source_fields'->'商机创建时间'" in sql
    assert "o.import_meta->'raw_fields'->'商机创建时间'" in sql
    assert "source_created_at" not in sql
    assert "o.import_meta," not in sql
    assert "o.import_meta)" not in sql


@pytest.mark.parametrize("alias", ["other", "o; DROP TABLE crm.opportunity", "o.created_at", ""])
def test_sql_projection_rejects_aliases_outside_its_fixed_internal_contract(alias):
    with pytest.raises(ValueError, match="Unknown internal opportunity alias"):
        creation_date_projection(alias)


@pytest.mark.asyncio
@pytest.mark.parametrize("year,quarters,expected_new", [
    (2025, [4], 1), (2026, [1], 1), (2026, [2, 3], 1), (2030, [2], 1), (2026, [], 4),
])
async def test_overview_counts_source_business_dates_and_never_returns_raw_date_facts(year, quarters, expected_new):
    actor = SimpleNamespace(role=SimpleNamespace(value="manager"), user_id="fixture-user", workspace_id="fixture-workspace", team_ids=[])
    metrics = {
        "demo_scene_count": 0, "won": 1, "total": 6, "active": 1,
        "missingCloseDates": 2, "missingWonDates": 0,
        "creation_date_facts": [
            {"historical": True, "source_created": "2025-12-31", "created_at": "2026-09-22T00:00:00Z"},
            {"historical": True, "legacy_created": "2026/03/31", "created_at": "2026-09-22T00:00:00Z"},
            {"historical": False, "created_at": "2026-03-31T16:00:00Z"},
            {"historical": True, "source_created": "2030-06-30", "created_at": "2026-09-22T00:00:00Z"},
            {"historical": True, "source_created": None, "created_at": "2026-09-22T00:00:00Z"},
            {"historical": True, "source_created": "bad", "legacy_created": "2026-03-31"},
        ],
    }
    original = deepcopy(metrics)
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=metrics))
    result = await opportunity_overview(connection, actor, year=year, quarters=quarters)
    assert result["metrics"]["newCount"] == expected_new
    assert result["metrics"]["missingCreatedDates"] == 2
    assert result["metrics"]["total"] == 6
    assert "creation_date_facts" not in result["metrics"]
    assert metrics == original
    assert result["definition_version"] == "opportunity_overview_v3"
