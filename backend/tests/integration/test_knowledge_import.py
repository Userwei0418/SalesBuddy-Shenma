"""Real HTTP plus restricted PostgreSQL/RLS, synthetic source records only."""

import asyncio
import copy
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.contracts.knowledge_import import KnowledgeImportInput, imported_contribution

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection

PREFIX = "/api/v1/knowledge"


def payload(kb, **updates):
    return {
        "workspace_id": str(kb.workspace),
        "owner_user_id": kb.actors["owner"].user_id,
        "grants": [{"subject_kind": "company", "actions": ["read"]}],
        "base_id": "baseSynthetic",
        "table_id": "tblSynthetic",
        "source_revision": 292,
        "dry_run": False,
        "records": [
            {
                "record_id": "recSyntheticOne",
                "original_name": "合成原名不改写",
                "product_label": "合成产品一",
                "category_label": "产品介绍",
                "scope_label": "可对客",
                "source_status": "可用",
                "provider_name": "原作者",
                "links": ["https://example.com/one"],
                "attachments": [{"name": "参考.pptx", "source_token": "opaqueFileToken", "size_bytes": 1500}],
            },
            {
                "record_id": "recSyntheticTwo",
                "original_name": "待补齐合成客户方案",
                "product_label": "合成产品二",
                "category_label": "客户方案",
                "scope_label": "仅内部",
                "source_status": "待复核",
                "links": [],
            },
        ],
        **updates,
    }


def headers(key=None):
    return {"Idempotency-Key": str(key or uuid4())}


@pytest.mark.asyncio
async def test_import_dry_run_lossless_listing_count_versions_and_no_archive(kb):
    async with kb.client() as client:
        request = payload(kb)
        dry = await client.post(PREFIX + "/imports/feishu", json={**request, "dry_run": True})
        assert dry.status_code == 200, dry.text
        assert dry.json()["counts"]["created"] == 2
        for table in ["material", "version", "source_binding", "audit"]:
            assert (
                await kb.admin.fetchval(
                    f"SELECT count(*) FROM knowledge.{table} WHERE workspace_id=$1",  # noqa: S608 -- fixed test table list
                    kb.workspace,
                )
                == 0
            )
        assert (
            await kb.admin.fetchval("SELECT count(*) FROM ops.mutation_receipt WHERE workspace_id=$1", kb.workspace)
            == 0
        )
        key = headers()
        done = await client.post(PREFIX + "/imports/feishu", json=request, headers=key)
        assert done.status_code == 200, done.text
        assert (await client.post(PREFIX + "/imports/feishu", json=request, headers=key)).json() == done.json()
        assert done.json()["counts"]["created"] == 2
        listed = await client.get(PREFIX + "/materials", params={"limit": 1})
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 2 and len(listed.json()["items"]) == 1
        assert {p["label"] for p in listed.json()["product_options"]} == {"合成产品一", "合成产品二"}
        stats = (await client.get(PREFIX + "/stats")).json()
        assert stats["metrics"]["total"]["count"] == 2
        assert stats["metrics"]["retrievable"]["count"] == 0 and stats["metrics"]["failed"]["count"] == 0
        assert {p["label"] for p in stats["products"]} == {"合成产品一", "合成产品二"}
        for item in done.json()["items"]:
            detail = await client.get(PREFIX + "/materials/" + item["material_id"])
            assert detail.status_code == 200, detail.text
            doc = detail.json()
            assert doc["source"]["base_id"] == "baseSynthetic"
            assert doc["active_version_id"] is None and doc["state"] == "saved"
            assert doc["versions"][0]["files"] == []
            assert doc["versions"][0]["contribution"]["kind"] == "legacy_import"
            assert doc["source"]["source_revision"] == 292
            denied = await client.put(
                PREFIX + f"/materials/{item['material_id']}/versions/{item['version_id']}/provider-binding",
                json={"provider_dataset_id": "dataset", "provider_document_id": "doc"},
                headers=headers(),
            )
            assert denied.status_code == 422, denied.text
        assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.file WHERE workspace_id=$1", kb.workspace) == 0
        assert (
            await kb.admin.fetchval(
                "SELECT count(*) FROM knowledge.provider_binding WHERE workspace_id=$1", kb.workspace
            )
            == 0
        )


@pytest.mark.asyncio
async def test_source_idempotency_revision_update_older_and_deletion_non_destructive(kb):
    async with kb.client() as client:
        request = payload(kb)
        first = (await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())).json()
        same = await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert same.status_code == 200 and same.json()["counts"]["unchanged"] == 2, same.text
        # A newer table revision with unchanged content remains the same material/version.
        newer = await client.post(
            PREFIX + "/imports/feishu", json={**request, "source_revision": 293}, headers=headers()
        )
        assert newer.json()["counts"]["unchanged"] == 2
        assert newer.json()["items"][0]["version_id"] == first["items"][0]["version_id"]
        older = await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert {i["conflict_code"] for i in older.json()["items"]} == {"SOURCE_REVISION_OLDER"}
        changed = copy.deepcopy(request)
        changed["source_revision"] = 294
        changed["records"] = changed["records"][:1]  # Missing source row never deletes another material.
        changed["records"][0]["original_name"] = "合成新源名称"
        update = await client.post(PREFIX + "/imports/feishu", json=changed, headers=headers())
        assert update.status_code == 200 and update.json()["counts"]["updated"] == 1, update.text
        assert update.json()["items"][0]["material_id"] == first["items"][0]["material_id"]
        assert update.json()["items"][0]["version_no"] == 2
        assert (await client.get(PREFIX + "/materials")).json()["total"] == 2
        changed["records"][0]["original_name"] = "同revision内容漂移"
        reused = await client.post(PREFIX + "/imports/feishu", json=changed, headers=headers())
        assert reused.json()["items"][0]["conflict_code"] == "SOURCE_REVISION_REUSED"


@pytest.mark.asyncio
async def test_local_version_or_acl_edits_survive_import(kb):
    await kb.configure()
    async with kb.client() as client:
        request = payload(kb)
        items = (await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())).json()["items"]
        native = await client.post(
            PREFIX + f"/materials/{items[0]['material_id']}/versions",
            headers=headers(),
            json={
                "expected_material_revision": 1,
                "contribution": selection(name="人工补齐的新版本").model_dump(mode="json"),
            },
        )
        assert native.status_code == 201, native.text
        detail = (await client.get(PREFIX + f"/materials/{items[0]['material_id']}")).json()
        assert detail["source"] is None and detail["versions"][1]["contribution"]["kind"] == "legacy_import"
        acl = await client.put(
            PREFIX + f"/materials/{items[1]['material_id']}/acl",
            json={"expected_acl_revision": 1, "grants": []},
            headers=headers(),
        )
        assert acl.status_code == 200, acl.text
        retry = await client.post(
            PREFIX + "/imports/feishu", json={**request, "source_revision": 293}, headers=headers()
        )
        assert retry.status_code == 200 and retry.json()["counts"]["conflict"] == 2, retry.text
        assert {i["conflict_code"] for i in retry.json()["items"]} == {"LOCAL_CHANGES"}
        assert (await client.get(PREFIX + f"/materials/{items[0]['material_id']}")).json()["material_revision"] == 2


@pytest.mark.asyncio
async def test_acl_scope_cross_company_owner_and_import_privilege(kb):
    request = payload(kb)
    async with kb.client() as client:
        invalid = await client.post(
            PREFIX + "/imports/feishu",
            json={**request, "workspace_id": kb.actors["other"].workspace_id},
            headers=headers(),
        )
        assert invalid.status_code == 403
        invalid = await client.post(
            PREFIX + "/imports/feishu", json={**request, "owner_user_id": kb.actors["other"].user_id}, headers=headers()
        )
        assert invalid.status_code == 422
        invalid = await client.post(
            PREFIX + "/imports/feishu",
            json={
                **request,
                "grants": [{"subject_kind": "user", "subject_id": kb.actors["other"].user_id, "actions": ["read"]}],
            },
            headers=headers(),
        )
        assert invalid.status_code == 422
        saved = await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert saved.status_code == 200, saved.text
    async with kb.client("reader") as reader, kb.client("other") as other:
        assert (await reader.get(PREFIX + "/materials")).json()["total"] == 2
        other_page = (await other.get(PREFIX + "/materials")).json()
        assert other_page["total"] == 0 and other_page["product_options"] == []
        await kb.grant_scope("reader", "self")
        self_page = (await reader.get(PREFIX + "/materials")).json()
        assert self_page["total"] == 0 and self_page["product_options"] == []
        await kb.grant_scope("reader", "workspace")
        await kb.grant_scope("reader", None, effect="deny")
        assert (await reader.get(PREFIX + "/stats")).status_code == 403
    await kb.user("editor", permissions=["read", "create", "edit", "manage"])
    async with kb.client("editor") as editor:
        assert (await editor.post(PREFIX + "/imports/feishu", json=request, headers=headers())).status_code == 403


@pytest.mark.asyncio
async def test_product_options_are_all_visible_products_not_current_page_or_private_grants(kb):
    await kb.configure()
    request = payload(kb)
    async with kb.client() as owner:
        assert (await owner.post(PREFIX + "/imports/feishu", json=request, headers=headers())).status_code == 200
        private = copy.deepcopy(request)
        private["records"] = [dict(private["records"][0], record_id="recPrivate", product_label="私有合成产品")]
        private["grants"] = []
        assert (await owner.post(PREFIX + "/imports/feishu", json=private, headers=headers())).status_code == 200
        page = (await owner.get(PREFIX + "/materials", params={"limit": 1})).json()
        assert len(page["product_options"]) == 3
    async with kb.client("reader") as reader:
        page = (await reader.get(PREFIX + "/materials", params={"limit": 1, "offset": 1})).json()
        assert page["total"] == 2 and len(page["items"]) == 1
        assert {p["label"] for p in page["product_options"]} == {"合成产品一", "合成产品二"}
        product = page["product_options"][0]["code"]
        filtered = (await reader.get(PREFIX + "/materials", params={"product_code": product})).json()
        assert filtered["total"] == 1 and filtered["product_options"] == page["product_options"]
        catalog_response = await reader.get(PREFIX + "/catalog")
        assert catalog_response.status_code == 200, catalog_response.text
        catalog = catalog_response.json()
        assert product not in {p["code"] for p in catalog["products"]}


@pytest.mark.asyncio
async def test_concurrent_import_no_duplicate_count_and_revoked_replay(kb):
    request = payload(kb)
    async with kb.client() as client:
        key = headers()
        results = await asyncio.gather(
            *[client.post(PREFIX + "/imports/feishu", json=request, headers=key) for _ in range(4)]
        )
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        assert all(r.json() == results[0].json() for r in results)
        assert (await client.get(PREFIX + "/stats")).json()["metrics"]["total"]["count"] == 2
        changed = {**request, "source_revision": 293}
        assert (await client.post(PREFIX + "/imports/feishu", json=changed, headers=key)).status_code == 409
        await kb.grant_scope("owner", None, effect="deny", action="catalog_manage")
        assert (await client.post(PREFIX + "/imports/feishu", json=request, headers=key)).status_code == 403


@pytest.mark.asyncio
async def test_different_keys_same_source_unique_and_qualified_source_ids(kb):
    request = payload(kb)
    async with kb.client() as client:
        assert (await client.post(PREFIX + "/imports/feishu", json=request)).status_code == 422
        results = await asyncio.gather(
            *[client.post(PREFIX + "/imports/feishu", json=request, headers=headers()) for _ in range(4)]
        )
        assert all(r.status_code == 200 for r in results)
        assert sum(r.json()["counts"]["created"] for r in results) == 2
        assert (await client.get(PREFIX + "/materials")).json()["total"] == 2
        different_table = await client.post(
            PREFIX + "/imports/feishu", json={**request, "table_id": "tblOtherSynthetic"}, headers=headers()
        )
        assert different_table.status_code == 200 and different_table.json()["counts"]["created"] == 2
        assert (await client.get(PREFIX + "/materials")).json()["total"] == 4


@pytest.mark.asyncio
async def test_explicit_other_owner_requires_actor_acl_even_on_dry_run(kb):
    request = payload(kb, owner_user_id=kb.actors["reader"].user_id)
    async with kb.client() as client:
        denied = await client.post(PREFIX + "/imports/feishu", json={**request, "dry_run": True})
        assert denied.status_code == 403
        request["grants"].append(
            {"subject_kind": "user", "subject_id": kb.actors["owner"].user_id, "actions": ["read", "manage"]}
        )
        approved = await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert approved.status_code == 200, approved.text
        mid = approved.json()["items"][0]["material_id"]
        acl = (await client.get(PREFIX + f"/materials/{mid}/acl")).json()
        assert acl["owner_user_id"] == kb.actors["reader"].user_id


@pytest.mark.asyncio
async def test_source_disabled_keeps_directory_and_no_generation_claim(kb):
    request = payload(kb)
    request["records"][0]["source_status"] = "已停用"
    async with kb.client() as client:
        response = await client.post(PREFIX + "/imports/feishu", json=request, headers=headers())
        assert response.status_code == 200, response.text
        mid = response.json()["items"][0]["material_id"]
        detail = (await client.get(PREFIX + f"/materials/{mid}")).json()
        assert detail["source"]["source_status"] == "已停用"
        assert "SOURCE_DISABLED" in detail["source"]["validation_issues"]
        assert detail["state"] == "saved" and detail["active_version_id"] is None
        assert (await client.get(PREFIX + "/stats")).json()["metrics"]["total"]["count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "javascript",
        "empty_host",
        "invalid_ipv6",
        "invalid_port",
        "remarks_control",
        "attachment",
        "extra",
        "null_action",
        "wrong_owner",
    ],
)
async def test_direct_sql_cannot_poison_directory_or_bypass_tenant(kb, corruption):
    body = KnowledgeImportInput.model_validate(payload(kb))
    doc = imported_contribution(body, body.records[0]).model_dump(mode="json")
    grants = [{"subject_kind": "company", "subject_id": None, "actions": ["read"]}]
    owner = body.owner_user_id
    if corruption == "javascript":
        doc["source"]["links"] = ["javascript:alert(1)"]
    if corruption == "empty_host":
        doc["source"]["links"] = ["https://?q=one"]
    if corruption == "invalid_ipv6":
        doc["source"]["links"] = ["https://[bad"]
    if corruption == "invalid_port":
        doc["source"]["links"] = ["https://example.com:65536/x"]
    if corruption == "remarks_control":
        doc["source"]["remarks"] = "x\x01y"
    if corruption == "attachment":
        doc["source"]["attachments"] = [{}]
    if corruption == "extra":
        doc["source"]["secret"] = "not-an-allowed-source-field"
    if corruption == "null_action":
        grants[0]["actions"] = ["read", None]
    if corruption == "wrong_owner":
        owner = UUID(kb.actors["other"].user_id)
    async with kb.database.transaction(kb.actors["owner"]) as connection:
        assert await connection.fetchval(
            "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        with pytest.raises(asyncpg.InvalidParameterValueError, match="KNOWLEDGE_IMPORT_"):
            async with connection.transaction():
                await connection.fetchval(
                    "SELECT security.knowledge_import_record($1,$2,$3::jsonb,$4,$5,$6,$7,$8::jsonb,false)",
                    body.workspace_id,
                    owner,
                    grants,
                    body.base_id,
                    body.table_id,
                    body.records[0].record_id,
                    292,
                    doc,
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute("DELETE FROM knowledge.source_binding")
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.material WHERE workspace_id=$1", kb.workspace) == 0
