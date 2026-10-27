"""Synthetic PDF and exact upload boundaries on the installed API role."""

import hashlib
from io import BytesIO
from uuid import UUID, uuid4

import asyncpg
import pytest
from pypdf import PdfWriter

from sales_backend.contracts.knowledge_distribution import AnalyticsQuery, PublicationWrite, RevokeWrite, ShareWrite
from sales_backend.services.knowledge import MAX_UPLOAD_BYTES

from .knowledge_fixtures import kb as kb
from .test_knowledge_distribution import answer, client, setup

pytestmark = pytest.mark.asyncio


def synthetic_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def protected_pdf(response, content):
    assert response.status_code == 200, response.text[:200]
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert response.headers["referrer-policy"] == "no-referrer"


def protected_image(response, content, media_type):
    assert response.status_code == 200, response.text[:200]
    assert response.content == content
    assert response.headers["content-type"] == media_type
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_exact_50mib_multipart_archive_and_all_size_enforcement_layers(kb):
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024
    content = synthetic_pdf().ljust(MAX_UPLOAD_BYTES, b" ")
    async with kb.client() as c:
        key = str(uuid4())
        response = await c.post(
            "/api/v1/knowledge/files",
            headers={"Idempotency-Key": key},
            files={"file": ("boundary.pdf", content, "application/pdf")},
        )
        assert response.status_code == 201, response.text
        archived = response.json()
        assert archived["size_bytes"] == MAX_UPLOAD_BYTES
        assert archived["sha256"] == hashlib.sha256(content).hexdigest()
        assert (
            await c.post(
                "/api/v1/knowledge/files",
                headers={"Idempotency-Key": key},
                files={"file": ("boundary.pdf", content, "application/pdf")},
            )
        ).json() == archived
        oversized = content + b"!"
        rejected = await c.post(
            "/api/v1/knowledge/files",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": ("over.pdf", oversized, "application/pdf")},
        )
        assert rejected.status_code == 413 and rejected.json()["detail"] == "KNOWLEDGE_FILE_TOO_LARGE"
        empty = await c.post(
            "/api/v1/knowledge/files",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        assert empty.status_code == 422 and empty.json()["detail"] == "KNOWLEDGE_FILE_SIZE_INVALID"
    with pytest.raises(ValueError, match="KNOWLEDGE_FILE_TOO_LARGE"):
        await kb.service.upload(kb.actors["owner"], uuid4(), "over.pdf", "application/pdf", oversized)
    for data, code in [(oversized, "P0413"), (b"", "22023")]:
        with pytest.raises(asyncpg.PostgresError) as error:
            async with kb.database.transaction(kb.actors["owner"]) as connection:
                await connection.fetchval(
                    "SELECT security.knowledge_store_file($1,$2,$3,$4)", uuid4(), "invalid.pdf", "application/pdf", data
                )
        assert error.value.sqlstate == code
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.file WHERE workspace_id=$1", kb.workspace) == 1


async def test_signed_image_internal_and_questionnaire_preview(kb):
    content = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360f8cf0000010400010d0a2db40000000049454e44ae426082"
    )
    svc, f, m, p, _ = await setup(kb, media="image/png", content=content, share=False)
    assert p["files"][0]["preview_supported"] is True
    mid = UUID(str(m["material_id"]))
    publication = await svc.publish(
        kb.actors["owner"], uuid4(), mid,
        PublicationWrite(expected_revision=1, version_id=m["version_id"], allow_download=False),
    )
    share = await svc.share(kb.actors["owner"], uuid4(), mid, ShareWrite(publication_id=publication["publication_id"]))
    async with kb.client() as c:
        protected_image(
            await c.get(f"/api/v1/knowledge/files/{f['file_id']}/preview", params={"version_id": m["version_id"]}),
            content, "image/png",
        )
    headers = {"X-Knowledge-Share": share["token"]}
    async with client(kb, svc) as c:
        granted = await c.post(
            "/api/v1/public/knowledge/responses", json=answer(version=2).model_dump(mode="json"),
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert granted.status_code == 200, granted.text
        headers["X-Knowledge-Grant"] = granted.json()["grant_token"]
        metadata = await c.get(
            "/api/v1/public/knowledge/share", headers={"X-Knowledge-Share": share["token"]},
            params={"browser_id": "synthetic-image-browser"},
        )
        assert metadata.status_code == 200 and metadata.json()["files"][0]["preview_supported"] is True
        protected_image(
            await c.get(f"/api/v1/public/knowledge/files/{f['file_id']}/preview", headers=headers), content, "image/png"
        )


async def test_pdf_internal_and_questionnaire_preview_only_share(kb):
    content = synthetic_pdf()
    svc, f, m, p, _ = await setup(kb, media="application/pdf", content=content, share=False)
    assert p["files"][0]["preview_supported"] is True
    mid = UUID(str(m["material_id"]))
    publication = await svc.publish(
        kb.actors["owner"],
        uuid4(),
        mid,
        PublicationWrite(expected_revision=1, version_id=m["version_id"], allow_download=False),
    )
    share = await svc.share(kb.actors["owner"], uuid4(), mid, ShareWrite(publication_id=publication["publication_id"]))
    async with kb.client() as c:
        protected_pdf(
            await c.get(f"/api/v1/knowledge/files/{f['file_id']}/preview", params={"version_id": m["version_id"]}),
            content,
        )
    async with kb.client("other") as c:
        assert (
            await c.get(f"/api/v1/knowledge/files/{f['file_id']}/preview", params={"version_id": m["version_id"]})
        ).status_code in {403, 404}
    headers = {"X-Knowledge-Share": share["token"]}
    path = f"/api/v1/public/knowledge/files/{f['file_id']}"
    async with client(kb, svc) as c:
        metadata = await c.get(
            "/api/v1/public/knowledge/share", headers=headers, params={"browser_id": "synthetic-pdf-browser"}
        )
        assert metadata.status_code == 200
        assert metadata.json()["files"][0]["preview_supported"] is True
        assert metadata.json()["allow_preview"] is True and metadata.json()["allow_download"] is False
        assert (await c.get(path + "/preview", headers=headers)).status_code == 403
        granted = await c.post(
            "/api/v1/public/knowledge/responses",
            json=answer(version=2).model_dump(mode="json"),
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert granted.status_code == 200, granted.text
        headers["X-Knowledge-Grant"] = granted.json()["grant_token"]
        for _ in range(2):
            protected_pdf(await c.get(path + "/preview", headers=headers), content)
        denied = await c.get(path + "/download", headers=headers)
        assert denied.status_code == 403 and denied.json()["detail"] == "KNOWLEDGE_DISTRIBUTION_DENIED"
    stats = await svc.read(kb.actors["owner"], "statistics", AnalyticsQuery())
    assert (stats["previews"], stats["download_requests"], stats["questionnaire_submissions"]) == (2, 0, 1)


@pytest.mark.parametrize(
    "media,content",
    [
        ("application/pdf", b"<html>not a PDF</html>"),
        ("application/pdf", b" %PDF-1.7"),
        ("application/octet-stream", b"%PDF-1.7"),
        ("video/mp4", b"synthetic-video"),
    ],
)
async def test_mime_and_signature_both_required_for_preview_and_capability(kb, media, content):
    svc, f, m, p, share = await setup(kb, media=media, content=content)
    assert p["files"][0]["preview_supported"] is False
    grant = await svc.public("response", share["token"], body=answer(), key=uuid4())
    async with client(kb, svc) as c:
        headers = {"X-Knowledge-Share": share["token"], "X-Knowledge-Grant": grant["grant_token"]}
        metadata = await c.get(
            "/api/v1/public/knowledge/share", headers=headers, params={"browser_id": "synthetic-pdf-browser"}
        )
        assert metadata.json()["files"][0]["preview_supported"] is False
        response = await c.get(f"/api/v1/public/knowledge/files/{f['file_id']}/preview", headers=headers)
        assert response.status_code == 415 and response.json()["detail"] == "KNOWLEDGE_PREVIEW_UNSUPPORTED"
        assert (
            await c.get(f"/api/v1/public/knowledge/files/{f['file_id']}/download", headers=headers)
        ).content == content
        rejected = await c.put(
            f"/api/v1/knowledge/distribution/materials/{m['material_id']}/publication",
            json=PublicationWrite(expected_revision=1, version_id=m["version_id"], allow_download=False).model_dump(
                mode="json"
            ),
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert rejected.status_code == 415, rejected.text
    async with kb.client() as c:
        response = await c.get(
            f"/api/v1/knowledge/files/{f['file_id']}/preview", params={"version_id": m["version_id"]}
        )
        assert response.status_code == 415
    assert (await svc.read(kb.actors["owner"], "statistics", AnalyticsQuery()))["previews"] == 0


@pytest.mark.parametrize(
    "boundary", ["revoke", "withdraw", "replace", "acl", "foreign_file", "foreign_share", "expired"]
)
async def test_pdf_grant_never_bypasses_current_share_version_or_acl(kb, boundary):
    svc, f, m, p, share = await setup(kb, media="application/pdf", content=synthetic_pdf())
    mid = UUID(str(m["material_id"]))
    grant = await svc.public("response", share["token"], body=answer(), key=uuid4())
    target_file = f["file_id"]
    expected = 404
    if boundary == "revoke":
        await svc.revoke(kb.actors["owner"], uuid4(), UUID(str(share["share_id"])), RevokeWrite())
    elif boundary in {"withdraw", "replace"}:
        await svc.publish(
            kb.actors["owner"],
            uuid4(),
            mid,
            PublicationWrite(expected_revision=1, version_id=m["version_id"], enabled=boundary != "withdraw"),
        )
    elif boundary == "acl":
        await kb.grant_scope("owner", None, effect="deny")
    elif boundary == "foreign_file":
        extra = await kb.service.upload(kb.actors["owner"], uuid4(), "other.pdf", "application/pdf", synthetic_pdf())
        target_file = extra["file_id"]
    elif boundary == "foreign_share":
        share = await svc.share(kb.actors["owner"], uuid4(), mid, ShareWrite(publication_id=p["publication_id"]))
        expected = 403
    else:
        await kb.admin.execute(
            "UPDATE knowledge.distribution_response SET expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
            UUID(str(grant["response_id"])),
        )
        expected = 403
    async with client(kb, svc) as c:
        response = await c.get(
            f"/api/v1/public/knowledge/files/{target_file}/preview",
            headers={"X-Knowledge-Share": share["token"], "X-Knowledge-Grant": grant["grant_token"]},
        )
        assert response.status_code == expected, response.text
