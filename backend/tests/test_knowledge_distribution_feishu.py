import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.domain.feishu_sync.cards import knowledge_publication_card
from sales_backend.domain.feishu_sync.config import ObjectKind, SyncConfig
from sales_backend.domain.feishu_sync.planning import SourceEvent, notification_plans
from sales_backend.integrations.feishu import FeishuError
from sales_backend.services.feishu_sync import knowledge_notification_snapshot
from sales_backend.services.knowledge_distribution_sync import enqueue_knowledge_distribution_event
from tests.test_feishu_sync_policy import payload
from tests.test_feishu_sync_service import Connection, setup


def kb_config(notify=True):
    value = payload()
    value["mappings"]["knowledge"] = {
        "enabled": True,
        "table_id": "tblKnowledge",
        "id_field_id": "fldSystemId",
        "fields": {"content_name": "fldName", "record_status": "fldStatus"},
    }
    if notify:
        value["notification"].update(on_create=["knowledge"], on_update=["knowledge"])
    return SyncConfig.model_validate(value)


def sample():
    config = kb_config()
    event = dict(
        id=UUID(int=11),
        workspace_id=config.workspace_id,
        object_id=UUID(int=12),
        object_kind="knowledge",
        first_formal_create=True,
        historical=False,
        notification_planned=False,
        connection_id=config.connection_id,
        lease_token=UUID(int=17),
    )
    snapshot = dict(
        schema_version=1,
        change_id=str(event["id"]),
        publication_id=str(event["id"]),
        workspace_id=str(config.workspace_id),
        material_id=str(event["object_id"]),
        version_id=str(UUID(int=13)),
        publication_revision=1,
        version_no=1,
        event_kind="published",
        content_name="<at id=all>资料</at>",
    )
    event["notification_snapshot"] = snapshot
    raw = {
        **snapshot,
        "id": str(event["object_id"]),
        "notification_publication_id": snapshot["publication_id"],
        "notification_publication_revision": snapshot["publication_revision"],
    }
    return config, event, raw


def test_notifications_require_explicit_opt_in_and_distinguish_new_versions():
    cfg, event, raw = sample()
    source = SourceEvent(event["id"], cfg.workspace_id, ObjectKind.KNOWLEDGE, event["object_id"], True)
    assert not notification_plans(kb_config(False), source)
    first = notification_plans(cfg, source)[0]
    assert first.chat_id == "oc_default"
    update = replace(source, first_formal_create=False, change_id=UUID(int=21))
    second = notification_plans(cfg, update)[0]
    assert first.dedupe_key != second.dedupe_key
    assert second.dedupe_key == notification_plans(cfg.model_copy(update={"revision": 9}), update)[0].dedupe_key
    assert second.dedupe_key != notification_plans(cfg, replace(update, change_id=UUID(int=22)))[0].dedupe_key
    assert not notification_plans(cfg, replace(update, historical=True))


@pytest.mark.parametrize("field", ["answers", "phone", "share_token", "source_url", "file_url", "questionnaire"])
def test_no_pii_or_access_secrets_can_be_mapped_to_feishu(field):
    value = kb_config().model_dump(mode="json")
    value["mappings"]["knowledge"]["fields"][field] = "fldForbidden"
    with pytest.raises(ValidationError):
        SyncConfig.model_validate(value)


def test_publication_snapshot_rejects_forgery_and_stale_source():
    cfg, event, raw = sample()
    assert knowledge_notification_snapshot(event, raw) == event["notification_snapshot"]
    for field in ("notification_publication_id", "version_id", "notification_publication_revision"):
        assert knowledge_notification_snapshot(event, {**raw, field: "changed"}) is None
    assert knowledge_notification_snapshot(event, {**raw, "excluded": True}) is None
    forged = {**event, "notification_snapshot": {**event["notification_snapshot"], "workspace_id": str(UUID(int=999))}}
    with pytest.raises(FeishuError, match="INVALID_KNOWLEDGE_SNAPSHOT"):
        knowledge_notification_snapshot(forged, raw)


def test_card_only_contains_allowlisted_plain_metadata_and_base_link():
    cfg, event, raw = sample()
    card = knowledge_publication_card({**raw, "share_token": "DO-NOT-SEND", "answers": "PRIVATE"}, cfg, "recOne")
    encoded = json.dumps(card, ensure_ascii=False)
    assert "DO-NOT-SEND" not in encoded and "PRIVATE" not in encoded
    assert card["elements"][0]["text"]["tag"] == "plain_text"
    assert "table=tblKnowledge" in card["elements"][-1]["actions"][0]["url"]


def test_card_translates_legacy_codes_in_frozen_snapshot():
    cfg, _, raw = sample()
    card = knowledge_publication_card(
        {**raw, "product": "general", "category": "conference_speech"}, cfg, None
    )
    encoded = json.dumps(card, ensure_ascii=False)
    assert "产品：通用" in encoded and "类别：会议演讲" in encoded
    assert "产品：general" not in encoded and "类别：conference_speech" not in encoded


def test_card_internal_buttons_open_material_and_get_current_sales_share():
    cfg, _, raw = sample()
    cfg = cfg.model_copy(update={"knowledge_web_url": "https://sales.example/web/"})
    card = knowledge_publication_card(raw, cfg, None)
    actions = card["elements"][-1]["actions"]
    assert [a["text"]["content"] for a in actions] == ["查看资料", "获取分享链接"]
    assert actions[0]["url"] == "https://sales.example/web/#/pages/knowledge/index?material_id=" + raw["material_id"]
    assert actions[1]["url"] == actions[0]["url"] + "&distribution=1"
    assert "token" not in json.dumps(actions)


@pytest.mark.parametrize(
    "url",
    [
        "http://sales.example/web/",
        "https://user:secret@sales.example/web/",
        "https://sales.example/web/?token=x",
        "https://sales.example/web/#token=x",
        "https://sales.example:8443/web/",
    ],
)
def test_internal_card_url_rejects_insecure_or_secret_bearing_config(url):
    value = kb_config().model_dump(mode="json")
    with pytest.raises(ValidationError):
        SyncConfig.model_validate({**value, "knowledge_web_url": url})


@pytest.mark.asyncio
async def test_revocation_clears_remote_metadata_without_sending_a_card():
    service, event, client = setup()
    cfg = kb_config()
    event.update(object_kind="knowledge", historical=True, first_formal_create=False)
    service.client = lambda row: (cfg, client)
    service.repository.source.return_value = {"id": str(event["object_id"]), "excluded": True}
    await service.handle(Connection(), event, {})
    fields = client.update_record.await_args.args[-1]
    assert fields["名称"] is None and fields["状态"] == "已归档"
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_directory_links_use_internal_route_and_clear_on_withdrawal():
    service, event, client = setup()
    value = kb_config(False).model_dump(mode="json")
    value["knowledge_web_url"] = "https://sales.example/web/"
    value["mappings"]["knowledge"]["fields"].update(internal_url="fldInternal", share_entry_url="fldShare")
    cfg = SyncConfig.model_validate(value)
    schema = client.fields.return_value
    schema.update(fldInternal={"field_name": "内部入口", "type": 1}, fldShare={"field_name": "分享入口", "type": 1})
    event.update(object_kind="knowledge", historical=True, first_formal_create=False)
    service.client = lambda row: (cfg, client)
    service.repository.source.return_value = {"id": str(event["object_id"]), "content_name": "资料"}
    await service.handle(Connection(), event, {})
    fields = client.update_record.await_args.args[-1]
    assert fields["分享入口"] == fields["内部入口"] + "&distribution=1"
    assert fields["内部入口"].endswith(str(event["object_id"]))
    service.repository.source.return_value = {"id": str(event["object_id"]), "excluded": True}
    await service.handle(Connection(), event, {})
    fields = client.update_record.await_args.args[-1]
    assert fields["内部入口"] is None and fields["分享入口"] is None
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_newer_version_cancels_pending_frozen_delivery_before_send():
    service, _, client = setup()
    cfg, event, raw = sample()
    service.client = lambda row: (cfg, client)
    card = knowledge_publication_card(event["notification_snapshot"], cfg, "recOne")
    delivery = dict(
        dedupe_key="test-key",
        status="pending",
        chat_id="oc_default",
        payload={
            "schema_version": 1,
            "kind": "knowledge_publication",
            "source": event["notification_snapshot"],
            "card": card,
        },
    )
    event["notification_planned"] = True
    service.repository.deliveries.return_value = [delivery]
    service.repository.source.side_effect = [raw, {**raw, "version_id": str(UUID(int=99))}]
    await service.handle(Connection(), event, {})
    client.send_card.assert_not_awaited()
    assert service.repository.delivery_state.await_args.kwargs["error"] == "SOURCE_CHANGED_BEFORE_NOTIFICATION"


@pytest.mark.asyncio
async def test_hook_passes_only_bound_identifiers_and_uses_same_connection():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=1))
    kwargs = dict(
        workspace_id=UUID(int=1),
        material_id=UUID(int=2),
        publication_id=UUID(int=3),
        version_id=UUID(int=4),
        event_kind="metadata_updated",
        revision=2,
        actor_id=UUID(int=5),
    )
    assert await enqueue_knowledge_distribution_event(connection, **kwargs) == 1
    assert connection.fetchval.await_args.args[1:] == tuple(kwargs.values())
    with pytest.raises(ValueError):
        await enqueue_knowledge_distribution_event(connection, **{**kwargs, "event_kind": "questionnaire_answer"})
    assert connection.fetchval.await_count == 1


@pytest.mark.parametrize("kind", ["published", "version_published"])
def test_same_version_metadata_keeps_original_notice_but_missing_origin_fails_closed(kind):
    _, event, raw = sample()
    event["first_formal_create"] = kind == "published"
    event["notification_snapshot"]["event_kind"] = kind
    current = {**raw, "publication_id": str(UUID(int=44)), "publication_revision": 4}
    assert knowledge_notification_snapshot(event, current) == event["notification_snapshot"]
    for field in ("notification_publication_id", "notification_publication_revision"):
        assert knowledge_notification_snapshot(event, {**current, field: None}) is None


def test_update_summary_uses_existing_description_plain_text_without_invented_fallback():
    cfg, event, raw = sample()
    note = "本版补充客户案例 <at id=all>；不解析为消息标签"
    card = knowledge_publication_card({**raw, "description": note}, cfg, None)
    assert {"tag": "div", "text": {"tag": "plain_text", "content": "更新摘要：" + note}} in card["elements"]
    empty = knowledge_publication_card({**raw, "description": ""}, cfg, None)
    assert "更新摘要" not in json.dumps(empty, ensure_ascii=False)
    cfg_data = cfg.model_dump(mode="json")
    cfg_data["mappings"]["knowledge"]["fields"]["description"] = "fldSummary"
    assert SyncConfig.model_validate(cfg_data).mappings["knowledge"].fields["description"] == "fldSummary"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "sent", "unknown"])
async def test_metadata_keeps_frozen_pending_card_and_never_resends_sent_or_unknown(status):
    service, _, client = setup()
    cfg, event, raw = sample()
    event["notification_planned"] = True
    event["notification_snapshot"]["description"] = "首次发布说明"
    frozen = dict(
        schema_version=1,
        kind="knowledge_publication",
        source=event["notification_snapshot"],
        card=knowledge_publication_card(event["notification_snapshot"], cfg, "recOne"),
    )
    delivery = dict(dedupe_key="stable-original-key", status=status, chat_id="oc_default", payload=frozen)
    service.repository.deliveries.return_value = [delivery]
    service.repository.source.return_value = {
        **raw,
        "publication_id": str(UUID(int=44)),
        "publication_revision": 4,
        "description": "后续元数据说明",
    }
    service.client = lambda row: (cfg, client)
    if status == "unknown":
        with pytest.raises(FeishuError, match="MESSAGE_RESULT_UNKNOWN"):
            await service.handle(Connection(), event, {})
    else:
        await service.handle(Connection(), event, {})
    if status == "pending":
        client.send_card.assert_awaited_once_with("oc_default", frozen["card"], "stable-original-key")
        assert [
            call.args[2] if len(call.args) > 2 else call.args[1]
            for call in service.repository.delivery_state.await_args_list
        ] == ["unknown", "sent"]
    else:
        client.send_card.assert_not_awaited()
    service.repository.freeze_delivery.assert_not_awaited()
