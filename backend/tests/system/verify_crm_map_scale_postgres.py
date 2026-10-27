"""17k synthetic archives: active map and complete portfolio, under native RLS.

Creates/drops its own disposable database; never accepts a business DB name.
Queries must complete within 5s, with 300 map points and an exact 17,001 count.
"""

import asyncio
import json
import os
import time

import asyncpg
from run_integration_postgres import main

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.repositories.customer_map import activity_since
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.customer_map import read_map


def emit(value):
    print(json.dumps(value, default=str, ensure_ascii=False), flush=True)


def size(value):
    return len(json.dumps(value, default=str, ensure_ascii=False).encode())


class Capture:
    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    def __getattr__(self, key):
        return getattr(self.conn, key)

    async def fetch(self, query, *args):
        start = time.perf_counter()
        result = await self.conn.fetch(query, *args)
        self.calls.append(
            {"method": "fetch", "sql": query, "args": args, "ms": round((time.perf_counter() - start) * 1000, 2)}
        )
        return result

    async def fetchrow(self, query, *args):
        start = time.perf_counter()
        result = await self.conn.fetchrow(query, *args)
        self.calls.append(
            {"method": "fetchrow", "sql": query, "args": args, "ms": round((time.perf_counter() - start) * 1000, 2)}
        )
        return result

    async def fetchval(self, query, *args):
        start = time.perf_counter()
        result = await self.conn.fetchval(query, *args)
        self.calls.append(
            {"method": "fetchval", "sql": query, "args": args, "ms": round((time.perf_counter() - start) * 1000, 2)}
        )
        return result


async def explain_after_measurement(connection, *, actor, iteration, groups):
    """Optional synthetic diagnostics; never replace the measured latency gate."""
    output = os.environ.get("SALES_MAP_SCALE_PLAN_OUTPUT")
    if not output:
        return
    # Plans are collected only after original timings and payload assertions.
    # This diagnostic run is not comparable to a normal uninstrumented run's
    # later iterations: EXPLAIN ANALYZE executes the query again.
    for phase, calls in groups.items():
        for call in calls:
            plan = await connection.fetchval(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + call["sql"], *call["args"]
            )
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as stream:
                stream.write(json.dumps({"actor": actor, "iteration": iteration, "phase": phase,
                                         "measured_ms": call["ms"], "plan": plan}, default=str) + "\n")


async def verify(config, name, role):
    conn = await asyncpg.connect(database=name, **config, command_timeout=60)
    await _initialize_connection(conn)
    report = {
        "environment": "disposable local database, non-bypass read role",
        "customer_seed_count": 17000,
        "active_seed_count": 300,
        "open_opportunity_seed_count": 500,
        "runs": [],
    }
    try:
        await conn.execute("SET statement_timeout='60s'")
        actors = {}
        for code in ("XS001", "ZJ001", "ZJL001"):
            row = await conn.fetchrow(
                "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", code
            )
            actors[code] = IdentityRepository._actor(row).context
        sales = actors["XS001"]
        async with conn.transaction():
            await set_request_context(conn, sales)
            start = time.perf_counter()
            await conn.execute(
                """INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,
      owner_team_id,created_by_user_ref_id,level_code)
      SELECT $1::uuid,'性能合成客户'||n,'性能合成客户'||n,$2::uuid,$3::uuid,$2::uuid,'Tier-2'
      FROM generate_series(1,17000)n""",
                sales.workspace_id,
                sales.user_id,
                sales.team_ids[0],
            )
            emit({"phase": "seed_customers", "ms": round((time.perf_counter() - start) * 1000)})
            await conn.execute(
                """INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,status,owner_user_ref_id,
      owner_team_id,created_by_user_ref_id,expected_close_year,expected_close_quarter)
      SELECT $1::uuid,id,'性能合成商机'||row_number() OVER(ORDER BY id),10000,'open',$2::uuid,$3::uuid,$2::uuid,2026,4
      FROM crm.customer WHERE name LIKE '性能合成客户%' ORDER BY id LIMIT 500""",
                sales.workspace_id,
                sales.user_id,
                sales.team_ids[0],
            )
            await conn.execute(
                """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
      recorder_team_id,created_by_user_ref_id,
     form_version_id,status,interaction_at,follow_up_record,next_action)
     SELECT $1::uuid,o.customer_id,o.id,$2::uuid,$3::uuid,$2::uuid,
       (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
       'archived',clock_timestamp()-interval '1 day',
       repeat('合成性能测试正文',100),'性能测试合成记录，不是业务数据'
     FROM crm.opportunity o WHERE name LIKE '性能合成商机%' ORDER BY o.customer_id LIMIT 300""",
                sales.workspace_id,
                sales.user_id,
                sales.team_ids[0],
            )
        await conn.execute(
            "ANALYZE crm.customer,crm.customer_ownership,crm.opportunity,activity.visit,activity.visit_opportunity"
        )
        for code, actor in actors.items():
            for attempt in range(1, 3):
                async with conn.transaction(readonly=True):
                    await conn.execute(f'SET LOCAL ROLE "{role}"')
                    assert not await conn.fetchval(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                    await set_request_context(conn, actor)
                    capture = Capture(conn)
                    start = time.perf_counter()
                    # Exercise the production service, including authorized
                    # directory resolution and subject-score selection.
                    items = await read_map(capture, actor, as_of=today())
                    map_ms = (time.perf_counter() - start) * 1000
                    assert len(items) == 300, (code, len(items))
                    map_queries = [call for call in capture.calls if "WITH recent_visits" in call["sql"]]
                    assert len(map_queries) == 1, "One map projection must select both customer and subject scores"
                    map_query = map_queries[0]
                    scope_calls = [call for call in capture.calls if call is not map_query]
                    map_payload = {
                        "items": items,
                        "as_of": today(),
                        "activity_since": activity_since(today()),
                        "data_source": "database",
                    }
                    capture = Capture(conn)
                    start = time.perf_counter()
                    assets = await CustomerAssetRepository().read(capture)
                    assets_ms = (time.perf_counter() - start) * 1000
                    assert assets["summary"]["portfolio_customer_count"] == 17001, assets["summary"]
                    assert assets["summary"]["acv_amount"] == 5000000, assets["summary"]
                    row = {
                        "actor": code,
                        "iteration": attempt,
                        "map_items": len(items),
                        "map_with_scope_ms": round(map_ms, 2),
                        "map_sql_ms": map_query["ms"],
                        "scope_and_service_ms": round(map_ms - map_query["ms"], 2),
                        "scope_sql_ms": [q["ms"] for q in scope_calls],
                        "subject_scores_in_map_projection": True,
                        "map_payload_bytes": size(map_payload),
                        "portfolio_customer_count": assets["summary"]["portfolio_customer_count"],
                        "assets_repository_ms": round(assets_ms, 2),
                        "assets_sql_ms": [q["ms"] for q in capture.calls],
                        "assets_payload_bytes": size(assets),
                    }
                    emit(row)
                    await explain_after_measurement(
                        conn, actor=code, iteration=attempt,
                        groups={"map": [map_query]},
                    )
                    assert map_ms < 5000, row
                    assert assets_ms < 5000, row
                    report["runs"].append(row)
        emit({"phase": "complete", "synthetic_only": True, "bounded_payload_and_queries": True})
    finally:
        await conn.close()


asyncio.run(main(serve=verify))
