"""Real HTTP/runtime-RLS meeting draft, confirmation and existing task workflow."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.services.task_imports import TaskImportService
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def no_external_model(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise ValueError("isolated test has no external model")

    monkeypatch.setattr("sales_backend.services.task_imports.load_runtime_configuration", unavailable)


async def preview(client, source="XS001：\n整理周会第一条方案\nXS002：\n完成第二条技术评审", **updates):
    response = await client.post(
        "/api/v1/task-imports/preview",
        json={
            "source_text": source,
            "meeting_date": datetime.now(UTC).date().isoformat(),
            "batch_name": "隔离周会",
            **updates,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirmation(batch):
    due = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    return {
        "version_no": batch["version_no"],
        "items": [
            {
                key: row.get(key)
                for key in (
                    "item_id",
                    "description",
                    "assignee_account_code",
                    "priority_code",
                    "association_kind",
                    "customer_id",
                    "opportunity_id",
                )
            }
            | {"due_at": due, "duplicate_acknowledged": False}
            for row in batch["items"]
        ],
    }


async def submit(client, batch, body=None, key=None):
    return await client.post(
        f"/api/v1/task-imports/{batch['id']}/confirm",
        json=body or confirmation(batch),
        headers={"Idempotency-Key": key or str(uuid4())},
    )


async def test_preview_does_not_create_tasks_and_same_source_recovers_draft(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client)
        assert batch["parse_method"] == "rules" and batch["warnings"]
        assert batch["items"][1]["assignee_account_code"] == "XS002"
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 0
        assert (await preview(client))["id"] == batch["id"]
        assert (await client.get("/api/v1/task-imports")).json()["items"][0]["id"] == batch["id"]
        await business_login(client, "XS002")
        assert (await client.get("/api/v1/task-imports/" + batch["id"])).status_code == 404


async def test_list_is_paged_and_contains_only_headers_not_source_or_task_payloads(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batches = [await preview(client, f"XS001：\n核对第{n}份周会方案") for n in range(4)]
        first = (await client.get("/api/v1/task-imports?page_size=2")).json()
        second = (await client.get("/api/v1/task-imports?page_size=2&offset=2")).json()
        assert first["has_more"] and first["next_offset"] == 2
        assert not second["has_more"] and second["next_offset"] is None
        rows = first["items"] + second["items"]
        assert {row["id"] for row in rows} == {batch["id"] for batch in batches}
        assert all(not {"source_text", "items", "editable_items", "task_ids", "tasks"} & row.keys() for row in rows)
        await business_login(client, "XS002")
        assert (await client.get("/api/v1/task-imports")).json()["items"] == []


@pytest.mark.parametrize("complete", [True, False])
async def test_controlled_ai_must_cover_every_source_line_or_use_full_rule_fallback(connection, monkeypatch, complete):
    async def runtime(database, who, defaults, **kwargs):
        assert kwargs["capability"] == "task_import"
        return SimpleNamespace(
            settings=replace(defaults, senseaudio_api_key="synthetic-no-network"), prompt_overrides={}
        )

    async def evaluate(self, **kwargs):
        assert kwargs["mode"] == "task_import"
        lines = kwargs["facts"]["candidates"]
        output = [
            {"source_line": line["source_line"], "description": "整理后的" + line["description"]} for line in lines
        ]
        return SimpleNamespace(payload=kwargs["validate"]({"items": output if complete else output[:1]}))

    monkeypatch.setattr("sales_backend.services.task_imports.load_runtime_configuration", runtime)
    monkeypatch.setattr("sales_backend.services.task_imports.InferenceService.evaluate", evaluate)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client)
        assert len(batch["items"]) == 2
        assert batch["parse_method"] == ("ai" if complete else "rules")
        assert bool(batch["warnings"]) is not complete
        assert [item["assignee_account_code"] for item in batch["items"]] == ["XS001", "XS002"]
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 0


async def test_atomic_confirmation_replay_and_original_creator_review(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client)
        body, key = confirmation(batch), str(uuid4())
        result = await submit(client, batch, body, key)
        assert result.status_code == 200, result.text
        saved = result.json()
        assert saved["status"] == "confirmed" and len(saved["tasks"]) == 2
        listing = (await client.get("/api/v1/task-imports?status=confirmed")).json()
        assert listing["items"][0]["summary"] == saved["summary"]
        assert {t["source_code"] for t in saved["tasks"]} == {"meeting_import"}
        assert (await submit(client, batch, body, key)).json() == saved
        replay = await submit(client, batch, body)
        assert replay.status_code == 200 and {t["id"] for t in replay.json()["tasks"]} == {
            t["id"] for t in saved["tasks"]
        }
        body["items"][0]["description"] = "改成另一条未确认事项"
        assert (await submit(client, batch, body)).status_code == 409
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 2
        other = next(t for t in saved["tasks"] if t["assignees"][0]["account_code"] == "XS002")
        await business_login(client, "XS002")
        visible = (await client.get("/api/v1/task-imports/" + batch["id"])).json()
        assert visible["source_text"] is None and visible["items"] == [] and visible["editable_items"] == []
        assert visible["summary"]["visible_task_count"] == 1
        assert visible["tasks"][0]["original_assignee"]["account_code"] == "XS002"
        assert (
            await client.post("/api/v1/tasks/" + other["id"] + "/events", json={"event_type": "accept"})
        ).status_code == 200
        done = await client.post(
            "/api/v1/tasks/" + other["id"] + "/events", json={"event_type": "complete", "note": "已完成技术评审"}
        )
        assert done.json()["status"] == "pending_review"
        progress = (await client.get("/api/v1/task-imports/" + batch["id"])).json()
        assert progress["summary"]["completed"] == 0 and progress["summary"]["pending_review"] == 1
        await business_login(client, "XS001")
        approved = await client.post(
            "/api/v1/tasks/" + other["id"] + "/events",
            json={"event_type": "approve_completion", "version_no": done.json()["version_no"]},
        )
        assert approved.status_code == 200, approved.text
        assert (await client.get("/api/v1/task-imports/" + batch["id"])).json()["summary"]["completed"] == 1


async def test_invalid_later_recipient_rolls_back_all_tasks_notifications_and_receipt(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        existing = await client.post(
            "/api/v1/tasks",
            json={
                "description": "既有任务与通知必须保留",
                "target_position": "self",
                "association_kind": "daily",
                "due_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            },
        )
        assert existing.status_code == 201, existing.text
        batch = await preview(client)
        await actor(connection, "XS001")
        tasks_before = {row["id"] for row in await connection.fetch("SELECT id FROM workflow.task")}
        notices_before = {row["id"] for row in await connection.fetch("SELECT id FROM workflow.notification")}
        body = confirmation(batch)
        body["items"][1]["assignee_account_code"] = "NOT_A_COMPANY_MEMBER"
        result = await submit(client, batch, body)
        assert result.status_code == 403, result.text
        await actor(connection, "XS001")
        assert {row["id"] for row in await connection.fetch("SELECT id FROM workflow.task")} == tasks_before
        assert {row["id"] for row in await connection.fetch("SELECT id FROM workflow.notification")} == notices_before
        assert (await client.get("/api/v1/task-imports/" + batch["id"])).json()["status"] == "draft"


async def test_draft_persistence_version_and_duplicate_acknowledgement(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client, "XS001：\n整理同一客户完整方案\n整理同一客户完整方案")
        assert all(row["duplicates"] for row in batch["items"])
        body = confirmation(batch)
        assert (await submit(client, batch, body)).status_code == 409
        draft = {"version_no": batch["version_no"], "items": [dict(item, selected=True) for item in body["items"]]}
        updated = await client.patch(f"/api/v1/task-imports/{batch['id']}/draft", json=draft)
        assert updated.status_code == 200, updated.text
        assert updated.json()["version_no"] == batch["version_no"] + 1
        assert (await client.patch(f"/api/v1/task-imports/{batch['id']}/draft", json=draft)).status_code == 409
        restored = (await client.get("/api/v1/task-imports/" + batch["id"])).json()
        assert all(item["due_at"] for item in restored["editable_items"])
        body["version_no"] = restored["version_no"]
        for item in body["items"]:
            item["duplicate_acknowledged"] = True
        result = await submit(client, batch, body)
        assert result.status_code == 200, result.text
        assert len(result.json()["tasks"]) == 2


async def test_revoked_create_blocks_confirm_but_read_remains(connection):
    who = await actor(connection, "XS001")
    admin = await actor(connection, "ADMIN001")
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client)
        await grant(
            connection,
            admin,
            who.user_id,
            [
                dict(permission="task.create_daily", effect="deny"),
                dict(permission="task.create_customer", effect="deny"),
            ],
        )
        assert (await submit(client, batch)).status_code == 403
        assert (await client.get("/api/v1/task-imports/" + batch["id"])).status_code == 200


async def test_each_row_uses_actual_task_kind_and_customer_only_grant_can_preview(connection):
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    admin = await actor(connection, "ADMIN001")
    await grant(connection, admin, who.user_id, [dict(permission="task.create_daily", effect="deny")])
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client, "XS001：\n整理客户技术方案\n核对另一个日常事项")
        body = confirmation(batch)
        body["items"][0].update(association_kind="customer", customer_id=op["customer_id"], opportunity_id=op["id"])
        denied = await submit(client, batch, body)
        assert denied.status_code == 403, denied.text
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 0
        assert (await client.get("/api/v1/task-imports/" + batch["id"])).json()["status"] == "draft"
        body["items"] = body["items"][:1]
        created = await submit(client, batch, body)
        assert created.status_code == 200, created.text
        assert created.json()["tasks"][0]["association_kind"] == "customer"


async def test_same_key_after_handover_reads_current_owner_without_creating_again(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client, "XS002：\n核对客户测试方案并准备交付")
        body, key = confirmation(batch), str(uuid4())
        saved = await submit(client, batch, body, key)
        assert saved.status_code == 200, saved.text
        task = saved.json()["tasks"][0]
        moved = await client.post(
            f"/api/v1/tasks/{task['id']}/events",
            json={
                "event_type": "reassign",
                "assignee_account_code": "XS001",
                "note": "人工转交回自己处理",
                "version_no": task["version_no"],
            },
        )
        assert moved.status_code == 200, moved.text
        replay = await submit(client, batch, body, key)
        assert replay.status_code == 200, replay.text
        current = replay.json()["tasks"][0]
        assert current["id"] == task["id"]
        assert current["assignees"][0]["account_code"] == "XS001"
        assert current["original_assignee"]["account_code"] == "XS002"
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 1


async def test_actor_rechecked_after_parse_before_persist(connection, monkeypatch):
    calls = 0
    original = TaskImportService.assert_current

    async def revoke(self, who):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("当前身份已变化")
        await original(self, who)

    monkeypatch.setattr(TaskImportService, "assert_current", revoke)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.post(
            "/api/v1/task-imports/preview",
            json={"source_text": "XS001：\n需要整理测试方案", "meeting_date": "2026-09-28", "batch_name": "不得保存"},
        )
        assert response.status_code == 403
        assert await connection.fetchval("SELECT count(*) FROM workflow.task_import_batch") == 0
