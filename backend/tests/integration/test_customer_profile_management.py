"""Customer master HTTP writes with real PostgreSQL, RLS, receipts and jobs."""

from uuid import uuid4

import pytest

from tests.integration.test_company_tenants import provision_fixture
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor, create_customer

pytestmark = pytest.mark.asyncio


async def test_profile_save_readback_clear_conflict_and_idempotency(connection):
    customer = await create_customer(connection)
    cid = customer["id"]
    await actor(connection, "XS001")
    claim = await connection.fetchval("SELECT security.claim_customer($1::uuid)", cid)
    await actor(connection, "OPS001")
    await connection.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','测试')", claim["request_id"])
    await connection.execute(
        "UPDATE crm.customer SET customer_code='TEST-CODE',external_customer_id='TEST-CRM' WHERE id=$1::uuid", cid
    )
    path = "/api/v1/console/customers/" + cid
    async with await client_for(connection) as client:
        await sign_in(client)
        before = (await client.get(path + "/overview")).json()
        assert before["customer_code"] == "TEST-CODE" and before["external_customer_id"] == "TEST-CRM"
        body = dict(
            version_no=before["version_no"],
            operation_type="渠道伙伴",
            cooperation_years=0,
            main_business="智能设备\n系统集成",
            customer_budget="50 万，待确认",
            demand_summary="企业知识库",
            next_action="下周确认需求",
            contact_name="林经理",
            contact_phone="13800000000",
            contact_title="业务经理",
            contact_email="test@example.invalid",
            industry="",
            partner_name="测试伙伴",
        )
        key = {"Idempotency-Key": str(uuid4())}
        saved = await client.patch(path, json=body, headers=key)
        assert saved.status_code == 200, saved.text
        replay = await client.patch(path, json=body, headers=key)
        assert replay.status_code == 200 and replay.json() == saved.json()
        after = (await client.get(path + "/overview")).json()
        assert after["cooperation_years"] == 0 and after["main_business"] == body["main_business"]
        assert after["contact_name"] == "林经理" and after["contact_email"] == body["contact_email"]
        assert after["version_no"] == before["version_no"] + 1
        assert after["ownership"] == before["ownership"] and after["company_reference"] == before["company_reference"]
        events = await connection.fetch("SELECT changes FROM crm.business_change WHERE customer_id=$1::uuid", cid)
        assert len(events) == 1
        years = next(change for change in events[0]["changes"] if change["label"] == "合作年限")
        assert years["before"] == "未填写" and years["after"] == "0"
        jobs = await connection.fetch(
            "SELECT job_type FROM ops.job WHERE payload->>'trigger_type'='customer.updated' "
            "AND (aggregate_id=$1::uuid OR payload->>'customer_id'=$1::text)",
            cid,
        )
        assert sorted(r["job_type"] for r in jobs) == ["battle_map.review", "customer.risk.review"]
        conflict = await client.patch(
            path, json={**body, "next_action": "过时修改"}, headers={"Idempotency-Key": str(uuid4())}
        )
        assert conflict.status_code == 409, conflict.text
        assert (await client.get(path + "/overview")).json()["next_action"] == body["next_action"]
        unchanged = await client.patch(path, json={"version_no": after["version_no"], "cooperation_years": 0})
        assert unchanged.status_code == 200 and unchanged.json()["changed"] is False
        cleared = await client.patch(
            path,
            json={
                "version_no": after["version_no"],
                "cooperation_years": None,
                "customer_budget": "",
                "partner_name": "",
                "contact_phone": "",
                "contact_role": "",
            },
        )
        assert cleared.status_code == 200, cleared.text
        final = (await client.get(path + "/overview")).json()
        assert final["cooperation_years"] is None and final["customer_budget"] == ""
        assert final["primary_partner_name"] == "" and final["contact_phone"] == "" and final["contact_role"] is None
        assert final["version_no"] == after["version_no"] + 1
        assert (
            await connection.fetchval("SELECT count(*) FROM crm.business_change WHERE customer_id=$1::uuid", cid) == 2
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("customer_code", "hijack"),
        ("external_customer_id", "hijack"),
        ("company_reference", "hijack"),
        ("owner_user_ref_id", str(uuid4())),
        ("workspace_id", str(uuid4())),
        ("attributes", {}),
        ("cooperation_years", -1),
        ("cooperation_years", 1.25),
        ("name", "   "),
        ("industry", None),
    ],
)
async def test_profile_rejects_readonly_and_invalid_values(connection, field, value):
    customer = await create_customer(connection)
    path = "/api/v1/console/customers/" + customer["id"]
    async with await client_for(connection) as client:
        await sign_in(client)
        before = (await client.get(path + "/overview")).json()
        r = await client.patch(path, json={"version_no": before["version_no"], field: value})
        assert r.status_code == 422, r.text
        assert (await client.get(path + "/overview")).json() == before


async def test_profile_company_selection_blocks_cross_company_read_write(connection):
    customer = await create_customer(connection)
    cid = customer["id"]
    _, _, wid, _ = await provision_fixture(connection)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        path = "/api/v1/console/customers/" + cid
        before = (await client.get(path + "/overview")).json()
        client.headers["X-Company-ID"] = wid
        assert (await client.get(path + "/overview")).status_code == 404
        denied = await client.patch(path, json={"version_no": before["version_no"], "main_business": "跨公司写入"})
        assert denied.status_code == 404, denied.text
        client.headers.pop("X-Company-ID")
        assert (await client.get(path + "/overview")).json() == before


async def test_profile_ordinary_sales_cannot_use_management_write(connection):
    customer = await create_customer(connection)
    async with await client_for(connection) as client:
        await sign_in(client)
        team = (await client.get("/api/v1/console/organization")).json()["departments"][0]["id"]
        code = "PROFILE" + uuid4().hex[:10]
        created = await client.post(
            "/api/v1/console/accounts",
            json={
                "account_code": code,
                "display_name": "普通销售",
                "team_id": team,
                "roles": ["sales"],
                "temporary_password": "Only-Isolated-2026",
            },
        )
        assert created.status_code == 201, created.text
        await sign_in(client, code, "Only-Isolated-2026")
        changed = await client.post(
            "/api/v1/console/auth/password",
            json={
                "old_password": "Only-Isolated-2026",
                "new_password": "Changed-Isolated-2026",
            },
        )
        assert changed.status_code == 200, changed.text
        path = "/api/v1/console/customers/" + customer["id"]
        assert (await client.get(path + "/overview")).status_code == 403
        assert (await client.patch(path, json={"version_no": 1, "operation_type": "拒绝"})).status_code == 403
