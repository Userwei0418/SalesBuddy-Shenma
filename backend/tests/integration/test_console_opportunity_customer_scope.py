"""Console opportunity reads stay independent of full customer visibility.

Fixtures use normal company-admin HTTP writes for the browser regression. Only
cross-company and deleted historical states use the disposable DB owner helper;
all assertions run under the real restricted role and actual HTTP authorization.
"""

import csv
import io
from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import create_customer
from tests.integration.test_sales_opportunity_permissions import PASSWORD, account_fixture, opportunity_payload

pytestmark = pytest.mark.asyncio
BASE = "/api/v1/console/opportunities"
ENTRY = dict(permission="access.console", effect="allow", scope="workspace")
EXPORT = dict(permission="opportunity.export", effect="allow", scope="self")
NO_CREATE = dict(permission="opportunity.create", effect="deny")


async def case(connection):
    await assert_restricted(connection)
    customer = await create_customer(connection)
    user, _, admin = await account_fixture(connection, ["sales"])
    other, _, _ = await account_fixture(connection, ["sales"])
    marker = "console-own-" + uuid4().hex
    own = []
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        for index, owner in enumerate([user, user, other]):
            payload = {**opportunity_payload(), "name": f"{marker}-{index}",
                       "customer_id": customer["id"], "owner_user_ref_id": owner["id"],
                       "amount": str((index + 1) * 1000)}
            response = await client.post(BASE, json=payload)
            assert response.status_code == 201, response.text
            row = {**response.json(), "name": payload["name"], "amount": Decimal(payload["amount"])}
            if owner is user:
                own.append(row)
            else:
                excluded = row
    await set_request_context(connection, admin)
    parent = await connection.fetchrow("SELECT c.owner_user_ref_id,o.state FROM crm.customer c "
                                      "JOIN crm.customer_ownership o ON o.customer_id=c.id "
                                      "WHERE c.id=$1::uuid", customer["id"])
    assert parent["owner_user_ref_id"] is None and parent["state"] == "unclaimed"
    await grant(connection, admin, user["id"], [ENTRY, EXPORT, NO_CREATE])
    return customer, user, admin, marker, own, excluded


def csv_rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


async def test_unclaimed_customer_does_not_hide_owned_console_opportunities(connection):
    customer, user, _, marker, own, excluded = await case(connection)
    expected = {row["id"] for row in own}
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        mine = (await client.get("/api/v1/console/permissions/me")).json()["permissions"]
        assert mine["access.console"] and mine["opportunity.read"] and not mine["opportunity.create"]
        # An owned opportunity does not imply full parent-customer access.
        assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM crm.customer WHERE id=$1::uuid)", customer["id"])
        assert (await client.get("/api/v1/console/customers/" + customer["id"])).status_code == 404
        for query in (None, marker, customer["name"]):
            result = await client.get(BASE, params={} if query is None else {"q": query})
            assert result.status_code == 200, result.text
            body = result.json()
            assert {r["id"] for r in body["items"]} == expected
            assert body["total"] == 2 and Decimal(str(body["total_amount"])) == 3000
            assert sum(s["count"] for s in body["stages"]) == 2
            assert sum(Decimal(str(s["amount"])) for s in body["stages"]) == 3000
            assert all(r["customer_name"] == customer["name"] and r["owner_user_ref_id"] == user["id"]
                       and r["can_edit"] is True for r in body["items"])
        pages = [(await client.get(BASE, params={"q": marker, "limit": 1, "offset": n})).json() for n in (0, 1, 5)]
        assert {p["items"][0]["id"] for p in pages[:2]} == expected
        assert pages[2]["items"] == []
        assert all(p["total"] == 2 and Decimal(str(p["total_amount"])) == 3000 for p in pages)
        for row in own:
            detail = await client.get(BASE + "/" + row["id"])
            assert detail.status_code == 200, detail.text
            assert detail.json()["customer_name"] == customer["name"]
            assert detail.json()["owner_user_ref_id"] == user["id"] and detail.json()["can_edit"] is True
        assert (await client.get(BASE + "/" + excluded["id"])).status_code == 404
        exported = csv_rows(await client.get(BASE + "/export", params={"q": customer["name"]}))
        assert {r["商机名称"] for r in exported} == {r["name"] for r in own}
        assert all(r["关联客户"] == customer["name"] for r in exported)
        assert sum(Decimal(r["ACV元"]) for r in exported) == 3000


async def test_customer_reference_deny_preserves_owned_opportunity_but_redacts_parent(connection):
    customer, user, admin, marker, own, _ = await case(connection)
    await grant(connection, admin, user["id"], [ENTRY, EXPORT, NO_CREATE, *[
        dict(permission=permission, effect="deny") for permission in
        ("customer.reference", "opportunity.update", "opportunity.quote_create")
    ]], version=1)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        listing = await client.get(BASE, params={"q": marker})
        assert listing.status_code == 200, listing.text
        assert {r["id"] for r in listing.json()["items"]} == {r["id"] for r in own}
        assert all(r["customer_name"] is None and not r["can_edit"] and not r["can_quote"]
                   for r in listing.json()["items"])
        assert (await client.get(BASE, params={"q": customer["name"]})).json()["total"] == 0
        detail = await client.get(BASE + "/" + own[0]["id"])
        assert detail.status_code == 200 and detail.json()["customer_name"] is None
        assert not detail.json()["can_edit"] and not detail.json()["can_quote"]
        exported = csv_rows(await client.get(BASE + "/export", params={"q": marker}))
        assert len(exported) == 2 and all(not r["关联客户"] for r in exported)
        assert customer["name"] not in listing.text and customer["name"] not in detail.text
        assert (await client.get("/api/v1/console/customers/" + customer["id"])).status_code == 404
        # Explicit revoke wins over the native sales role and the stored objects.
        await grant(connection, admin, user["id"], [ENTRY, NO_CREATE, *[
            dict(permission=p, effect="deny") for p in ("opportunity.read", "opportunity.export")
        ]], version=2)
        assert (await client.get(BASE)).status_code == 403
        assert (await client.get(BASE + "/" + own[0]["id"])).status_code == 403
        assert (await client.get(BASE + "/export")).status_code == 403


async def test_deleted_parent_and_foreign_rows_do_not_leak_through_reference_fallback(connection):
    customer, user, admin, marker, own, excluded = await case(connection)
    async with fixture_owner(connection):
        await connection.execute("UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1::uuid", customer["id"])
        await connection.execute("UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid", own[1]["id"])
        foreign = uuid4()
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离公司')",
                                 foreign, str(foreign))
        foreign_user = await connection.fetchval(
            "INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name) VALUES($1,$2,'隔离外公司用户') RETURNING id",
            foreign, str(uuid4()),
        )
        foreign_customer = await connection.fetchval(
            "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id) VALUES($1,$2,$2,$3) RETURNING id",
            foreign, customer["name"], foreign_user,
        )
        foreign_opportunity = await connection.fetchval(
            "INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount) VALUES($1,$2,$3,999999) RETURNING id",
            foreign, foreign_customer, marker + "-foreign",
        )
    await set_request_context(connection, admin)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        result = await client.get(BASE, params={"q": marker})
        assert result.status_code == 200, result.text
        data = result.json()
        assert [r["id"] for r in data["items"]] == [own[0]["id"]]
        assert data["total"] == 1 and Decimal(str(data["total_amount"])) == 1000
        assert data["items"][0]["customer_name"] is None
        assert (await client.get(BASE, params={"q": customer["name"]})).json()["total"] == 0
        for identifier in (own[1]["id"], excluded["id"], str(foreign_opportunity)):
            assert (await client.get(BASE + "/" + identifier)).status_code == 404
        detail = await client.get(BASE + "/" + own[0]["id"])
        assert detail.status_code == 200 and detail.json()["customer_name"] is None
        exported = csv_rows(await client.get(BASE + "/export", params={"q": marker}))
        assert len(exported) == 1 and exported[0]["商机名称"] == own[0]["name"] and not exported[0]["关联客户"]
