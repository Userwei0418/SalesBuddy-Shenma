"""Generated work delivery through HTTP and a real restricted PostgreSQL role.

Provider retrieval and DOCX bytes are explicit fixtures. These tests validate persistence/authorization,
not a real external provider deployment.
"""

import asyncio
import io
import os
import zipfile
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.api.knowledge import ACLWrite
from sales_backend.api.knowledge import service as service_dependency
from sales_backend.contracts.knowledge import KnowledgeChunk
from sales_backend.contracts.knowledge_retrieval import KnowledgeGenerateInput
from sales_backend.db import _initialize_connection
from sales_backend.services.knowledge_artifacts import MIME_TYPES, validate_knowledge_artifact
from sales_backend.services.knowledge_document_workflow import WorkflowDocuments

from ..test_knowledge_artifacts import docx, package
from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection
from .test_knowledge_shared_wiring import gated_client

PREFIX = "/api/v1/knowledge"


@asynccontextmanager
async def runtime_pool(kb, size):
    """Use the same disposable restricted role with an explicitly small pool."""
    original_pool, original_settings = kb.database.pool, kb.database.settings
    kb.database.pool = await asyncpg.create_pool(
        host=str(Path(os.environ["KNOWLEDGE_TEST_CLUSTER"]) / "socket"),
        port=55438,
        database="postgres",
        user="kb_runtime",
        min_size=size,
        max_size=size,
        init=_initialize_connection,
    )
    kb.database.settings = replace(original_settings, database_pool_wait_seconds=2)
    try:
        yield kb.database.pool
    finally:
        await kb.database.pool.close()
        kb.database.pool, kb.database.settings = original_pool, original_settings


def key():
    return {"Idempotency-Key": str(uuid4())}


class EvidenceProvider:
    configured = True
    agent_id = "synthetic-agent"
    source_only_reviewed = True
    dataset_ids = ("synthetic-dataset",)

    def __init__(self):
        self.calls = 0
        self.prompts = []
        self.requests = []
        self.after_retrieval = None
        self.after_generation = None

    async def retrieve(self, request):
        self.requests.append(request)
        if self.after_retrieval:
            await self.after_retrieval()
        return tuple(
            KnowledgeChunk(
                source=doc.source,
                provider_document_id=doc.provider_document_id,
                chunk_id=f"chunk-{i}",
                text="授权原文：方案支持离线部署，交付时间需另行确认。",
            )
            for i, doc in enumerate(request.documents)
        )

    async def generate(self, prompt, *, user):
        self.calls += 1
        self.prompts.append(prompt)
        await asyncio.sleep(0.02)
        if self.after_generation:
            await self.after_generation()
        return WorkflowDocuments(
            str(uuid4()),
            (
                validate_knowledge_artifact(
                    package(docx("<w:p><w:r><w:t>方案支持离线部署。交付时间待确认。</w:t></w:r></w:p>")),
                    filename="外部生成.docx",
                    media_type=MIME_TYPES["docx"],
                    expected_format="docx",
                ),
            ),
        )


async def material(kb, *, name="生成来源", owner="owner"):
    saved = await kb.service.save(kb.actors[owner], uuid4(), selection(name=name))
    mid, vid = UUID(saved["material_id"]), UUID(saved["version_id"])
    await kb.admin.execute("UPDATE knowledge.version SET state='retrievable' WHERE id=$1", vid)
    await kb.admin.execute("UPDATE knowledge.material SET active_version_id=$2 WHERE id=$1", mid, vid)
    await kb.admin.execute(
        "INSERT INTO knowledge.provider_binding("
        "workspace_id,material_id,version_id,provider_dataset_id,provider_document_id) "
        "VALUES($1,$2,$3,'synthetic-dataset',$4)",
        kb.workspace,
        mid,
        vid,
        str(vid),
    )
    return mid, vid


async def setup_work(kb, *, name="生成来源", actor="owner"):
    await kb.configure()
    mid, vid = await material(kb, name=name)
    provider = EvidenceProvider()
    kb.service.provider = provider
    kb.service.document_workflow = provider
    body = dict(purpose="验收方案", material_ids=[str(mid)], format="docx", audience="内部评审")
    receipt = await kb.service.generate_work(kb.actors[actor], uuid4(), KnowledgeGenerateInput(**body))
    wid = UUID(receipt["job_id"])
    return mid, vid, wid, body, provider


async def share_source(kb, mid, *, revision=1, team=False):
    await kb.service.save_acl(
        kb.actors["owner"],
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=revision,
            grants=[
                {
                    "subject_kind": "team" if team else "user",
                    "subject_id": kb.team if team else kb.actors["reader"].user_id,
                    "actions": ["read"],
                }
            ],
        ),
    )


@pytest.mark.asyncio
async def test_work_share_download_revoke_real_http_and_gate(kb, monkeypatch):
    mid, vid, wid, body, provider = await setup_work(kb)
    await share_source(kb, mid)
    # Exercise the actual shared permission dispatcher too; identity only is synthetic.
    async with gated_client(kb, monkeypatch) as owner:
        owner._transport.app.dependency_overrides[service_dependency] = lambda: kb.service
        detail = await owner.get(f"{PREFIX}/works/{wid}")
        assert detail.status_code == 200, detail.text
        work = detail.json()
        assert work["revision"] == work["version_no"] == 1 and work["can_manage"]
        assert work["recipients"] == []
        candidates = await owner.get(f"{PREFIX}/works/{wid}/share-candidates", params={"subject_kind": "user"})
        assert candidates.status_code == 200, candidates.text
        assert kb.actors["reader"].user_id in {r["subject_id"] for r in candidates.json()["items"]}
        assert kb.actors["other"].user_id not in {r["subject_id"] for r in candidates.json()["items"]}
        grant = {
            "expected_revision": 1,
            "recipients": [{"subject_kind": "user", "subject_id": kb.actors["reader"].user_id}],
        }
        headers = key()
        published = await owner.post(f"{PREFIX}/works/{wid}/publish", json=grant, headers=headers)
        assert published.status_code == 200, published.text
        assert published.json()["revision"] == 2
        assert (
            await owner.post(f"{PREFIX}/works/{wid}/publish", json=grant, headers=headers)
        ).json() == published.json()
        assert (await owner.post(f"{PREFIX}/works/{wid}/publish", json=grant, headers=key())).status_code == 409
        fid = work["files"][0]["file_id"]
        async with kb.client("reader") as reader:
            shared = (await reader.get(f"{PREFIX}/works/{wid}")).json()
            assert not shared["can_manage"] and shared["recipients"] == []
            assert (await reader.get(PREFIX + "/works")).json()["total"] == 1
            downloaded = await reader.get(f"{PREFIX}/works/{wid}/files/{fid}/download")
            assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "no-store"
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                assert "方案支持离线部署" in archive.read("word/document.xml").decode()
            assert (
                await reader.get(f"{PREFIX}/works/{wid}/share-candidates", params={"subject_kind": "user"})
            ).status_code == 403
            assert (
                await reader.post(
                    f"{PREFIX}/works/{wid}/publish", json={"expected_revision": 2, "recipients": []}, headers=key()
                )
            ).status_code == 403
            revoked = await owner.post(
                f"{PREFIX}/works/{wid}/publish", json={"expected_revision": 2, "recipients": []}, headers=key()
            )
            assert revoked.status_code == 200 and revoked.json()["revision"] == 3
            assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 404
            assert (await reader.get(f"{PREFIX}/works/{wid}/files/{fid}/download")).status_code == 404
            assert (await reader.get(PREFIX + "/works")).json()["total"] == 0
            replay = await owner.post(f"{PREFIX}/works/{wid}/publish", json=grant, headers=headers)
            assert replay.json()["revision"] == 3 and replay.json()["recipients"] == []
    assert provider.calls == 1 and "授权原文" in provider.prompts[0]
    assert (
        await kb.admin.fetchval(
            "SELECT count(*) FROM knowledge.audit WHERE workspace_id=$1 AND operation='work.share'", kb.workspace
        )
        == 2
    )


@pytest.mark.asyncio
async def test_source_acl_always_required_even_owner_and_receipt_replay(kb):
    mid, vid, wid, body, provider = await setup_work(kb)
    async with kb.client() as owner, kb.client("reader") as reader, kb.client("other") as other:
        work = (await owner.get(f"{PREFIX}/works/{wid}")).json()
        fid = work["files"][0]["file_id"]
        grant = {
            "expected_revision": 1,
            "recipients": [{"subject_kind": "user", "subject_id": kb.actors["reader"].user_id}],
        }
        headers = key()
        assert (await owner.post(f"{PREFIX}/works/{wid}/publish", json=grant, headers=headers)).status_code == 200
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 404  # sharing cannot grant source access
        await share_source(kb, mid)
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 200
        await kb.service.save_acl(kb.actors["owner"], uuid4(), mid, ACLWrite(expected_acl_revision=2))
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 404
        assert (await other.get(f"{PREFIX}/works/{wid}")).status_code == 404
        assert (await other.get(f"{PREFIX}/works/{wid}/files/{fid}/download")).status_code == 404
        # Even its author cannot copy the derived file into a less restricted material.
        copied = await owner.post(
            PREFIX + "/materials", json=selection(files=[fid], name="copy").model_dump(mode="json"), headers=key()
        )
        assert copied.status_code in {403, 422}, copied.text
        await kb.service.save_acl(
            kb.actors["owner"],
            uuid4(),
            mid,
            ACLWrite(
                expected_acl_revision=3,
                denied_user_ids=[kb.actors["owner"].user_id],
            ),
        )
        for actor in [owner, reader]:
            assert (await actor.get(PREFIX + "/works")).json()["total"] == 0
            assert (await actor.get(f"{PREFIX}/works/{wid}/files/{fid}/download")).status_code == 404
        assert (await owner.post(f"{PREFIX}/works/{wid}/publish", json=grant, headers=headers)).status_code == 404
        async with kb.database.transaction(kb.actors["owner"], readonly=True) as c:
            for table in ["work", "work_source", "work_file", "work_grant", "file"]:
                assert await c.fetchval(f"SELECT count(*) FROM knowledge.{table}") == 0  # noqa: S608


@pytest.mark.asyncio
async def test_work_team_membership_function_deny_and_file_substitution(kb):
    mid, vid, wid, body, provider = await setup_work(kb)
    await kb.membership("reader")
    await share_source(kb, mid, team=True)
    async with kb.client() as owner, kb.client("reader") as reader:
        work = (await owner.get(f"{PREFIX}/works/{wid}")).json()
        fid = work["files"][0]["file_id"]
        published = await owner.post(
            f"{PREFIX}/works/{wid}/publish",
            json={"expected_revision": 1, "recipients": [{"subject_kind": "team", "subject_id": str(kb.team)}]},
            headers=key(),
        )
        assert published.status_code == 200, published.text
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 200
        second = await owner.post(PREFIX + "/works", json={**body, "purpose": "第二份"}, headers=key())
        second_id = second.json()["job_id"]
        assert (await owner.get(f"{PREFIX}/works/{second_id}/files/{fid}/download")).status_code == 404
        await kb.grant_scope("reader", "self")
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 404
        await kb.grant_scope("reader", "workspace")
        await kb.admin.execute(
            "DELETE FROM platform.team_membership WHERE workspace_id=$1 AND user_ref_id=$2",
            kb.workspace,
            UUID(kb.actors["reader"].user_id),
        )
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 404
        for recipients in [
            [{"subject_kind": "user", "subject_id": kb.actors["other"].user_id}],
            [{"subject_kind": "user", "subject_id": kb.actors["reader"].user_id}] * 2,
        ]:
            rejected = await owner.post(
                f"{PREFIX}/works/{wid}/publish", json={"expected_revision": 2, "recipients": recipients}, headers=key()
            )
            assert rejected.status_code == 422, rejected.text
        await kb.grant_scope("owner", None, effect="deny")
        assert (await owner.get(PREFIX + "/works")).status_code == 403


@pytest.mark.asyncio
async def test_generation_concurrent_replay_and_private_linear_versions(kb):
    mid, vid, wid, body, provider = await setup_work(kb)
    async with kb.client() as owner, kb.client("reader") as reader:
        headers = key()
        requests = await asyncio.gather(*(owner.post(PREFIX + "/works", json=body, headers=headers) for _ in range(2)))
        assert all(r.status_code == 202 for r in requests), [r.text for r in requests]
        assert requests[0].json() == requests[1].json()
        assert provider.calls == 2  # one initial + one concurrent request, not three
        assert (
            await owner.post(PREFIX + "/works", json={**body, "purpose": "changed"}, headers=headers)
        ).status_code == 409
        await share_source(kb, mid)
        assert (
            await owner.post(
                f"{PREFIX}/works/{wid}/publish",
                json={
                    "expected_revision": 1,
                    "recipients": [{"subject_kind": "user", "subject_id": kb.actors["reader"].user_id}],
                },
                headers=key(),
            )
        ).status_code == 200
        newbody = {**body, "parent_work_id": str(wid), "expected_parent_revision": 2}
        assert (await reader.post(PREFIX + "/works", json=newbody, headers=key())).status_code == 403
        newer = await owner.post(PREFIX + "/works", json=newbody, headers=key())
        assert newer.status_code == 202, newer.text
        nwid = newer.json()["job_id"]
        newwork = (await owner.get(f"{PREFIX}/works/{nwid}")).json()
        assert newwork["version_no"] == 2 and newwork["parent_work_id"] == str(wid) and newwork["recipients"] == []
        assert (await reader.get(f"{PREFIX}/works/{nwid}")).status_code == 404
        assert (await reader.get(f"{PREFIX}/works/{wid}")).status_code == 200
        assert (await owner.post(PREFIX + "/works", json=newbody, headers=key())).status_code == 409
        assert (
            await owner.post(PREFIX + "/works", json={**newbody, "expected_parent_revision": 3}, headers=key())
        ).status_code == 409
        # Existing works remain usable without current provider configuration.
        kb.service.provider = None
        assert (await owner.get(PREFIX + "/works")).json()["total"] == 3
        assert (await owner.post(PREFIX + "/works", json=body, headers=headers)).status_code == 202
        await kb.admin.execute("UPDATE knowledge.version SET state='deleted' WHERE id=$1", vid)
        assert (await owner.get(PREFIX + "/works")).json()["total"] == 0
        assert (await owner.post(PREFIX + "/works", json=body, headers=headers)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["retrieve", "generate"])
async def test_revocation_during_provider_call_archives_nothing(kb, phase):
    await kb.configure()
    mid, vid = await material(kb)
    provider = EvidenceProvider()
    kb.service.provider = provider
    kb.service.document_workflow = provider

    async def revoke():
        await kb.admin.execute(
            "UPDATE knowledge.material_acl SET denied_user_ids=ARRAY[$2::uuid] WHERE material_id=$1",
            mid,
            UUID(kb.actors["owner"].user_id),
        )

    if phase == "retrieve":
        provider.after_retrieval = revoke
    else:
        provider.after_generation = revoke
    async with kb.client() as owner:
        result = await owner.post(
            PREFIX + "/works",
            json={"purpose": "失败不得归档", "material_ids": [str(mid)], "format": "docx", "audience": "内部"},
            headers=key(),
        )
        assert result.status_code == 404, result.text
    for table in ["work", "file"]:
        assert (
            await kb.admin.fetchval(
                f"SELECT count(*) FROM knowledge.{table} WHERE workspace_id=$1",  # noqa: S608
                kb.workspace,
            )
            == 0
        )
    assert provider.calls == (1 if phase == "generate" else 0)


@pytest.mark.asyncio
async def test_work_tables_are_read_only_for_runtime(kb):
    await setup_work(kb)
    async with kb.database.transaction(kb.actors["owner"]) as c:
        for table in ["work", "work_file", "work_source", "work_grant"]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with c.transaction():
                    await c.execute(f"DELETE FROM knowledge.{table}")  # noqa: S608


@pytest.mark.asyncio
async def test_provider_status_does_not_probe_count_or_invent_agent_capability(kb):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    kb.service.provider = SimpleNamespace(
        configured=True, agent_id="", count=AsyncMock(side_effect=RuntimeError("count unavailable"))
    )
    async with kb.client() as owner:
        status = (await owner.get(PREFIX + "/provider/status")).json()
        assert status["retrieval"]["status"] == "available"
        assert status["generation"]["status"] == "unconfigured"
        kb.service.provider.agent_id = "synthetic-agent"
        kb.service.provider.source_only_reviewed = True
        assert (await owner.get(PREFIX + "/provider/status")).json()["generation"]["status"] == "unconfigured"
        kb.service.document_workflow = SimpleNamespace(configured=True, source_only_reviewed=True)
        status = (await owner.get(PREFIX + "/provider/status")).json()
        assert status["generation"]["formats"] == ["docx"]
    kb.service.provider.count.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_retrieval_evidence_cannot_generate_an_ungrounded_work(kb):
    from unittest.mock import AsyncMock

    await kb.configure()
    mid, vid = await material(kb)
    kb.service.provider = EvidenceProvider()
    kb.service.document_workflow = kb.service.provider
    kb.service.provider.retrieve = AsyncMock(return_value=())
    async with kb.client() as owner:
        result = await owner.post(
            PREFIX + "/works",
            json={"purpose": "无证据", "material_ids": [str(mid)], "format": "docx", "audience": "内部"},
            headers=key(),
        )
        assert result.status_code == 409 and result.json()["detail"] == "KNOWLEDGE_GENERATION_NO_EVIDENCE"
    assert kb.service.provider.calls == 0
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.work WHERE workspace_id=$1", kb.workspace) == 0


@pytest.mark.asyncio
async def test_v266_backfill_keeps_valid_manifest_private_and_skips_invalid(kb):
    import hashlib
    import json
    from pathlib import Path

    from pydantic_core import to_jsonable_python

    await kb.configure()
    mid, vid = await material(kb)
    owner = kb.actors["owner"]
    archived = await kb.service.upload(owner, uuid4(), "old.docx", "application/octet-stream", b"legacy-docx")
    replay_key = uuid4()
    wid = UUID(int=UUID(owner.user_id).int ^ replay_key.int)
    original_body = {"purpose": "旧作品", "material_ids": [str(mid)], "format": "docx", "audience": "内部"}
    original_payload = {"payload": {**original_body, "work_id": str(wid)}, "scope": owner.model_dump()}
    digest = hashlib.sha256(
        json.dumps(to_jsonable_python(original_payload), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    manifest = {
        "work_id": str(wid),
        "title": "旧作品",
        "format": "docx",
        "audience": "内部",
        "sources": [{"material_id": str(mid), "version_id": str(vid)}],
        "files": [{k: archived[k] for k in ("file_id", "filename", "media_type", "size_bytes")}],
    }
    for document in [manifest, {**manifest, "work_id": "bad-id"}]:
        await kb.admin.execute(
            "INSERT INTO ops.mutation_receipt(workspace_id,actor_id,operation,request_key,request_digest,response) "
            "VALUES($1,$2,'knowledge.work.generate',$3,$4,$5::jsonb)",
            kb.workspace,
            UUID(owner.user_id),
            replay_key if document is manifest else uuid4(),
            digest,
            {"_manifest": document, "job_id": str(wid), "status": "succeeded"},
        )
    migration = (
        Path(__file__).resolve().parents[3] / "database/migrations/V266__knowledge_work_sharing.sql"
    ).read_text()
    backfill = migration[migration.index("DO $$") : migration.index("-- Generated files are never")]
    await kb.admin.execute(backfill)
    # Retry backfill safely; existing durable work remains unchanged.
    await kb.admin.execute(backfill)
    async with kb.client() as client:
        response = await client.get(f"{PREFIX}/works/{wid}")
        assert response.status_code == 200, response.text
        assert response.json()["recipients"] == [] and response.json()["revision"] == 1
        replay = await client.post(PREFIX + "/works", json=original_body, headers={"Idempotency-Key": str(replay_key)})
        assert replay.status_code == 202, replay.text
        assert replay.json()["job_id"] == str(wid)
        fid = archived["file_id"]
        assert (await client.get(f"{PREFIX}/works/{wid}/files/{fid}/download")).content == b"legacy-docx"
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.work WHERE workspace_id=$1", kb.workspace) == 1
    assert (
        await kb.admin.fetchval(
            "SELECT count(*) FROM ops.mutation_receipt WHERE workspace_id=$1 AND operation='knowledge.work.generate'",
            kb.workspace,
        )
        == 2
    )


@pytest.mark.asyncio
async def test_generation_provider_wall_timeout_never_archives_partial_work(kb):
    await kb.configure()
    mid, _ = await material(kb)
    kb.service.provider = EvidenceProvider()
    kb.service.document_workflow = kb.service.provider
    kb.service.provider_deadline_seconds = 0.005
    original = kb.service.provider.retrieve

    async def delayed(request):
        await asyncio.sleep(0.02)
        return await original(request)

    kb.service.provider.retrieve = delayed
    async with kb.client() as owner:
        result = await owner.post(
            PREFIX + "/works",
            json={"purpose": "网络超时", "material_ids": [str(mid)], "format": "docx", "audience": "内部"},
            headers=key(),
        )
        assert result.status_code == 503
        assert result.json()["detail"] == "KNOWLEDGE_PROVIDER_UNAVAILABLE"
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.work WHERE workspace_id=$1", kb.workspace) == 0
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.file WHERE workspace_id=$1", kb.workspace) == 0


@pytest.mark.asyncio
async def test_generation_requires_reviewed_source_only_agent(kb):
    await kb.configure()
    mid, _ = await material(kb)
    kb.service.provider = EvidenceProvider()
    kb.service.document_workflow = kb.service.provider
    kb.service.provider.source_only_reviewed = False
    async with kb.client() as owner:
        result = await owner.post(
            PREFIX + "/works",
            json={"purpose": "未经审查的 Agent", "material_ids": [str(mid)], "format": "docx", "audience": "内部"},
            headers=key(),
        )
        assert result.status_code == 409
        assert result.json()["detail"] == "KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED"
    assert kb.service.provider.calls == 0 and kb.service.provider.requests == []


@pytest.mark.asyncio
async def test_active_source_update_during_generation_keeps_original_version(kb):
    await kb.configure()
    mid, vid = await material(kb)
    kb.service.provider = EvidenceProvider()
    kb.service.document_workflow = kb.service.provider

    async def advance_source():
        newer = await kb.service.save(kb.actors["owner"], uuid4(), selection(name="更新后的来源"), mid, 1)
        await kb.admin.execute(
            "UPDATE knowledge.version SET state='retrievable' WHERE id=$1", UUID(newer["version_id"])
        )
        await kb.admin.execute(
            "UPDATE knowledge.material SET active_version_id=$2 WHERE id=$1", mid, UUID(newer["version_id"])
        )

    kb.service.provider.after_generation = advance_source
    async with kb.client() as owner:
        result = await owner.post(
            PREFIX + "/works",
            json={"purpose": "冻结原始来源", "material_ids": [str(mid)], "format": "docx", "audience": "内部"},
            headers=key(),
        )
        assert result.status_code == 202, result.text
        work = (await owner.get(f"{PREFIX}/works/{result.json()['job_id']}")).json()
        assert work["sources"][0]["version_id"] == str(vid)
        assert (await owner.get(PREFIX + "/works")).json()["total"] == 1


@pytest.mark.asyncio
async def test_workflow_original_bytes_http_archive_and_no_signed_url_in_manifest(kb):
    from ..test_knowledge_document_workflow import BODY, RUN, Fixture

    await kb.configure()
    mid, vid = await material(kb)
    kb.service.provider = EvidenceProvider()
    fixture = Fixture()
    kb.service.document_workflow = fixture.client
    async with kb.client() as owner:
        headers = key()
        body = {"purpose": "中台真实文件契约夹具", "material_ids": [str(mid)], "format": "docx", "audience": "内部"}
        response = await owner.post(PREFIX + "/works", json=body, headers=headers)
        assert response.status_code == 202, response.text
        wid = response.json()["job_id"]
        work = (await owner.get(f"{PREFIX}/works/{wid}")).json()
        downloaded = await owner.get(f"{PREFIX}/works/{wid}/files/{work['files'][0]['file_id']}/download")
        assert downloaded.content == BODY
        assert (await owner.post(PREFIX + "/works", json=body, headers=headers)).json() == response.json()
        assert len(fixture.requests) == len(fixture.downloads) == 1
    manifest = await kb.admin.fetchval("SELECT manifest FROM knowledge.work WHERE id=$1", UUID(wid))
    assert manifest["provider_run_id"] == RUN
    assert manifest["sources"][0]["version_id"] == str(vid)
    assert "signature" not in str(manifest) and "workflow-test-key" not in str(manifest)
    assert str(vid) in fixture.requests[0].content.decode()
    assert "授权原文" in __import__("json").loads(fixture.requests[0].content)["inputs"]["source_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["basic401", "failed", "unknown"])
async def test_workflow_failure_receipt_never_automatically_regenerates_same_key(kb, failure):
    from ..test_knowledge_document_workflow import RUN, Fixture, events

    await kb.configure()
    mid, _ = await material(kb)
    kb.service.provider = EvidenceProvider()
    fixture = (
        Fixture(file_status=401)
        if failure == "basic401"
        else Fixture(rows=events(status="failed") if failure == "failed" else events()[:1])
    )
    kb.service.document_workflow = fixture.client
    body = {"purpose": "失败回执", "material_ids": [str(mid)], "format": "docx", "audience": "内部"}
    headers = key()
    async with kb.client() as owner:
        first = await owner.post(PREFIX + "/works", json=body, headers=headers)
        second = await owner.post(PREFIX + "/works", json=body, headers=headers)
        assert first.status_code == second.status_code == 503
        assert first.json() == second.json()
        assert len(fixture.requests) == 1 and len(kb.service.provider.requests) == 1
        assert (
            await owner.post(PREFIX + "/works", json=body | {"purpose": "different"}, headers=headers)
        ).status_code == 409
    for table in ["work", "file"]:
        assert (
            await kb.admin.fetchval(
                f"SELECT count(*) FROM knowledge.{table} WHERE workspace_id=$1",  # noqa: S608
                kb.workspace,
            )
            == 0
        )
    receipt = await kb.admin.fetchval(
        "SELECT response FROM ops.mutation_receipt WHERE workspace_id=$1 AND request_key=$2 "
        "AND operation='knowledge.work.generate'",
        kb.workspace,
        UUID(headers["Idempotency-Key"]),
    )
    assert receipt["_generation_failure"]["run_id"] == RUN
    assert "private-test-signature" not in str(receipt) and "workflow-test-key" not in str(receipt)
    await kb.grant_scope("owner", None, effect="deny")
    async with kb.client() as owner:
        assert (await owner.post(PREFIX + "/works", json=body, headers=headers)).status_code == 403
    assert len(fixture.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["source_acl", "source_deleted", "function"])
async def test_workflow_download_time_revocation_archives_nothing(kb, revocation):
    from ..test_knowledge_document_workflow import Fixture

    await kb.configure()
    mid, vid = await material(kb)
    kb.service.provider = EvidenceProvider()
    fixture = Fixture()
    kb.service.document_workflow = fixture.client

    async def revoke():
        if revocation == "source_acl":
            await kb.admin.execute(
                "UPDATE knowledge.material_acl SET denied_user_ids=ARRAY[$2::uuid] WHERE material_id=$1",
                mid,
                UUID(kb.actors["owner"].user_id),
            )
        elif revocation == "source_deleted":
            await kb.admin.execute("UPDATE knowledge.version SET state='deleted' WHERE id=$1", vid)
        else:
            await kb.grant_scope("owner", None, effect="deny")

    fixture.after_download = revoke
    async with kb.client() as owner:
        response = await owner.post(
            PREFIX + "/works",
            json={"purpose": "下载期间撤权", "material_ids": [str(mid)], "format": "docx", "audience": "内部"},
            headers=key(),
        )
        assert response.status_code == (403 if revocation == "function" else 404), response.text
    assert len(fixture.requests) == len(fixture.downloads) == 1
    for table in ["work", "file"]:
        assert (
            await kb.admin.fetchval(
                f"SELECT count(*) FROM knowledge.{table} WHERE workspace_id=$1",  # noqa: S608
                kb.workspace,
            )
            == 0
        )


@pytest.mark.asyncio
async def test_document_attempt_survives_business_rollback_and_never_restarts_remote_run(kb, monkeypatch):
    from ..test_knowledge_document_workflow import Fixture

    await kb.configure()
    mid, _ = await material(kb)
    kb.service.provider = EvidenceProvider()
    fixture = Fixture()
    kb.service.document_workflow = fixture.client
    body = KnowledgeGenerateInput(purpose="归档事务失败", material_ids=[str(mid)], format="docx", audience="内部")
    request_key = uuid4()
    original = kb.service.repository.archive

    async def broken_archive(*args):
        raise RuntimeError("synthetic archive failure")

    monkeypatch.setattr(kb.service.repository, "archive", broken_archive)
    with pytest.raises(RuntimeError, match="synthetic archive failure"):
        await kb.service.generate_work(kb.actors["owner"], request_key, body)
    monkeypatch.setattr(kb.service.repository, "archive", original)
    async with kb.client() as owner:
        retry = await owner.post(
            PREFIX + "/works", json=body.model_dump(mode="json"), headers={"Idempotency-Key": str(request_key)}
        )
        assert retry.status_code == 503 and retry.json()["detail"] == "KNOWLEDGE_DOCUMENT_RESULT_UNKNOWN"
    assert len(fixture.requests) == len(fixture.downloads) == 1
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.work WHERE workspace_id=$1", kb.workspace) == 0
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.file WHERE workspace_id=$1", kb.workspace) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("pool_size,request_count,same_key", [(1, 1, True), (1, 8, True), (8, 8, False), (8, 8, True)])
async def test_generation_single_pool_lease_and_concurrent_receipts(kb, pool_size, request_count, same_key):
    await kb.configure()
    mid, _ = await material(kb)
    provider = EvidenceProvider()
    kb.service.provider = kb.service.document_workflow = provider
    body = {"purpose": "单连接与并发验证", "material_ids": [str(mid)], "format": "docx", "audience": "内部"}
    entered, release = asyncio.Event(), asyncio.Event()
    retrieved = 0

    async def hold_retrieval():
        nonlocal retrieved
        retrieved += 1
        if same_key or retrieved == request_count:
            entered.set()
        await release.wait()

    provider.after_retrieval = hold_retrieval
    headers = key()
    async with runtime_pool(kb, pool_size) as pool, kb.client() as client:
        tasks = [
            asyncio.create_task(client.post(PREFIX + "/works", json=body, headers=headers if same_key else key()))
            for _ in range(request_count)
        ]
        try:
            await asyncio.wait_for(entered.wait(), 8)
            if pool_size == request_count:
                async with asyncio.timeout(5):
                    while pool.get_idle_size() != 0:
                        await asyncio.sleep(0.01)
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*tasks), 15)
            assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
            assert provider.calls == len(provider.requests) == (1 if same_key else request_count)
            assert len({r.json()["job_id"] for r in responses}) == (1 if same_key else request_count)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["source_acl", "source_deleted", "function"])
async def test_document_reservation_wait_revalidates_before_external_send(kb, monkeypatch, revocation):
    await kb.configure()
    mid, vid = await material(kb)
    provider = EvidenceProvider()
    kb.service.provider = kb.service.document_workflow = provider
    original = kb.service._reserve_document_attempt

    async def revoke_after_reserve(*args):
        await original(*args)
        if revocation == "source_acl":
            await kb.admin.execute(
                "UPDATE knowledge.material_acl SET denied_user_ids=ARRAY[$2::uuid] WHERE material_id=$1",
                mid,
                UUID(kb.actors["owner"].user_id),
            )
        elif revocation == "source_deleted":
            await kb.admin.execute("UPDATE knowledge.version SET state='deleted' WHERE id=$1", vid)
        else:
            await kb.grant_scope("owner", None, effect="deny")

    monkeypatch.setattr(kb.service, "_reserve_document_attempt", revoke_after_reserve)
    async with kb.client() as client:
        response = await client.post(
            PREFIX + "/works",
            headers=key(),
            json={
                "purpose": "提交标记期间撤权",
                "material_ids": [str(mid)],
                "format": "docx",
                "audience": "内部",
            },
        )
        assert response.status_code == (403 if revocation == "function" else 404), response.text
    assert provider.calls == 0
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.work WHERE workspace_id=$1", kb.workspace) == 0


@pytest.mark.asyncio
async def test_generation_oversized_evidence_is_correctable_before_attempt(kb):
    await kb.configure()
    mid, _ = await material(kb)
    provider = EvidenceProvider()
    kb.service.provider = kb.service.document_workflow = provider
    retrieve = provider.retrieve

    async def excessive(request):
        chunks = await retrieve(request)
        return tuple(chunk.model_copy(update={"text": "原文" * 6000}) for chunk in chunks)

    provider.retrieve = excessive
    headers = key()
    body = {"purpose": "过量选材", "material_ids": [str(mid)], "format": "docx", "audience": "内部"}
    async with kb.client() as client:
        response = await client.post(PREFIX + "/works", headers=headers, json=body)
        assert response.status_code == 422 and response.json()["detail"] == "KNOWLEDGE_DOCUMENT_SOURCE_LIMIT"
        assert provider.calls == 0
        assert (
            await kb.admin.fetchval(
                "SELECT count(*) FROM ops.mutation_receipt WHERE request_key=$1", UUID(headers["Idempotency-Key"])
            )
            == 0
        )
        provider.retrieve = retrieve
        corrected = await client.post(PREFIX + "/works", headers=headers, json=body)
        assert corrected.status_code == 202, corrected.text
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_cancelled_generation_releases_session_lock_and_preserves_attempt(kb):
    await kb.configure()
    mid, _ = await material(kb)
    provider = EvidenceProvider()
    kb.service.provider = kb.service.document_workflow = provider
    entered = asyncio.Event()

    async def never_finish(*args, **kwargs):
        provider.calls += 1
        entered.set()
        await asyncio.Event().wait()

    provider.generate = never_finish
    body = KnowledgeGenerateInput(purpose="取消后重放", material_ids=[str(mid)], format="docx", audience="内部")
    request_key = uuid4()
    async with runtime_pool(kb, 1) as pool:
        task = asyncio.create_task(kb.service.generate_work(kb.actors["owner"], request_key, body))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with pool.acquire() as connection:
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'"
                )
                == 0
            )
        async with kb.client() as client:
            replay = await asyncio.wait_for(
                client.post(
                    PREFIX + "/works", headers={"Idempotency-Key": str(request_key)}, json=body.model_dump(mode="json")
                ),
                5,
            )
            assert replay.status_code == 503 and replay.json()["detail"] == "KNOWLEDGE_DOCUMENT_RESULT_UNKNOWN"
        assert provider.calls == 1
