"""Real seek pagination and generated ordering fields with unchanged role policies."""

from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.detail_paging import read_visit_cursor
from sales_backend.repositories.detail_history import DetailHistoryRepository
from tests.integration.test_detail_read_models import histories
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def fixture(connection, count=30):
    sales, project, people, _ = await fde_fixture(connection)
    await histories(connection, sales, project, count)
    await connection.execute("SET LOCAL statement_timeout='20s'")
    return sales, project, people


async def test_cursor_matches_legacy_order_including_null_times_date_fallback_and_uuid_ties(connection):
    sales, project, people = await fixture(connection)
    await connection.execute(
        """WITH rows AS (SELECT id,row_number() OVER(ORDER BY id) AS n FROM activity.visit
          WHERE customer_id=$1::uuid AND deleted_at IS NULL)
        UPDATE activity.visit v SET interaction_at=CASE WHEN n%3=0 THEN NULL
          ELSE '2026-09-01 00:00+08'::timestamptz+((n%2)::integer * interval '1 day') END,
          recorded_on=CASE WHEN n%2=0 THEN '2026-09-05'::date END
        FROM rows WHERE v.id=rows.id""",
        project["customer_id"],
    )
    repo = DetailHistoryRepository()
    for code in ["XS001", people["lead"]["code"]]:
        await actor(connection, code)
        expected = await connection.fetch(
            """SELECT v.id::text FROM activity.visit v WHERE v.customer_id=$1::uuid AND v.deleted_at IS NULL
              ORDER BY v.interaction_at DESC NULLS LAST,
                COALESCE(v.recorded_on,timezone('Asia/Shanghai',v.created_at)::date) DESC,v.id DESC""",
            project["customer_id"],
        )
        seen, cursor = [], None
        for _ in range(6):
            page = await repo.visits(
                connection, project["customer_id"], opportunity_id=project["id"], limit=7, cursor=cursor
            )
            seen.extend(row["id"] for row in page["items"])
            assert len(page["items"]) <= 7
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            assert page["next_cursor"] and page["next_cursor"] != cursor
            if cursor:
                assert page["next_offset"] is None
            cursor = page["next_cursor"]
        else:
            pytest.fail("cursor did not reach the bounded fixture tail")
        assert seen == [row["id"] for row in expected]
        assert len(seen) == len(set(seen)) == 30
        # Offset remains a supported legacy request; its first page is identical.
        old = await repo.visits(connection, project["customer_id"], opportunity_id=project["id"], limit=7, offset=7)
        assert [row["id"] for row in old["items"]] == seen[7:14]


async def test_cursor_survives_newer_insert_and_earlier_delete_without_offset_shift(connection):
    sales, project, _ = await fixture(connection)
    repo = DetailHistoryRepository()
    first = await repo.visits(connection, project["customer_id"], limit=10)
    baseline = await connection.fetch(
        "SELECT id::text FROM activity.visit WHERE customer_id=$1::uuid ORDER BY history_sort_date DESC,id DESC",
        project["customer_id"],
    )
    await connection.execute(
        "UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1::uuid", first["items"][0]["id"]
    )
    await histories(connection, sales, project, 1)
    await connection.execute(
        "UPDATE activity.visit SET interaction_at='2027-01-01 00:00+08'::timestamptz "
        "WHERE customer_id=$1::uuid AND id<>ALL($2::uuid[])",
        project["customer_id"],
        [row["id"] for row in baseline],
    )
    second = await repo.visits(connection, project["customer_id"], limit=10, cursor=first["next_cursor"])
    third = await repo.visits(connection, project["customer_id"], limit=10, cursor=second["next_cursor"])
    assert [row["id"] for row in first["items"] + second["items"] + third["items"]] == [row["id"] for row in baseline]
    assert not third["has_more"]


async def test_cursor_never_grants_access_after_subject_mismatch_identity_switch_or_participation_removal(connection):
    _, project, people = await fixture(connection)
    repo = DetailHistoryRepository()
    fde = await actor(connection, people["first"]["code"])
    first = await repo.visits(connection, project["customer_id"], opportunity_id=project["id"], limit=5)
    cursor = first["next_cursor"]
    with pytest.raises(ValueError):
        read_visit_cursor(cursor, uuid4(), project["id"])
    with pytest.raises(ValueError):
        read_visit_cursor(cursor, project["customer_id"], uuid4())
    with pytest.raises(ValueError):
        await repo.visits(connection, project["customer_id"], opportunity_id=project["id"], offset=5, cursor=cursor)
    await actor(connection, "XS002")
    assert not (await repo.visits(connection, project["customer_id"], opportunity_id=project["id"], cursor=cursor))[
        "items"
    ]
    await actor(connection, "ADMIN001")
    await connection.execute(
        "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='removed for test' "
        "WHERE user_ref_id=$1::uuid AND opportunity_id=$2::uuid",
        fde.user_id,
        project["id"],
    )
    await set_request_context(connection, fde)
    assert not (await repo.visits(connection, project["customer_id"], opportunity_id=project["id"], cursor=cursor))[
        "items"
    ]


async def test_generated_history_date_tracks_source_changes_and_rejects_independent_writes(connection):
    _, project, _ = await fixture(connection, 1)
    visit_id = await connection.fetchval(
        "SELECT id FROM activity.visit WHERE customer_id=$1::uuid", project["customer_id"]
    )
    await connection.execute(
        "UPDATE activity.visit SET recorded_on=NULL,created_at='2026-09-01 23:30Z'::timestamptz WHERE id=$1", visit_id
    )
    assert await connection.fetchval("SELECT history_sort_date FROM activity.visit WHERE id=$1", visit_id) == date(
        2026, 9, 2
    )
    await connection.execute(
        "UPDATE activity.visit SET recorded_on='2026-10-08',created_at='2026-09-03 23:30Z'::timestamptz WHERE id=$1",
        visit_id,
    )
    assert await connection.fetchval("SELECT history_sort_date FROM activity.visit WHERE id=$1", visit_id) == date(
        2026, 10, 8
    )
    await connection.execute("UPDATE activity.visit SET recorded_on=NULL WHERE id=$1", visit_id)
    assert await connection.fetchval("SELECT history_sort_date FROM activity.visit WHERE id=$1", visit_id) == date(
        2026, 9, 4
    )
    with pytest.raises(asyncpg.GeneratedAlwaysError):
        async with connection.transaction():
            await connection.execute("UPDATE activity.visit SET history_sort_date='2026-01-01' WHERE id=$1", visit_id)
    assert (
        await connection.fetchval(
            "SELECT attgenerated::text FROM pg_attribute "
            "WHERE attrelid='activity.visit'::regclass AND attname='history_sort_date'"
        )
        == "s"
    )


async def test_visit_cursor_endpoint_validates_boundary_and_keeps_legacy_offset(connection):
    from tests.integration.test_operations_api import client_for
    from tests.integration.test_profile_scores import business_login

    _, project, _ = await fixture(connection)
    params = {"customer_id": project["customer_id"], "opportunity_id": project["id"], "page_size": 7}
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        first = await client.get("/api/v1/visits", params=params)
        assert first.status_code == 200 and first.json()["next_offset"] == 7
        cursor = first.json()["next_cursor"]
        second = await client.get("/api/v1/visits", params={**params, "cursor": cursor})
        assert second.status_code == 200 and second.json()["next_offset"] is None
        assert not {row["id"] for row in first.json()["items"]} & {row["id"] for row in second.json()["items"]}
        assert (await client.get("/api/v1/visits", params={**params, "cursor": cursor, "offset": 7})).status_code == 422
        assert (await client.get("/api/v1/visits", params={**params, "cursor": "%%%"})).status_code == 422
        await business_login(client, "XS002")
        assert (await client.get("/api/v1/visits", params={**params, "cursor": cursor})).status_code == 404


async def test_created_desc_uses_actual_entry_time_and_stable_pages_independent_of_manual_dates(connection):
    sales, project, people = await fixture(connection, 0)
    # Archived timestamps are immutable: set real entry dates at INSERT, never
    # weaken the archive guard just to build an ordering fixture.
    await connection.execute(
        """INSERT INTO activity.visit(
        workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,
        created_by_user_ref_id,form_version_id,status,created_at,interaction_at,recorded_on,
        follow_up_record,next_action)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$4::uuid,
        (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
        CASE WHEN n%2=0 THEN 'archived' ELSE 'pending_confirm' END,
        '2026-09-16T00:00Z'::timestamptz+(n%4)::int*interval '1 second',
        '2026-09-01T00:00Z'::timestamptz+(30-n)::int*interval '1 day',
        '2026-01-01'::date+(n%3)::int,'排序测试','核验分页' FROM generate_series(1,30) n""",
        sales.workspace_id,
        project["customer_id"],
        project["id"],
        sales.user_id,
        sales.team_ids[0],
    )
    repo = DetailHistoryRepository()
    expected = await connection.fetch(
        "SELECT id::text FROM activity.visit WHERE customer_id=$1::uuid ORDER BY created_at DESC,id DESC",
        project["customer_id"],
    )
    for code in ["XS001", people["lead"]["code"]]:
        await actor(connection, code)
        for opportunity in (None, project["id"]):
            seen = []
            cursor = None
            for _ in range(6):
                page = await repo.visits(
                    connection,
                    project["customer_id"],
                    opportunity_id=opportunity,
                    limit=7,
                    cursor=cursor,
                    sort="created_desc",
                )
                assert page["sort"] == "created_desc"
                seen.extend(row["id"] for row in page["items"])
                assert all(row["created_at"].year == 2026 for row in page["items"])
                if not page["has_more"]:
                    break
                cursor = page["next_cursor"]
            assert seen == [r["id"] for r in expected] and len(set(seen)) == 30
    await actor(connection, "XS001")
    first = await repo.visits(connection, project["customer_id"], limit=7, sort="created_desc")
    await histories(connection, sales, project, 1)
    await connection.execute(
        "UPDATE activity.visit SET created_at='2027-01-01T00:00Z' WHERE customer_id=$1::uuid AND id<>ALL($2::uuid[])",
        project["customer_id"],
        [r["id"] for r in expected],
    )
    second = await repo.visits(
        connection, project["customer_id"], limit=7, sort="created_desc", cursor=first["next_cursor"]
    )
    assert [r["id"] for r in second["items"]] == [r["id"] for r in expected][7:14]
    with pytest.raises(ValueError):
        await repo.visits(connection, project["customer_id"], cursor=first["next_cursor"])
    await actor(connection, "XS002")
    assert not (
        await repo.visits(connection, project["customer_id"], sort="created_desc", cursor=first["next_cursor"])
    )["items"]


async def test_created_sort_api_echo_and_default_backward_compatibility(connection):
    from tests.integration.test_operations_api import client_for
    from tests.integration.test_profile_scores import business_login

    _, project, _ = await fixture(connection, 5)
    params = {"customer_id": project["customer_id"], "page_size": 2}
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        default = await client.get("/api/v1/visits", params=params)
        assert default.status_code == 200 and "sort" not in default.json()
        created = await client.get("/api/v1/visits", params={**params, "sort": "created_desc"})
        assert created.status_code == 200 and created.json()["sort"] == "created_desc"
        assert all("created_at" in v for v in created.json()["items"])
        assert (await client.get("/api/v1/visits", params={**params, "sort": "invalid"})).status_code == 422
        assert (
            await client.get(
                "/api/v1/visits", params={**params, "sort": "created_desc", "cursor": default.json()["next_cursor"]}
            )
        ).status_code == 422
        assert (
            await client.get("/api/v1/visits", params={**params, "cursor": created.json()["next_cursor"]})
        ).status_code == 422
        assert (
            await client.get(
                "/api/v1/visits",
                params={**params, "sort": "created_desc", "cursor": created.json()["next_cursor"], "offset": 2},
            )
        ).status_code == 422
