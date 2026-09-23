"""Approval/legacy ownership commits enqueue risk work in the same real RLS transaction."""

import os
import re
from uuid import uuid4

import pytest

from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor, create_customer

pytestmark = pytest.mark.asyncio


async def pending_claim(connection):
    customer = await create_customer(connection)
    owner = await actor(connection, "XS001")
    request = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    operator = await actor(connection, "OPS001")
    return customer, owner, request, operator


async def risk_receipts(connection, customer_id):
    return await connection.fetch(
        "SELECT a.*,j.payload,j.status AS job_status,j.job_type FROM insight.customer_risk_assessment a "
        "JOIN ops.job j ON j.id=a.job_id AND j.workspace_id=a.workspace_id WHERE a.customer_id=$1::uuid",
        customer_id,
    )


async def test_approved_claim_queues_current_owner_and_records_actual_reviewer(connection):
    customer, owner, request, operator = await pending_claim(connection)
    assert not await risk_receipts(connection, customer["id"])
    result = await OperationsCustomerRepository().review(
        connection, request["request_id"], "approved", "核实归属", actor=operator,
    )
    rows = await risk_receipts(connection, customer["id"])
    assert result["status"] == "approved" and len(rows) == 1
    row = rows[0]
    assert row["status"] == row["job_status"] == "queued"
    assert row["job_type"] == "customer.risk.review" and row["trigger_type"] == "customer.claimed"
    assert row["trigger_id"] == request["request_id"]
    assert str(row["actor_user_ref_id"]) == row["payload"]["user_id"] == owner.user_id
    assert str(row["initiated_by_user_ref_id"]) == operator.user_id
    assert row["payload"]["role"] == "sales"


async def test_rejected_claim_does_not_enqueue_and_optional_actor_uses_real_context(connection):
    customer, _, request, _ = await pending_claim(connection)
    result = await OperationsCustomerRepository().review(connection, request["request_id"], "rejected", "待补核实材料")
    assert result["status"] == "rejected" and not await risk_receipts(connection, customer["id"])
    assert await connection.fetchval(
        "SELECT state FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"],
    ) == "unclaimed"


async def test_approval_transaction_rollback_leaves_no_risk_job_or_ownership_change(connection):
    customer, _, request, operator = await pending_claim(connection)
    jobs_before = await connection.fetchval("SELECT count(*) FROM ops.job")
    transaction = connection.transaction()
    await transaction.start()
    try:
        await OperationsCustomerRepository().review(
            connection, request["request_id"], "approved", "事务回滚验证", actor=operator,
        )
        assert len(await risk_receipts(connection, customer["id"])) == 1
    finally:
        await transaction.rollback()
    assert not await risk_receipts(connection, customer["id"])
    assert jobs_before == await connection.fetchval("SELECT count(*) FROM ops.job")
    assert await connection.fetchval(
        "SELECT status FROM crm.customer_claim_request WHERE id=$1::uuid", request["request_id"],
    ) == "pending"
    assert await connection.fetchval(
        "SELECT state FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"],
    ) == "unclaimed"


async def test_review_cannot_attribute_another_operator_passed_as_argument(connection):
    customer, owner, request, _ = await pending_claim(connection)
    with pytest.raises(PermissionError, match="认证身份"):
        await OperationsCustomerRepository().review(
            connection, request["request_id"], "approved", "拒绝冒用", actor=owner,
        )
    assert not await risk_receipts(connection, customer["id"])
    assert await connection.fetchval(
        "SELECT status FROM crm.customer_claim_request WHERE id=$1::uuid", request["request_id"],
    ) == "pending"


async def test_claim_approval_http_idempotency_keeps_one_assessment(connection):
    customer, _, request, _ = await pending_claim(connection)
    async with await client_for(connection) as client:
        await sign_in(client)
        headers = {"Idempotency-Key": str(uuid4())}
        endpoint = f"/api/v1/console/claims/{request['request_id']}/decision"
        response = await client.post(endpoint, headers=headers, json={"decision": "approved", "reason": "HTTP核实"})
        assert response.status_code == 200, response.text
        replay = await client.post(endpoint, headers=headers, json={"decision": "approved", "reason": "HTTP核实"})
        assert replay.status_code == 200 and replay.json() == response.json()
    await actor(connection, "OPS001")
    assert len(await risk_receipts(connection, customer["id"])) == 1


async def test_legacy_resolution_queues_owner_and_preserves_operator_provenance(connection):
    runtime = os.environ.get("SALES_TEST_ROLE", "")
    database = await connection.fetchval("SELECT current_database()")
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime) or not database.startswith("salegent_verify_"):
        pytest.skip("legacy fixture setup requires the isolated integration harness")
    customer = await create_customer(connection)
    owner = await actor(connection, "XS001")
    operator = await actor(connection, "OPS001")
    # Only seed an otherwise-unreachable historical state in this disposable DB.
    # The actual resolution and risk enqueue run below under the non-bypass role.
    await connection.execute("RESET ROLE")
    try:
        await connection.execute(
            "UPDATE crm.customer_ownership SET state='legacy_review' WHERE customer_id=$1::uuid",
            customer["id"],
        )
        await connection.execute(
            "INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id) "
            "VALUES($1::uuid,$2::uuid,$3::uuid)", owner.workspace_id, customer["id"], owner.user_id,
        )
    finally:
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    version = await connection.fetchval(
        "SELECT version_no FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"],
    )
    result = await OperationsCustomerRepository().resolve_legacy(
        connection, customer["id"], version, owner.user_id, "历史资料核验", actor=operator,
    )
    rows = await risk_receipts(connection, customer["id"])
    assert result["status"] == "approved" and len(rows) == 1
    assert rows[0]["trigger_type"] == "owner.resolved"
    assert rows[0]["trigger_id"] == result["request_id"]
    assert str(rows[0]["initiated_by_user_ref_id"]) == operator.user_id
    assert str(rows[0]["actor_user_ref_id"]) == rows[0]["payload"]["user_id"] == owner.user_id
    assert rows[0]["job_status"] == "queued"
