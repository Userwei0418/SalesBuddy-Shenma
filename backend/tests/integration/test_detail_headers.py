"""Master-data headers preserve existing RLS scopes without reading history aggregates."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.detail_overviews import DetailOverviewRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def forbid_summary(*args, **kwargs):
    raise AssertionError("header must not read visit/task/risk aggregates")


async def test_header_endpoints_keep_personal_scope_and_never_read_summary(connection, monkeypatch):
    project = await opportunity(connection, "XS001", 100)
    customer_id, opportunity_id = project["customer_id"], project["id"]
    monkeypatch.setattr("sales_backend.repositories.detail_overviews.related_summary", forbid_summary)
    monkeypatch.setattr("sales_backend.repositories.detail_overviews.opportunity_risk_summaries", forbid_summary)
    paths = [f"/customers/{customer_id}/header", f"/opportunities/{opportunity_id}/header",
             f"/customers/{customer_id}/opportunities/{opportunity_id}/header"]
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        for path in paths:
            response = await client.get("/api/v1" + path)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["read_model"] == "detail_header_v1"
            assert result["id"] == customer_id
            assert not {"summary", "profile", "visits", "tasks", "risks"} & result.keys()
            assert result["primary_opportunity"]["id"] == opportunity_id
        assert (await client.get("/api/v1/customers/bad/header")).status_code == 422
        mismatch = f"/api/v1/customers/{uuid4()}/opportunities/{opportunity_id}/header"
        assert (await client.get(mismatch)).status_code == 404
        await business_login(client, "XS002")
        for path in paths:
            assert (await client.get("/api/v1" + path)).status_code == 404


async def test_fde_header_keeps_customer_panorama_and_target_customer_binding(connection, monkeypatch):
    sales, project, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    other = await save_opportunity(connection, sales, customer_id=project["customer_id"], data={
        "name": "全景主档其他商机", "amount": Decimal(900), "probability": 10,
        "expected_close_date": date(2026, 12, 20),
    })
    monkeypatch.setattr("sales_backend.repositories.detail_overviews.related_summary", forbid_summary)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        response = await client.get(f"/api/v1/customers/{project['customer_id']}/header")
        assert response.status_code == 200
        response = await client.get(f"/api/v1/customers/{project['customer_id']}/opportunities/{other['id']}/header")
        assert response.status_code == 200 and response.json()["opportunities"][0]["id"] == other["id"]
        assert (await client.get(f"/api/v1/customers/{uuid4()}/opportunities/{other['id']}/header")).status_code == 404
    await actor(connection, "XS002")
    assert await DetailOverviewRepository().customer_header(connection, project["customer_id"]) is None
