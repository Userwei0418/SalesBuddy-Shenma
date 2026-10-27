"""Archive a reviewed legacy-period opportunity update through real HTTP and RLS.

Only the structure/quality model result is synthetic. Persistence, review binding,
optimistic concurrency and idempotency use the production services and PostgreSQL.
The integration runner supplies a disposable database and rolls each test back.
"""

from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from sales_backend.contracts.models import OpportunityCreate
from sales_backend.db import set_request_context
from tests.integration.feishu_fixtures import assert_restricted, seed_execute
from tests.integration.test_business_rankings import opportunity as create_opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login
from tests.integration.test_visit_attendance import review_attendance

pytestmark = pytest.mark.asyncio


async def legacy_opportunity(connection):
    opportunity = await create_opportunity(connection, "XS001", 100000)
    person = await actor(connection, "XS001")
    # Reproduce an imported fact: only year/quarter was known, so no exact date.
    # Owner elevation is restricted by seed_execute to runner-created databases.
    await seed_execute(
        connection,
        "UPDATE crm.opportunity SET expected_close_date=NULL,expected_close_year=2026,"
        "expected_close_quarter=2 WHERE id=$1::uuid",
        opportunity["id"],
    )
    await set_request_context(connection, person)
    await assert_restricted(connection)
    stored = await opportunity_state(connection, opportunity["id"])
    assert stored["expected_close_date"] is None
    assert (stored["expected_close_year"], stored["expected_close_quarter"]) == (2026, 2)
    mutation = OpportunityCreate(
        action="update", opportunity_id=opportunity["id"], name=opportunity["name"],
        probability=10, amount=100000, expected_close_date="2026-09-30",
        version_no=stored["version_no"], sales_channel="direct",
    ).model_dump(mode="json")
    return person, opportunity, mutation


async def opportunity_state(connection, opportunity_id):
    return dict(await connection.fetchrow(
        "SELECT expected_close_date,expected_close_year,expected_close_quarter,version_no,amount "
        "FROM crm.opportunity WHERE id=$1::uuid", opportunity_id,
    ))


async def review_state(connection, run_id):
    return dict(await connection.fetchrow(
        "SELECT a.status,(SELECT count(*) FROM agent.confirmation c WHERE c.artifact_id=a.id) AS confirmations,"
        "(SELECT count(*) FROM activity.visit v WHERE v.source_artifact_id=a.id) AS visits "
        "FROM agent.artifact a WHERE a.run_id=$1::uuid AND a.artifact_type='visit_entry'", run_id,
    ))


async def reviewed_submission(connection, person, opportunity, mutation):
    _, submission = await review_attendance(
        connection, person, opportunity["customer_id"], opportunity["id"], [], mutation,
    )
    body = submission.model_dump(mode="json")
    run_id = body["fields"]["_quality_review_run_id"]
    assert await review_state(connection, run_id) == {
        "status": "pending_confirm", "confirmations": 0, "visits": 0,
    }
    return body, run_id


async def assert_archived_once(connection, run_id, opportunity_id, previous_version):
    assert await review_state(connection, run_id) == {
        "status": "applied", "confirmations": 1, "visits": 1,
    }
    stored = await opportunity_state(connection, opportunity_id)
    assert stored["expected_close_date"] == date(2026, 9, 30)
    # The confirmed exact date replaces the old imported period fallback.
    assert stored["expected_close_year"] is None and stored["expected_close_quarter"] is None
    assert stored["version_no"] == previous_version + 1
    return stored


async def test_quality_archive_updates_legacy_close_period_once_across_replay_keys(connection):
    person, opportunity, mutation = await legacy_opportunity(connection)
    body, run_id = await reviewed_submission(connection, person, opportunity, mutation)
    key = str(uuid4())
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 201, response.text
        visit_id = response.json()["id"]
        first = await assert_archived_once(connection, run_id, opportunity["id"], mutation["version_no"])

        same_key = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": key})
        assert same_key.status_code == 201, same_key.text
        assert same_key.json() == response.json()
        new_key = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": str(uuid4())})
        assert new_key.status_code == 201, new_key.text
        assert new_key.json()["id"] == visit_id and new_key.json()["replayed"]
        assert await assert_archived_once(connection, run_id, opportunity["id"], mutation["version_no"]) == first
        assert await connection.fetchval(
            "SELECT count(*) FROM activity.visit WHERE opportunity_id=$1::uuid", opportunity["id"],
        ) == 1


async def test_archive_version_conflict_rolls_back_review_and_requires_fresh_quality(connection):
    person, opportunity, mutation = await legacy_opportunity(connection)
    body, run_id = await reviewed_submission(connection, person, opportunity, mutation)
    # Another valid write lands after quality review; the reviewed version is now stale.
    await seed_execute(connection, "UPDATE crm.opportunity SET amount=amount+1 WHERE id=$1::uuid", opportunity["id"])
    await set_request_context(connection, person)
    concurrent = await opportunity_state(connection, opportunity["id"])
    assert concurrent["version_no"] == mutation["version_no"] + 1
    pending = {"status": "pending_confirm", "confirmations": 0, "visits": 0}
    key = str(uuid4())
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 409, response.text
        assert await review_state(connection, run_id) == pending
        assert await opportunity_state(connection, opportunity["id"]) == concurrent
        assert await connection.fetchval(
            "SELECT count(*) FROM activity.visit WHERE opportunity_id=$1::uuid", opportunity["id"],
        ) == 0

        changed = deepcopy(body)
        changed["fields"]["_opportunity_mutation"]["version_no"] = concurrent["version_no"]
        mismatch = await client.post("/api/v1/visits", json=changed, headers={"Idempotency-Key": str(uuid4())})
        assert mismatch.status_code == 422, mismatch.text
        assert "重新质检" in mismatch.json()["detail"]
        assert await review_state(connection, run_id) == pending
        assert await opportunity_state(connection, opportunity["id"]) == concurrent

        # Refresh the authoritative opportunity and obtain a new passing quality
        # artifact. Never patch the stored review or bypass its snapshot binding.
        corrected = {**mutation, "version_no": concurrent["version_no"], "amount": str(concurrent["amount"])}
        fresh_body, fresh_run_id = await reviewed_submission(connection, person, opportunity, corrected)
        fresh = await client.post("/api/v1/visits", json=fresh_body, headers={"Idempotency-Key": str(uuid4())})
        assert fresh.status_code == 201, fresh.text
        await assert_archived_once(connection, fresh_run_id, opportunity["id"], concurrent["version_no"])
        assert await review_state(connection, run_id) == pending
        assert await connection.fetchval(
            "SELECT count(*) FROM activity.visit WHERE opportunity_id=$1::uuid", opportunity["id"],
        ) == 1
