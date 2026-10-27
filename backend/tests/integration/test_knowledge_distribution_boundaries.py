"""Distribution boundaries exercised through HTTP and a non-owner RLS connection."""

from uuid import UUID, uuid4

import pytest

from sales_backend.api.knowledge import ACLWrite
from sales_backend.contracts.knowledge_distribution import AnalyticsQuery, PublicationWrite, ShareWrite
from sales_backend.domain.knowledge_access import KnowledgeACLGrant
from sales_backend.services.knowledge_distribution import DistributionService

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection
from .test_knowledge_distribution import answer, client, no_sync, setup

pytestmark = pytest.mark.asyncio
PNG = b"\x89PNG\r\n\x1a\nsynthetic-image"


async def company_read(kb, material_id, revision=1):
    await kb.service.save_acl(
        kb.actors["owner"],
        uuid4(),
        UUID(str(material_id)),
        ACLWrite(expected_acl_revision=revision, grants=[KnowledgeACLGrant(subject_kind="company", actions={"read"})]),
    )


async def direct_counts(kb, actor, share_id):
    async with kb.database.transaction(actor) as connection:
        role = await connection.fetchrow(
            "SELECT current_user AS role,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert role["role"] == "kb_runtime"
        assert not role["rolsuper"] and not role["rolbypassrls"]
        assert await connection.fetchval(
            "SELECT has_table_privilege(current_user,'knowledge.distribution_response','SELECT')"
        )
        shares = await connection.fetchval(
            "SELECT count(*) FROM knowledge.distribution_share WHERE id=$1", UUID(str(share_id))
        )
        responses = await connection.fetch(
            "SELECT answers,receipt FROM knowledge.distribution_response WHERE share_id=$1", UUID(str(share_id))
        )
        events = await connection.fetchval(
            "SELECT count(*) FROM knowledge.distribution_event WHERE share_id=$1", UUID(str(share_id))
        )
        return shares, len(responses), events


@pytest.mark.parametrize("source", ["internal", "other_public", "unattached", "older_version", "other_tenant"])
async def test_publication_rejects_cover_outside_exact_version(kb, source):
    await kb.configure()
    owner = kb.actors["owner"]
    reader = kb.actors["reader"]
    image = await kb.service.upload(
        kb.actors["other"] if source == "other_tenant" else owner,
        uuid4(),
        "source.png",
        "image/png",
        PNG,
    )
    if source in {"internal", "other_public"}:
        original = await kb.service.save(
            owner,
            uuid4(),
            selection(
                files=[image["file_id"]],
                name="Foreign cover source",
                content_scope="internal" if source == "internal" else "public",
            ),
        )
        await company_read(kb, original["material_id"])
    else:
        # Use the uploader for unattached/older files so rejection is due to the
        # exact-version boundary, not merely an existing file-read denial.
        reader = owner
    document = await kb.service.upload(reader, uuid4(), "target.txt", "text/plain", b"public target")
    target = await kb.service.save(
        reader,
        uuid4(),
        selection(
            files=[document["file_id"], image["file_id"]] if source == "older_version" else [document["file_id"]]
        ),
    )
    if source == "older_version":
        target = await kb.service.save(
            reader,
            uuid4(),
            selection(files=[document["file_id"]]),
            UUID(str(target["material_id"])),
            target["material_revision"],
        )
    svc = DistributionService(kb.database, enqueue=no_sync)
    name = "owner" if reader is owner else "reader"
    async with client(kb, svc, name) as c:
        response = await c.put(
            f"/api/v1/knowledge/distribution/materials/{target['material_id']}/publication",
            json=PublicationWrite(
                expected_revision=0, version_id=target["version_id"], cover_file_id=image["file_id"]
            ).model_dump(mode="json"),
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 422, response.text
        assert "KNOWLEDGE_COVER_UNAVAILABLE" in response.text
    async with kb.database.transaction(reader) as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM knowledge.distribution_publication WHERE material_id=$1",
                UUID(str(target["material_id"])),
            )
            == 0
        )


@pytest.mark.parametrize("source_scope", ["internal", "public"])
async def test_historical_foreign_cover_never_returns_bytes(kb, source_scope):
    svc, document, target, publication, _ = await setup(kb)
    image = await kb.service.upload(kb.actors["owner"], uuid4(), "private.png", "image/png", PNG)
    source = await kb.service.save(
        kb.actors["owner"],
        uuid4(),
        selection(files=[image["file_id"]], name="Historical cover source", content_scope=source_scope),
    )
    await company_read(kb, source["material_id"])
    await company_read(kb, target["material_id"])
    # Fixture-only insertion of a historical V269-valid, V271-invalid binding.
    # This models already persisted rows; runtime has no publication UPDATE grant.
    await kb.admin.execute(
        "UPDATE knowledge.distribution_publication SET cover_file_id=$1 WHERE id=$2",
        UUID(str(image["file_id"])),
        UUID(str(publication["publication_id"])),
    )
    link = await svc.share(
        kb.actors["reader"],
        uuid4(),
        UUID(str(target["material_id"])),
        ShareWrite(publication_id=publication["publication_id"]),
    )
    async with client(kb, svc, "reader") as c:
        headers = {"X-Knowledge-Share": link["token"]}
        assert (await c.get("/api/v1/public/knowledge/cover", headers=headers)).status_code == 404
        await kb.service.save_acl(
            kb.actors["owner"], uuid4(), UUID(str(source["material_id"])), ACLWrite(expected_acl_revision=2)
        )
        async with kb.database.transaction(kb.actors["reader"]) as connection:
            assert not await connection.fetchval("SELECT security.knowledge_file_read($1)", UUID(str(image["file_id"])))
        assert (await c.get("/api/v1/public/knowledge/cover", headers=headers)).status_code == 404
        # Reject only the invalid cover, not the legitimate published body.
        grant = await svc.public("response", link["token"], body=answer(), key=uuid4())
        body = await c.get(
            f"/api/v1/public/knowledge/files/{document['file_id']}/download",
            headers={**headers, "X-Knowledge-Grant": grant["grant_token"]},
        )
        assert body.status_code == 200 and body.content == b"Archived document"


async def test_valid_version_cover_stops_after_sharing_sales_acl_revocation(kb):
    svc, image, material, _, _ = await setup(kb, media="image/png", content=PNG)
    publication = await svc.publish(
        kb.actors["owner"],
        uuid4(),
        UUID(str(material["material_id"])),
        PublicationWrite(expected_revision=1, version_id=material["version_id"], cover_file_id=image["file_id"]),
    )
    await company_read(kb, material["material_id"])
    link = await svc.share(
        kb.actors["reader"],
        uuid4(),
        UUID(str(material["material_id"])),
        ShareWrite(publication_id=publication["publication_id"]),
    )
    async with client(kb, svc, "reader") as c:
        headers = {"X-Knowledge-Share": link["token"]}
        response = await c.get("/api/v1/public/knowledge/cover", headers=headers)
        assert response.status_code == 200 and response.content == PNG
        await kb.service.save_acl(
            kb.actors["owner"], uuid4(), UUID(str(material["material_id"])), ACLWrite(expected_acl_revision=2)
        )
        assert (await c.get("/api/v1/public/knowledge/cover", headers=headers)).status_code == 404


async def test_withdrawn_history_readable_until_source_acl_revoked_in_http_and_rls(kb):
    svc, _, material, publication, _ = await setup(kb)
    await company_read(kb, material["material_id"])
    link = await svc.share(
        kb.actors["reader"],
        uuid4(),
        UUID(str(material["material_id"])),
        ShareWrite(publication_id=publication["publication_id"]),
    )
    await svc.public("metadata", link["token"], browser_id="synthetic-boundary-browser")
    await svc.public("response", link["token"], body=answer(), key=uuid4())
    assert await direct_counts(kb, kb.actors["reader"], link["share_id"]) == (1, 1, 1)
    assert await direct_counts(kb, kb.actors["other"], link["share_id"]) == (0, 0, 0)
    await svc.publish(
        kb.actors["owner"],
        uuid4(),
        UUID(str(material["material_id"])),
        PublicationWrite(expected_revision=1, version_id=material["version_id"], enabled=False),
    )
    assert await direct_counts(kb, kb.actors["reader"], link["share_id"]) == (1, 1, 1)
    assert (await svc.read(kb.actors["reader"], "responses", AnalyticsQuery()))["total"] == 1
    assert (await svc.read(kb.actors["reader"], "statistics", AnalyticsQuery()))["questionnaire_submissions"] == 1
    await kb.service.save_acl(
        kb.actors["owner"], uuid4(), UUID(str(material["material_id"])), ACLWrite(expected_acl_revision=2)
    )
    assert await direct_counts(kb, kb.actors["reader"], link["share_id"]) == (0, 0, 0)
    async with client(kb, svc, "reader") as c:
        responses = await c.get("/api/v1/knowledge/distribution/responses")
        assert responses.status_code == 200 and responses.json()["total"] == 0
    # The source owner retains authorized history, including the other seller's answers.
    assert await direct_counts(kb, kb.actors["owner"], link["share_id"]) == (1, 1, 1)
    assert (await svc.read(kb.actors["owner"], "responses", AnalyticsQuery(scope="all")))["total"] == 1


async def test_catalog_manager_cannot_read_private_material_answers_directly(kb):
    svc, _, _, _, link = await setup(kb)
    await svc.public("metadata", link["token"], browser_id="synthetic-manager-browser")
    await svc.public("response", link["token"], body=answer(), key=uuid4())
    async with kb.database.transaction(kb.actors["reader"]) as connection:
        assert await connection.fetchval("SELECT security.kd_admin()")
    assert await direct_counts(kb, kb.actors["reader"], link["share_id"]) == (0, 0, 0)
    assert (await svc.read(kb.actors["reader"], "responses", AnalyticsQuery(scope="all")))["total"] == 0
