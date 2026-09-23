"""Actual SQL state transitions under the isolated non-bypass runtime role."""

from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.identity import IdentityRepository

pytestmark = pytest.mark.asyncio


async def actor(connection, code):
    row = await connection.fetchrow(
        "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", code
    )
    assert row
    context = IdentityRepository._actor(row).context
    await set_request_context(connection, context)
    return context


async def create_customer(connection):
    context = await actor(connection, "OPS001")
    return await CustomerMutationRepository().create(
        connection,
        context,
        data={
            "name": "隔离审批客户" + uuid4().hex,
            "industry": "软件",
            "customer_type": "潜在客户",
            "level_code": "Tier-2",
            "source": "销售线索",
            "target_team": "南区",
            "partner_name": "",
            "contact_name": "测试联系人",
            "contact_title": "经理",
            "contact_role": "决策者",
            "company_reference": "ISOLATED-" + uuid4().hex,
        },
    )


async def test_customer_create_requires_operations_and_company_verification(connection):
    customer = await create_customer(connection)
    assert customer["status"] == "unclaimed"
    assert (
        await connection.fetchval("SELECT state FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"])
        == "unclaimed"
    )
    sales = await actor(connection, "XS001")
    with pytest.raises(PermissionError):
        await CustomerMutationRepository().create(connection, sales, data={"name": "not allowed"})


async def test_claim_approval_release_and_reclaim(connection):
    customer = await create_customer(connection)
    first = await actor(connection, "XS001")
    req = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    replay = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    assert req == replay and req["status"] == "pending" and not req["claimed"]
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])
    await actor(connection, "XS002")
    req2 = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await actor(connection, "OPS001")
    await connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'approved','核实通过')", req["request_id"]
    )
    assert (
        await connection.fetchval(
            "SELECT owner_user_ref_id::text FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"]
        )
        == first.user_id
    )
    assert (
        await connection.fetchval("SELECT status FROM crm.customer_claim_request WHERE id=$1::uuid", req2["request_id"])
        == "rejected"
    )
    await actor(connection, "XS002")
    with pytest.raises(asyncpg.RaiseError):
        async with connection.transaction():
            await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await actor(connection, "OPS001")
    result = await connection.fetchval("SELECT security.release_customer($1::uuid,2,'人员交接')", customer["id"])
    assert result["state"] == "unclaimed"
    await actor(connection, "XS002")
    req3 = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    assert req3["request_id"] != req2["request_id"]
    await actor(connection, "OPS001")
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM crm.customer_ownership_event WHERE customer_id=$1::uuid", customer["id"]
        )
        == 2
    )


async def test_rejection_requires_reason_and_sales_cannot_approve(connection):
    customer = await create_customer(connection)
    await actor(connection, "XS001")
    req = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval(
                "SELECT security.review_customer_claim($1::uuid,'approved','')", req["request_id"]
            )
    await actor(connection, "OPS001")
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.fetchval(
                "SELECT security.review_customer_claim($1::uuid,'rejected','  ')", req["request_id"]
            )
    result = await connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'rejected','资料需要核对')", req["request_id"]
    )
    assert result["status"] == "rejected"


async def test_directory_is_company_wide_but_does_not_grant_history(connection):
    customer = await create_customer(connection)
    await actor(connection, "XS001")
    rows = await connection.fetch("SELECT * FROM security.company_customer_directory($1,100,0)", customer["name"])
    assert len(rows) == 1
    item = rows[0][0]
    assert item["id"] == customer["id"] and "contact_phone" not in item and "visits" not in item
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])


async def test_privileged_accounts_have_no_demo_login_or_direct_password_read(connection):
    assert not await connection.fetchrow("SELECT * FROM security.resolve_demo_actor('demo-sales-workspace','OPS001')")
    await actor(connection, "OPS001")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval("SELECT password_hash FROM platform.password_credential LIMIT 1")


async def test_failed_login_attempt_is_persisted_and_limited(connection):
    key = uuid4().hex * 2
    for _ in range(5):
        assert await connection.fetchval("SELECT security.login_attempt($1,5)", key)
    assert not await connection.fetchval("SELECT security.login_attempt($1,5)", key)
