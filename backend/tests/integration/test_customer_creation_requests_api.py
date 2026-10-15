"""Customer creation HTTP flows on the disposable non-bypass PostgreSQL role.

The normal routers, authentication, repositories, transactions and RLS all run.
The shared integration connection rolls every test back; no network or model
adapter is stubbed in place of a business result.
"""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login

pytestmark = pytest.mark.asyncio

REQUESTS = "/api/v1/customers/creation-requests"
QUEUE = "/api/v1/console/customer-creation-requests"


def mutation_headers():
    return {"Idempotency-Key": str(uuid4())}


async def request_payload(client, *, name=None):
    response = await client.get("/api/v1/directory/teams", params={"purpose": "creation_request"})
    assert response.status_code == 200, response.text
    teams = response.json()["teams"]
    assert teams, "A sales applicant must have a selectable team without direct customer.create permission"
    return {
        "name": name or "隔离申请客户-" + uuid4().hex,
        "company_reference": "ISOLATED-REQUEST-" + uuid4().hex,
        "industry": "软件",
        "customer_type": "潜在客户",
        "level_code": "Tier-2",
        "source": "销售自拓",
        "target_team": teams[0]["name"],
        "target_team_id": teams[0]["id"],
        "partner_name": "",
        "contact_name": "隔离联系人",
        "contact_title": "采购经理",
        "contact_role": "影响者",
        "contact_phone": "",
        "contact_email": "",
    }


async def submit(client, payload, *, headers=None):
    response = await client.post(REQUESTS, json=payload, headers=headers or mutation_headers())
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "pending" and result["created_customer_id"] is None
    return result


async def mine(client, **params):
    # This exact static URL must resolve before /customers/{customer_id}.
    response = await client.get(REQUESTS, params=params)
    assert response.status_code == 200, response.text
    page = response.json()
    assert isinstance(page["items"], list)
    assert isinstance(page["total"], int)
    return page


async def customer_count(connection, name):
    await actor(connection, "OPS001")
    return await connection.fetchval(
        "SELECT count(*) FROM crm.customer WHERE workspace_id=common.current_workspace_id() "
        "AND normalized_name=lower(regexp_replace(btrim($1),'\\s+','','g')) AND deleted_at IS NULL",
        name,
    )


async def test_submit_list_routing_and_sales_cannot_approve_or_create_customer(connection):
    account, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as sales, await client_for(connection) as operations:
        await native_login(sales, account)
        payload = await request_payload(sales)
        created = await submit(sales, payload)

        page = await mine(sales, q=payload["name"], status="pending")
        assert page["total"] == 1
        assert [item["id"] for item in page["items"]] == [created["id"]]
        assert page["items"][0]["applicant_user_ref_id"] == account["id"]
        assert await customer_count(connection, payload["name"]) == 0

        denied = await sales.post(
            QUEUE + "/" + created["id"] + "/decision",
            json={"decision": "approved", "reason": "不可自行审批"},
            headers=mutation_headers(),
        )
        assert denied.status_code == 403, denied.text
        assert (await sales.get(QUEUE)).status_code == 403
        direct = await sales.post("/api/v1/customers", json=payload, headers=mutation_headers())
        assert direct.status_code == 403, direct.text

        await sign_in(operations)
        queue = await operations.get(QUEUE, params={"q": payload["name"]})
        assert queue.status_code == 200, queue.text
        assert queue.json()["total"] == 1
        assert queue.json()["items"][0]["id"] == created["id"]
        assert await customer_count(connection, payload["name"]) == 0


async def test_approval_and_receipt_replay_create_one_unclaimed_customer(connection):
    account, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as sales, await client_for(connection) as operations:
        await native_login(sales, account)
        payload = await request_payload(sales)
        submit_key = mutation_headers()
        created = await submit(sales, payload, headers=submit_key)
        replay = await sales.post(REQUESTS, json=payload, headers=submit_key)
        assert replay.status_code == 201 and replay.json() == created, replay.text
        assert await customer_count(connection, payload["name"]) == 0

        await sign_in(operations)
        path = QUEUE + "/" + created["id"] + "/decision"
        decision = {"decision": "approved", "reason": "已核实公司建档编号"}
        decision_key = mutation_headers()
        approved = await operations.post(path, json=decision, headers=decision_key)
        assert approved.status_code == 200, approved.text
        result = approved.json()
        assert result["status"] == "approved"
        assert result["created_customer_id"] == result["customer"]["id"]
        repeated = await operations.post(path, json=decision, headers=decision_key)
        assert repeated.status_code == 200 and repeated.json() == result, repeated.text
        assert await customer_count(connection, payload["name"]) == 1

        customer_id = result["created_customer_id"]
        row = await connection.fetchrow(
            "SELECT c.company_reference,c.owner_user_ref_id,o.state "
            "FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id "
            "AND o.workspace_id=c.workspace_id WHERE c.id=$1::uuid",
            customer_id,
        )
        assert row["company_reference"] == payload["company_reference"]
        assert row["owner_user_ref_id"] is None and row["state"] == "unclaimed"
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.contact WHERE customer_id=$1::uuid AND deleted_at IS NULL", customer_id
        ) == 1

        page = await mine(sales, q=payload["name"], status="approved")
        assert page["total"] == 1 and page["items"][0]["created_customer_id"] == customer_id
        withdrawn = await sales.post(REQUESTS + "/" + created["id"] + "/withdraw", headers=mutation_headers())
        assert withdrawn.status_code == 409, withdrawn.text
        assert await customer_count(connection, payload["name"]) == 1


async def test_rejection_requires_reason_and_does_not_create_customer(connection):
    account, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as sales, await client_for(connection) as operations:
        await native_login(sales, account)
        payload = await request_payload(sales)
        created = await submit(sales, payload)
        await sign_in(operations)
        path = QUEUE + "/" + created["id"] + "/decision"
        for body in ({"decision": "rejected"}, {"decision": "rejected", "reason": "   "}):
            invalid = await operations.post(path, json=body, headers=mutation_headers())
            assert invalid.status_code == 422, invalid.text
        pending = await mine(sales, q=payload["name"], status="pending")
        assert pending["total"] == 1
        assert await customer_count(connection, payload["name"]) == 0

        rejected = await operations.post(
            path, json={"decision": "rejected", "reason": "公司编号尚需补充核实"}, headers=mutation_headers()
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["created_customer_id"] is None
        page = await mine(sales, q=payload["name"], status="rejected")
        assert page["total"] == 1
        assert page["items"][0]["decision_reason"] == "公司编号尚需补充核实"
        assert await customer_count(connection, payload["name"]) == 0


async def test_other_applicants_cannot_query_or_withdraw_request_and_owner_can_withdraw(connection):
    first, _, _ = await account_fixture(connection, ["sales"])
    second, _, admin = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as owner, await client_for(connection) as other, await client_for(connection) as operations:
        await native_login(owner, first)
        payload = await request_payload(owner)
        created = await submit(owner, payload)
        await native_login(other, second)
        page = await mine(other, q=payload["name"])
        assert page["total"] == 0 and page["items"] == []
        denied = await other.post(REQUESTS + "/" + created["id"] + "/withdraw", headers=mutation_headers())
        assert denied.status_code == 404, denied.text

        identity = await IdentityRepository().find_actor_by_id(
            connection, workspace_id=admin.workspace_id, user_id=second["id"], role="sales"
        )
        assert identity is not None
        await set_request_context(connection, identity.context)
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.customer_creation_request WHERE id=$1::uuid", created["id"]
        ) == 0
        await set_request_context(connection, identity.context.model_copy(update={"workspace_id": str(uuid4())}))
        assert await connection.fetchval("SELECT count(*) FROM crm.customer_creation_request") == 0

        withdraw_key = mutation_headers()
        path = REQUESTS + "/" + created["id"] + "/withdraw"
        withdrawn = await owner.post(path, headers=withdraw_key)
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["status"] == "cancelled"
        replay = await owner.post(path, headers=withdraw_key)
        assert replay.status_code == 200 and replay.json() == withdrawn.json(), replay.text
        page = await mine(owner, q=payload["name"], status="cancelled")
        assert page["total"] == 1 and page["items"][0]["decision_reason"] == "申请人撤回"

        await sign_in(operations)
        queue = await operations.get(QUEUE, params={"q": payload["name"], "status": "pending"})
        assert queue.status_code == 200 and queue.json()["total"] == 0, queue.text
        blocked = await operations.post(
            QUEUE + "/" + created["id"] + "/decision",
            json={"decision": "approved", "reason": "已撤回不能再审批"}, headers=mutation_headers(),
        )
        assert blocked.status_code == 409, blocked.text
        assert await customer_count(connection, payload["name"]) == 0
