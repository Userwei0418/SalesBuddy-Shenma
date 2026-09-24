"""Claim directory HTTP/SQL against CI's disposable non-bypass PostgreSQL role."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio

DIRECTORY_FIELDS = {"id", "name", "industry_code", "level_code", "customer_type_code", "team_name",
                    "owner_name", "ownership_state", "claimed", "can_claim", "claim_status"}


async def company_customers(connection, count):
    operator = await actor(connection, "OPS001")
    prefix = "目录分页-" + uuid4().hex + "-"
    items = []
    for index in range(count):
        items.append(await CustomerMutationRepository().create(connection, operator, data={
            "name": prefix + f"{index:03}", "industry": "软件", "customer_type": "潜在客户",
            "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
            "contact_name": "受控联系人", "contact_title": "经理", "contact_role": "决策者",
            "contact_phone": "测试号码不应出现在目录", "company_reference": "ISOLATED-" + uuid4().hex,
        }))
    return prefix, items


async def test_company_directory_full_total_121_browse_search_empty_page_and_legacy_compatibility(connection):
    query, customers = await company_customers(connection, 121)
    # Ownership and applications must not turn the directory total into a count
    # of customers this salesperson can claim. All three states remain visible.
    await actor(connection, "XS002")
    claim = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customers[0]["id"])
    await actor(connection, "OPS001")
    await connection.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','分页验收')", claim["request_id"])
    sales = await actor(connection, "XS001")
    await connection.fetchval("SELECT security.claim_customer($1::uuid)", customers[1]["id"])
    # Ordinary customer history RLS has no rows, while this directory has 121.
    assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE name LIKE $1", query + "%") == 0
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        pages = []
        for offset in (0, 50, 100):
            response = await client.get("/api/v1/customers/claim-pool", params={"q": query, "offset": offset})
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["total"] == 121
            assert page["has_more"] == (offset < 100)
            assert page["next_offset"] == (offset + 50 if offset < 100 else None)
            assert all(set(row) == DIRECTORY_FIELDS for row in page["items"])
            pages.append(page)
        rows = [row for page in pages for row in page["items"]]
        assert [row["id"] for row in rows] == [row["id"] for row in customers]
        assert len({row["id"] for row in rows}) == 121
        assert rows[0]["ownership_state"] == "claimed" and not rows[0]["can_claim"]
        assert rows[1]["claim_status"] == "pending" and rows[1]["can_claim"]
        assert rows[2]["claim_status"] is None and rows[2]["can_claim"]
        for scope in ("company", "department"):
            legacy = await client.get("/api/v1/customers", params={"scope": scope, "q": query, "page_size": 100})
            assert legacy.status_code == 200, legacy.text
            assert legacy.json() == {"items": rows[:100], "next_cursor": None}
        beyond = await client.get("/api/v1/customers/claim-pool", params={"q": query, "offset": 200})
        assert beyond.json() == {"items": [], "total": 121, "has_more": False, "next_offset": None}
        empty = await client.get("/api/v1/customers/claim-pool", params={"q": query + "missing"})
        assert empty.json() == {"items": [], "total": 0, "has_more": False, "next_offset": None}
        search = await client.get("/api/v1/customers/claim-pool", params={"q": query + "00", "page_size": 3})
        assert search.json()["total"] == 10 and search.json()["next_offset"] == 3
        assert search.json()["items"] == rows[:3]
        assert (await client.get("/api/v1/customers/" + customers[0]["id"])).status_code == 404
        assert (await client.get("/api/v1/customers/" + customers[2]["id"])).status_code == 404
    await set_request_context(connection, sales.model_copy(update={"workspace_id": str(uuid4())}))
    foreign = await CustomerMemberRepository().claim_pool_page(connection, query=query)
    assert foreign == {"items": [], "total": 0, "has_more": False, "next_offset": None}


async def test_claim_application_changes_reference_status_but_not_total_or_ownership(connection):
    query, customers = await company_customers(connection, 2)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        before = (await client.get("/api/v1/customers/claim-pool", params={"q": query})).json()
        endpoint = "/api/v1/customers/" + customers[0]["id"] + "/claims"
        headers = {"Idempotency-Key": str(uuid4())}
        application = await client.post(endpoint, headers=headers)
        assert application.status_code == 201, application.text
        assert application.json()["status"] == "pending" and not application.json()["claimed"]
        assert (await client.post(endpoint, headers=headers)).json() == application.json()
        after = (await client.get("/api/v1/customers/claim-pool", params={"q": query})).json()
        assert after["total"] == before["total"] == 2
        assert after["items"][0]["claim_status"] == "pending"
        assert after["items"][0]["ownership_state"] == "unclaimed"
        assert (await client.get("/api/v1/customers/" + customers[0]["id"])).status_code == 404


@pytest.mark.parametrize("person", ["first", "lead"])
async def test_native_fde_login_cannot_use_claim_directory_and_keeps_existing_customer_scope(connection, person):
    _, project, people, _ = await fde_fixture(connection)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people[person])
        assert (await client.get("/api/v1/customers/claim-pool")).status_code == 403
        assert (await client.get("/api/v1/customers/claim-pool/options")).status_code == 403
        before = await client.get("/api/v1/customers", params={"scope": "mine"})
        legacy = await client.get("/api/v1/customers", params={"scope": "company"})
        assert before.status_code == legacy.status_code == 200
        assert before.json() == legacy.json()
        if person == "first":
            assert project["customer_id"] in {row["id"] for row in before.json()["items"]}


async def test_industries_states_pinyin_and_pagination_share_server_criteria(connection):
    prefix, customers = await company_customers(connection, 5)
    await actor(connection, "OPS001")
    for index, customer in enumerate(customers):
        await connection.execute("UPDATE crm.customer SET name=$2,industry_code=$3 WHERE id=$1::uuid",
            customer["id"], "商汤筛选" + prefix + str(index), "软件" if index < 4 else "金融")
    await actor(connection, "XS002")
    claim = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customers[0]["id"])
    await actor(connection, "OPS001")
    await connection.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','筛选测试')", claim["request_id"])
    await actor(connection, "XS001")
    mine = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customers[1]["id"])
    await actor(connection, "OPS001")
    await connection.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','本人筛选')", mine["request_id"])
    await actor(connection, "XS001")
    await connection.fetchval("SELECT security.claim_customer($1::uuid)", customers[2]["id"])
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        options = await client.get("/api/v1/customers/claim-pool/options")
        assert options.status_code == 200, options.text
        assert {"value": "金融", "label": "金融"} in options.json()["industries"]
        for term in ("商汤筛选" + prefix, "shangtangshaixuan", "STSX"):
            all_rows = (await client.get("/api/v1/customers/claim-pool", params={"q": term})).json()
            assert all_rows["total"] == 5, all_rows
            assert {row["id"] for row in all_rows["items"]} == {c["id"] for c in customers}
            for state, indices in [("claimed", [0, 1]), ("mine", [1]), ("pending", [2]), ("unclaimed", [2, 3])]:
                pages = []
                for offset in range(len(indices)):
                    response = await client.get("/api/v1/customers/claim-pool", params={
                        "q": term, "industry": "软件", "claim_status": state, "page_size": 1, "offset": offset})
                    assert response.status_code == 200, response.text
                    page = response.json()
                    assert page["total"] == len(indices)
                    assert page["next_offset"] == (offset + 1 if offset < len(indices) - 1 else None)
                    if state == "pending":
                        assert all(row["claim_status"] == "pending" for row in page["items"])
                    pages += page["items"]
                assert {row["id"] for row in pages} == {customers[i]["id"] for i in indices}
        empty = await client.get("/api/v1/customers/claim-pool", params={"q": "st", "industry": "不存在行业"})
        assert empty.json() == {"items": [], "total": 0, "has_more": False, "next_offset": None}
        assert (await client.get("/api/v1/customers/" + customers[0]["id"])).status_code == 404
        # Percent and underscore are literal customer-name characters, not SQL wildcards.
        assert (await client.get("/api/v1/customers/claim-pool", params={"q": "%_"})).json()["total"] == 0
    sales = await actor(connection, "XS001")
    repository = CustomerMemberRepository()
    # Cached phonetic keys must not preserve access after a workspace change.
    await set_request_context(connection, sales.model_copy(update={"workspace_id": str(uuid4())}))
    assert (await repository.claim_pool_page(connection, query="shangtang"))["total"] == 0
    assert (await repository.claim_pool_options(connection))["industries"] == [{"value": "", "label": "全部行业"}]
    await actor(connection, "OPS001")
    await connection.execute("UPDATE crm.customer SET name='改名后客户' WHERE id=ANY($1::uuid[])", [c["id"] for c in customers])
    await actor(connection, "XS001")
    assert (await repository.claim_pool_page(connection, query="shangtangshaixuan"))["total"] == 0
