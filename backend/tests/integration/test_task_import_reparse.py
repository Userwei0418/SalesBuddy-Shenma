"""Real HTTP + RLS + trigger protection for explicit draft-only AI reparse."""

from uuid import uuid4

import pytest

from sales_backend.domain.task_imports import candidate_lines, materialize_items
from tests.integration.test_operations_api import client_for
from tests.integration.test_profile_scores import business_login
from tests.integration.test_task_imports import preview, submit

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def no_external_model(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise ValueError("isolated test has no external model")

    monkeypatch.setattr("sales_backend.services.task_imports.load_runtime_configuration", unavailable)


def successful_ai(monkeypatch):
    async def extract(self, actor, request, people, *, require_ai=False):
        assert require_ai
        lines = candidate_lines(request.source_text, people)
        extracted = [
            {"source_line": line["source_line"], "description": line["description"] + "并核对验收结果"}
            for line in lines
        ]
        return materialize_items(extracted, lines, people, request.meeting_date), "ai", []

    monkeypatch.setattr("sales_backend.services.task_imports.TaskImportService._extract", extract)


async def test_reparse_same_batch_does_not_dispatch_or_mutate_immutable_source(connection, monkeypatch):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client, source="XS001：\n整理重新核对测试方案")
        before = dict(
            await connection.fetchrow(
                "SELECT source_text,source_hash,meeting_date,batch_name,creator_user_ref_id "
                "FROM workflow.task_import_batch WHERE id=$1::uuid",
                batch["id"],
            )
        )
        successful_ai(monkeypatch)
        response = await client.post(
            f"/api/v1/task-imports/{batch['id']}/reparse", json={"version_no": batch["version_no"]}
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["id"] == batch["id"] and updated["version_no"] == batch["version_no"] + 1
        assert updated["parse_method"] == "ai" and "验收结果" in updated["items"][0]["description"]
        assert (
            dict(
                await connection.fetchrow(
                    "SELECT source_text,source_hash,meeting_date,batch_name,creator_user_ref_id "
                    "FROM workflow.task_import_batch WHERE id=$1::uuid",
                    batch["id"],
                )
            )
            == before
        )
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 0
        assert await connection.fetchval("SELECT count(*) FROM workflow.task_import_batch") == 1
        # An uncertain caller must refresh; replaying an old version never reparses again.
        assert (
            await client.post(f"/api/v1/task-imports/{batch['id']}/reparse", json={"version_no": batch["version_no"]})
        ).status_code == 409
        assert (
            await client.post(
                f"/api/v1/task-imports/{batch['id']}/reparse",
                json={"version_no": updated["version_no"], "source_text": "different"},
            )
        ).status_code == 422
        await business_login(client, "XS002")
        assert (
            await client.post(f"/api/v1/task-imports/{batch['id']}/reparse", json={"version_no": updated["version_no"]})
        ).status_code == 404


async def test_reparse_failure_keeps_rules_draft_and_edit_conflict_is_explicit(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client, source="XS001：\n保留人工核对测试方案")
        path = f"/api/v1/task-imports/{batch['id']}"
        failed = await client.post(path + "/reparse", json={"version_no": batch["version_no"]})
        assert failed.status_code == 409
        saved = (await client.get(path)).json()
        assert saved["version_no"] == batch["version_no"] and saved["items"] == batch["items"]
        fields = {
            "item_id",
            "selected",
            "description",
            "assignee_account_code",
            "due_at",
            "priority_code",
            "association_kind",
            "customer_id",
            "opportunity_id",
            "duplicate_acknowledged",
        }
        edits = [{k: v for k, v in item.items() if k in fields} for item in saved["editable_items"]]
        edits[0]["description"] = "人工更改不能被AI覆盖"
        response = await client.patch(
            path + "/draft",
            json={"version_no": saved["version_no"], "items": edits},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 200, response.text
        changed = response.json()
        blocked = await client.post(path + "/reparse", json={"version_no": changed["version_no"]})
        assert blocked.status_code == 409 and "人工编辑" in blocked.text
        assert (await client.get(path)).json()["editable_items"][0]["description"] == edits[0]["description"]
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 0


async def test_confirmed_import_cannot_be_reparsed(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        batch = await preview(client, source="XS001：\n确认后不可重新整理的事项")
        confirmed = await submit(client, batch)
        assert confirmed.status_code == 200, confirmed.text
        row = confirmed.json()
        blocked = await client.post(
            f"/api/v1/task-imports/{batch['id']}/reparse", json={"version_no": row["version_no"]}
        )
        assert blocked.status_code == 409
        assert await connection.fetchval("SELECT count(*) FROM workflow.task") == 1
