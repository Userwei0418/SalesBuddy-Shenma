import asyncio
from uuid import UUID, uuid4

import pytest

from sales_backend.api.knowledge import ACLWrite

from .knowledge_fixtures import catalog_values, selection
from .knowledge_fixtures import kb as kb


def mutation():
    return {"Idempotency-Key": str(uuid4())}


@pytest.mark.asyncio
async def test_http_upload_save_reopen_preview_download_and_revoke(kb):
    prefix = "/api/v1/knowledge"
    async with kb.client() as client:
        assert (await client.get(prefix + "/catalog")).status_code == 409
        response = await client.put(prefix + "/catalog", json=catalog_values(), headers=mutation())
        assert response.status_code == 200, response.text
        assert (await client.get(prefix + "/catalog")).json() == response.json()
        upload = await client.post(
            prefix + "/files", files={"file": ("材料.txt", b"archived bytes", "text/plain")}, headers=mutation()
        )
        assert upload.status_code == 201, upload.text
        file_id = upload.json()["file_id"]
        assert "storage_key" not in upload.json()
        body = selection(files=[file_id]).model_dump(mode="json")
        key = mutation()
        saved = await client.post(prefix + "/materials", json=body, headers=key)
        assert saved.status_code == 201, saved.text
        assert (await client.post(prefix + "/materials", json=body, headers=key)).json() == saved.json()
        mid, vid = saved.json()["material_id"], saved.json()["version_id"]
        detail = await client.get(prefix + f"/materials/{mid}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["versions"][0]["files"][0]["file_id"] == file_id
        assert detail.json()["versions"][0]["files"][0]["preview_supported"] is True
        assert detail.json()["active_version_id"] is None
        assert (await client.get(prefix + "/materials")).json()["total"] == 1
        acl = await client.get(prefix + f"/materials/{mid}/acl")
        assert acl.status_code == 200, acl.text
        count = await client.get(prefix + "/count")
        assert count.json()["status"] == "unconfigured" and count.json()["count"] is None
        grant = {
            "expected_acl_revision": 1,
            "grants": [{"subject_kind": "user", "subject_id": kb.actors["reader"].user_id, "actions": ["read"]}],
        }
        result = await client.put(prefix + f"/materials/{mid}/acl", json=grant, headers=mutation())
        assert result.status_code == 200, result.text
        async with kb.client("reader") as reader:
            url = prefix + f"/files/{file_id}"
            for operation in ("preview", "download"):
                response = await reader.get(url + f"/{operation}", params={"version_id": vid})
                assert response.status_code == 200 and response.content == b"archived bytes", response.text
                assert response.headers["cache-control"] == "no-store"
                etag = response.headers["etag"]
                part = await reader.get(
                    url + f"/{operation}", params={"version_id": vid}, headers={"Range": "bytes=0-3", "If-Match": etag}
                )
                assert part.status_code == 206 and part.content == b"arch"
                assert part.headers["content-range"] == "bytes 0-3/14"
                stale = await reader.get(
                    url + f"/{operation}",
                    params={"version_id": vid},
                    headers={"Range": "bytes=4-", "If-Match": '"changed"'},
                )
                assert stale.status_code == 412 and not stale.content
            changed = await client.put(
                prefix + f"/materials/{mid}/acl", json={"expected_acl_revision": 2}, headers=mutation()
            )
            assert changed.status_code == 200, changed.text
            for operation in ("preview", "download"):
                assert (await reader.get(url + f"/{operation}", params={"version_id": vid})).status_code == 404
                denied_part = await reader.get(
                    url + f"/{operation}", params={"version_id": vid}, headers={"Range": "bytes=4-", "If-Match": etag}
                )
                assert denied_part.status_code == 404
                assert "etag" not in denied_part.headers and "content-range" not in denied_part.headers
            assert (await reader.get(prefix + f"/materials/{mid}")).status_code == 404


@pytest.mark.asyncio
async def test_generation_persists_work_job_and_archived_docx(kb):
    """The configured-provider path is durable and readable from the HTTP API."""
    await kb.configure()
    saved = await kb.service.save(kb.actors["owner"], uuid4(), selection(name="生成素材"))
    material_id, version_id = UUID(saved["material_id"]), UUID(saved["version_id"])
    await kb.admin.execute("UPDATE knowledge.version SET state='retrievable' WHERE id=$1", version_id)
    await kb.admin.execute("UPDATE knowledge.material SET active_version_id=$2 WHERE id=$1", material_id, version_id)
    from .test_knowledge_works import EvidenceProvider

    kb.service.provider = EvidenceProvider()
    kb.service.document_workflow = kb.service.provider
    await kb.admin.execute(
        "INSERT INTO knowledge.provider_binding("
        "workspace_id,material_id,version_id,provider_dataset_id,provider_document_id) "
        "VALUES($1,$2,$3,'synthetic-dataset',$4)",
        kb.workspace,
        material_id,
        version_id,
        str(version_id),
    )
    prefix = "/api/v1/knowledge"
    body = {
        "purpose": "客户方案初稿",
        "material_ids": [str(material_id)],
        "format": "docx",
        "audience": "内部评审",
    }
    key = mutation()
    async with kb.client() as client:
        response = await client.post(prefix + "/works", json=body, headers=key)
        assert response.status_code == 202, response.text
        receipt = response.json()
        assert receipt["status"] == "succeeded"
        work = await client.get(prefix + f"/works/{receipt['job_id']}")
        assert work.status_code == 200, work.text
        assert work.json()["status"] == "succeeded"
        assert work.json()["sources"][0]["version_id"] == str(version_id)
        file = work.json()["files"][0]
        assert file["filename"].endswith(".docx") and file["size_bytes"] > 0
        page = await client.get(prefix + "/works")
        assert page.status_code == 200 and page.json()["total"] == 1
        assert (await client.post(prefix + "/works", json=body, headers=key)).json() == receipt


@pytest.mark.asyncio
async def test_http_contract_errors_and_link_versions(kb):
    await kb.configure()
    prefix = "/api/v1/knowledge"
    async with kb.client() as client:
        data = selection().model_dump(mode="json")
        assert (await client.post(prefix + "/materials", json=data)).status_code == 422
        assert (
            await client.post(prefix + "/materials", json={**data, "content_name": "spoof"}, headers=mutation())
        ).status_code == 422
        result = await client.post(prefix + "/materials", json=data, headers=mutation())
        assert result.status_code == 201, result.text
        mid = result.json()["material_id"]
        assert (await client.post(prefix + "/materials", json=data, headers=mutation())).status_code == 409
        data["supplementary_text"] = "v2"
        update = {"expected_material_revision": 1, "contribution": data}
        response = await client.post(prefix + f"/materials/{mid}/versions", json=update, headers=mutation())
        assert response.status_code == 201 and response.json()["version_no"] == 2, response.text
        assert (
            await client.post(prefix + f"/materials/{mid}/versions", json=update, headers=mutation())
        ).status_code == 409
        detail = (await client.get(prefix + f"/materials/{mid}")).json()
        assert len(detail["versions"]) == 2
        assert detail["versions"][1]["contribution"]["selection"]["content"]["links"] == ["https://example.com/source"]
        assert (await client.get(prefix + f"/files/{uuid4()}/download")).status_code == 422


@pytest.mark.asyncio
async def test_preview_never_executes_active_content(kb):
    await kb.configure()
    async with kb.client() as client:
        prefix = "/api/v1/knowledge"
        response = await client.post(
            prefix + "/files",
            headers=mutation(),
            files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")},
        )
        assert response.status_code == 201, response.text
        file_id = response.json()["file_id"]
        saved = await client.post(
            prefix + "/materials", json=selection(files=[file_id]).model_dump(mode="json"), headers=mutation()
        )
        assert saved.status_code == 201, saved.text
        params = {"version_id": saved.json()["version_id"]}
        assert (await client.get(prefix + f"/files/{file_id}/preview", params=params)).status_code == 415
        download = await client.get(prefix + f"/files/{file_id}/download", params=params)
        assert download.status_code == 200 and download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment;")


@pytest.mark.asyncio
async def test_directory_page_total_uses_same_visibility_and_saved_versions(kb):
    await kb.configure()
    for name in ["one", "two", "three"]:
        await kb.service.save(kb.actors["owner"], uuid4(), selection(name=name))
    async with kb.client() as client:
        response = await client.get("/api/v1/knowledge/materials", params={"limit": 1, "offset": 1})
        assert response.status_code == 200, response.text
        page = response.json()
        assert {"items", "total", "limit", "offset", "scope_label", "capabilities", "product_options"} == set(page)
        assert page["total"] == 3 and len(page["items"]) == 1 and page["limit"] == page["offset"] == 1
        assert page["items"][0]["state"] == "saved"
        assert page["items"][0]["active_version_id"] is None and page["items"][0]["active_version_state"] is None
        empty = (await client.get("/api/v1/knowledge/materials", params={"offset": 99})).json()
        assert empty["items"] == [] and empty["total"] == 3
        assert empty["product_options"] == page["product_options"] == [{"code": "general", "label": "通用"}]
        assert empty["capabilities"] == page["capabilities"] == {"create": True}
    async with kb.client("reader") as client:
        page = (await client.get("/api/v1/knowledge/materials")).json()
        assert page["total"] == 0 and page["items"] == []
        assert page["product_options"] == []


@pytest.mark.asyncio
async def test_server_filters_scope_projection_and_complete_stats(kb):
    await kb.configure()
    owner, reader = kb.actors["owner"], kb.actors["reader"]
    a = await kb.service.save(owner, uuid4(), selection(name="old source"))
    b = await kb.service.save(
        owner, uuid4(), selection(name="needle", product_code="test_product", content_category="product_intro")
    )
    await kb.service.save(owner, uuid4(), selection(name="hidden owner material"))
    await kb.service.save(reader, uuid4(), selection(name="reader own material"))
    aid, bid = UUID(a["material_id"]), UUID(b["material_id"])
    await kb.membership("reader")
    await kb.service.save_acl(
        owner,
        uuid4(),
        aid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "team",
                    "subject_id": kb.team,
                    "actions": ["read"],
                }
            ],
        ),
    )
    await kb.service.save_acl(
        owner,
        uuid4(),
        bid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "company",
                    "actions": ["read"],
                }
            ],
        ),
    )
    # Synthetic provider-state fixture only: no claim that a provider or indexing job ran.
    await kb.admin.execute("UPDATE knowledge.version SET state='retrievable' WHERE id=$1", UUID(a["version_id"]))
    await kb.admin.execute(
        "UPDATE knowledge.material SET active_version_id=$2,created_at=clock_timestamp()-interval '45 days' "
        "WHERE id=$1",
        aid,
        UUID(a["version_id"]),
    )
    newer = await kb.service.save(owner, uuid4(), selection(name="new version failed"), aid, 1)
    await kb.admin.execute("UPDATE knowledge.version SET state='failed' WHERE id=$1", UUID(newer["version_id"]))
    async with kb.client("reader") as client:
        prefix = "/api/v1/knowledge"
        for filters, expected in [
            ({}, 3),
            ({"scope": "personal"}, 1),
            ({"scope": "team"}, 1),
            ({"scope": "shared"}, 2),
            ({"period": "30d"}, 2),
            ({"retrievable": "true"}, 1),
            ({"state": "failed"}, 1),
            ({"q": "needle", "product_code": "test_product", "content_category": "product_intro"}, 1),
            ({"q": "' OR 1=1 --"}, 0),
            ({"scope": "personal", "retrievable": "true"}, 0),
        ]:
            page = await client.get(prefix + "/materials", params={**filters, "limit": 1})
            assert page.status_code == 200, page.text
            assert page.json()["total"] == expected and len(page.json()["items"]) == min(1, expected)
            assert page.json()["product_options"] == [
                {"code": "test_product", "label": "测试产品"},
                {"code": "general", "label": "通用"},
            ]
            assert all(not any(key.startswith("_") for key in item) for item in page.json()["items"])
            stats = await client.get(prefix + "/stats", params=filters)
            assert stats.status_code == 200, stats.text
            assert stats.json()["metrics"]["total"]["count"] == expected
            assert sum(item["count"] for item in stats.json()["categories"]) == expected
            assert sum(item["count"] for item in stats.json()["products"]) == expected
            assert sum(item["count"] for item in stats.json()["contributions"]) == expected
            assert stats.headers["cache-control"] == "no-store"
        stats = (await client.get(prefix + "/stats")).json()
        assert stats["source"] == "salesbuddy.internal_materials" and stats["observed_at"]
        assert {key: value["count"] for key, value in stats["metrics"].items()} == {
            "total": 3,
            "added_30d": 2,
            "retrievable": 1,
            "failed": 1,
            "external": 3,
            "contributors": 2,
            "mine": 1,
        }
        assert len(stats["trend"]) == 30 and stats["trend"][0]["count"] == 1 and stats["trend"][-1]["count"] == 3
        assert len(stats["contributions"]) == 2
        row = (await client.get(prefix + "/materials", params={"retrievable": "true"})).json()["items"][0]
        assert row["version_no"] == 2 and row["version_id"] == newer["version_id"]
        assert row["state"] == "failed" and row["active_version_state"] == "retrievable"
        assert row["content_category"] == "sales_support" and row["product_label"] == "通用"
        assert row["contributor_user_id"] == owner.user_id and row["contributor_name"]
        assert row["sharing_label"] == "团队共享" and row["can_edit"] is False
        assert "_scope" not in row and "storage_key" not in row
        assert (await client.get(prefix + "/count")).json()["count"] is None
        await kb.service.save_acl(owner, uuid4(), aid, ACLWrite(expected_acl_revision=2))
        revoked = (await client.get(prefix + "/stats")).json()
        assert revoked["metrics"]["total"]["count"] == 2 and revoked["metrics"]["retrievable"]["count"] == 0
        assert (await client.get(prefix + "/materials", params={"scope": "team"})).json()["total"] == 0
        for query in [{"scope": "another"}, {"period": "7d"}, {"state": "invented"}, {"limit": 0}, {"other": "x"}]:
            assert (await client.get(prefix + "/materials", params=query)).status_code == 422


@pytest.mark.asyncio
async def test_internal_stats_empty_is_real_zero_and_create_capability_has_object_scope(kb):
    await kb.configure()
    async with kb.client("reader") as client:
        response = await client.get("/api/v1/knowledge/stats")
        assert response.status_code == 200, response.text
        assert all(
            value["count"] == 0 and value["status"] == "available" for value in response.json()["metrics"].values()
        )
        assert response.json()["categories"] == response.json()["products"] == []
        page = (await client.get("/api/v1/knowledge/materials")).json()
        assert page["capabilities"]["create"] is True
        await kb.grant_scope("reader", "assigned")
        page = (await client.get("/api/v1/knowledge/materials")).json()
        assert page["capabilities"]["create"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["catalog", "version", "acl", "upload", "create"])
async def test_receipt_wait_rechecks_specific_action_permission(kb, case):
    """Two real PG connections: hold receipt lock, observe wait, revoke, release."""
    await kb.configure()
    owner = kb.actors["owner"]
    key = uuid4()
    method = "POST"
    if case in {"version", "acl"}:
        saved = await kb.service.save(owner, uuid4(), selection())
        mid = saved["material_id"]
    if case == "catalog":
        method, path, operation, revoke = "PUT", "/catalog", "knowledge.catalog", ["catalog_manage"]
        data = {"json": {**catalog_values(), "expected_revision": 1}}
    elif case == "version":
        path, operation, revoke = f"/materials/{mid}/versions", f"knowledge.version:{mid}", ["edit"]
        data = {
            "json": {
                "expected_material_revision": 1,
                "contribution": selection(name="next version").model_dump(mode="json"),
            }
        }
    elif case == "acl":
        method, path, operation, revoke = "PUT", f"/materials/{mid}/acl", f"knowledge.acl:{mid}", ["manage"]
        data = {"json": {"expected_acl_revision": 1, "grants": []}}
    elif case == "upload":
        path, operation, revoke = "/files", "knowledge.upload", ["create", "edit"]
        data = {"files": {"file": ("receipt.txt", b"receipt bytes", "text/plain")}}
    else:
        path, operation, revoke = "/materials", "knowledge.create", ["create"]
        data = {"json": selection().model_dump(mode="json")}
    lock = f"mutation:{owner.workspace_id}:{owner.user_id}:{operation}:{key}"
    async with kb.client() as client:

        async def request():
            return await client.request(
                method, "/api/v1/knowledge" + path, headers={"Idempotency-Key": str(key)}, **data
            )

        original = await request()
        assert original.status_code in {200, 201}, original.text
        await kb.admin.execute("SELECT pg_advisory_lock(hashtextextended($1,0))", lock)
        pending = asyncio.create_task(request())
        try:
            # Observe the actual database wait; do not infer it from a sleep or a mocked hook.
            async with asyncio.timeout(5):
                while not await kb.admin.fetchval("""
                    SELECT EXISTS(SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
                     WHERE a.usename='kb_runtime' AND NOT l.granted AND l.locktype='advisory'
                      AND pg_backend_pid()=ANY(pg_blocking_pids(a.pid)))
                """):
                    assert not pending.done(), "Request returned before the receipt lock wait was observed"
                    await asyncio.sleep(0.01)
            for permission in revoke:
                await kb.grant_scope("owner", None, effect="deny", action=permission)
        finally:
            await kb.admin.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", lock)
            response = await asyncio.wait_for(pending, timeout=5)
        assert response.status_code == 403, (case, response.status_code, response.text)
