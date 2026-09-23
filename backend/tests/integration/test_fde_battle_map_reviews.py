"""Private FDE score persistence under real PostgreSQL RLS; no provider calls."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
import re

import asyncpg
import pytest

from sales_backend.config import get_settings
from sales_backend.db import Database, set_request_context
from sales_backend.services.battle_map_reviews import BattleMapReviewHandler
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


def handler_for(connection):
    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield connection

    settings = get_settings()
    return BattleMapReviewHandler(Database(settings, pool=Pool()), settings)


def answer(handler, facts, potential=75):
    return handler._normalize_result(
        {"potential_score": potential, "relationship_score": 50, "evidence": [], "summary": "合成评估", "rationale": {"potential": "已提供事实", "relationship": "已有跟进"}}, facts
    )


async def archived_visit(connection, sales, customer_id):
    await set_request_context(connection, sales)
    return str(await connection.fetchval(
        "INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,recorder_team_id,"
        "form_version_id,status,interaction_at,created_by_user_ref_id) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,"
        "(SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),"
        "'archived',clock_timestamp(),$3::uuid) RETURNING id",
        sales.workspace_id, customer_id, sales.user_id, sales.team_ids[0],
    ))


@pytest.mark.parametrize("member", ["first", "lead"])
async def test_fde_replaces_only_private_score_without_commercial_writes_or_sales_broadcast(connection, member):
    sales, opportunity, people, _ = await fde_fixture(connection)
    customer = opportunity["customer_id"]
    visit = await archived_visit(connection, sales, customer)
    sales_visit = await archived_visit(connection, sales, customer)
    handler = handler_for(connection)
    sales_facts = await handler._load_facts(customer, sales)
    await handler._persist(customer, sales, sales_facts, answer(handler, sales_facts), {
        "trigger_type": "visit.archived", "trigger_id": sales_visit,
    })
    fde = await actor(connection, people[member]["code"])
    assert not await connection.fetchval("SELECT security.has_customer_write_access($1::uuid)", customer)
    assert await connection.fetchval("SELECT id FROM crm.customer WHERE id=$1::uuid", customer)
    # The old persistence check rejects the same readable customer.
    assert await connection.fetchval("SELECT id FROM crm.customer WHERE id=$1::uuid FOR UPDATE", customer) is None
    facts = await handler._load_facts(customer, fde)
    trigger = {"trigger_type": "visit.archived", "trigger_id": visit}
    await handler._persist(customer, fde, facts, answer(handler, facts), trigger)
    first = await connection.fetchrow(
        "SELECT id,potential_score FROM insight.quadrant_score WHERE customer_id=$1::uuid "
        "AND subject_user_ref_id=$2::uuid AND valid_to='infinity'", customer, fde.user_id,
    )
    facts = await handler._load_facts(customer, fde)
    await handler._persist(customer, fde, facts, answer(handler, facts, 82), trigger)
    own = await connection.fetch(
        "SELECT id,potential_score,valid_to='infinity' AS current FROM insight.quadrant_score "
        "WHERE customer_id=$1::uuid AND subject_user_ref_id=$2::uuid", customer, fde.user_id,
    )
    assert len(own) == 2 and sum(row["current"] for row in own) == 1
    assert next(row for row in own if row["current"])["potential_score"] == 82
    assert not next(row for row in own if row["id"] == first["id"])["current"]
    assert await connection.execute("UPDATE crm.customer SET name='forbidden' WHERE id=$1::uuid", customer) == "UPDATE 0"
    assert await connection.execute("UPDATE crm.opportunity SET amount=999 WHERE id=$1::uuid", opportunity["id"]) == "UPDATE 0"
    await actor(connection, "ADMIN001")
    assert await connection.fetchval(
        "SELECT potential_score FROM insight.quadrant_score WHERE customer_id=$1::uuid "
        "AND subject_user_ref_id=$2::uuid AND valid_to='infinity'", customer, sales.user_id,
    ) == 75
    # Notification RLS is recipient-only, including for administrators. Check
    # both actual sales-hierarchy recipients so zero is evidence of no fanout.
    for recipient in ("ZJ001", "ZJL001"):
        await actor(connection, recipient)
        assert await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE template_code='battle_map_updated' "
            "AND payload->>'trigger_id'=$1", sales_visit,
        ) == 1
        assert await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE template_code='battle_map_updated' "
            "AND payload->>'trigger_id'=$1", visit,
        ) == 0


@pytest.mark.parametrize("member", ["first", "lead"])
async def test_fde_cannot_insert_change_reassign_or_delete_another_subject_score(connection, member):
    sales, opportunity, people, _ = await fde_fixture(connection)
    customer = opportunity["customer_id"]
    handler = handler_for(connection)
    facts = await handler._load_facts(customer, sales)
    await handler._persist(customer, sales, facts, answer(handler, facts), {})
    fde = await actor(connection, people[member]["code"])
    for assignment in ("potential_score=99", "subject_user_ref_id=common.current_user_ref_id()"):
        assert await connection.execute(
            f"UPDATE insight.quadrant_score SET {assignment} WHERE customer_id=$1::uuid "
            "AND subject_user_ref_id=$2::uuid", customer, sales.user_id,
        ) == "UPDATE 0"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,relationship_score,"
                "quadrant_code,rule_set_id,subject_user_ref_id) "
                "VALUES($1::uuid,$2::uuid,90,90,'customer_asset',$3::uuid,$4::uuid)",
                fde.workspace_id, customer, facts["company_policy"]["id"], people["second"]["id"],
            )
    facts = await handler._load_facts(customer, fde)
    await handler._persist(customer, fde, facts, answer(handler, facts), {})
    assert await connection.execute("DELETE FROM insight.quadrant_score WHERE customer_id=$1::uuid", customer) == "DELETE 0"


@pytest.mark.parametrize("member", ["first", "lead"])
async def test_fde_losing_last_customer_relation_after_inference_cannot_persist(connection, member):
    _, opportunity, people, _ = await fde_fixture(connection)
    customer = opportunity["customer_id"]
    fde = await actor(connection, people[member]["code"])
    handler = handler_for(connection)
    facts = await handler._load_facts(customer, fde)
    await handler._persist(customer, fde, facts, answer(handler, facts), {})
    await actor(connection, "ADMIN001")
    await connection.execute(
        "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='撤权回归' "
        "WHERE opportunity_id=$1::uuid AND valid_to='infinity'", opportunity["id"],
    )
    # Stale actor/facts must be rechecked against current database permissions.
    with pytest.raises(LookupError, match="persistence"):
        await handler._persist(customer, fde, facts, answer(handler, facts, 99), {})
    await set_request_context(connection, fde)
    assert await connection.execute("UPDATE insight.quadrant_score SET potential_score=99 WHERE customer_id=$1::uuid", customer) == "UPDATE 0"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,relationship_score,"
                "quadrant_code,rule_set_id,subject_user_ref_id) "
                "VALUES($1::uuid,$2::uuid,90,90,'customer_asset',$3::uuid,$4::uuid)",
                fde.workspace_id, customer, facts["company_policy"]["id"], fde.user_id,
            )
    await actor(connection, "ADMIN001")
    assert await connection.fetchval(
        "SELECT potential_score FROM insight.quadrant_score WHERE customer_id=$1::uuid "
        "AND subject_user_ref_id=$2::uuid AND valid_to='infinity'", customer, fde.user_id,
    ) == 75


async def test_sales_archived_visit_still_notifies_sales_leaders(connection):
    sales, opportunity, _, _ = await fde_fixture(connection)
    customer = opportunity["customer_id"]
    visit = await archived_visit(connection, sales, customer)
    handler = handler_for(connection)
    facts = await handler._load_facts(customer, sales)
    await handler._persist(customer, sales, facts, answer(handler, facts), {
        "trigger_type": "visit.archived", "trigger_id": visit,
    })
    # V043 sends to the customer's team supervisor and workspace managers;
    # each recipient must read their own inbox under the normal notification RLS.
    for recipient in ("ZJ001", "ZJL001"):
        await actor(connection, recipient)
        assert await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE template_code='battle_map_updated' "
            "AND payload->>'trigger_id'=$1", visit,
        ) == 1


async def test_concurrent_fde_reviews_leave_one_current_score_in_disposable_database():
    # Use the runner's serve callback to get a separate disposable DB: committed
    # concurrency fixtures must not affect the remaining suite's business totals.
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", os.environ.get("SALES_TEST_ROLE", "")):
        pytest.skip("Committed concurrency verification requires the disposable runner")
    from tests.system.run_integration_postgres import main as run_disposable

    async def verify(config, database_name, role):
        async def connect():
            conn = await asyncpg.connect(database=database_name, **config)
            assert (await conn.fetchval("SELECT current_database()")).startswith("salegent_verify_integration_")
            for kind in ("json", "jsonb"):
                await conn.set_type_codec(kind, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
            await conn.execute(f'SET ROLE "{role}"')
            assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            return conn

        setup = await connect()
        try:
            async with setup.transaction():
                _, opportunity, people, _ = await fde_fixture(setup)
                members = [await actor(setup, people[label]["code"]) for label in ("first", "second")]
            customer = opportunity["customer_id"]
            facts = [await handler_for(setup)._load_facts(customer, member) for member in members]
            started = [asyncio.Event() for _ in range(4)]
            release = asyncio.Event()

            async def persist(index):
                conn = await connect()
                try:
                    # Two competing results per subject, all using facts loaded
                    # before any score exists, on four real database connections.
                    started[index].set()
                    await release.wait()
                    handler = handler_for(conn)
                    member_index = index // 2
                    await handler._persist(customer, members[member_index], facts[member_index],
                                           answer(handler, facts[member_index], 75 + index), {})
                finally:
                    await conn.close()

            requests = [asyncio.create_task(persist(index)) for index in range(4)]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=5)
                release.set()
                await asyncio.wait_for(asyncio.gather(*requests), timeout=10)
            finally:
                release.set()
                for request in requests:
                    if not request.done():
                        request.cancel()
                await asyncio.gather(*requests, return_exceptions=True)
            async with setup.transaction():
                for member in members:
                    await set_request_context(setup, member)
                    rows = await setup.fetch(
                        "SELECT valid_to='infinity' AS current FROM insight.quadrant_score "
                        "WHERE customer_id=$1::uuid AND subject_user_ref_id=$2::uuid", customer, member.user_id,
                    )
                    assert len(rows) == 2 and sum(row["current"] for row in rows) == 1
        finally:
            await setup.close()

    await run_disposable(serve=verify)
