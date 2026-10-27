"""Real PostgreSQL / non-owner RLS tests for the public token boundary."""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sales_backend.api.dependencies import RequestIdentity, get_identity
from sales_backend.api.knowledge import ACLWrite
from sales_backend.api.knowledge_distribution import public_router, router, service
from sales_backend.contracts.knowledge_distribution import (
    AnalyticsQuery,
    PublicationWrite,
    Questionnaire,
    ResponseWrite,
    RevokeWrite,
    ShareWrite,
)
from sales_backend.domain.knowledge_access import KnowledgeACLGrant
from sales_backend.repositories.identity import ActorRecord
from sales_backend.services.knowledge_distribution import DistributionService, digest

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection

pytestmark = pytest.mark.asyncio


async def no_sync(connection, **event):
    assert connection.is_in_transaction()
    assert set(event) == {
        "workspace_id",
        "material_id",
        "publication_id",
        "version_id",
        "event_kind",
        "revision",
        "actor_id",
    }


async def setup(
    kb, *, scope="public", media="text/plain", content=b"Archived document", questionnaire=None, share=True
):
    await kb.configure()
    f = await kb.service.upload(kb.actors["owner"], uuid4(), "customer.txt", media, content)
    m = await kb.service.save(kb.actors["owner"], uuid4(), selection(files=[f["file_id"]], content_scope=scope))
    svc = DistributionService(kb.database, enqueue=no_sync)
    body = PublicationWrite(
        expected_revision=0, version_id=m["version_id"], **({"questionnaire": questionnaire} if questionnaire else {})
    )
    p = await svc.publish(kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), body)
    s = (
        await svc.share(
            kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p["publication_id"])
        )
        if share
        else None
    )
    return svc, f, m, p, s


def answer(version=1, **updates):
    return ResponseWrite(
        browser_id="browser-synthetic-0001",
        questionnaire_version=version,
        answers={
            "name": "客户甲",
            "company": "合成公司",
            "contact": "synthetic@example.test",
            "contact_intent": "希望联系",
        },
        **updates,
    )


@asynccontextmanager
async def client(kb, svc, name="owner"):
    app = FastAPI()
    app.include_router(router)
    app.include_router(public_router)
    actor = kb.actors[name]
    app.dependency_overrides[service] = lambda: svc
    app.dependency_overrides[get_identity] = lambda: RequestIdentity(
        actor, "synthetic", ActorRecord(actor, "test", "Test", ()), must_change_password=False
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_public_questionnaire_gate_and_request_statistics(kb):
    svc, f, m, p, s = await setup(kb)
    headers = {"X-Knowledge-Share": s["token"]}
    path = f"/api/v1/public/knowledge/files/{f['file_id']}"
    async with client(kb, svc) as c:
        for _ in range(2):
            meta = await c.get(
                "/api/v1/public/knowledge/share", params={"browser_id": "browser-synthetic-0001"}, headers=headers
            )
            assert meta.status_code == 200, meta.text
            assert "content" not in meta.json() and "token" not in meta.json()
            assert meta.headers["cache-control"] == "no-store"
        assert (await c.get(path + "/download", headers=headers)).status_code == 403
        key = str(uuid4())
        form = answer().model_dump(mode="json")
        result = await c.post(
            "/api/v1/public/knowledge/responses", json=form, headers={**headers, "Idempotency-Key": key}
        )
        assert result.status_code == 200, result.text
        assert (
            await c.post("/api/v1/public/knowledge/responses", json=form, headers={**headers, "Idempotency-Key": key})
        ).json() == result.json()
        bad = {**form, "answers": {**form["answers"], "name": "other"}}
        assert (
            await c.post("/api/v1/public/knowledge/responses", json=bad, headers={**headers, "Idempotency-Key": key})
        ).status_code == 409
        granted = {**headers, "X-Knowledge-Grant": result.json()["grant_token"]}
        preview = await c.get(path + "/preview", headers=granted)
        assert preview.status_code == 200 and preview.text == "Archived document"
        assert preview.headers["content-type"].startswith("text/plain")
        assert (await c.get(path + "/download", headers=granted)).content == b"Archived document"
    stats = await svc.read(kb.actors["owner"], "statistics", AnalyticsQuery())
    assert {
        k: stats[k]
        for k in ["visits", "unique_visitors_estimate", "questionnaire_submissions", "previews", "download_requests"]
    } == {
        "visits": 2,
        "unique_visitors_estimate": 1,
        "questionnaire_submissions": 1,
        "previews": 1,
        "download_requests": 1,
    }
    responses = await svc.read(kb.actors["owner"], "responses", AnalyticsQuery())
    assert responses["items"][0]["sales_user_id"] == UUID(kb.actors["owner"].user_id)
    assert responses["items"][0]["version_id"] == UUID(str(m["version_id"]))
    assert responses["items"][0]["answers"]["name"] == "客户甲"


async def test_member_manage_does_not_authorize_publication_or_all_statistics(kb):
    await kb.configure()
    actor = await kb.user("contributor", permissions=["read", "create", "edit", "manage"])
    f = await kb.service.upload(actor, uuid4(), "owned.txt", "text/plain", b"owned")
    m = await kb.service.save(actor, uuid4(), selection(files=[f["file_id"]], content_category="conference_speech"))
    svc = DistributionService(kb.database, enqueue=no_sync)
    with pytest.raises(PermissionError):
        await svc.publish(
            actor,
            uuid4(),
            UUID(str(m["material_id"])),
            PublicationWrite(expected_revision=0, version_id=m["version_id"]),
        )
    with pytest.raises(PermissionError):
        await svc.read(actor, "statistics", AnalyticsQuery(scope="all"))
    with pytest.raises(PermissionError):
        await svc.read(actor, "responses", AnalyticsQuery(sales_user_id=UUID(kb.actors["owner"].user_id)))


@pytest.mark.parametrize("scope", ["internal", "partner"])
async def test_internal_scopes_not_implicitly_published(kb, scope):
    with pytest.raises(asyncpg.InvalidParameterValueError, match="CONTENT_REQUIRED"):
        await setup(kb, scope=scope)


async def test_unarchived_source_link_not_downloadable(kb):
    await kb.configure()
    m = await kb.service.save(kb.actors["owner"], uuid4(), selection())
    svc = DistributionService(kb.database, enqueue=no_sync)
    with pytest.raises(asyncpg.InvalidParameterValueError, match="CONTENT_REQUIRED"):
        await svc.publish(
            kb.actors["owner"],
            uuid4(),
            UUID(str(m["material_id"])),
            PublicationWrite(expected_revision=0, version_id=m["version_id"]),
        )


async def test_grants_cannot_cross_share_file_or_tenant(kb):
    svc, f, m, p, s = await setup(kb)
    grant = await svc.public("response", s["token"], body=answer(), key=uuid4())
    s2 = await svc.share(
        kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p["publication_id"])
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await svc.public("download", s2["token"], grant=grant["grant_token"], file_id=UUID(str(f["file_id"])))
    extra = await kb.service.upload(kb.actors["owner"], uuid4(), "other.txt", "text/plain", b"not part of version")
    with pytest.raises(asyncpg.NoDataFoundError):
        await svc.public("download", s["token"], grant=grant["grant_token"], file_id=UUID(str(extra["file_id"])))
    assert (await svc.read(kb.actors["other"], "materials", "", None, None, 50, 0))["total"] == 0
    assert (await svc.read(kb.actors["other"], "responses", AnalyticsQuery(scope="all")))["total"] == 0
    with pytest.raises(LookupError):
        await svc.share(
            kb.actors["other"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p["publication_id"])
        )


async def test_revoke_and_publication_replacement_stop_existing_grants(kb):
    svc, f, m, p, s = await setup(kb)
    grant = await svc.public("response", s["token"], body=answer(), key=uuid4())
    key = uuid4()
    await svc.revoke(kb.actors["owner"], key, UUID(str(s["share_id"])), RevokeWrite())
    await svc.revoke(kb.actors["owner"], key, UUID(str(s["share_id"])), RevokeWrite())
    with pytest.raises(asyncpg.NoDataFoundError):
        await svc.public("download", s["token"], grant=grant["grant_token"], file_id=UUID(str(f["file_id"])))
    s2 = await svc.share(
        kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p["publication_id"])
    )
    p2 = await svc.publish(
        kb.actors["owner"],
        uuid4(),
        UUID(str(m["material_id"])),
        PublicationWrite(expected_revision=1, version_id=m["version_id"], description="new description"),
    )
    assert p2["revision"] == 2
    with pytest.raises(asyncpg.NoDataFoundError):
        await svc.public("metadata", s2["token"], browser_id="browser-synthetic-0001")
    history = await svc.read(kb.actors["owner"], "responses", AnalyticsQuery())
    assert history["items"][0]["questionnaire_version"] == 1


async def test_acl_revocation_and_internal_latest_version_stop_public_access(kb):
    svc, f, m, p, s = await setup(kb)
    await kb.service.save_acl(
        kb.actors["owner"],
        uuid4(),
        UUID(str(m["material_id"])),
        ACLWrite(expected_acl_revision=1, grants=[KnowledgeACLGrant(subject_kind="company", actions={"read"})]),
    )
    sale = await svc.share(
        kb.actors["reader"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p["publication_id"])
    )
    assert (await svc.public("metadata", sale["token"], browser_id="browser-synthetic-0001"))["version_no"] == 1
    await kb.service.save_acl(
        kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), ACLWrite(expected_acl_revision=2)
    )
    with pytest.raises(asyncpg.NoDataFoundError):
        await svc.public("metadata", sale["token"], browser_id="browser-synthetic-0001")
    await kb.service.save(
        kb.actors["owner"],
        uuid4(),
        selection(files=[f["file_id"]], content_scope="internal", name="internal now"),
        UUID(str(m["material_id"])),
        m["material_revision"],
    )
    with pytest.raises(asyncpg.NoDataFoundError):
        await svc.public("metadata", s["token"], browser_id="browser-synthetic-0001")
    assert (await svc.read(kb.actors["owner"], "materials", "", None, None, 50, 0))["total"] == 0


async def test_disabled_questionnaire_grant_is_not_counted_as_lead(kb):
    svc, f, m, p, s = await setup(kb, questionnaire=Questionnaire(enabled=False))
    form = ResponseWrite(browser_id="browser-synthetic-0001", questionnaire_version=1)
    grant = await svc.public("response", s["token"], body=form, key=uuid4())
    assert (await svc.public("download", s["token"], grant=grant["grant_token"], file_id=UUID(str(f["file_id"]))))[
        "content"
    ] == b"Archived document"
    with pytest.raises(asyncpg.InvalidParameterValueError):
        await svc.public("response", s["token"], body=answer(), key=uuid4())
    assert (await svc.read(kb.actors["owner"], "statistics", AnalyticsQuery()))["questionnaire_submissions"] == 0


async def test_binary_preview_reports_real_capability(kb):
    svc, f, m, p, s = await setup(kb, media="application/vnd.ms-powerpoint", content=b"synthetic-ppt")
    assert p["files"][0]["preview_supported"] is False
    grant = await svc.public("response", s["token"], body=answer(), key=uuid4())
    async with client(kb, svc) as c:
        headers = {"X-Knowledge-Share": s["token"], "X-Knowledge-Grant": grant["grant_token"]}
        response = await c.get(f"/api/v1/public/knowledge/files/{f['file_id']}/preview", headers=headers)
        assert response.status_code == 415
        downloaded = await c.get(f"/api/v1/public/knowledge/files/{f['file_id']}/download", headers=headers)
        assert downloaded.status_code == 200 and downloaded.headers["content-type"] == "application/octet-stream"


async def test_public_functions_do_not_leak_identity_or_offer_helpers(kb):
    svc, f, m, p, s = await setup(kb)
    async with kb.database.connection() as conn:
        async with conn.transaction():
            assert await conn.fetchval("SELECT count(*) FROM knowledge.distribution_response") == 0
            before = await conn.fetchval("SELECT current_setting('app.workspace_id',true)")
            await conn.fetchval(
                "SELECT security.kd_public_metadata($1,$2)", digest(s["token"]), digest("browser-synthetic-0001")
            )
            after = await conn.fetchval("SELECT current_setting('app.workspace_id',true)")
            assert (before or "") == (after or "")
            assert await conn.fetchval("SELECT count(*) FROM knowledge.material") == 0
    async with kb.database.connection() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("SELECT security.kd_files($1)", UUID(str(p["publication_id"])))


async def test_transaction_hook_failure_rolls_back_publication_and_receipt(kb):
    await kb.configure()
    f = await kb.service.upload(kb.actors["owner"], uuid4(), "safe.txt", "text/plain", b"safe")
    m = await kb.service.save(kb.actors["owner"], uuid4(), selection(files=[f["file_id"]]))

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic outbox failure")

    svc = DistributionService(kb.database, enqueue=fail)
    key = uuid4()
    with pytest.raises(RuntimeError):
        await svc.publish(
            kb.actors["owner"],
            key,
            UUID(str(m["material_id"])),
            PublicationWrite(expected_revision=0, version_id=m["version_id"]),
        )
    assert (
        await kb.admin.fetchval(
            "SELECT count(*) FROM knowledge.distribution_publication WHERE material_id=$1", UUID(str(m["material_id"]))
        )
        == 0
    )
    assert await kb.admin.fetchval("SELECT count(*) FROM ops.mutation_receipt WHERE request_key=$1", key) == 0


async def test_public_rate_limit_and_module_revocation(kb):
    svc, f, m, p, s = await setup(kb)
    await kb.admin.execute(
        """INSERT INTO knowledge.distribution_event(workspace_id,share_id,browser_hash,kind)
      SELECT $1,$2,$3,'visit' FROM generate_series(1,300)""",
        kb.workspace,
        UUID(str(s["share_id"])),
        digest("browser-synthetic-0001"),
    )
    async with client(kb, svc) as c:
        response = await c.get(
            "/api/v1/public/knowledge/share",
            params={"browser_id": "browser-synthetic-0001"},
            headers={"X-Knowledge-Share": s["token"]},
        )
        assert response.status_code == 429
    await kb.admin.execute(
        "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='knowledge'", kb.workspace
    )
    with pytest.raises(asyncpg.NoDataFoundError):
        await svc.public("metadata", s["token"], browser_id="browser-synthetic-0002")


async def test_publication_share_idempotency_optimistic_revision_and_event_kinds(kb):
    svc, f, m, p, s = await setup(kb)
    seen = []

    async def record(connection, **event):
        seen.append(event)

    svc.enqueue = record
    body = PublicationWrite(expected_revision=1, version_id=m["version_id"], description="updated")
    key = uuid4()
    p2 = await svc.publish(kb.actors["owner"], key, UUID(str(m["material_id"])), body)
    replay = await svc.publish(kb.actors["owner"], key, UUID(str(m["material_id"])), body)
    assert str(p2["publication_id"]) == str(replay["publication_id"]) and len(seen) == 1
    assert seen[0]["event_kind"] == "metadata_updated"
    with pytest.raises(asyncpg.SerializationError):
        await svc.publish(kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), body)
    share_key = uuid4()
    link = await svc.share(
        kb.actors["owner"], share_key, UUID(str(m["material_id"])), ShareWrite(publication_id=p2["publication_id"])
    )
    again = await svc.share(
        kb.actors["owner"], share_key, UUID(str(m["material_id"])), ShareWrite(publication_id=p2["publication_id"])
    )
    assert link["token"] == again["token"] and len(link["token"]) >= 43
    assert link["sales_user_name"] is not None
    await svc.revoke(kb.actors["owner"], uuid4(), UUID(str(link["share_id"])), RevokeWrite())
    with pytest.raises(LookupError):
        await svc.share(
            kb.actors["owner"], share_key, UUID(str(m["material_id"])), ShareWrite(publication_id=p2["publication_id"])
        )


async def test_required_form_schema_and_grant_expiry(kb):
    svc, f, m, p, s = await setup(kb)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="QUESTIONNAIRE_REQUIRED"):
        await svc.public(
            "response",
            s["token"],
            body=ResponseWrite(browser_id="browser-synthetic-0001", questionnaire_version=1),
            key=uuid4(),
        )
    with pytest.raises(asyncpg.SerializationError, match="QUESTIONNAIRE_CHANGED"):
        await svc.public("response", s["token"], body=answer(version=2), key=uuid4())
    grant = await svc.public("response", s["token"], body=answer(), key=uuid4())
    await kb.admin.execute(
        "UPDATE knowledge.distribution_response SET expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
        UUID(str(grant["response_id"])),
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await svc.public("download", s["token"], grant=grant["grant_token"], file_id=UUID(str(f["file_id"])))


async def test_safe_cover_and_unusable_preview_only_rejected(kb):
    svc, f, m, p, s = await setup(kb, media="application/pdf", content=b"invalid-pdf-signature")
    with pytest.raises(asyncpg.PostgresError, match="PREVIEW_UNSUPPORTED"):
        await svc.publish(
            kb.actors["owner"],
            uuid4(),
            UUID(str(m["material_id"])),
            PublicationWrite(expected_revision=1, version_id=m["version_id"], allow_download=False),
        )
    cover = await kb.service.upload(
        kb.actors["owner"], uuid4(), "cover.png", "image/png", b"\x89PNG\r\n\x1a\nsynthetic"
    )
    covered = await kb.service.save(
        kb.actors["owner"],
        uuid4(),
        selection(files=[f["file_id"], cover["file_id"]]),
        UUID(str(m["material_id"])),
        m["material_revision"],
    )
    p2 = await svc.publish(
        kb.actors["owner"],
        uuid4(),
        UUID(str(m["material_id"])),
        PublicationWrite(expected_revision=1, version_id=covered["version_id"], cover_file_id=cover["file_id"]),
    )
    link = await svc.share(
        kb.actors["owner"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p2["publication_id"])
    )
    async with client(kb, svc) as c:
        response = await c.get("/api/v1/public/knowledge/cover", headers={"X-Knowledge-Share": link["token"]})
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        body = await c.get(
            f"/api/v1/public/knowledge/files/{f['file_id']}/download", headers={"X-Knowledge-Share": link["token"]}
        )
        assert body.status_code == 403


async def test_scope_self_statistics_and_answer_details_do_not_cross_sales(kb):
    svc, f, m, p, s = await setup(kb)
    await kb.service.save_acl(
        kb.actors["owner"],
        uuid4(),
        UUID(str(m["material_id"])),
        ACLWrite(expected_acl_revision=1, grants=[KnowledgeACLGrant(subject_kind="company", actions={"read"})]),
    )
    other = await svc.share(
        kb.actors["reader"], uuid4(), UUID(str(m["material_id"])), ShareWrite(publication_id=p["publication_id"])
    )
    await svc.public("response", other["token"], body=answer(), key=uuid4())
    assert (await svc.read(kb.actors["owner"], "responses", AnalyticsQuery()))["total"] == 0
    assert (await svc.read(kb.actors["reader"], "responses", AnalyticsQuery()))["total"] == 1
    assert (await svc.read(kb.actors["owner"], "responses", AnalyticsQuery(scope="all")))["total"] == 1


async def test_directory_date_count_and_analytics_version_filters(kb):
    svc, f, m, p, s = await setup(kb)
    async with client(kb, svc) as c:
        prefix = "/api/v1/knowledge/distribution"
        result = await c.get(
            prefix + "/materials", params={"date_from": "2000-01-01T00:00:00Z", "date_to": "2100-01-01T00:00:00Z"}
        )
        assert result.status_code == 200 and result.json()["total"] == 1
        assert result.json()["source"] == "salesbuddy.customer_distribution"
        outside = await c.get(prefix + "/materials", params={"date_to": "2000-01-01T00:00:00Z"})
        assert outside.status_code == 200 and outside.json()["total"] == 0
        invalid = await c.get(
            prefix + "/materials", params={"date_from": "2100-01-01T00:00:00Z", "date_to": "2000-01-01T00:00:00Z"}
        )
        assert invalid.status_code == 422
        assert (await c.get(prefix + "/materials", params={"date_from": "2000-01-01T00:00:00"})).status_code == 422
        shares = await c.get(prefix + "/shares", params={"version_id": m["version_id"]})
        assert shares.status_code == 200 and shares.json()["total"] == 1
    await svc.public("response", s["token"], body=answer(), key=uuid4())
    stats = await svc.read(kb.actors["owner"], "statistics", AnalyticsQuery(version_id=uuid4()))
    assert stats["questionnaire_submissions"] == 0
    responses = await svc.read(kb.actors["owner"], "responses", AnalyticsQuery(version_id=UUID(str(m["version_id"]))))
    assert responses["total"] == 1
