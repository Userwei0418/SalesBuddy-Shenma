"""Real publication/audit/source/worker queue; only Feishu transport is replaced."""

import json
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.api.knowledge import ACLWrite
from sales_backend.contracts.knowledge_distribution import PublicationWrite
from sales_backend.db import json_value
from sales_backend.domain.feishu_sync.cards import knowledge_publication_card
from sales_backend.feishu_worker import verify_worker_role
from sales_backend.integrations.feishu import FeishuError
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.services.feishu_sync import knowledge_notification_snapshot
from sales_backend.services.knowledge_distribution import DistributionService
from tests.test_feishu_sync_service import setup as worker_setup
from tests.test_knowledge_distribution_feishu import kb_config

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection
from .test_knowledge_distribution_feishu import configure, source

pytestmark = pytest.mark.asyncio


async def publish(kb, mid, version_id, revision, **values):
    return await DistributionService(kb.database).publish(
        kb.actors["owner"],
        uuid4(),
        mid,
        PublicationWrite(expected_revision=revision, version_id=version_id, **values),
    )


async def new_version(kb, mid, expected_version):
    file = await kb.service.upload(kb.actors["owner"], uuid4(), "next.txt", "text/plain", b"next archived version")
    return await kb.service.save(
        kb.actors["owner"],
        uuid4(),
        selection(files=[file["file_id"]]),
        mid,
        expected_version,
    )


async def formal(kb, version_update=False):
    cid, material, mid = await configure(kb)
    publication = await publish(kb, mid, material["version_id"], 0, description="原始正式发布说明")
    if version_update:
        material = await new_version(kb, mid, 1)
        publication = await publish(kb, mid, material["version_id"], 1, description="新增两个归档案例")
        # The previous version has already been handled in this synthetic history.
        await kb.admin.execute(
            "UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1 AND id<>$2",
            cid,
            publication["publication_id"],
        )
    event = dict(await kb.admin.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", publication["publication_id"]))
    event["notification_snapshot"] = json_value(event["notification_snapshot"])
    return cid, material, mid, publication, event


@pytest.mark.parametrize("version_update", [False, True])
async def test_formal_intent_and_original_summary_survive_multiple_metadata_revisions(kb, version_update):
    cid, material, mid, original, event = await formal(kb, version_update)
    for index in range(3):
        later = await publish(
            kb, mid, material["version_id"], original["revision"] + index, description=f"元数据说明 {index}"
        )
        raw = await source(kb, cid, mid)
        assert raw["publication_id"] == str(later["publication_id"])
        assert raw["notification_publication_id"] == str(original["publication_id"])
        assert raw["notification_publication_revision"] == original["revision"]
        assert raw["description"] == f"元数据说明 {index}"
        assert knowledge_notification_snapshot(event, raw) == event["notification_snapshot"]
        assert event["notification_snapshot"]["description"] in {"原始正式发布说明", "新增两个归档案例"}
        record = await kb.admin.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", later["publication_id"])
        assert record["historical"] and record["notification_snapshot"] is None
    assert not await kb.admin.fetchval(
        "SELECT enabled FROM knowledge.distribution_publication WHERE id=$1",
        original["publication_id"],
    )  # Superseded enabled=false is not an actual withdrawal audit.


async def test_later_metadata_summary_does_not_backfill_empty_formal_summary(kb):
    cid, material, mid = await configure(kb)
    original = await publish(kb, mid, material["version_id"], 0)
    event = dict(await kb.admin.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", original["publication_id"]))
    event["notification_snapshot"] = json_value(event["notification_snapshot"])
    await publish(kb, mid, material["version_id"], 1, description="后来补填的发布说明")
    snapshot = knowledge_notification_snapshot(event, await source(kb, cid, mid))
    assert snapshot == event["notification_snapshot"]
    assert not snapshot.get("description")
    encoded = json.dumps(knowledge_publication_card(snapshot, kb_config(), "record-1"), ensure_ascii=False)
    assert "更新摘要" not in encoded and "后来补填的发布说明" not in encoded


@pytest.mark.parametrize(
    "boundary", ["acl", "withdraw", "republish", "new_version", "version_roundtrip", "missing_audit"]
)
async def test_metadata_never_bypasses_revocation_or_revives_an_old_publication(kb, boundary):
    cid, material, mid, publication, event = await formal(kb)
    await publish(kb, mid, material["version_id"], 1, description="元数据")
    if boundary == "acl":
        await kb.service.save_acl(kb.actors["owner"], uuid4(), mid, ACLWrite(expected_acl_revision=2))
    elif boundary in {"withdraw", "republish"}:
        await publish(kb, mid, material["version_id"], 2, enabled=False)
        if boundary == "republish":
            renewed = await publish(kb, mid, material["version_id"], 3, description="重新上架")
            assert (await source(kb, cid, mid))["notification_publication_id"] == str(renewed["publication_id"])
    elif boundary in {"new_version", "version_roundtrip"}:
        next_material = await new_version(kb, mid, 1)
        await publish(kb, mid, next_material["version_id"], 2)
        if boundary == "version_roundtrip":
            await publish(kb, mid, material["version_id"], 3)
    else:
        # Simulated historical corruption is never interpreted as authority.
        await kb.admin.execute(
            "DELETE FROM knowledge.audit WHERE workspace_id=$1 AND material_id=$2 AND detail->>'publication_id'=$3",
            kb.workspace,
            mid,
            str(publication["publication_id"]),
        )
    assert knowledge_notification_snapshot(event, await source(kb, cid, mid)) is None


@pytest.mark.parametrize("boundary", ["suspended", "module_disabled"])
async def test_inactive_company_or_feishu_module_stops_source_access(kb, boundary):
    cid, material, mid, _, _ = await formal(kb)
    await publish(kb, mid, material["version_id"], 1)
    if boundary == "suspended":
        await kb.admin.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", kb.workspace)
    else:
        await kb.admin.execute(
            "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='feishu'",
            kb.workspace,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await source(kb, cid, mid)


@pytest.mark.parametrize("version_update", [False, True])
@pytest.mark.parametrize("outcome", ["sent", "unknown"])
async def test_installed_worker_claim_freezes_original_card_and_never_duplicates(kb, version_update, outcome):
    cid, material, mid, original, _ = await formal(kb, version_update)
    await publish(kb, mid, material["version_id"], original["revision"], description="后续问卷和说明调整")
    # Isolate the queue from synthetic connections retained by earlier fixtures.
    await kb.admin.execute("UPDATE config.feishu_connection SET enabled=false WHERE id<>$1", cid)
    service, _, client = worker_setup()
    service.repository = FeishuRepository()
    config = kb_config().model_copy(update={"workspace_id": kb.workspace, "connection_id": cid})
    service.client = lambda row: (config, client)
    if outcome == "unknown":
        client.send_card.side_effect = FeishuError("SYNTHETIC_UNKNOWN", unknown=True)
    await kb.admin.execute("SET ROLE kb_feishu")
    try:
        await verify_worker_role(kb.admin)
        event = dict(await service.repository.claim(kb.admin))
        assert str(event["id"]) == str(original["publication_id"])
        if outcome == "unknown":
            with pytest.raises(FeishuError, match="SYNTHETIC_UNKNOWN"):
                await service.handle(kb.admin, event, {})
            with pytest.raises(FeishuError, match="MESSAGE_RESULT_UNKNOWN"):
                await service.handle(kb.admin, event, {})
        else:
            await service.handle(kb.admin, event, {})
            await service.handle(kb.admin, event, {})
            await service.repository.finish(kb.admin, event)
            # The later metadata event updates the row and stays silent.
            following = dict(await service.repository.claim(kb.admin))
            assert following["historical"] and following["notification_snapshot"] is None
            await service.handle(kb.admin, following, {})
        client.send_card.assert_awaited_once()
        encoded = json.dumps(client.send_card.await_args.args[1], ensure_ascii=False)
        assert "更新摘要" in encoded and "后续问卷和说明调整" not in encoded
        rows = await service.repository.deliveries(kb.admin, original["publication_id"])
        assert len(rows) == 1 and rows[0]["status"] == outcome
    finally:
        await kb.admin.execute("RESET ROLE")
