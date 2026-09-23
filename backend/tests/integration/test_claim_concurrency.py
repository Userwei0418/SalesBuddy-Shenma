"""Two committed sessions in the runner's disposable database, never in production."""
import asyncio
import json
import os
import re
import asyncpg
import pytest
from sales_backend.db import normalize_database_url
from tests.integration.test_operations_claims_sql import actor, create_customer

@pytest.mark.asyncio
async def test_two_operations_approvals_have_only_one_effective_owner():
    dsn = os.environ.get("SALES_TEST_DATABASE_URL", "")
    role = os.environ.get("SALES_TEST_ROLE", "")
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role):
        pytest.skip("Committed concurrency verification requires the disposable runner")
    async def connect():
        c = await asyncpg.connect(normalize_database_url(dsn))
        assert (await c.fetchval("SELECT current_database()")).startswith("salegent_verify_integration_")
        await c.set_type_codec("jsonb",schema="pg_catalog",encoder=json.dumps,decoder=json.loads,format="text")
        await c.execute(f'SET ROLE "{role}"')
        return c
    setup = await connect()
    try:
        async with setup.transaction():
            customer = await create_customer(setup)
            await actor(setup,"XS001")
            a = await setup.fetchval("SELECT security.claim_customer($1::uuid)",customer["id"])
            await actor(setup,"XS002")
            b = await setup.fetchval("SELECT security.claim_customer($1::uuid)",customer["id"])
        async def approve(req):
            c = await connect()
            try:
                async with c.transaction():
                    await actor(c,"OPS001")
                    return await c.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','隔离并发验收')",req["request_id"])
            finally:
                await c.close()
        results = await asyncio.gather(approve(a),approve(b),return_exceptions=True)
        assert any(isinstance(x,dict) and x["status"] == "approved" for x in results),results
        async with setup.transaction():
            await actor(setup,"OPS001")
            assert await setup.fetchval("SELECT count(*) FROM crm.customer_ownership WHERE customer_id=$1::uuid AND state='claimed'",customer["id"]) == 1
            assert await setup.fetchval("SELECT count(*) FROM crm.customer_claim_request WHERE customer_id=$1::uuid AND status='approved'",customer["id"]) == 1
    finally:
        await setup.close()
