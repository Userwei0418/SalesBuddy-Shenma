"""HTTP read contracts for legal historical unknowns, using rollback-only PostgreSQL."""

from datetime import date
from decimal import Decimal

import pytest

from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def legacy_opportunity(connection, *, amount, probability, close_date, year=None, quarter=None):
    project = await opportunity(connection, "XS001", 100)
    # New-write validation deliberately requires complete facts. Existing/imported
    # rows may legally lack them; exercise those rows without weakening that API.
    row = await connection.fetchrow(
        """UPDATE crm.opportunity SET amount=$2,probability=$3,expected_close_date=$4,
          expected_close_year=$5,expected_close_quarter=$6
        WHERE id=$1::uuid RETURNING amount,probability,expected_close_date,
          expected_close_year,expected_close_quarter,version_no,updated_at""",
        project["id"], amount, probability, close_date, year, quarter,
    )
    await connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert dict(row) == {
        "amount": amount, "probability": probability, "expected_close_date": close_date,
        "expected_close_year": year, "expected_close_quarter": quarter,
        "version_no": row["version_no"], "updated_at": row["updated_at"],
    }, "The fixture must first satisfy the actual database constraints"
    return project, dict(row)


@pytest.mark.parametrize(
    "amount,probability,close_date,year,quarter",
    [
        pytest.param(None, None, None, None, None, id="all-unknown"),
        pytest.param(None, None, None, 2026, 3, id="quarter-precision-only"),
        pytest.param(0, 10, date(2026, 9, 12), None, None, id="explicit-zero-is-known"),
    ],
)
async def test_legacy_opportunity_http_reads_preserve_unknown_and_quarter_precision(
    connection, amount, probability, close_date, year, quarter,
):
    project, stored = await legacy_opportunity(
        connection, amount=amount, probability=probability, close_date=close_date, year=year, quarter=quarter,
    )
    customer_id, opportunity_id = project["customer_id"], project["id"]

    def numeric(actual, expected):
        # Legacy dict routes serialize Decimal as text; typed detail routes use
        # JSON numbers. Both must retain unknown vs explicitly recorded zero.
        if expected is None:
            assert actual is None
        else:
            assert actual is not None
            assert Decimal(str(actual)) == Decimal(expected)

    def facts(row):
        assert row["id"] == opportunity_id
        numeric(row["amount"], amount)
        numeric(row["probability"], probability)
        assert row["expected_close_date"] == (close_date.isoformat() if close_date else None)
        assert row["expected_close_year"] == year
        assert row["expected_close_quarter"] == quarter

    def summary(row):
        assert row["unknown_open_amount_count"] == int(amount is None)
        numeric(row["open_amount"], amount)

    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")

        async def get(path, **kwargs):
            response = await client.get("/api/v1" + path, **kwargs)
            assert response.status_code == 200, (path, response.text)
            return response.json()

        # These endpoints validate nested references through Pydantic before
        # serializing; direct repository tests cannot detect response-model drift.
        for subject in (
            f"/customers/{customer_id}", f"/opportunities/{opportunity_id}",
            f"/customers/{customer_id}/opportunities/{opportunity_id}",
        ):
            header = await get(subject + "/header")
            facts(header["primary_opportunity"])
            overview = await get(subject + "/overview")
            facts(overview["primary_opportunity"])
            summary(overview["summary"])
            if "opportunities" in header:
                assert len(header["opportunities"]) == len(overview["opportunities"]) == 1
                facts(header["opportunities"][0])
                facts(overview["opportunities"][0])
            else:
                assert overview["summary"]["max_probability"] == probability

        for path, params in (
            ("/opportunities", {"customer_id": customer_id, "include_closed": "true", "order": "quarter_stage"}),
            (f"/customers/{customer_id}/opportunities", {}),
        ):
            page = await get(path, params=params)
            assert page["summary"]["total"] == len(page["items"]) == 1
            facts(page["items"][0])
            summary(page["summary"])

        for params in ({}, {"customer_id": customer_id}):
            timeline = await get(f"/opportunities/{opportunity_id}/timeline", params=params)
            assert any(item["key"] == "created" for item in timeline["items"])
            assert all(item["object_id"] == opportunity_id for item in timeline["items"])
            assert timeline["has_more"] is False

        # Older clients still use the untyped detail route during rollout.
        detail = await get(f"/opportunities/{opportunity_id}/detail")
        facts(detail["opportunities"][0])

    after = await connection.fetchrow(
        """SELECT amount,probability,expected_close_date,expected_close_year,
          expected_close_quarter,version_no,updated_at FROM crm.opportunity WHERE id=$1::uuid""",
        opportunity_id,
    )
    assert dict(after) == stored, "Read projections must not fill unknown stored facts"


async def test_console_legacy_opportunity_detail_and_browse_preserve_null_fields(connection):
    project, _ = await legacy_opportunity(connection, amount=None, probability=None, close_date=None)
    async with await client_for(connection) as client:
        await sign_in(client)
        for path, params in (
            (f"/api/v1/console/opportunities/{project['id']}", {}),
            ("/api/v1/console/opportunities", {"customer": project["customer_id"]}),
        ):
            response = await client.get(path, params=params)
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            rows = body["items"] if "items" in body else [body]
            assert len(rows) == 1
            row = rows[0]
            assert row["id"] == project["id"]
            assert row["amount"] is None
            assert row["probability"] is None
            assert row["expected_close_date"] is None
