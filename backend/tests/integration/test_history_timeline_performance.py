"""Bounded timeline reads and complete cursor traversal under the real runtime role."""

import asyncio
import time

import pytest

from sales_backend.repositories.detail_history import DetailHistoryRepository
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_principal_timeline_and_cursor_read_1000_histories_without_duplicate_or_missing_tail(connection):
    async with asyncio.timeout(90):
        await connection.execute("SET LOCAL statement_timeout='20s'")
        project = await opportunity(connection, "XS001", 1000)
        person = await actor(connection, "XS001")
        await connection.execute(
            """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
              recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record)
            SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$4::uuid,
              (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
              'archived','2026-09-21 00:00+08'::timestamptz,'性能验收合成跟进'||n
            FROM generate_series(1,1000)n""",
            person.workspace_id, project["customer_id"], project["id"], person.user_id, person.team_ids[0],
        )
        repository = DetailHistoryRepository()
        started = time.perf_counter()
        timeline = await repository.timeline(connection, project["id"], limit=20)
        print(f"principal_timeline_1000_ms={(time.perf_counter() - started) * 1000:.3f}")
        assert len(timeline["items"]) == 20 and timeline["has_more"]
        assert len({row["key"] for row in timeline["items"]}) == 20
        expected = await connection.fetch(
            """SELECT id::text FROM activity.visit WHERE opportunity_id=$1::uuid AND deleted_at IS NULL
            ORDER BY interaction_at DESC NULLS LAST,history_sort_date DESC,id DESC""", project["id"],
        )
        seen, cursor = [], None
        for _ in range(10):
            page = await repository.visits(
                connection, project["customer_id"], opportunity_id=project["id"], limit=100, cursor=cursor,
            )
            assert len(page["items"]) == 100
            seen.extend(row["id"] for row in page["items"])
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            assert page["next_cursor"] and page["next_cursor"] != cursor
            cursor = page["next_cursor"]
        else:
            pytest.fail("1000-record history cursor did not reach its tail")
        assert seen == [row["id"] for row in expected]
        assert len(seen) == len(set(seen)) == 1000
