"""Real HTTP creation controls for non-administrator console users."""
import pytest

from sales_backend.db import set_request_context
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import create_customer
from tests.integration.test_sales_opportunity_permissions import PASSWORD, account_fixture, opportunity_payload

pytestmark = pytest.mark.asyncio
OPTIONS = "/api/v1/opportunities/create-options"
CONSOLE = "/api/v1/console/opportunities"
ENTRY = dict(permission="access.console", effect="allow", scope="workspace")


async def test_self_console_options_and_create_need_no_organization_permission(connection):
    customer = await create_customer(connection)
    user, teams, admin = await account_fixture(connection, ["sales"])
    await grant(connection, admin, user["id"], [ENTRY])
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        legacy = await client.get(OPTIONS)
        assert legacy.status_code == 200 and set(legacy.json()) == {"teams", "default_team_id"}
        extended = await client.get(OPTIONS + "?include_owners=true")
        assert extended.status_code == 200, extended.text
        choices = extended.json()
        assert [u["id"] for u in choices["owners"]] == [user["id"]]
        assert choices["default_owner_id"] == user["id"]
        assert choices["teams"] == legacy.json()["teams"]
        assert set(choices["owners"][0]) == {"id", "name", "account_code", "teams", "default_team_id"}
        pool = await client.get("/api/v1/customers/claim-pool")
        assert pool.status_code == 200 and any(c["id"] == customer["id"] for c in pool.json()["items"])
        for payload in [opportunity_payload(), {**opportunity_payload(), "owner_user_ref_id": user["id"]}]:
            response = await client.post(CONSOLE, json={**payload, "customer_id": customer["id"], "owner_team_id": teams["sales"]})
            assert response.status_code == 201, response.text
            row = await connection.fetchrow("SELECT owner_user_ref_id::text,owner_team_id::text FROM crm.opportunity WHERE id=$1::uuid", response.json()["id"])
            assert tuple(row) == (user["id"], teams["sales"])
        explicit_null = await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"], "owner_user_ref_id": None})
        assert explicit_null.status_code == 422, explicit_null.text
        assert "请选择商机负责人" in explicit_null.text
        await grant(connection, admin, user["id"], [ENTRY, dict(permission="opportunity.create", effect="deny")], version=1)
        assert (await client.get(OPTIONS + "?include_owners=true")).status_code == 403
        assert (await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"]})).status_code == 403


async def test_delegated_owner_choices_require_both_create_scopes_and_writes_recheck(connection):
    customer = await create_customer(connection)
    user, _, admin = await account_fixture(connection, ["sales"])
    allowed, a, _ = await account_fixture(connection, ["sales"])
    excluded, _, _ = await account_fixture(connection, ["sales"])
    permissions = [ENTRY, *[dict(permission=p, effect="allow", scope="teams", team_ids=[a["sales"]]) for p in
                           ("opportunity.create", "opportunity.create_for_others")]]
    await grant(connection, admin, user["id"], permissions)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        data = (await client.get(OPTIONS + "?include_owners=true")).json()
        assert {u["id"] for u in data["owners"]} == {user["id"]}
        # Existing INSERT RETURNING obeys read RLS as well; never broaden it.
        unreadable = await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"], "owner_user_ref_id": allowed["id"]})
        assert unreadable.status_code == 403, unreadable.text
        await grant(connection, admin, user["id"], [*permissions,
            dict(permission="opportunity.read", effect="allow", scope="teams", team_ids=[a["sales"]])], version=1)
        data = (await client.get(OPTIONS + "?include_owners=true")).json()
        assert {u["id"] for u in data["owners"]} == {user["id"], allowed["id"]}
        result = await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"], "owner_user_ref_id": allowed["id"]})
        assert result.status_code == 201, result.text
        wrong = await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"], "owner_user_ref_id": excluded["id"]})
        assert wrong.status_code == 403, wrong.text
        await grant(connection, admin, user["id"], [ENTRY, dict(permission="opportunity.create_for_others", effect="deny")], version=2)
        data = (await client.get(OPTIONS + "?include_owners=true")).json()
        assert [u["id"] for u in data["owners"]] == [user["id"]]
        stale = await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"], "owner_user_ref_id": allowed["id"]})
        assert stale.status_code == 403, stale.text


async def test_explicitly_authorized_fde_can_create_for_self_without_fake_sales_appointment(connection):
    customer = await create_customer(connection)
    user, teams, admin = await account_fixture(connection, ["fde"])
    await grant(connection, admin, user["id"], [ENTRY, dict(permission="opportunity.create", effect="allow", scope="self")])
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        data = (await client.get(OPTIONS + "?include_owners=true")).json()
        assert [u["id"] for u in data["owners"]] == [user["id"]]
        response = await client.post(CONSOLE, json={**opportunity_payload(), "customer_id": customer["id"], "owner_team_id": teams["fde"]})
        assert response.status_code == 201, response.text
    await set_request_context(connection, admin)
    assert await connection.fetchval("SELECT count(*) FROM platform.role_binding WHERE user_ref_id=$1::uuid AND role_code='sales'", user["id"]) == 0
    assert (await connection.fetchval("SELECT owner_user_ref_id FROM crm.opportunity WHERE id=$1::uuid", response.json()["id"])).hex == user["id"].replace("-", "")
