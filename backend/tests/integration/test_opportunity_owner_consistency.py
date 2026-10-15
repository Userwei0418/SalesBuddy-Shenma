"""A delegated opportunity keeps the explicitly selected owner/team pairing."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture, opportunity_payload

pytestmark = pytest.mark.asyncio
OPTIONS = "/api/v1/opportunities/create-options?include_owners=true"
CONSOLE = "/api/v1/console/opportunities"


async def two_team_owner(connection):
    """Build current appointments through the account service, without seed SQL."""
    owner, teams, admin = await account_fixture(connection, ["sales"])
    secondary = await OperationsAccountRepository().save_department(connection, admin, None, dict(
        code=uuid4().hex, name="商机归属一致性-兼任团队", kind="sales", status="active", parent_team_id=None,
    ))
    memberships = [dict(team_id=team, roles=["sales"]) for team in (teams["sales"], secondary["id"])]
    data = dict(
        version_no=owner["version_no"], display_name="商机归属一致性销售", status="active",
        roles=["sales"], company_roles=[], team_id=teams["sales"], memberships=memberships,
    )
    updated = await OperationsAccountService().update(connection, admin, owner["id"], data)
    return {**owner, **updated}, teams["sales"], secondary["id"], admin, {**data, **updated}


async def selected_owner(client, user_id):
    response = await client.get(OPTIONS)
    assert response.status_code == 200, response.text
    return next(person for person in response.json()["owners"] if person["id"] == user_id)


async def saved_owner(connection, opportunity_id):
    row = await connection.fetchrow(
        "SELECT owner_user_ref_id::text,owner_team_id::text FROM crm.opportunity WHERE id=$1::uuid",
        opportunity_id,
    )
    assert row is not None
    return tuple(row)


async def test_stale_delegated_team_is_rejected_instead_of_silently_using_new_primary(connection):
    customer = await create_customer(connection)
    owner, original_team, new_primary, admin, account_data = await two_team_owner(connection)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        initial = await selected_owner(client, owner["id"])
        assert [team["id"] for team in initial["teams"]] == [original_team]
        assert initial["default_team_id"] == original_team
        body = {**opportunity_payload(), "customer_id": customer["id"],
                "owner_user_ref_id": owner["id"], "owner_team_id": original_team}

        # The operator's open form still contains A after another legitimate
        # account update makes B the primary team. A remains a valid appointment.
        await set_request_context(connection, admin)
        await OperationsAccountService().update(
            connection, admin, owner["id"], {**account_data, "team_id": new_primary},
        )
        before = await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
        )
        stale = await client.post(CONSOLE, json=body, headers={"Idempotency-Key": str(uuid4())})
        assert stale.status_code == 422, stale.text
        assert "团队" in stale.text
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
        ) == before

        refreshed = await selected_owner(client, owner["id"])
        assert [team["id"] for team in refreshed["teams"]] == [new_primary]
        assert refreshed["default_team_id"] == new_primary
        created = await client.post(CONSOLE, json={**body, "owner_team_id": new_primary},
                                    headers={"Idempotency-Key": str(uuid4())})
        assert created.status_code == 201, created.text
        assert await saved_owner(connection, created.json()["id"]) == (owner["id"], new_primary)
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
        ) == before + 1


@pytest.mark.parametrize("team_selection", ["current_primary", "omitted", "secondary"])
async def test_delegated_team_contract_preserves_primary_default_and_never_substitutes_explicit_team(
    connection, team_selection,
):
    customer = await create_customer(connection)
    owner, primary, secondary, _, _ = await two_team_owner(connection)
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        choice = await selected_owner(client, owner["id"])
        assert [team["id"] for team in choice["teams"]] == [primary]
        body = {**opportunity_payload(), "customer_id": customer["id"], "owner_user_ref_id": owner["id"]}
        if team_selection != "omitted":
            body["owner_team_id"] = primary if team_selection == "current_primary" else secondary
        else:
            body.pop("owner_team_id", None)
        before = await connection.fetchval(
            "SELECT count(*) FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
        )
        response = await client.post(CONSOLE, json=body, headers={"Idempotency-Key": str(uuid4())})
        if team_selection == "secondary":
            # A valid secondary membership is not currently a delegated form
            # choice. Reject it; do not save another team under a successful 201.
            assert response.status_code == 422, response.text
            assert "团队" in response.text
            assert await connection.fetchval(
                "SELECT count(*) FROM crm.opportunity WHERE customer_id=$1::uuid", customer["id"],
            ) == before
        else:
            assert response.status_code == 201, response.text
            assert await saved_owner(connection, response.json()["id"]) == (owner["id"], primary)
