"""V179 matches the effective V132+V146 audit contract, without live providers."""
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.agent_audit import AgentAuditRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[3]
FUNCTION = "security.agent_operation_rows_v066"
ORACLE = "security.agent_operation_rows_v066_before_v179"
PRIVATE = "RAW_PRIVATE_TEST_TEXT_MUST_NOT_ESCAPE"
START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def legacy_definition():
    source = (ROOT / "database/migrations/V132__console_permission_adapters.sql").read_text()
    start = source.index("CREATE OR REPLACE FUNCTION " + FUNCTION + "(")
    end = source.index("CREATE OR REPLACE FUNCTION security.ai_invocation_report(", start)
    definition = source[start:end]
    # The immutable V146 patch is part of the old effective function, including
    # multiple independently assigned tasks. It must not be lost in this rewrite.
    definition = definition.replace("'task_id',s.task_id,", "'task_id',s.task_id,'task_ids',"
        "(SELECT COALESCE(jsonb_agg(st.id ORDER BY st.created_at,st.id),'[]'::jsonb) "
        "FROM workflow.task st WHERE st.source_suggestion_id=s.id AND st.workspace_id=s.workspace_id),")
    definition = definition.replace("s.task_id=e.task_id", "EXISTS(SELECT 1 FROM workflow.task st "
        "WHERE st.id=e.task_id AND st.source_suggestion_id=s.id AND st.workspace_id=s.workspace_id)")
    return definition


async def install_oracle(connection):
    runtime = await connection.fetchval("SELECT current_user")
    async with fixture_owner(connection):
        await connection.execute(legacy_definition().replace(FUNCTION + "(", ORACLE + "(", 1))
        await connection.execute(f"REVOKE ALL ON FUNCTION {ORACLE}(timestamptz,timestamptz) FROM PUBLIC")
        await connection.execute(f'GRANT EXECUTE ON FUNCTION {ORACLE}(timestamptz,timestamptz) TO "{runtime}"')


async def invocation(connection, who, operation, *, number=1, at=START, provider="agent_platform",
                     status="failed", record_kind="provider_attempt", identity=None):
    return await connection.fetchval(
        """INSERT INTO agent.model_invocation(id,workspace_id,actor_user_ref_id,actor_role_code,
           operation_id,operation_code,provider_code,model_id,endpoint_code,request_hash,request_snapshot,
           response_snapshot,status,attempt_no,started_at,completed_at,http_status,record_kind)
           VALUES($1,$2::uuid,$3::uuid,'sales',$4,'operating_report',$5,'fixture-model','fixture-endpoint',
             repeat('0',64),$6::jsonb,$7::jsonb,$8,$9,$10::timestamptz,$10::timestamptz+interval '1 second',200,$11) RETURNING id""",
        identity or uuid4(), who.workspace_id, who.user_id, operation, provider,
        {"private": PRIVATE, "test_fault_injected": number == 3, "network_dispatch_suppressed": number == 3},
        {"private": PRIVATE, "transport": {"retry": number}}, status, number, at, record_kind,
    )


async def seed_mixed(connection):
    sales = await actor(connection, "XS001")
    admin = await actor(connection, "ADMIN001")
    operations = [uuid4() for _ in range(7)]
    async with fixture_owner(connection):
        for index, state in enumerate(("accepted", "failed", "running")):
            await connection.execute(
                """INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,
                   capability,status,started_at,input_summary,events,trace)
                   VALUES($1,$2::uuid,$3::uuid,'sales','operating_report',$4,$5,
                     '{"record_counts":{"visits":2}}',$6::jsonb,$7::jsonb)""",
                operations[index], admin.workspace_id, sales.user_id, state, START,
                [{"phase": "fallback_requested"}] if index == 0 else [],
                {"provider": "senseaudio", "fallback_reason": "TimeoutError"} if index == 0 else {},
            )
        for index in (0, 2, 3):
            # The same timestamp tests the id tie-break, not insertion order.
            identifiers = sorted([uuid4() for _ in range(3)])
            for ordinal in (2, 0, 1):
                await invocation(connection, sales, operations[index], number=ordinal+1, identity=identifiers[ordinal],
                                 provider="senseaudio" if ordinal else "agent_platform",
                                 status="succeeded" if ordinal else "failed")
        # First-attempt null metadata must stay null rather than being filled
        # from a later retry by a min/filter-style aggregate.
        await connection.execute(
            """UPDATE agent.model_invocation SET actor_user_ref_id=NULL,actor_role_code=NULL,operation_code=NULL
               WHERE operation_id=$1 AND attempt_no=1""", operations[3],
        )
        await invocation(connection, sales, None)
        await invocation(connection, sales, operations[4], record_kind="legacy_logical")
        await invocation(connection, sales, operations[5], at=END)
        await invocation(connection, sales, operations[6])
        await connection.execute(
            """INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,
               capability,started_at) VALUES($1,$2::uuid,$3::uuid,'sales','old-receipt',$4)""",
            operations[6], admin.workspace_id, sales.user_id, START-timedelta(days=1),
        )
        foreign, foreign_user = uuid4(), uuid4()
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离审计公司')",
                                 foreign, str(foreign))
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'隔离审计人员')",
                                 foreign_user, foreign, str(foreign_user))
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')",
                                 foreign, foreign_user)
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", foreign)
        foreign_actor = admin.model_copy(update={"workspace_id": str(foreign), "user_id": str(foreign_user)})
        # Even an identical historical operation ID cannot mix company attempts.
        await invocation(connection, foreign_actor, operations[0])
        await invocation(connection, foreign_actor, uuid4())
        customer = await connection.fetchval("INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id) VALUES($1::uuid,'隔离审计客户','隔离审计客户',$2::uuid) RETURNING id",
                                             admin.workspace_id, admin.user_id)
        advice = await connection.fetchval(
            """INSERT INTO insight.business_advice(workspace_id,actor_user_ref_id,actor_role_code,subject_kind,
               subject_id,customer_id,section,cache_key,facts_fingerprint,configuration_fingerprint,
               identity_snapshot,facts_snapshot,configuration_snapshot,status,inference_trace)
               VALUES($1::uuid,$2::uuid,'sales','customer',$3,$3,'tasks',repeat('1',64),repeat('2',64),
                 repeat('3',64),'{}','{}','{}','succeeded',$4::jsonb) RETURNING id""",
            admin.workspace_id, sales.user_id, customer, {"operation_id": str(operations[0])},
        )
        suggestion = await connection.fetchval(
            """INSERT INTO insight.business_suggestion(workspace_id,advice_id,ordinal,title,evidence,action,evidence_refs)
               VALUES($1::uuid,$2,1,'核对资料','隔离依据','核对范围','[]') RETURNING id""", admin.workspace_id, advice,
        )
        tasks = []
        for ordinal in range(2):
            task = await connection.fetchval(
                """INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,due_at,source_suggestion_id)
                   VALUES($1::uuid,$2,'隔离任务说明',$3::uuid,clock_timestamp()+interval '2 days',$4) RETURNING id""",
                admin.workspace_id, f"隔离独立任务{ordinal}", sales.user_id, suggestion,
            )
            tasks.append(str(task))
            await connection.execute(
                """INSERT INTO workflow.task_event(workspace_id,task_id,event_type,actor_user_ref_id,to_status)
                   VALUES($1::uuid,$2,'created',$3::uuid,'pending_confirm')""", admin.workspace_id, task, sales.user_id,
            )
        await connection.execute(
            """UPDATE insight.business_suggestion SET decision='adopted',task_id=$2::uuid,
               decided_at=clock_timestamp(),decided_by_user_ref_id=$3::uuid WHERE id=$1""", suggestion, tasks[0], sales.user_id,
        )
    return admin, sales, foreign_actor, operations, tasks


async def rows(connection, function):
    return [dict(row) for row in await connection.fetch(
        f"SELECT * FROM {function}($1,$2) ORDER BY operation_id", START, END,
    )]


async def test_attempt_groups_preserve_every_field_scope_order_privacy_and_multi_tasks(connection):
    await assert_restricted(connection)
    await install_oracle(connection)
    admin, sales, foreign, operations, tasks = await seed_mixed(connection)
    await set_request_context(connection, admin)
    expected, actual = await rows(connection, ORACLE), await rows(connection, FUNCTION)
    assert actual == expected and len(actual) == 5
    assert PRIVATE not in json.dumps(actual, default=str)
    by_id = {str(row["operation_id"]): row["record"] for row in actual}
    assert len(by_id[str(operations[0])]["attempts"]) == 3
    assert by_id[str(operations[1])]["attempts"] == []
    assert by_id[str(operations[3])]["audit_source"] == "historical_attempts"
    assert by_id[str(operations[3])]["actor_id"] is None and by_id[str(operations[3])]["capability"] is None
    assert by_id[str(operations[6])]["capability"] == "operating_report"
    effects = by_id[str(operations[0])]["business_effects"]
    assert set(effects["suggestions"][0]["task_ids"]) == set(tasks)
    assert {item["id"] for item in effects["created_tasks"]} == set(tasks)
    for record in by_id.values():
        attempts = record["attempts"]
        assert [item["id"] for item in attempts] == sorted(item["id"] for item in attempts)
    for context, count in ((sales, 0), (foreign, 2), (admin, 5)):
        await set_request_context(connection, context)
        assert await rows(connection, ORACLE) == await rows(connection, FUNCTION)
        assert len(await rows(connection, FUNCTION)) == count
    await grant(connection, admin, admin.user_id, [dict(permission=p, effect="deny") for p in ("ai.run_read", "ai.run_export")])
    await set_request_context(connection, admin)
    assert await rows(connection, ORACLE) == await rows(connection, FUNCTION) == []


async def test_attempt_group_replacement_preserves_function_identity_owner_acl_and_empty_results(connection):
    await assert_restricted(connection)
    await actor(connection, "ADMIN001")
    async with fixture_owner(connection):
        metadata = "SELECT oid,proowner,proacl::text,proconfig,prosecdef,provolatile,prorettype,proargtypes::text FROM pg_proc WHERE oid=$1::regprocedure"
        signature = FUNCTION + "(timestamptz,timestamptz)"
        before = await connection.fetchrow(metadata, signature)
        # CREATE OR REPLACE on the historical body then the candidate must retain
        # exactly the identity and grants. This is rolled back with the fixture.
        current = await connection.fetchval("SELECT pg_get_functiondef($1::regprocedure)", signature)
        await connection.execute(legacy_definition())
        assert await connection.fetchrow(metadata, signature) == before
        await connection.execute(current)
        assert await connection.fetchrow(metadata, signature) == before
    assert await rows(connection, FUNCTION) == []


async def seed_scale(connection, size):
    admin = await actor(connection, "ADMIN001")
    async with fixture_owner(connection):
        await connection.execute(
            """INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,
               capability,status,started_at,trace)
               SELECT md5('scale-operation-'||n)::uuid,$1::uuid,$2::uuid,'administrator','operating_report',
                 CASE WHEN n%3=0 THEN 'failed' ELSE 'accepted' END,$3,'{"provider":"senseaudio"}'
               FROM generate_series(1,$4::integer)n WHERE n%4<>0""", admin.workspace_id, admin.user_id, START, size//10,
        )
        await connection.execute(
            """INSERT INTO agent.model_invocation(workspace_id,actor_user_ref_id,actor_role_code,operation_id,
               operation_code,provider_code,model_id,endpoint_code,request_hash,request_snapshot,response_snapshot,
               status,attempt_no,started_at)
               SELECT $1::uuid,$2::uuid,'administrator',md5('scale-operation-'||((n-1)/10+1))::uuid,
                 'operating_report',CASE WHEN n%10=0 THEN 'senseaudio' ELSE 'agent_platform' END,
                 'fixture-model','fixture-endpoint',repeat('0',64),jsonb_build_object('private',$5::text),
                 '{}',CASE WHEN n%10=0 THEN 'succeeded' ELSE 'failed' END,(n-1)%10+1,
                 $3::timestamptz+((n-1)%10)*interval '1 second' FROM generate_series(1,$4::integer)n""",
            admin.workspace_id, admin.user_id, START, size, PRIVATE,
        )
        await connection.execute("ANALYZE agent.model_invocation; ANALYZE agent.inference_operation")


async def measured_plan(connection, function):
    async with fixture_owner(connection):
        definition = await connection.fetchval("SELECT pg_get_functiondef($1::regprocedure)", function+"(timestamptz,timestamptz)")
        body = definition.split("AS $function$", 1)[1].rsplit("$function$", 1)[0].strip().rstrip(';')
        body = body.replace("p_start", "$1").replace("p_end", "$2")
        try:
            async with connection.transaction():
                await connection.execute("SET LOCAL statement_timeout='15s'")
                plan = (await connection.fetchval("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + body, START, END))[0]
        except asyncpg.QueryCanceledError:
            return {"status": "timeout", "statement_timeout_ms": 15000}
    def scan_nodes(node):
        own = [{"type": node["Node Type"], "cte": node.get("CTE Name"), "loops": node.get("Actual Loops"),
                "rows": node.get("Actual Rows"), "removed": node.get("Rows Removed by Filter", 0)}]
        return own + [item for child in node.get("Plans", []) for item in scan_nodes(child)]
    return {"status": "complete", "execution_ms": plan["Execution Time"],
            "shared_hits": plan["Plan"].get("Shared Hit Blocks", 0),
            "temp_read": plan["Plan"].get("Temp Read Blocks", 0), "nodes": scan_nodes(plan["Plan"])}


@pytest.mark.parametrize("size", [1000, 100000])
async def test_attempt_group_scale_reports_old_and_new_plans_and_page_cost(connection, size):
    if size == 100000 and os.environ.get("SALES_AGENT_AUDIT_CAPACITY_OBSERVATION") != "1":
        pytest.skip("100k audit diagnostic is opt-in; normal CI checks equivalence and the 1k budget")
    await assert_restricted(connection)
    await install_oracle(connection)
    await seed_scale(connection, size)
    old = await measured_plan(connection, ORACLE)
    new = await measured_plan(connection, FUNCTION)
    assert new["status"] == "complete"
    await connection.execute("SET LOCAL statement_timeout='15s'")
    summary_sql = "SELECT count(*) AS count,sum(jsonb_array_length(record->'attempts')) AS attempts FROM {}($1,$2)"
    started = perf_counter()
    actual = await connection.fetchrow(summary_sql.format(FUNCTION), START, END)
    function_ms = (perf_counter()-started)*1000
    assert actual["count"] == size//10 and actual["attempts"] == size
    if size == 1000:
        assert await rows(connection, FUNCTION) == await rows(connection, ORACLE)
        assert old["status"] == "complete"
        repeated = [node for node in old["nodes"] if node["cte"] == "attempts" and node["loops"] > 1]
        assert repeated and not [node for node in new["nodes"] if node["cte"] == "attempts" and node["loops"] > 1]
    started = perf_counter()
    result = await AgentAuditRepository().cases(connection, start=START, end=END)
    page_ms = (perf_counter()-started)*1000
    assert result["total"] == size//10 and len(result["items"]) == 50
    assert PRIVATE not in json.dumps(result, default=str)
    print("AGENT_AUDIT_SCALE " + json.dumps({"attempts": size, "operations": size//10,
          "baseline": old, "candidate": new, "page_ms": round(page_ms, 3),
          "runtime_function_ms": round(function_ms, 3),
          "page_count": len(result["items"]), "statistics": result["statistics"]}))
