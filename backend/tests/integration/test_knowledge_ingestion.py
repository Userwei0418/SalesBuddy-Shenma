"""Normal HTTP file contributions through real restricted PostgreSQL and Worker.

External provider responses are synthetic protocol fixtures, not live provider acceptance.
No test marks a version retrievable or writes a binding directly.
"""

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.config import get_settings
from sales_backend.contracts.knowledge_distribution import PublicationWrite, ShareWrite
from sales_backend.contracts.knowledge_ingestion import KnowledgeIngestionInput
from sales_backend.db import Database, _initialize_connection
from sales_backend.services.knowledge import KnowledgeService
from sales_backend.services.knowledge_distribution import DistributionService
from sales_backend.services.knowledge_provider import KnowledgeProviderClient, KnowledgeProviderError
from sales_backend.worker import Worker

from .knowledge_fixtures import Fixture, selection
from .knowledge_fixtures import kb as kb
from .test_knowledge_shared_wiring import gated_client

P = "/api/v1/knowledge"


def key():
    return {"Idempotency-Key": str(uuid4())}


def path(saved):
    return f"{P}/materials/{saved['material_id']}/versions/{saved['version_id']}/ingestion"


class ProviderProtocol:
    """Instrument the adapter network boundary; real parser/Worker/persistence remain."""

    def __init__(self):
        self.uploads = []
        self.parses = []
        self.retrievals = []
        self.docs = {}
        self.upload_error = False
        self.parse_error = False
        self.on_upload = None
        self.on_status = None
        self.on_retrieval = None
        self.force_document = None

    async def request(self, client, method, endpoint, **kwargs):
        dataset = endpoint.split("/")[4] if endpoint.startswith("/api/v1/datasets/") else None
        if method == "POST" and endpoint.endswith("/documents"):
            filename, data, media = kwargs["files"]["file"]
            assert kwargs["params"] == {"type": "local"}
            self.uploads.append((dataset, filename, data, media))
            if self.upload_error:
                raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNAVAILABLE")
            doc = self.force_document or "doc-" + str(uuid4())
            self.docs[doc] = {"id": doc, "dataset_id": dataset, "run": "0", "chunk_count": 0}
            if self.on_upload:
                await self.on_upload()
            return {"code": 0, "data": [self.docs[doc]]}
        if method == "POST" and endpoint.endswith("/chunks"):
            ids = kwargs["json"]["document_ids"]
            self.parses.append(tuple(ids))
            if self.parse_error:
                raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNAVAILABLE")
            for doc in ids:
                self.docs[doc].update(run="3", chunk_count=2)
            return {"code": 0, "data": True}
        if method == "GET" and endpoint.endswith("/documents"):
            if self.on_status:
                hook, self.on_status = self.on_status, None
                await hook()
            return {"code": 0, "data": {"docs": [x for x in self.docs.values() if x["dataset_id"] == dataset]}}
        if endpoint == "/api/v1/retrieval":
            body = kwargs["json"]
            self.retrievals.append(body)
            if self.on_retrieval:
                hook, self.on_retrieval = self.on_retrieval, None
                await hook()
            return {
                "code": 0,
                "data": {
                    "chunks": [
                        {"document_id": doc, "id": "chunk-" + doc, "content": "授权测试材料内容"}
                        for doc in body["document_ids"]
                    ]
                },
            }
        raise AssertionError((method, endpoint))


async def setup(kb, monkeypatch, *, configured=True):
    await kb.configure()
    settings = replace(
        kb.settings,
        worker_lock_seconds=30,
        knowledge_provider_base_url="https://provider.example",
        knowledge_provider_api_key="test-only-fixture",
        knowledge_provider_dataset_ids='["test-dataset","unselected-company-dataset"]',
        knowledge_ingestion_workspace_datasets=json.dumps({str(kb.workspace): "test-dataset"}) if configured else "{}",
    )
    kb.settings = kb.database.settings = settings
    kb.service = KnowledgeService(kb.database, settings)
    fake = ProviderProtocol()

    async def request(client, method, endpoint, **kwargs):
        return await fake.request(client, method, endpoint, **kwargs)

    monkeypatch.setattr(KnowledgeProviderClient, "_request", request)
    # No waiting is needed for a deterministic provider fixture.
    from sales_backend.services.knowledge_ingestion import KnowledgeIngestionService

    original = KnowledgeIngestionService.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.poll_interval = 0
        self.poll_attempts = 1

    monkeypatch.setattr(KnowledgeIngestionService, "__init__", initialize)
    return fake


async def create(client, *, name="Normal", files=2, previous=None, links=None):
    ids = []
    for n in range(files):
        r = await client.post(
            P + "/files", files={"file": (f"{name}-{n}.txt", f"{name}-{n}".encode(), "text/plain")}, headers=key()
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["file_id"])
    body = selection(files=ids, name=name).model_dump(mode="json")
    if links is not None:
        body["content"]["links"] = links
    route = P + "/materials"
    if previous:
        route += "/" + previous["material_id"] + "/versions"
        body = {"expected_material_revision": previous["material_revision"], "contribution": body}
    r = await client.post(route, json=body, headers=key())
    assert r.status_code == 201, r.text
    return r.json()


async def run(kb):
    assert await Worker(kb.database).run_once(job_types=("knowledge.ingest",))


@pytest.mark.asyncio
async def test_installed_api_worker_roles_http_lifecycle(monkeypatch):
    """Fresh product permission policy, separate physical API/Worker connections."""
    if os.environ.get("KNOWLEDGE_INSTALLED_ROLES") != "1":
        pytest.skip("Run the ingestion runner with --installed-roles")
    base = Path(os.environ["KNOWLEDGE_TEST_CLUSTER"])
    assert base.name.startswith("sb-knowledge-")
    assert (base / "PRIVATE_KNOWLEDGE_TEST").read_text() == "disposable-local-only\n"
    args = dict(host=str(base / "socket"), port=55438, database="postgres")
    admin = await asyncpg.connect(**args, user="kb_owner")
    await _initialize_connection(admin)
    settings = replace(get_settings(), app_env="test", database_url="", worker_lock_seconds=30)
    api, worker = Database(settings), Database(settings)
    roles = {}
    try:
        assert not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kb_runtime')")
        for name, database in (("api", api), ("worker", worker)):
            database.pool = await asyncpg.create_pool(
                **args, user="kb_installed_" + name, min_size=1, max_size=3, init=_initialize_connection
            )
            async with database.connection() as c:
                roles[name] = dict(await c.fetchrow(
                    "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ))
                assert roles[name]["current_user"] == "kb_installed_" + name
                assert roles[name]["rolsuper"] is False and roles[name]["rolbypassrls"] is False
                if name == "api":
                    assert not await c.fetchval(
                        "SELECT has_table_privilege(current_user,'config.authorization_scope_revision','SELECT')"
                    )
                    roles[name]["authorization_counter_table_not_granted"] = True
                signature = (
                    "security.knowledge_ingestion_step(uuid,text,uuid,text,integer,text)" if name == "api"
                    else "security.knowledge_ingestion_start(uuid,uuid,integer,text,text,text,jsonb)"
                )
                assert not await c.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", signature)
                forbidden = (
                    "SELECT security.knowledge_ingestion_step(NULL,'validate',NULL,NULL,NULL,NULL)" if name == "api"
                    else "SELECT security.knowledge_ingestion_start(NULL,NULL,1,'x','x','x','{}'::jsonb)"
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.execute(forbidden)
                roles[name]["opposite_ingestion_function_denied"] = True
                if name == "worker":
                    for table in ("knowledge.ingestion", "knowledge.ingestion_file", "knowledge.file"):
                        assert not await c.fetchval(
                            "SELECT has_table_privilege(current_user,$1,'INSERT,UPDATE,DELETE')", table
                        )
                    roles[name]["direct_knowledge_writes_denied"] = True
        kb = Fixture(admin, api)
        await admin.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Installed KB fixture')",
            kb.workspace, str(kb.workspace),
        )
        await admin.execute(
            "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='knowledge'",
            kb.workspace,
        )
        await kb.user("owner")
        fake = await setup(kb, monkeypatch)
        worker.settings = kb.settings
        async with gated_client(kb, monkeypatch) as client:
            saved = await create(client, name="InstalledRoles", files=2)
            assert saved["state"] == "syncing" and saved["active_version_id"] is None
            before = await client.get(path(saved))
            assert before.status_code == 200 and before.json()["total_files"] == 2, before.text
            assert await Worker(worker).run_once(job_types=("knowledge.ingest",))
            after = await client.get(path(saved))
            assert after.status_code == 200, after.text
            assert after.json()["availability"] == "complete" and after.json()["processed_files"] == 2, after.text
            detail = await client.get(P + "/materials/" + saved["material_id"])
            assert detail.status_code == 200, detail.text
            assert detail.json()["state"] == "retrievable"
            assert detail.json()["active_version_id"] == saved["version_id"]
            asked = await client.post(P + "/ask", json={"query": "fixture?", "material_ids": [saved["material_id"]]})
            assert asked.status_code == 200 and len(asked.json()["citations"]) == 2, asked.text
            assert len(fake.uploads) == 2 and len(fake.parses) == 2
            assert fake.retrievals[-1]["dataset_ids"] == ["test-dataset"]
            assert set(fake.retrievals[-1]["document_ids"]) == set(fake.docs)
            async with api.transaction(kb.actors["owner"], readonly=True) as c:
                archive = [dict(row) for row in await c.fetch(
                    "SELECT file_id,source_sha256,status,provider_dataset_id,provider_document_id,chunk_count "
                    "FROM knowledge.ingestion_file WHERE ingestion_id=$1", UUID(after.json()["ingestion_id"])
                )]
            stats = await client.get(P + "/stats")
            assert stats.status_code == 200 and stats.json()["metrics"]["retrievable"]["count"] == 1, stats.text
            # Exercise V269 plus the real V270 hook with exactly the installed
            # API grants. No Feishu connection is configured in this fixture.
            distribution = DistributionService(api)
            publication = await distribution.publish(kb.actors["owner"], uuid4(), UUID(saved["material_id"]),
                PublicationWrite(expected_revision=0, version_id=saved["version_id"]))
            shared = await distribution.share(kb.actors["owner"], uuid4(), UUID(saved["material_id"]),
                ShareWrite(publication_id=publication["publication_id"]))
            metadata = await distribution.public("metadata", shared["token"], browser_id="installed-role-browser")
            assert metadata["version_no"] == 1
            assert not await admin.fetchval("SELECT count(*) FROM ops.feishu_event WHERE workspace_id=$1", kb.workspace)
            assert not await Worker(worker).run_once(job_types=("knowledge.ingest",))
            async with api.transaction(kb.actors["owner"], readonly=True) as c:
                original_actor = await kb.service._knowledge_actor(c, kb.actors["owner"])
                same_actor = await kb.service._knowledge_actor(c, kb.actors["owner"])
                assert original_actor.authorization_revision == same_actor.authorization_revision != 1
            await admin.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'live-team','Synthetic live team')",
                kb.team, kb.workspace,
            )
            await kb.membership("owner")
            async with api.transaction(kb.actors["owner"], readonly=True) as c:
                changed_actor = await kb.service._knowledge_actor(c, kb.actors["owner"])
            assert changed_actor.authorization_revision != original_actor.authorization_revision
            assert changed_actor.team_ids == frozenset({kb.team}) and not kb.actors["owner"].team_ids

            async def revoke_read():
                await kb.grant_scope("owner", None, effect="deny")

            fake.on_retrieval = revoke_read
            denied = await client.post(P + "/ask", json={"query": "revoke?", "material_ids": [saved["material_id"]]})
            assert denied.status_code in {403, 404}, denied.text
            assert "citations" not in denied.json() and len(fake.retrievals) == 2
            if evidence_path := os.environ.get("KNOWLEDGE_INGESTION_EVIDENCE"):
                policy_path = Path(__file__).resolve().parents[3] / "tools/installation/runtime_permissions.json"
                Path(evidence_path).write_text(json.dumps({
                    "boundary": "isolated PostgreSQL; ASGI HTTP and real Worker; synthetic external protocol",
                    "permissions_source": "tools/installation/runtime_permissions.json",
                    "permissions_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
                    "extra_runtime_grants": False,
                    "roles": roles,
                    "save_receipt": saved,
                    "before": before.json(),
                    "after": after.json(),
                    "active_version_id": detail.json()["active_version_id"],
                    "archived_files": archive,
                    "upload_count": len(fake.uploads),
                    "retrieval": fake.retrievals[-1],
                    "citation_count": len(asked.json()["citations"]),
                    "retrievable_count": stats.json()["metrics"]["retrievable"]["count"],
                    "authorization_snapshot_stable_and_changed": True,
                    "team_membership_fresh_from_database": True,
                    "revoke_during_retrieval_status": denied.status_code,
                    "revoke_during_retrieval_no_citations": True,
                }, ensure_ascii=False, indent=2, default=str) + "\n")
    finally:
        await api.close()
        await worker.close()
        await admin.close()


@pytest.mark.asyncio
async def test_http_files_worker_activation_and_scoped_multidocument_retrieval(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    async with gated_client(kb, monkeypatch) as client:
        saved = await create(client)
        assert saved["state"] == "syncing" and saved["active_version_id"] is None
        before = (await client.get(path(saved))).json()
        assert before["availability"] == "running" and before["total_files"] == 2 and not before["can_retry"]
        await run(kb)
        after = (await client.get(path(saved))).json()
        assert after["availability"] == "complete" and after["processed_files"] == 2, after
        detail = (await client.get(P + "/materials/" + saved["material_id"])).json()
        assert detail["active_version_id"] == saved["version_id"] and detail["state"] == "retrievable", detail
        stats = (await client.get(P + "/stats")).json()
        assert stats["metrics"]["retrievable"]["count"] == 1
        asked = await client.post(P + "/ask", json={"query": "fixture?", "material_ids": [saved["material_id"]]})
        assert asked.status_code == 200, asked.text
        assert len(asked.json()["citations"]) == 2
        assert fake.retrievals[-1]["dataset_ids"] == ["test-dataset"]
        assert set(fake.retrievals[-1]["document_ids"]) == set(fake.docs)
        assert {x[2] for x in fake.uploads} == {b"Normal-0", b"Normal-1"}
        if evidence_path := os.environ.get("KNOWLEDGE_INGESTION_EVIDENCE"):
            async with kb.database.transaction(kb.actors["owner"], readonly=True) as c:
                archive = [
                    dict(r)
                    for r in await c.fetch(
                        "SELECT file_id,source_sha256,status,provider_dataset_id,provider_document_id,chunk_count "
                        "FROM knowledge.ingestion_file WHERE ingestion_id=$1",
                        UUID(after["ingestion_id"]),
                    )
                ]
                role = dict(
                    await c.fetchrow(
                        "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                )
            Path(evidence_path).write_text(
                json.dumps(
                    {
                        "boundary": "isolated PostgreSQL; real HTTP + restricted Worker; synthetic external protocol",
                        "runtime_role": role,
                        "save_receipt": saved,
                        "before": before,
                        "after": after,
                        "active_version_id": detail["active_version_id"],
                        "archived_files": archive,
                        "uploads": [
                            {
                                "dataset": x[0],
                                "filename": x[1],
                                "sha256": hashlib.sha256(x[2]).hexdigest(),
                                "size_bytes": len(x[2]),
                            }
                            for x in fake.uploads
                        ],
                        "retrieval": fake.retrievals[-1],
                        "citation_count": len(asked.json()["citations"]),
                        "retrievable_count": stats["metrics"]["retrievable"]["count"],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                + "\n"
            )
        # Replay/start cannot introduce another generation or active mapping.
        accepted = await client.post(path(saved), json={"expected_material_revision": 1}, headers=key())
        assert accepted.status_code == 202 and accepted.json()["availability"] == "complete", accepted.text
        assert not await Worker(kb.database).run_once(job_types=("knowledge.ingest",))
    async with kb.client("other") as other:
        assert (await other.get(path(saved))).status_code in {403, 404}
        assert (await other.post(path(saved), json={"expected_material_revision": 1}, headers=key())).status_code in {
            403,
            404,
            409,
        }
    await kb.grant_scope("owner", None, effect="deny")
    async with kb.client() as client:
        assert (await client.get(path(saved))).status_code in {403, 404}
        before = len(fake.retrievals)
        assert (
            await client.post(P + "/ask", json={"query": "x", "material_ids": [saved["material_id"]]})
        ).status_code in {403, 404}
        assert len(fake.retrievals) == before


@pytest.mark.asyncio
async def test_unconfigured_and_unarchived_links_remain_saved_not_indexed(kb, monkeypatch):
    fake = await setup(kb, monkeypatch, configured=False)
    async with kb.client() as client:
        saved = await create(client, files=1)
        assert saved["state"] == "saved"
        assert (await client.get(path(saved))).json()["availability"] == "unconfigured"
        # Enable only the synthetic test configuration; existing contributions are explicitly startable.
        settings = replace(
            kb.settings, knowledge_ingestion_workspace_datasets=json.dumps({str(kb.workspace): "test-dataset"})
        )
        kb.settings = kb.database.settings = settings
        kb.service = KnowledgeService(kb.database, settings)
    async with kb.client() as client:
        link = await create(client, name="link", files=1, links=["https://example.com/not-downloaded"])
        assert link["state"] == "saved"
        v = (await client.get(path(link))).json()
        assert v["availability"] == "links_not_archived" and not v["can_retry"]
        r = await client.post(path(link), json={"expected_material_revision": 1}, headers=key())
        assert r.status_code == 422 and r.json()["detail"] == "KNOWLEDGE_INGESTION_LINKS_NOT_ARCHIVED"
        onlylink = await create(client, name="onlylink", files=0)
        assert (await client.get(path(onlylink))).json()["availability"] == "files_required"
        assert fake.uploads == []


@pytest.mark.asyncio
async def test_parse_retry_reuses_document_old_active_survives_and_key_is_idempotent(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    async with kb.client() as client:
        first = await create(client, files=1)
        await run(kb)
        fake.parse_error = True
        newer = await create(client, name="new", files=1, previous=first)
        await run(kb)
        v = (await client.get(path(newer))).json()
        assert v["availability"] == "retryable" and v["state"] == "failed", v
        detail = (await client.get(P + "/materials/" + first["material_id"])).json()
        assert detail["active_version_id"] == first["version_id"]
        n = len(fake.uploads)
        fake.parse_error = False
        headers = key()
        for _ in range(2):
            accepted = await client.post(path(newer), json={"expected_material_revision": 2}, headers=headers)
            assert accepted.status_code == 202, accepted.text
        await run(kb)
        assert len(fake.uploads) == n
        assert (await client.get(path(newer))).json()["availability"] == "complete"
        assert (await client.get(P + "/materials/" + first["material_id"])).json()["active_version_id"] == newer[
            "version_id"
        ]
        conflict = await client.post(path(newer), json={"expected_material_revision": 1}, headers=key())
        assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_unknown_upload_is_persistent_and_cannot_be_blindly_retried(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    fake.upload_error = True
    async with kb.client() as client:
        saved = await create(client, files=1)
        await run(kb)
        view = (await client.get(path(saved))).json()
        assert view["availability"] == "unknown" and view["can_retry"] is False, view
        for _ in range(2):
            response = await client.post(path(saved), json={"expected_material_revision": 1}, headers=key())
            assert response.status_code == 409 and response.json()["detail"] == "KNOWLEDGE_INGESTION_UPLOAD_UNKNOWN"
        assert len(fake.uploads) == 1 and not fake.parses


@pytest.mark.asyncio
async def test_permission_revoked_after_upload_never_parses_or_activates(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    async with kb.client() as client:
        saved = await create(client, files=1)

    async def revoke():
        await kb.grant_scope("owner", None, effect="deny")

    fake.on_upload = revoke
    await run(kb)
    assert len(fake.uploads) == 1 and not fake.parses
    row = await kb.admin.fetchrow(
        "SELECT active_version_id FROM knowledge.material WHERE id=$1", UUID(saved["material_id"])
    )
    assert row["active_version_id"] is None
    task = await kb.admin.fetchrow(
        "SELECT status FROM knowledge.ingestion WHERE version_id=$1", UUID(saved["version_id"])
    )
    assert task["status"] == "unknown"


@pytest.mark.asyncio
async def test_create_only_contributor_can_process_but_reader_cannot(kb, monkeypatch):
    fake = await setup(kb, monkeypatch, configured=False)
    await kb.user("contributor", permissions=["read", "create"], scope="self")
    async with gated_client(kb, monkeypatch, "contributor") as client:
        saved = await create(client, files=1)
    settings = replace(
        kb.settings, knowledge_ingestion_workspace_datasets=json.dumps({str(kb.workspace): "test-dataset"})
    )
    kb.settings = kb.database.settings = settings
    kb.service = KnowledgeService(kb.database, settings)
    async with gated_client(kb, monkeypatch, "contributor") as client:
        detail = (await client.get(P + "/materials/" + saved["material_id"])).json()
        assert detail["can_edit"] is False and detail["can_manage"] is False
        view = (await client.get(path(saved))).json()
        assert view["can_retry"]
        accepted = await client.post(path(saved), json={"expected_material_revision": 1}, headers=key())
        assert accepted.status_code == 202, accepted.text
        await run(kb)
        assert (await client.get(path(saved))).json()["availability"] == "complete"
    assert len(fake.uploads) == 1


@pytest.mark.asyncio
async def test_newer_version_during_parse_prevents_old_job_activating(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    async with kb.client() as client:
        first = await create(client, files=1)
        await run(kb)
        second = await create(client, name="second", files=1, previous=first)
        later = {}

        async def replace_version():
            # Independent HTTP request must not inherit the worker's contextvars.
            from contextvars import Context

            task = asyncio.create_task(create(client, name="third", files=1, previous=second), context=Context())
            later.update(await task)

        fake.on_status = replace_version
        await run(kb)
        detail = (await client.get(P + "/materials/" + first["material_id"])).json()
        assert detail["active_version_id"] == first["version_id"] and detail["latest_version_id"] == later["version_id"]
        assert (await client.get(path(second))).json()["availability"] == "stale"
        await run(kb)
        assert (await client.get(P + "/materials/" + first["material_id"])).json()["active_version_id"] == later[
            "version_id"
        ]


@pytest.mark.asyncio
async def test_parallel_start_and_worker_claim_process_each_file_once(kb, monkeypatch):
    fake = await setup(kb, monkeypatch, configured=False)
    async with kb.client() as client:
        saved = await create(client, files=1)
    settings = replace(
        kb.settings, knowledge_ingestion_workspace_datasets=json.dumps({str(kb.workspace): "test-dataset"})
    )
    kb.settings = kb.database.settings = settings
    kb.service = KnowledgeService(kb.database, settings)
    body = KnowledgeIngestionInput(expected_material_revision=1)
    mid, vid = UUID(saved["material_id"]), UUID(saved["version_id"])
    values = await asyncio.gather(
        *[kb.service.ingestion.start(kb.actors["owner"], uuid4(), mid, vid, body) for _ in range(4)]
    )
    assert len({str(x["ingestion_id"]) for x in values}) == 1
    await asyncio.gather(*(Worker(kb.database).run_once(job_types=("knowledge.ingest",)) for _ in range(2)))
    assert len(fake.uploads) == 1
    assert (await kb.service.ingestion.view(kb.actors["owner"], mid, vid))["availability"] == "complete"


@pytest.mark.asyncio
async def test_provider_doc_cannot_alias_across_companies_or_manual_binding(kb, monkeypatch):
    from sales_backend.api.knowledge import CatalogWrite

    from .knowledge_fixtures import catalog_values

    fake = await setup(kb, monkeypatch)
    fake.force_document = "shared-provider-id"
    async with kb.client() as client:
        first = await create(client, files=1)
        await run(kb)
        # A manual one-doc binding cannot override an ingestion-owned multi-doc identity.
        manual = await client.put(
            f"{P}/materials/{first['material_id']}/versions/{first['version_id']}/provider-binding",
            json={"provider_dataset_id": "test-dataset", "provider_document_id": "shared-provider-id"},
            headers=key(),
        )
        assert manual.status_code == 409, manual.text
    other = kb.actors["other"]
    settings = replace(
        kb.settings,
        knowledge_ingestion_workspace_datasets=json.dumps(
            {str(kb.workspace): "test-dataset", other.workspace_id: "test-dataset"}
        ),
    )
    kb.settings = kb.database.settings = settings
    kb.service = KnowledgeService(kb.database, settings)
    await kb.service.save_catalog(other, uuid4(), CatalogWrite(**catalog_values()))
    async with kb.client("other") as client:
        second = await create(client, files=1)
        await run(kb)
        v = (await client.get(path(second))).json()
        assert v["availability"] == "unknown" and not v["can_retry"], v
        detail = (await client.get(P + "/materials/" + second["material_id"])).json()
        assert detail["active_version_id"] is None
    assert len(fake.uploads) == 2 and len(fake.parses) == 1


@pytest.mark.asyncio
async def test_source_bytes_change_is_rejected_before_any_network(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    async with kb.client() as client:
        saved = await create(client, files=1)
        fid = await kb.admin.fetchval(
            "SELECT file_id FROM knowledge.version_file WHERE version_id=$1", UUID(saved["version_id"])
        )
        # Test-only administrative corruption is not an application mutation path.
        await kb.admin.execute("UPDATE knowledge.file SET content=$2 WHERE id=$1", fid, b"changed")
        await run(kb)
        v = (await client.get(path(saved))).json()
        assert v["state"] == "failed" and v["error_code"] == "KNOWLEDGE_INGESTION_SOURCE_CHANGED", v
        assert not fake.uploads


@pytest.mark.asyncio
async def test_cannot_activate_until_every_file_parses_and_retry_keeps_completed_file(kb, monkeypatch):
    fake = await setup(kb, monkeypatch)
    original = fake.request

    async def fail_second(client, method, endpoint, **kwargs):
        if endpoint.endswith("/chunks") and len(fake.uploads) == 2:
            raise KnowledgeProviderError("KNOWLEDGE_PROVIDER_UNAVAILABLE")
        return await original(client, method, endpoint, **kwargs)

    fake.request = fail_second
    async with kb.client() as client:
        saved = await create(client, files=2)
        await run(kb)
        view = (await client.get(path(saved))).json()
        assert view["processed_files"] == 1 and view["availability"] == "retryable", view
        assert (await client.get(P + "/materials/" + saved["material_id"])).json()["active_version_id"] is None
        fake.request = original
        accepted = await client.post(path(saved), json={"expected_material_revision": 1}, headers=key())
        assert accepted.status_code == 202
        await run(kb)
        assert (await client.get(path(saved))).json()["availability"] == "complete"
        assert len(fake.uploads) == 2 and len(fake.parses) == 2
