"""Read-only search on 17k synthetic customers in an automatically dropped DB."""
import asyncio
import json
import time

import asyncpg
from run_integration_postgres import main

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.customer_search import name_search_keys
from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.identity import IdentityRepository


async def verify(config, name, role):
    connection = await asyncpg.connect(database=name, **config, command_timeout=30)
    await _initialize_connection(connection)
    try:
        record = await connection.fetchrow("SELECT * FROM security.resolve_account_actor('demo-sales-workspace','XS001',NULL)")
        actor = IdentityRepository._actor(record).context
        async with connection.transaction():
            await set_request_context(connection, actor)
            await connection.execute("""INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,
                owner_team_id,created_by_user_ref_id,industry_code,level_code)
                SELECT $1::uuid,'商汤合成客户'||n,'商汤合成客户'||n,$2::uuid,$3::uuid,$2::uuid,
                  CASE WHEN n%2=0 THEN '软件' ELSE '金融' END,'Tier-2'
                FROM generate_series(1,17000)n""", actor.workspace_id, actor.user_id, actor.team_ids[0])
        await connection.execute("ANALYZE crm.customer,crm.customer_ownership")
        name_search_keys.cache_clear()
        async with connection.transaction(readonly=True):
            await connection.execute(f'SET LOCAL ROLE "{role}"')
            assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            await set_request_context(connection, actor)
            repository = CustomerMemberRepository()
            for query, industry, total in [("shangtang", None, 17000), ("st", None, 17000), ("ST", "软件", 8500), ("商汤", "金融", 8500)]:
                start = time.perf_counter()
                page = await repository.claim_pool_page(connection, query=query, industry=industry, limit=50, offset=50)
                elapsed = time.perf_counter() - start
                assert page["total"] == total and len(page["items"]) == 50 and page["next_offset"] == 100
                payload = len(json.dumps(page, ensure_ascii=False).encode())
                result = dict(query=query,industry=industry,customer_count=17000,ms=round(elapsed*1000,2),
                              response_items=len(page["items"]),total=page["total"],payload_bytes=payload)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                assert payload < 50000 and elapsed < 5, result
    finally:
        await connection.close()


if __name__ == '__main__':
    asyncio.run(main(serve=verify))
