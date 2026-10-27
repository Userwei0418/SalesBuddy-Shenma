"""Archive storage, rollback and replay on disposable PostgreSQL; no model calls.

Run only this file with the existing integration fixture after the index migration.
Structure/quality results are synthetic; the endpoint, review gate, repository,
transaction and idempotency receipt use their real implementations.
"""

from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.api.business import create_visit
from sales_backend.repositories.visits import VisitRepository
from tests.integration.feishu_fixtures import assert_restricted, seed_fetchval
from tests.integration.test_visit_attendance import review_attendance
from tests.integration.test_visit_close_date_archive import review_state
from tests.integration.test_visit_entry_platform import TransactionDatabase

pytestmark = pytest.mark.asyncio

# Distinct CJK characters avoid a repeating sentence that compresses enough to
# conceal the old full-text B-tree limit. This reaches the accepted character cap.
LONG_CHINESE = "".join(chr(0x4E00 + (index * 7919) % 20902) for index in range(15000))


async def reviewed_visit(connection, actor, text):
    await assert_restricted(connection)
    customer_id = await connection.fetchval(
        "SELECT id::text FROM crm.customer WHERE workspace_id=$1::uuid "
        "AND owner_user_ref_id=$2::uuid AND deleted_at IS NULL ORDER BY id LIMIT 1",
        actor.workspace_id, actor.user_id,
    )
    assert customer_id, "disposable fixture must provide an owned customer"
    _, submitted = await review_attendance(
        connection, actor, customer_id, None, [],
        fields_override={"follow_up_record": text},
    )
    return submitted


async def assert_stored_once(connection, submitted, key, result):
    visit_id = result["id"]
    text = submitted.fields["follow_up_record"]
    assert result["archived_fields"]["follow_up_record"] == text
    stored = await connection.fetchrow(
        "SELECT f.value_text,f.value_json FROM activity.visit_field_value f "
        "JOIN config.field_definition d ON d.id=f.field_definition_id "
        "WHERE f.visit_id=$1::uuid AND d.field_key='follow_up_record'", visit_id,
    )
    assert stored and stored["value_text"] == stored["value_json"] == text
    assert await connection.fetchval(
        "SELECT follow_up_record FROM activity.visit WHERE id=$1::uuid", visit_id,
    ) == text
    assert await review_state(connection, submitted.fields["_quality_review_run_id"]) == {
        "status": "applied", "confirmations": 1, "visits": 1,
    }
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.mutation_receipt "
        "WHERE operation='visits.archive' AND request_key=$1", key,
    ) == 1


@pytest.mark.parametrize("text", ["双方确认下周开展试点并核对验收标准。", LONG_CHINESE], ids=["ordinary", "long_chinese"])
async def test_archive_preserves_text_and_replays_once(connection, sales_actor, text):
    submitted = await reviewed_visit(connection, sales_actor, text)
    database, identity = TransactionDatabase(connection), SimpleNamespace(actor=sales_actor)
    key = uuid4()
    result = await create_visit(submitted, identity, database, key)
    assert await create_visit(submitted, identity, database, key) == result
    await assert_stored_once(connection, submitted, key, result)


async def test_archive_sql_failure_rolls_back_then_same_key_retries_once(connection, sales_actor, monkeypatch):
    submitted = await reviewed_visit(connection, sales_actor, LONG_CHINESE)
    database, identity = TransactionDatabase(connection), SimpleNamespace(actor=sales_actor)
    key, failed_visit_ids = uuid4(), []
    original_create = VisitRepository.create

    async def fail_after_field_writes(self, connection, actor, **kwargs):
        result = await original_create(self, connection, actor, **kwargs)
        failed_visit_ids.append(result["id"])
        assert await seed_fetchval(
            connection, "SELECT count(*) FROM ops.audit_log WHERE object_id=$1::uuid", result["id"],
        ) > 0
        # A real SQL failure after the visit, participants and dynamic fields
        # have been written must abort the endpoint's complete transaction.
        await connection.execute("SELECT 1 / 0")
        raise AssertionError("PostgreSQL accepted division by zero")

    with monkeypatch.context() as patch:
        patch.setattr(VisitRepository, "create", fail_after_field_writes)
        with pytest.raises(asyncpg.DivisionByZeroError):
            await create_visit(submitted, identity, database, key)
    assert len(failed_visit_ids) == 1
    failed_visit_id = failed_visit_ids[0]
    assert await connection.fetchval(
        "SELECT count(*) FROM activity.visit WHERE id=$1::uuid", failed_visit_id,
    ) == 0
    assert await connection.fetchval(
        "SELECT count(*) FROM activity.visit_field_value WHERE visit_id=$1::uuid", failed_visit_id,
    ) == 0
    assert await connection.fetchval(
        "SELECT count(*) FROM activity.visit_participant WHERE visit_id=$1::uuid", failed_visit_id,
    ) == 0
    # Audit visibility is restricted for sales. Use the disposable-fixture owner
    # for this count so an RLS-hidden row cannot falsely prove rollback.
    assert await seed_fetchval(
        connection, "SELECT count(*) FROM ops.audit_log WHERE object_id=$1::uuid", failed_visit_id,
    ) == 0
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.mutation_receipt "
        "WHERE operation='visits.archive' AND request_key=$1", key,
    ) == 0
    assert await review_state(connection, submitted.fields["_quality_review_run_id"]) == {
        "status": "pending_confirm", "confirmations": 0, "visits": 0,
    }
    result = await create_visit(submitted, identity, database, key)
    assert result["id"] != failed_visit_id
    assert await create_visit(submitted, identity, database, key) == result
    await assert_stored_once(connection, submitted, key, result)
