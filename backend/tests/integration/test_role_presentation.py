"""Real tenant/RLS, HTTP and current-account role label projections."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/console/companies/role-labels"


async def test_company_display_configuration_is_versioned_audited_and_does_not_change_permissions(connection):
    admin = await actor(connection, "ADMIN001")
    async with fixture_owner(connection):
        await connection.execute("UPDATE platform.workspace SET attributes=attributes||'{\"keep_fixture\":true}' WHERE id=$1::uuid", admin.workspace_id)
        other = uuid4()
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'第二家公司')", other, uuid4().hex)
    async with await client_for(connection, auth_mode="demo") as client:
        await sign_in(client, "ADMIN001")
        before = (await client.get(PATH)).json()
        assert before["labels"] == {"sales": "一线销售"}
        permissions = (await client.get("/api/v1/console/auth/me")).json()["actor"]["permissions"]
        body = {"labels": {"sales": "客户经理"}, "version_no": before["version_no"]}
        key = {"Idempotency-Key": str(uuid4())}
        response = await client.put(PATH, json=body, headers=key)
        assert response.status_code == 200, response.text
        assert response.json() == {"labels": {"sales": "客户经理"}, "version_no": before["version_no"]+1}
        assert (await client.put(PATH, json=body, headers=key)).json() == response.json()
        assert (await client.put(PATH, json=body, headers={"Idempotency-Key": str(uuid4())})).status_code == 409
        assert (await client.get("/api/v1/console/auth/me")).json()["actor"]["permissions"] == permissions
        org = (await client.get("/api/v1/console/organization")).json()
        assert next(r["name"] for r in org["roles"] if r["code"] == "sales") == "客户经理"
        assert next(r["name"] for r in org["roles"] if r["code"] == "fde") == "FDE"
        await set_request_context(connection, admin)
        audit = await connection.fetch("SELECT before_snapshot,after_snapshot FROM ops.audit_log WHERE action_code='company.role_labels'")
        assert len(audit) == 1 and audit[0]["after_snapshot"]["labels"] == {"sales": "客户经理"}
        assert await connection.fetchval("SELECT attributes->>'keep_fixture' FROM platform.workspace WHERE id=$1::uuid", admin.workspace_id) == "true"
        async with fixture_owner(connection):
            assert await connection.fetchval("SELECT attributes ? 'business_role_labels' FROM platform.workspace WHERE id=$1", other) is False
        await business_login(client, "XS001")
        me = (await client.get("/api/v1/auth/me")).json()
        assert me["role"] == "sales" and me["role_name"] == "客户经理"
        recipients = (await client.get("/api/v1/tasks/recipients")).json()["items"]
        assert next(r["role_name"] for r in recipients if r["account_code"] == "XS001") == "客户经理"
        assert (await client.put(PATH, json=body)).status_code == 403
        await sign_in(client, "OPS001")
        assert (await client.put(PATH, json=body)).status_code == 403
        # A caller-supplied other-company header does not supply a grant.
        assert (await client.put(PATH, json=body, headers={"X-Company-ID": str(other)})).status_code == 403


async def test_product_titles_follow_active_role_departments_and_refresh_without_changing_codes(connection):
    admin = await actor(connection, "ADMIN001")
    async with fixture_owner(connection):
        await connection.execute("UPDATE platform.workspace SET attributes=attributes||'{\"business_role_labels\":{\"sales\":\"客户经理\"}}' WHERE id=$1::uuid", admin.workspace_id)
        await connection.execute("UPDATE platform.team SET attributes=attributes||'{\"kind\":\"product_sales\"}' WHERE workspace_id=$1::uuid", admin.workspace_id)
    async with await client_for(connection, auth_mode="demo") as client:
        for code, role, name in [("XS001", "sales", "产品销售"), ("ZJ001", "supervisor", "产品销售主管")]:
            login = await client.post("/api/v1/auth/session", json={"account_code": code, "workspace": "demo-sales-workspace"})
            assert login.status_code == 200, login.text
            assert login.json()["actor"]["role_name"] == name and login.json()["actor"]["role"] == role
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            assert (await client.get("/api/v1/auth/me")).json()["role_name"] == name
            refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})
            assert refreshed.status_code == 200 and refreshed.json()["actor"]["role_name"] == name
        await business_login(client, "XS001")
        members = (await client.get("/api/v1/tasks/recipients")).json()["items"]
        assert next(r["role_name"] for r in members if r["account_code"] == "XS001") == "产品销售"
        assert next(r["role_name"] for r in members if r["account_code"] == "ZJ001") == "产品销售主管"
        # Identity projection rejects a foreign tenant even with a known UUID.
        assert await IdentityRepository().company_identity(connection, str(uuid4())) == {"company_code": None, "company_name": None}


@pytest.mark.parametrize("labels", [{"sales": ""}, {"sales": " 客户经理"}, {"sales": "管理员"},
                                     {"sales": "客户经理", "administrator": "客户经理"}, {"sales": True}])
async def test_only_explicit_sales_names_are_accepted(connection, labels):
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        before = (await client.get(PATH)).json()
        response = await client.put(PATH, json={"labels": labels, "version_no": before["version_no"]})
        assert response.status_code == 422, response.text
        assert (await client.get(PATH)).json() == before
