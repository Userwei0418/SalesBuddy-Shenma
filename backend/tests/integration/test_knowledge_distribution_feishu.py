"""Real V270 outbox and worker projection; no remote requests or production data."""

from uuid import UUID, uuid4

import pytest

from sales_backend.api.knowledge import ACLWrite
from sales_backend.contracts.knowledge_distribution import PublicationWrite, ShareWrite
from sales_backend.db import json_value
from sales_backend.domain.knowledge_access import KnowledgeACLGrant
from sales_backend.feishu_worker import verify_worker_role
from sales_backend.services.knowledge_distribution import DistributionService

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection
from .test_knowledge_distribution import answer

pytestmark = pytest.mark.asyncio


async def source(kb, cid, mid):
    async with kb.admin.transaction():
        await kb.admin.execute("SET LOCAL ROLE salegent_feishu_worker")
        await verify_worker_role(kb.admin)
        return json_value(await kb.admin.fetchval("SELECT ops.feishu_source($1,'knowledge',$2)", cid, mid))


async def configure(kb):
    await kb.configure()
    await kb.admin.execute(
        "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='feishu'", kb.workspace
    )
    cid = uuid4()
    settings = dict(
        workspace_id=str(kb.workspace),
        connection_id=str(cid),
        revision=1,
        notification=dict(enabled=True, on_create=["knowledge"], on_update=["knowledge"], default_chat_id="oc_fixture"),
        mappings=dict(
            knowledge=dict(
                enabled=True, table_id="tblFixture", id_field_id="fldSystemId", fields={"record_status": "fldStatus"}
            )
        ),
    )
    await kb.admin.execute(
        """INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,settings,validated_revision,updated_by)
        VALUES($1,$2,1,true,$3::jsonb,1,$4)""",
        cid,
        kb.workspace,
        settings,
        UUID(kb.actors["owner"].user_id),
    )
    f = await kb.service.upload(kb.actors["owner"], uuid4(), "fictional.txt", "text/plain", b"fictional source")
    m = await kb.service.save(kb.actors["owner"], uuid4(), selection(files=[f["file_id"]]))
    mid = UUID(str(m["material_id"]))
    await kb.service.save_acl(
        kb.actors["owner"],
        uuid4(),
        mid,
        ACLWrite(expected_acl_revision=1, grants=(KnowledgeACLGrant(subject_kind="company", actions=("read",)),)),
    )
    return cid, m, mid


async def test_true_hook_transaction_idempotency_silent_metadata_and_revocation(kb):
    cid, m, mid = await configure(kb)
    svc = DistributionService(kb.database)
    body = PublicationWrite(expected_revision=0, version_id=m["version_id"])
    key = uuid4()
    p = await svc.publish(kb.actors["owner"], key, mid, body)
    again = await svc.publish(kb.actors["owner"], key, mid, body)
    assert p == again
    rows = await kb.admin.fetch("SELECT * FROM ops.feishu_event WHERE connection_id=$1", cid)
    assert len(rows) == 1 and rows[0]["first_formal_create"] and not rows[0]["historical"]
    snapshot = json_value(rows[0]["notification_snapshot"])
    assert snapshot["publication_id"] == str(p["publication_id"])
    assert not any(k in snapshot for k in ["questionnaire", "answers", "source_url", "token"])
    raw = await source(kb, cid, mid)
    assert raw["publication_id"] == str(p["publication_id"]) and raw["content_format"] == "文本文档"
    p2 = await svc.publish(
        kb.actors["owner"],
        uuid4(),
        mid,
        PublicationWrite(expected_revision=1, version_id=m["version_id"], description="updated metadata"),
    )
    event = await kb.admin.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", p2["publication_id"])
    assert event["historical"] and event["notification_snapshot"] is None
    await kb.service.save_acl(kb.actors["owner"], uuid4(), mid, ACLWrite(expected_acl_revision=2))
    assert (await source(kb, cid, mid)) == {"id": str(mid), "excluded": True}
    # The pending metadata event is reused for the same material's ACL repair;
    # the worker reads the current projection, so revocation must not duplicate it.
    pending = await kb.admin.fetch(
        "SELECT notification_snapshot FROM ops.feishu_event WHERE connection_id=$1 AND historical AND status='pending'",
        cid,
    )
    assert len(pending) == 1 and pending[0]["notification_snapshot"] is None
    # Reconcile remains executable by the existing worker and never adds a notice.
    async with kb.admin.transaction():
        await kb.admin.execute("SET LOCAL ROLE salegent_feishu_worker")
        await kb.admin.fetchval("SELECT ops.feishu_reconcile($1)", cid)
    assert (
        await kb.admin.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND NOT historical", cid)
        == 1
    )


async def test_silent_publication_shares_existing_files_and_syncs_without_notice(kb):
    cid, material, mid = await configure(kb)
    svc = DistributionService(kb.database)
    owner = kb.actors["owner"]
    key = uuid4()
    body = PublicationWrite(expected_revision=0, version_id=material["version_id"], notify=False)
    publication = await svc.publish(owner, key, mid, body)
    assert await svc.publish(owner, key, mid, body) == publication
    assert publication["enabled"] and publication["can_share"] and publication["files"]
    assert str(publication["version_id"]) == material["version_id"]
    rows = await kb.admin.fetch("SELECT * FROM ops.feishu_event WHERE connection_id=$1", cid)
    assert len(rows) == 1
    assert rows[0]["historical"] and not rows[0]["first_formal_create"]
    assert rows[0]["notification_snapshot"] is None
    assert rows[0]["object_id"] == mid and rows[0]["status"] == "pending"
    assert (await source(kb, cid, mid))["publication_id"] == str(publication["publication_id"])
    # The client choice cannot be lost if the enqueue hook is retried directly.
    async with kb.database.transaction(owner) as connection:
        assert await connection.fetchval(
            "SELECT security.knowledge_distribution_enqueue($1,$2,$3,$4,'published',1,$5)",
            kb.workspace, mid, publication["publication_id"], UUID(material["version_id"]), UUID(owner.user_id),
        ) == 0
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.material WHERE workspace_id=$1", kb.workspace) == 1
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.version WHERE workspace_id=$1", kb.workspace) == 1
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.file WHERE workspace_id=$1", kb.workspace) == 1
    # A later, explicitly normal new-version release still sends its own notice.
    newer = await kb.service.save(
        owner, uuid4(), selection(files=[publication["files"][0]["file_id"]], name="updated source"), mid, 1
    )
    normal = await svc.publish(
        owner, uuid4(), mid, PublicationWrite(expected_revision=1, version_id=newer["version_id"])
    )
    event = await kb.admin.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", normal["publication_id"])
    assert not event["historical"] and event["notification_snapshot"] is not None


async def test_unpublished_private_and_other_company_never_export_content(kb):
    cid, m, mid = await configure(kb)
    assert await source(kb, cid, mid) == {"id": str(mid), "excluded": True}
    # Publication is allowed by the owner, but a private ACL cannot enter a group directory.
    await kb.service.save_acl(kb.actors["owner"], uuid4(), mid, ACLWrite(expected_acl_revision=2))
    svc = DistributionService(kb.database)
    await svc.publish(
        kb.actors["owner"], uuid4(), mid, PublicationWrite(expected_revision=0, version_id=m["version_id"])
    )
    assert await source(kb, cid, mid) == {"id": str(mid), "excluded": True}
    row = await kb.admin.fetchrow(
        "SELECT historical,notification_snapshot FROM ops.feishu_event WHERE connection_id=$1", cid
    )
    assert row["historical"] and row["notification_snapshot"] is None
    unrelated = uuid4()
    assert await source(kb, cid, unrelated) == {"id": str(unrelated), "excluded": True}


async def test_draft_does_not_change_published_directory_title(kb):
    cid, m, mid = await configure(kb)
    svc = DistributionService(kb.database)
    await svc.publish(
        kb.actors["owner"], uuid4(), mid, PublicationWrite(expected_revision=0, version_id=m["version_id"])
    )
    before = await source(kb, cid, mid)
    await kb.service.save(kb.actors["owner"], uuid4(), selection(name="UNPUBLISHED DRAFT"), mid, 1)
    after = await source(kb, cid, mid)
    assert after["content_name"] == before["content_name"]
    assert after["version_id"] == before["version_id"] and after["updated_at"] == before["updated_at"]


@pytest.mark.parametrize("withdraw", [False, True])
async def test_customer_statistics_batch_coalesces_without_notice_and_revoke_is_immediate(kb, withdraw):
    cid, m, mid = await configure(kb)
    svc = DistributionService(kb.database)
    p = await svc.publish(
        kb.actors["owner"], uuid4(), mid, PublicationWrite(expected_revision=0, version_id=m["version_id"])
    )
    s = await svc.share(kb.actors["owner"], uuid4(), mid, ShareWrite(publication_id=p["publication_id"]))
    await kb.admin.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
    for _ in range(3):
        await svc.public("metadata", s["token"], browser_id="browser-synthetic-0001")
    grant = await svc.public("response", s["token"], body=answer(), key=uuid4())
    files = await kb.admin.fetch("SELECT file_id FROM knowledge.version_file WHERE version_id=$1", m["version_id"])
    await svc.public("download", s["token"], grant=grant["grant_token"], file_id=files[0]["file_id"])
    stats = await source(kb, cid, mid)
    assert (
        stats["visits"],
        stats["unique_visitors_estimate"],
        stats["questionnaire_submissions"],
        stats["download_requests"],
    ) == (3, 1, 1, 1)
    pending = await kb.admin.fetch(
        "SELECT historical,notification_snapshot,available_at>clock_timestamp() delayed "
        "FROM ops.feishu_event WHERE connection_id=$1 AND status='pending'",
        cid,
    )
    assert (
        len(pending) == 1
        and pending[0]["historical"]
        and pending[0]["notification_snapshot"] is None
        and pending[0]["delayed"]
    )
    if withdraw:
        await svc.publish(
            kb.actors["owner"],
            uuid4(),
            mid,
            PublicationWrite(expected_revision=1, version_id=m["version_id"], enabled=False),
        )
    else:
        await kb.service.save_acl(kb.actors["owner"], uuid4(), mid, ACLWrite(expected_acl_revision=2))
    assert not await kb.admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=$1 "
        "AND status='pending' AND available_at>clock_timestamp())",
        cid,
    )
    assert (await source(kb, cid, mid))["excluded"]
