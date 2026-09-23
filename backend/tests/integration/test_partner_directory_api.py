"""Real endpoints, transactions, RLS, partner identity and quarter-plan boundaries."""

from uuid import uuid4

import pytest

from tests.integration.test_operations_api import client_for, sign_in

pytestmark = pytest.mark.asyncio


async def test_partner_directory_opportunity_roundtrip_and_retirement(connection):
    async with await client_for(connection) as client:
        await sign_in(client)
        key = str(uuid4())
        body = {"name": "隔离伙伴-" + uuid4().hex[:10]}
        created = await client.post("/api/v1/console/partners", json=body, headers={"Idempotency-Key": key})
        assert created.status_code == 200, created.text
        partner = created.json()
        replay = await client.post("/api/v1/console/partners", json=body, headers={"Idempotency-Key": key})
        assert replay.json() == partner
        assert (await client.get("/api/v1/directory/partners", params={"q": body["name"]})).json()["total"] == 1
        duplicate = await client.post("/api/v1/console/partners", json={"name": " " + body["name"] + " "})
        assert duplicate.status_code == 409
        org = (await client.get("/api/v1/console/organization")).json()
        owner = next(a for a in org["accounts"] if a["account_code"] == "XS001")
        customer = await client.post(
            "/api/v1/customers",
            json={
                "name": "伙伴集成客户-" + uuid4().hex[:8],
                "company_reference": "ISOLATED-" + uuid4().hex,
                "customer_type": "潜在客户",
                "level_code": "Tier-2",
                "source": "其他",
                "target_team": "南区",
                "contact_name": "隔离联系人",
                "contact_title": "经理",
                "contact_role": "使用者",
            },
        )
        assert customer.status_code == 201, customer.text
        customer_id = customer.json()["id"]
        base = {
            "customer_id": customer_id,
            "owner_user_ref_id": owner["id"],
            "name": "隔离商机",
            "sales_channel": "partner",
            "partner_id": partner["id"],
            "probability": 30,
            "amount": 100000,
            "expected_close_date": "2027-03-31",
        }
        missing = await client.post("/api/v1/console/opportunities", json=base)
        assert missing.status_code == 422 and "季度" in missing.text
        quarters = [{"year": 2026, "quarter": 4, "recognized_amount": 0, "collection_amount": 0}]
        result = await client.post("/api/v1/console/opportunities", json={**base, "quarterly_forecasts": quarters})
        assert result.status_code == 201, result.text
        opportunity = result.json()
        assert opportunity["partner_id"] == partner["id"] and opportunity["partner_name"] == partner["name"]
        detail = (await client.get("/api/v1/console/opportunities/" + opportunity["id"])).json()
        assert detail["sales_channel"] == "partner" and detail["quarterly_forecasts"][0]["year"] == 2026
        update = {
            **base,
            "action": "update",
            "opportunity_id": opportunity["id"],
            "version_no": opportunity["version_no"],
            "expected_close_date": "2028-01-01",
        }
        moved = await client.post("/api/v1/console/opportunities", json=update)
        assert moved.status_code == 201, moved.text
        assert moved.json()["quarterly_forecasts"][0]["year"] == 2026
        retired = await client.post(
            "/api/v1/console/partners",
            json={
                "id": partner["id"],
                "name": partner["name"],
                "status": "inactive",
                "version_no": partner["version_no"],
            },
        )
        assert retired.status_code == 200, retired.text
        assert (await client.get("/api/v1/directory/partners", params={"q": partner["name"]})).json()["total"] == 0
        denied = await client.post(
            "/api/v1/console/opportunities",
            json={**base, "name": "不能新关联停用伙伴", "quarterly_forecasts": quarters},
        )
        assert denied.status_code == 422 and "停用" in denied.text
        stale = await client.post(
            "/api/v1/console/partners",
            json={"id": partner["id"], "name": "不可覆盖", "version_no": partner["version_no"]},
        )
        assert stale.status_code == 409
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM ops.audit_log WHERE object_type='partner' AND object_id=$1::uuid", partner["id"]
            )
            == 2
        )


async def test_sales_can_search_company_partners_but_not_manage(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        response = await client.post(
            "/api/v1/auth/session", json={"account_code": "XS001", "workspace": "demo-sales-workspace"}
        )
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        assert (await client.get("/api/v1/directory/partners")).status_code == 200
        assert (await client.get("/api/v1/console/partners")).status_code == 403
        assert (await client.post("/api/v1/console/partners", json={"name": "越权"})).status_code == 403
