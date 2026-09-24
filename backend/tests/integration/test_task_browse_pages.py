"""Bounded task cards and full counters under the existing runtime-role RLS."""

import asyncio

import pytest
from tests.integration.feishu_fixtures import seed_execute

from sales_backend.repositories.task_browse import TaskBrowseRepository
from sales_backend.repositories.tasks import TaskRepository
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture, make_task
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def test_task_pages_filter_before_paging_with_full_counts_and_light_bodies(connection):
    async with asyncio.timeout(30):
        await connection.execute("SET LOCAL statement_timeout='10s'")
        project = await opportunity(connection, "XS001", 100)
        person = await actor(connection, "XS001")
        await seed_execute(
            connection, """WITH inserted AS (
              INSERT INTO workflow.task(workspace_id,customer_id,opportunity_id,creator_user_ref_id,
                creator_team_id,title,description,status,created_at,due_at,completed_at)
              SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,'分页任务'||n,repeat('任务说明',500),
                CASE WHEN n>40 THEN 'completed' ELSE 'pending_execution' END,
                '2026-09-01 00:00+08'::timestamptz,
                '2026-09-01 00:00+08'::timestamptz+n*interval '1 day',
                CASE WHEN n>40 THEN '2026-09-30 23:59+08'::timestamptz END
              FROM generate_series(1,45) n RETURNING id,workspace_id)
              INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,
                assignee_team_id,assignee_role,responsibility)
              SELECT id,workspace_id,$4::uuid,$5::uuid,'sales','owner' FROM inserted""",
            person.workspace_id,
            project["customer_id"],
            project["id"],
            person.user_id,
            person.team_ids[0],
        )
        repo = TaskBrowseRepository()
        filters = dict(
            customer_id=project["customer_id"],
            member_id=person.user_id,
            completed_year=2026,
            completed_quarters=[3],
            order="due_asc",
        )
        first = await repo.page(connection, **filters)
        assert first["summary"] == dict(
            total=45, pending_count=40, completed_count=5, rejected_count=0, filtered_total=40
        )
        assert len(first["items"]) == 20 and first["next_offset"] == 20
        assert all(len(t["description"]) == 600 and "import_meta" not in t for t in first["items"])
        second = await repo.page(connection, offset=20, **filters)
        assert len(second["items"]) == 20 and not second["has_more"]
        assert not {t["id"] for t in first["items"]} & {t["id"] for t in second["items"]}
        assert first["items"][-1]["due_at"] < second["items"][0]["due_at"]
        done = await repo.page(connection, tab="completed", **filters)
        assert len(done["items"]) == done["summary"]["filtered_total"] == 5
        filters["completed_quarters"] = [4]
        no_completed = await repo.page(connection, tab="completed", **filters)
        assert no_completed["items"] == [] and no_completed["summary"]["total"] == 40
        await actor(connection, "XS002")
        invisible = await repo.page(connection, **filters)
        assert invisible["items"] == [] and invisible["summary"]["total"] == 0


async def test_fde_pages_preserve_personal_and_department_scope(connection):
    async with asyncio.timeout(30):
        sales, project, people, _ = await fde_fixture(connection)
        task = await make_task(connection, sales, project)
        repo = TaskBrowseRepository()
        for code, view in [(people["first"]["code"], "self"), (people["lead"]["code"], "team")]:
            await actor(connection, code)
            legacy = await TaskRepository().list(
                connection, status=None, customer_id=project["customer_id"], limit=100, fde_view=view
            )
            result = await repo.page(connection, tab="all", customer_id=project["customer_id"], fde_view=view)
            assert {t["id"] for t in result["items"]} == {t["id"] for t in legacy} == {task["id"]}
            assert result["summary"]["total"] == 1
        lead = await actor(connection, people["lead"]["code"])
        personal = await repo.page(connection, tab="all", customer_id=project["customer_id"], fde_view="self")
        assert personal["summary"]["total"] == 0  # The lead can coordinate, but is not a candidate/owner.
        team_narrowed = await repo.page(connection, tab="all", fde_view="team", member_id=lead.user_id)
        assert team_narrowed["summary"]["total"] == 0


async def test_task_page_query_contract_and_invalid_completion_period(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get(
            "/api/v1/tasks",
            params={
                "tab": "pending",
                "page_size": 20,
                "completed_year": 2026,
                "completed_quarters": [1, 3],
                "order": "due_asc",
            },
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) <= 20 and "filtered_total" in response.json()["summary"]
        for params in (
            {"tab": "wrong"},
            {"tab": "all", "completed_quarters": [3]},
            {"tab": "all", "completed_year": 2026, "completed_quarters": [5]},
        ):
            assert (await client.get("/api/v1/tasks", params=params)).status_code == 422
