"""Small completeness checks, without database or model calls."""

from decimal import Decimal
from pathlib import Path

import pytest

from sales_backend.repositories.fde_dashboard import open_acv_totals


@pytest.mark.parametrize(("rows", "amount", "known", "missing"), [
    ([], "0", 0, 0),
    ([{"status": "open", "amount": None}], "0", 0, 1),
    ([{"status": "open", "amount": Decimal("0.00")}], "0.00", 1, 0),
    ([{"status": "open", "amount": None}, {"status": "open", "amount": Decimal("12.34")},
      {"status": "won", "amount": Decimal("500")}, {"status": "lost", "amount": None}], "12.34", 1, 1),
])
def test_fde_open_acv_keeps_known_zero_and_excludes_closed_opportunities(rows, amount, known, missing):
    assert open_acv_totals(rows) == {
        "open_acv": Decimal(amount),
        "open_acv_known_count": known,
        "open_acv_missing_count": missing,
    }


def test_ranking_migration_keeps_existing_authorization_scope_amounts_and_ordering():
    migrations = Path(__file__).resolve().parents[2] / "database" / "migrations"
    before = (migrations / "V148__dashboard_team_per_capita_rankings.sql").read_text()
    after = (migrations / "V295__dashboard_acv_amount_coverage.sql").read_text()
    before = before[before.index("CREATE OR REPLACE FUNCTION"):before.index("-- Inherit owner,")]
    after = after[after.index("CREATE OR REPLACE FUNCTION"):after.index("INSERT INTO ops.schema_migration")]
    added = (
        ",\n    count(e.person) FILTER (WHERE p_metric='opportunity_acv' AND e.value IS NOT NULL) AS known_amount_count"
        ",\n    count(e.person) FILTER (WHERE p_metric='opportunity_acv' AND e.value IS NULL) AS missing_amount_count"
    )
    # Both team modes and the personal cohort must retain their exact original
    # permissions and filters. Count real joined events, not the empty LEFT JOIN.
    assert after.count(added) == 3
    assert after.replace(added, "") == before

