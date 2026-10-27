"""Exact source archives through HTTP and real restricted PostgreSQL roles.

All source descriptions and bytes are synthetic. No Feishu or provider calls.
"""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sales_backend.api.dependencies import RequestIdentity, get_identity
from sales_backend.api.knowledge import router as knowledge_router
from sales_backend.api.knowledge_distribution import public_router
from sales_backend.api.knowledge_distribution import router as distribution_router
from sales_backend.api.knowledge_distribution import service as distribution_service
from sales_backend.api.knowledge_source_files import router
from sales_backend.contracts.knowledge_distribution import PublicationWrite
from sales_backend.repositories.identity import ActorRecord
from sales_backend.services.knowledge_distribution import DistributionService

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection
from .test_knowledge_distribution import answer, no_sync
from .test_knowledge_import import headers, payload

pytestmark = pytest.mark.asyncio
PREFIX = "/api/v1/knowledge"
PDF = b"%PDF-1.4\nSynthetic source archive\n%%EOF\n"


@asynccontextmanager
async def client(kb, name="owner"):
    app = FastAPI()
    app.state.database = kb.database
    app.state.settings = kb.settings
    app.include_router(knowledge_router)
    app.include_router(router)
    app.include_router(distribution_router)
    app.include_router(public_router)
    app.dependency_overrides[distribution_service] = lambda: DistributionService(kb.database, enqueue=no_sync)
    actor = kb.actors[name]
    app.dependency_overrides[get_identity] = lambda: RequestIdentity(
        actor, "synthetic-session", ActorRecord(actor, "test", "Test", ()), must_change_password=False
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def source_request(kb, *, count=1, status="可用"):
    request = payload(kb)
    request["records"] = [request["records"][0]]
    request["records"][0].update(
        category_label="客户方案",
        source_status=status,
        attachments=[
            {"name": f"source-{i}.pdf", "source_token": f"sourceToken{i}", "size_bytes": len(PDF)} for i in range(count)
        ],
    )
    return request


async def prepare(kb, c, *, count=1, status="可用"):
    request = source_request(kb, count=count, status=status)
    response = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    path = PREFIX + f"/materials/{item['material_id']}/source-files"
    state = await c.get(path)
    assert state.status_code == 200, state.text
    files = []
    for attachment in request["records"][0]["attachments"]:
        uploaded = await c.post(
            PREFIX + "/files", files={"file": (attachment["name"], PDF, "application/pdf")}, headers=headers()
        )
        assert uploaded.status_code == 201, uploaded.text
        files.append({**attachment, "file_id": uploaded.json()["file_id"]})
    return request, path, state.json(), files


def archive_input(state, files):
    return {
        "expected_material_revision": state["material_revision"],
        "expected_acl_revision": state["acl_revision"],
        "expected_source_revision": state["source_revision"],
        "attachments": files,
    }


async def version_files(kb, version_id):
    return [
        str(row["file_id"])
        for row in await kb.admin.fetch(
            "SELECT file_id FROM knowledge.version_file WHERE workspace_id=$1 AND version_id=$2 ORDER BY ordinal",
            kb.workspace,
            UUID(version_id),
        )
    ]


async def test_archive_keeps_identity_source_acl_hash_and_old_version_without_publication_or_parsing(kb):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c, count=2)
        mid = UUID(state["material_id"])
        before = await kb.admin.fetchrow(
            "SELECT source_hash,imported_acl_revision FROM knowledge.source_binding WHERE material_id=$1", mid
        )
        original = (await c.get(PREFIX + f"/materials/{mid}")).json()
        response = await c.post(path, json=archive_input(state, files), headers=headers())
        assert response.status_code == 200, response.text
        after = response.json()
        assert after["changed"] and after["can_archive"]
        assert after["material_id"] == state["material_id"]
        assert after["material_revision"] == after["version_no"] == 2
        assert after["acl_revision"] == state["acl_revision"] == 1
        assert after["source_revision"] == state["source_revision"]
        assert after["attachments"] == state["attachments"]
        assert [f["file_id"] for f in after["files"]] == [f["file_id"] for f in files]
        assert {f["sha256"] for f in after["files"]} == {hashlib.sha256(PDF).hexdigest()}
        current = (await c.get(PREFIX + f"/materials/{mid}")).json()
        assert current["source"] == original["source"]
        assert current["versions"][0]["contribution"] == original["versions"][0]["contribution"]
        assert current["versions"][1] == original["versions"][0]
        assert current["active_version_id"] is None and current["state"] == "saved"
        assert current["source"]["validation_issues"] == original["source"]["validation_issues"]
        assert await version_files(kb, state["version_id"]) == []
        assert await version_files(kb, after["version_id"]) == [f["file_id"] for f in files]
        assert (
            await kb.admin.fetchrow(
                "SELECT source_hash,imported_acl_revision FROM knowledge.source_binding WHERE material_id=$1", mid
            )
            == before
        )
        assert (await c.get(PREFIX + "/materials")).json()["total"] == 1
        stats = (await c.get(PREFIX + "/stats")).json()["metrics"]
        assert stats["total"]["count"] == 1 and stats["retrievable"]["count"] == 0
        for table in ["provider_binding", "ingestion", "distribution_publication"]:
            assert (
                await kb.admin.fetchval(
                    f"SELECT count(*) FROM knowledge.{table} WHERE workspace_id=$1",  # noqa: S608 -- fixed test tables
                    kb.workspace,
                )
                == 0
            )
        assert (
            await kb.admin.fetchval(
                "SELECT count(*) FROM ops.feishu_event WHERE workspace_id=$1 AND object_kind='knowledge'", kb.workspace
            )
            == 0
        )
        raw = await c.get(PREFIX + f"/files/{files[0]['file_id']}/download", params={"version_id": after["version_id"]})
        assert raw.status_code == 200 and raw.content == PDF
        unchanged = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert unchanged.status_code == 200 and unchanged.json()["counts"]["unchanged"] == 1, unchanged.text
        assert unchanged.json()["items"][0]["version_id"] == after["version_id"]


async def test_same_key_concurrency_and_new_key_noop_without_edit_reauthorize_current_permissions(kb):
    await kb.grant_scope("owner", None, effect="deny", action="edit")
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        body = archive_input(state, files)
        key = headers()
        responses = await asyncio.gather(*[c.post(path, json=body, headers=key) for _ in range(3)])
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        assert all(r.json() == responses[0].json() for r in responses)
        current = responses[0].json()
        same = await c.post(path, json=archive_input(current, files), headers=headers())
        assert same.status_code == 200 and same.json()["changed"] is False, same.text
        assert same.json()["version_id"] == current["version_id"]
        conflict = await c.post(path, json=archive_input(current, files), headers=key)
        assert conflict.status_code == 409 and conflict.json()["detail"] == "IDEMPOTENCY_CONFLICT"
        await kb.grant_scope("owner", None, effect="deny", action="manage")
        for key_to_use, request_body in [(key, body), (headers(), archive_input(current, files))]:
            denied = await c.post(path, json=request_body, headers=key_to_use)
            assert denied.status_code == 403 and denied.json()["detail"] == "KNOWLEDGE_SOURCE_ARCHIVE_DENIED"
        assert (await c.get(path)).status_code == 403
        assert (
            await kb.admin.fetchval("SELECT count(*) FROM knowledge.version WHERE workspace_id=$1", kb.workspace) == 2
        )


async def test_two_fresh_keys_with_same_expected_revision_cannot_create_duplicate_versions(kb):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        responses = await asyncio.gather(
            *[c.post(path, json=archive_input(state, files), headers=headers()) for _ in range(2)]
        )
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert (await c.get(path)).json()["version_no"] == 2


async def test_incremental_archive_cannot_omit_existing_files_to_bypass_attach_authority(kb):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c, count=2)
        saved = (await c.post(path, json=archive_input(state, files[:1]), headers=headers())).json()
        await kb.grant_scope("owner", None, effect="deny", action="edit")
        for submitted in [files[1:], files]:
            response = await c.post(path, json=archive_input(saved, submitted), headers=headers())
            assert response.status_code == 403 and response.json()["detail"] == "KNOWLEDGE_FILE_DENIED", response.text
        assert (await c.get(path)).json()["version_id"] == saved["version_id"]
        noop = await c.post(path, json=archive_input(saved, files[:1]), headers=headers())
        assert noop.status_code == 200 and noop.json()["changed"] is False
        await kb.grant_scope("owner", "workspace", action="edit")
        allowed = await c.post(path, json=archive_input(saved, files[1:]), headers=headers())
        assert allowed.status_code == 200 and len(allowed.json()["files"]) == 2, allowed.text


@pytest.mark.parametrize("field", ["expected_material_revision", "expected_acl_revision", "expected_source_revision"])
@pytest.mark.parametrize("direction", [-1, 1])
async def test_old_and_future_revisions_rejected_without_write(kb, field, direction):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        # Ensure the stale value is valid protocol shape (positive revisions).
        if field == "expected_material_revision":
            first = await c.post(path, json=archive_input(state, files), headers=headers())
            state = first.json()
        elif field == "expected_acl_revision":
            changed = await c.put(
                PREFIX + f"/materials/{state['material_id']}/acl",
                json={
                    "expected_acl_revision": 1,
                    "grants": [{"subject_kind": "company", "actions": ["read"]}],
                },
                headers=headers(),
            )
            assert changed.status_code == 200, changed.text
            state = (await c.get(path)).json()
        body = archive_input(state, files)
        body[field] += direction
        response = await c.post(path, json=body, headers=headers())
        assert response.status_code == 409 and response.json()["detail"] == "KNOWLEDGE_SOURCE_REVISION_CONFLICT", (
            response.text
        )
        assert (await c.get(path)).json()["version_id"] == state["version_id"]


@pytest.mark.parametrize(
    "field,value", [("source_token", "unknownToken"), ("name", "wrong.pdf"), ("size_bytes", len(PDF) + 1)]
)
async def test_exact_source_attachment_tuple_required(kb, field, value):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        files[0][field] = value
        response = await c.post(path, json=archive_input(state, files), headers=headers())
        assert response.status_code == 422 and response.json()["detail"] == "KNOWLEDGE_SOURCE_ATTACHMENT_MISMATCH"
        assert (await c.get(path)).json()["files"] == []


@pytest.mark.parametrize("variant", ["size", "name", "other_user", "other_company"])
async def test_actual_uploaded_file_must_match_and_be_attachable(kb, variant):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        actor = kb.actors["reader" if variant == "other_user" else "other" if variant == "other_company" else "owner"]
        uploaded = await kb.service.upload(
            actor,
            uuid4(),
            "wrong.pdf" if variant == "name" else "source-0.pdf",
            "application/pdf",
            PDF + b"x" if variant == "size" else PDF,
        )
        files[0]["file_id"] = str(uploaded["file_id"])
        response = await c.post(path, json=archive_input(state, files), headers=headers())
        expected = 422 if variant in {"size", "name"} else 403
        assert response.status_code == expected, response.text
        assert response.json()["detail"] == (
            "KNOWLEDGE_SOURCE_FILE_MISMATCH" if expected == 422 else "KNOWLEDGE_FILE_DENIED"
        )
        assert (await c.get(path)).json()["files"] == []


async def test_existing_token_different_bytes_is_conflict_and_new_upload_same_bytes_is_noop(kb):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        saved = (await c.post(path, json=archive_input(state, files), headers=headers())).json()
        for content, expected in [(PDF, 200), (PDF.replace(b"Synthetic", b"Different"), 409)]:
            assert len(content) == len(PDF)
            upload = await c.post(
                PREFIX + "/files", files={"file": ("source-0.pdf", content, "application/pdf")}, headers=headers()
            )
            files[0]["file_id"] = upload.json()["file_id"]
            response = await c.post(path, json=archive_input(saved, files), headers=headers())
            assert response.status_code == expected, response.text
            if expected == 200:
                assert response.json()["changed"] is False and response.json()["files"] == saved["files"]
            else:
                assert response.json()["detail"] == "KNOWLEDGE_SOURCE_FILE_ALREADY_ARCHIVED"


async def test_same_file_cannot_be_attached_to_another_source_token_on_later_request(kb):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c, count=2)
        request["source_revision"] += 1
        request["records"][0]["attachments"][1]["name"] = files[0]["name"]
        update = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert update.status_code == 200
        state = (await c.get(path)).json()
        saved = (await c.post(path, json=archive_input(state, files[:1]), headers=headers())).json()
        repeated = {**files[0], "source_token": files[1]["source_token"]}
        response = await c.post(path, json=archive_input(saved, [repeated]), headers=headers())
        assert response.status_code == 422 and response.json()["detail"] == "KNOWLEDGE_SOURCE_FILE_DUPLICATE"
        assert (await c.get(path)).json()["version_id"] == saved["version_id"]


async def test_revision_only_reimport_and_partial_archive_then_metadata_retains_exact_files(kb):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c, count=2)
        first = (await c.post(path, json=archive_input(state, files[:1]), headers=headers())).json()
        request["source_revision"] += 1
        revision_only = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert revision_only.json()["counts"]["unchanged"] == 1
        revision_state = (await c.get(path)).json()
        assert revision_state["source_revision"] == request["source_revision"]
        assert revision_state["version_id"] == first["version_id"]
        second = await c.post(path, json=archive_input(revision_state, files[1:]), headers=headers())
        assert second.status_code == 200, second.text
        second = second.json()
        assert [x["file_id"] for x in second["files"]] == [x["file_id"] for x in files]
        assert await version_files(kb, first["version_id"]) == [files[0]["file_id"]]
        request["source_revision"] += 1
        request["records"][0]["original_name"] = "Source metadata updated"
        update = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert update.status_code == 200 and update.json()["counts"]["updated"] == 1, update.text
        current = (await c.get(path)).json()
        assert current["files"] == second["files"]
        assert current["version_no"] == 4 and current["material_id"] == state["material_id"]
        assert (await c.get(PREFIX + "/materials")).json()["total"] == 1
        assert (await c.get(PREFIX + "/stats")).json()["metrics"]["retrievable"]["count"] == 0


@pytest.mark.parametrize("change", ["delete", "token", "name", "size", "disabled", "review"])
async def test_reimport_never_carries_removed_replaced_or_unavailable_files(kb, change):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c, count=2)
        before = (await c.post(path, json=archive_input(state, files), headers=headers())).json()
        request["source_revision"] += 1
        record = request["records"][0]
        if change == "delete":
            record["attachments"].pop(0)
        elif change == "token":
            record["attachments"][0]["source_token"] = "replacementToken"
        elif change == "name":
            record["attachments"][0]["name"] = "replacement.pdf"
        elif change == "size":
            record["attachments"][0]["size_bytes"] += 1
        else:
            record["source_status"] = "已停用" if change == "disabled" else "待复核"
        update = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert update.status_code == 200 and update.json()["counts"]["updated"] == 1, update.text
        current = (await c.get(path)).json()
        expected = [] if change in {"disabled", "review"} else [files[1]["file_id"]]
        assert await version_files(kb, current["version_id"]) == expected
        assert [x["file_id"] for x in current["files"]] == expected
        assert await version_files(kb, before["version_id"]) == [x["file_id"] for x in files]
        assert current["can_archive"] is (change not in {"disabled", "review"})


async def test_archive_does_not_move_existing_publication_or_generate_notification(kb):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c, count=2)
        first = (await c.post(path, json=archive_input(state, files[:1]), headers=headers())).json()
        svc = DistributionService(kb.database, enqueue=no_sync)
        publication = await svc.publish(
            kb.actors["owner"],
            uuid4(),
            UUID(state["material_id"]),
            PublicationWrite(expected_revision=0, version_id=first["version_id"]),
        )
        second = await c.post(path, json=archive_input(first, files[1:]), headers=headers())
        assert second.status_code == 200, second.text
        row = await kb.admin.fetchrow(
            "SELECT version_id,revision FROM knowledge.distribution_publication WHERE material_id=$1",
            UUID(state["material_id"]),
        )
        assert str(row["version_id"]) == first["version_id"] and row["revision"] == publication["revision"]
        assert (
            await kb.admin.fetchval(
                "SELECT count(*) FROM ops.feishu_event WHERE workspace_id=$1 AND object_kind='knowledge'", kb.workspace
            )
            == 0
        )


@pytest.mark.parametrize("status", ["待复核", "已停用"])
async def test_latest_source_status_blocks_old_share_and_grant_then_restores_existing_share_policy(kb, status):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c)
        archived = (await c.post(path, json=archive_input(state, files), headers=headers())).json()
        publication_path = PREFIX + f"/distribution/materials/{state['material_id']}/publication"
        published = await c.put(
            publication_path, json={"expected_revision": 0, "version_id": archived["version_id"]}, headers=headers()
        )
        assert published.status_code == 200, published.text
        publication = published.json()
        share = await c.post(
            PREFIX + f"/distribution/materials/{state['material_id']}/shares",
            json={
                "publication_id": publication["publication_id"],
            },
            headers=headers(),
        )
        assert share.status_code == 201, share.text
        token_headers = {"X-Knowledge-Share": share.json()["token"]}
        public = "/api/v1/public/knowledge"
        meta = await c.get(public + "/share", params={"browser_id": "browser-synthetic-0001"}, headers=token_headers)
        assert meta.status_code == 200
        grant = await c.post(
            public + "/responses", json=answer().model_dump(mode="json"), headers={**token_headers, **headers()}
        )
        assert grant.status_code == 200, grant.text
        access = {**token_headers, "X-Knowledge-Grant": grant.json()["grant_token"]}
        download = public + f"/files/{files[0]['file_id']}/download"
        preview = public + f"/files/{files[0]['file_id']}/preview"
        assert (await c.get(download, headers=access)).content == PDF
        assert (await c.get(PREFIX + "/distribution/materials")).json()["total"] == 1
        request["source_revision"] += 1
        request["records"][0]["source_status"] = status
        updated = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert updated.status_code == 200 and updated.json()["counts"]["updated"] == 1, updated.text
        assert (await c.get(PREFIX + "/distribution/materials")).json()["total"] == 0
        for endpoint, params in [
            (public + "/share", {"browser_id": "browser-synthetic-0001"}),
            (download, {}),
            (preview, {}),
        ]:
            denied = await c.get(endpoint, params=params, headers=access)
            assert denied.status_code == 404 and denied.json()["detail"] == "KNOWLEDGE_SHARE_UNAVAILABLE", denied.text
        denied = await c.post(
            public + "/responses", json=answer().model_dump(mode="json"), headers={**token_headers, **headers()}
        )
        assert denied.status_code == 404
        # A valid older archived version may not bypass the current source state.
        denied = await c.put(
            publication_path, json={"expected_revision": 1, "version_id": archived["version_id"]}, headers=headers()
        )
        assert denied.status_code == 422 and denied.json()["detail"] == "KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED"
        latest = (await c.get(path)).json()
        denied = await c.put(
            publication_path, json={"expected_revision": 1, "version_id": latest["version_id"]}, headers=headers()
        )
        assert denied.status_code == 422
        request["source_revision"] += 1
        request["records"][0]["source_status"] = "可用"
        restored = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert restored.status_code == 200 and restored.json()["counts"]["updated"] == 1
        # Source availability is a live guard, not a permanent share revocation.
        # A still-enabled, non-revoked share resumes; no new publication is created.
        assert (await c.get(PREFIX + "/distribution/materials")).json()["total"] == 1
        assert (await c.get(download, headers=access)).content == PDF
        assert (
            await c.get(public + "/share", params={"browser_id": "browser-synthetic-0001"}, headers=token_headers)
        ).status_code == 200
        assert (
            await kb.admin.fetchval(
                "SELECT count(*) FROM knowledge.distribution_publication WHERE workspace_id=$1", kb.workspace
            )
            == 1
        )
        assert (
            await kb.admin.fetchval(
                "SELECT count(*) FROM knowledge.audit WHERE workspace_id=$1 AND operation='distribution.publish'",
                kb.workspace,
            )
            == 1
        )


async def test_preexisting_bad_published_legacy_snapshot_cannot_bypass_valid_latest_source(kb):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c)
        archived = (await c.post(path, json=archive_input(state, files), headers=headers())).json()
        publication_path = PREFIX + f"/distribution/materials/{state['material_id']}/publication"
        published = await c.put(
            publication_path, json={"expected_revision": 0, "version_id": archived["version_id"]}, headers=headers()
        )
        assert published.status_code == 200
        request["source_revision"] += 1
        request["records"][0]["remarks"] = "A newer valid source snapshot"
        updated = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert updated.status_code == 200
        # Synthetic pre-migration bad published data, never an authorized product write.
        await kb.admin.execute(
            "UPDATE knowledge.version SET contribution=jsonb_set(contribution,'{source,source_status}',"
            "to_jsonb('待复核'::text)) WHERE workspace_id=$1 AND id=$2",
            kb.workspace,
            UUID(archived["version_id"]),
        )
        assert (await c.get(path)).json()["can_archive"] is True
        assert (await c.get(PREFIX + "/distribution/materials")).json()["total"] == 0
        denied = await c.put(
            publication_path, json={"expected_revision": 1, "version_id": archived["version_id"]}, headers=headers()
        )
        assert denied.status_code == 422 and denied.json()["detail"] == "KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED"


@pytest.mark.parametrize("action", ["catalog_manage", "read", "manage"])
async def test_permission_revocation_and_cross_tenant_metadata_and_replay_are_closed(kb, action):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c)
        body, key = archive_input(state, files), headers()
        saved = await c.post(path, json=body, headers=key)
        assert saved.status_code == 200
        async with client(kb, "other") as other:
            assert (await other.get(path)).status_code == 403
            assert (await other.post(path, json=body, headers=headers())).status_code == 403
        await kb.grant_scope("owner", None, effect="deny", action=action)
        assert (await c.get(path)).status_code == 403
        assert (await c.post(path, json=body, headers=key)).status_code == 403
        assert (await c.post(path, json=archive_input(saved.json(), files), headers=headers())).status_code == 403


async def test_local_native_version_and_acl_conflicts_remain_import_conflicts(kb):
    await kb.configure()
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c)
        saved = (await c.post(path, json=archive_input(state, files), headers=headers())).json()
        native = await c.post(
            PREFIX + f"/materials/{state['material_id']}/versions",
            json={
                "expected_material_revision": saved["material_revision"],
                "contribution": selection(name="Explicit native edit").model_dump(mode="json"),
            },
            headers=headers(),
        )
        assert native.status_code == 201, native.text
        assert (await c.get(path)).status_code == 404
        request["source_revision"] += 1
        reimport = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert reimport.json()["items"][0]["conflict_code"] == "LOCAL_CHANGES"


async def test_material_acl_revoke_denies_archive_receipt_and_new_intent_without_removing_normal_detail(kb):
    async with client(kb) as owner, client(kb, "reader") as reader:
        _, path, state, _ = await prepare(kb, owner)
        # A normal reader remains entitled to the normal material detail, not the maintenance endpoint.
        assert (await reader.get(PREFIX + f"/materials/{state['material_id']}")).status_code == 200
        assert (await reader.get(path)).status_code == 403
        acl = await owner.put(
            PREFIX + f"/materials/{state['material_id']}/acl",
            headers=headers(),
            json={
                "expected_acl_revision": 1,
                "grants": [{"subject_kind": "company", "actions": ["read", "manage"]}],
            },
        )
        assert acl.status_code == 200, acl.text
        state = (await reader.get(path)).json()
        uploaded = await reader.post(
            PREFIX + "/files", files={"file": ("source-0.pdf", PDF, "application/pdf")}, headers=headers()
        )
        files = [{**state["attachments"][0], "file_id": uploaded.json()["file_id"]}]
        files[0].pop("media_type")
        key = headers()
        body = archive_input(state, files)
        saved = await reader.post(path, json=body, headers=key)
        assert saved.status_code == 200, saved.text
        revoked = await owner.put(
            PREFIX + f"/materials/{state['material_id']}/acl",
            headers=headers(),
            json={
                "expected_acl_revision": 2,
                "grants": [{"subject_kind": "company", "actions": ["read"]}],
            },
        )
        assert revoked.status_code == 200
        assert (await reader.get(path)).status_code == 403
        assert (await reader.post(path, json=body, headers=key)).status_code == 403
        assert (await reader.post(path, json=archive_input(saved.json(), files), headers=headers())).status_code == 403
        assert (await reader.get(PREFIX + f"/materials/{state['material_id']}")).status_code == 200


@pytest.mark.parametrize("status", ["待复核", "已停用"])
async def test_unavailable_source_never_archived(kb, status):
    async with client(kb) as c:
        _, path, state, files = await prepare(kb, c, status=status)
        assert state["can_archive"] is False
        response = await c.post(path, json=archive_input(state, files), headers=headers())
        assert response.status_code == 422 and response.json()["detail"] == "KNOWLEDGE_SOURCE_ARCHIVE_UNAVAILABLE"
        assert (await c.get(path)).json()["files"] == []


async def test_missing_idempotency_key_and_acl_change_do_not_reset_import_conflict(kb):
    async with client(kb) as c:
        request, path, state, files = await prepare(kb, c)
        assert (await c.post(path, json=archive_input(state, files))).status_code == 422
        changed = await c.put(
            PREFIX + f"/materials/{state['material_id']}/acl",
            headers=headers(),
            json={
                "expected_acl_revision": 1,
                "grants": [],
            },
        )
        assert changed.status_code == 200
        current = (await c.get(path)).json()
        archived = await c.post(path, json=archive_input(current, files), headers=headers())
        assert archived.status_code == 200, archived.text
        request["source_revision"] += 1
        update = await c.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert update.json()["items"][0]["conflict_code"] == "LOCAL_CHANGES"


async def test_direct_sql_runtime_cannot_write_table_or_invoke_import_predecessor(kb):
    async with kb.database.transaction(kb.actors["owner"]) as connection:
        role = await connection.fetchrow(
            "SELECT current_user AS name,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert role["name"] == "kb_runtime" and not role["rolsuper"] and not role["rolbypassrls"]
        assert not await connection.fetchval(
            "SELECT has_table_privilege(current_user,'knowledge.source_file_binding','INSERT')"
        )
        assert not await connection.fetchval(
            "SELECT has_function_privilege(current_user,$1,'EXECUTE')",
            "security.knowledge_import_before_source_files(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean)",
        )
        assert await connection.fetchval(
            "SELECT has_function_privilege(current_user,$1,'EXECUTE')",
            "security.knowledge_archive_source_files(uuid,integer,integer,bigint,jsonb)",
        )


@pytest.mark.parametrize("invalid", [None, {}, [], [None], ["bad"], [{"source_token": "x"}]])
async def test_direct_sql_rejects_invalid_attachment_payloads_with_no_writes(kb, invalid):
    async with client(kb) as c:
        _, _, state, _ = await prepare(kb, c)
    with pytest.raises(asyncpg.PostgresError) as error:
        async with kb.database.transaction(kb.actors["owner"]) as connection:
            await connection.fetchval(
                "SELECT security.knowledge_archive_source_files($1,$2,$3,$4,$5::jsonb)",
                UUID(state["material_id"]),
                state["material_revision"],
                state["acl_revision"],
                state["source_revision"],
                invalid,
            )
    assert error.value.sqlstate == "22023"
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.version WHERE workspace_id=$1", kb.workspace) == 1
