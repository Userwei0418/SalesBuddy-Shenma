"""A confirmed exact date supersedes a historical quarter without weakening constraints."""

from datetime import date

import pytest

from sales_backend.repositories.opportunity_browse import browse_opportunities
from sales_backend.services.opportunities import save_opportunity
from tests.integration.feishu_fixtures import seed_execute
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("original_date,period,new_date,remaining_period", [
    (None, (2026, 2), date(2026, 9, 30), (None, None)),
    (None, (2026, 2), date(2027, 1, 1), (None, None)),
    (date(2026, 6, 15), (2026, 2), date(2026, 9, 30), (None, None)),
    (date(2026, 6, 15), (2026, 2), date(2026, 6, 30), (None, None)),
    (date(2026, 9, 30), (2026, 3), date(2026, 9, 30), (2026, 3)),
    (None, (None, None), date(2026, 9, 30), (None, None)),
    # Exact dates have never been constrained to the quarter-only 2000–2100 range.
    (None, (2026, 2), date(1999, 12, 31), (None, None)),
    (None, (2026, 2), date(2000, 1, 1), (None, None)),
    (None, (2026, 2), date(2100, 12, 31), (None, None)),
    (None, (2026, 2), date(2101, 1, 1), (None, None)),
])
async def test_manual_edit_keeps_exact_date_and_original_import_evidence(
    connection, original_date, period, new_date, remaining_period,
):
    project = await opportunity(connection, "XS001", 100)
    person = await actor(connection, "XS001")
    evidence = {"source_fields": {"预计关单季度": "2026 Q2"}}
    await seed_execute(connection, """UPDATE crm.opportunity SET expected_close_date=$2,
        expected_close_year=$3,expected_close_quarter=$4,import_meta=$5::jsonb WHERE id=$1::uuid""",
        project["id"], original_date, *period, evidence)
    version = await connection.fetchval(
        "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", project["id"],
    )
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        payload = {
            "name": project["name"], "amount": "200", "probability": 10,
            "expected_close_date": new_date.isoformat(), "action": "update",
            "opportunity_id": project["id"], "version_no": version,
        }
        response = await client.post(f"/api/v1/customers/{project['customer_id']}/opportunities", json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["expected_close_date"] == new_date.isoformat()
        saved = dict(await connection.fetchrow("""SELECT expected_close_date,expected_close_year,
            expected_close_quarter,import_meta,version_no FROM crm.opportunity WHERE id=$1::uuid""", project["id"]))
        assert saved["expected_close_date"] == new_date
        assert (saved["expected_close_year"], saved["expected_close_quarter"]) == remaining_period
        assert saved["import_meta"] == evidence
        assert saved["version_no"] > version
        # An out-of-date form remains a conflict, not a second successful update.
        stale = await client.post(f"/api/v1/customers/{project['customer_id']}/opportunities", json=payload)
        assert stale.status_code == 409, stale.text
        await business_login(client, "XS002")
        denied = await client.post(
            f"/api/v1/customers/{project['customer_id']}/opportunities",
            json={**payload, "version_no": saved["version_no"]},
        )
        assert denied.status_code in (403, 404), denied.text
    await actor(connection, "XS001")
    assert dict(await connection.fetchrow("""SELECT expected_close_date,expected_close_year,
        expected_close_quarter,import_meta,version_no FROM crm.opportunity WHERE id=$1::uuid""", project["id"])) == saved
    if new_date == date(2026, 9, 30):
        current = await browse_opportunities(connection, person, limit=20, year=2026, quarters=[3])
        previous = await browse_opportunities(connection, person, limit=20, year=2026, quarters=[2])
        assert project["id"] in {row["id"] for row in current["items"]}
        assert project["id"] not in {row["id"] for row in previous["items"]}


async def test_edit_without_a_new_exact_date_preserves_quarter_only_precision(connection):
    project = await opportunity(connection, "XS001", 100)
    person = await actor(connection, "XS001")
    await connection.execute("""UPDATE crm.opportunity SET expected_close_date=NULL,
        expected_close_year=2026,expected_close_quarter=2 WHERE id=$1::uuid""", project["id"])
    version = await connection.fetchval(
        "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", project["id"],
    )
    await save_opportunity(connection, person, customer_id=project["customer_id"], data={
        "name": project["name"], "amount": 200, "action": "update",
        "opportunity_id": project["id"], "version_no": version,
    })
    row = await connection.fetchrow("""SELECT expected_close_date,expected_close_year,expected_close_quarter
        FROM crm.opportunity WHERE id=$1::uuid""", project["id"])
    assert tuple(row) == (None, 2026, 2)
