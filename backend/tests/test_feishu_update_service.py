"""Update cards use captured facts, including across queue lag and retries."""
import copy
import json
from uuid import UUID

import pytest

from sales_backend.domain.feishu_sync.config import DepartmentRoute
from sales_backend.integrations.feishu import FeishuError
from tests.test_feishu_sync_service import Connection, setup


def setup_update():
    service, event, client = setup()
    config, _ = service.client({})
    mapping = config.mappings["customer"].model_copy(update={"table_id": "tblOpportunities"})
    config = config.model_copy(update={"mappings": {"opportunity": mapping}})
    service.client = lambda row: (config, client)
    event.update(object_kind="opportunity", first_formal_create=False, notification_snapshot={
        "schema_version": 1, "change_id": str(UUID(int=40)),
        "workspace_id": str(event["workspace_id"]), "opportunity_id": str(event["object_id"]),
        "opportunity_name": "修改时的商机", "customer_name": "修改时的客户",
        "owner_name": "修改时的负责人", "actor_name": "销售甲", "department_id": str(UUID(int=11)),
        "changed_at": "2026-09-27T12:34:00Z", "currency": "CNY",
        "changes": [{"label": "ACV（万元）", "before": "100000", "after": "10"}],
    })
    service.repository.source.return_value = {
        "id": str(event["object_id"]), "name": "更晚的商机名称", "owner_name": "更晚负责人",
        "owner_team_id": str(UUID(int=12)), "amount": "900", "data_kind": "production",
    }
    return service, event, client, config


@pytest.mark.asyncio
async def test_update_card_and_route_are_frozen_from_change_not_latest_source():
    service, event, client, config = setup_update()
    config = config.model_copy(update={"notification": config.notification.model_copy(update={
        "routing_mode": "department_routes", "routes": (
            DepartmentRoute(department_id=UUID(int=11), chat_ids=("oc_original",)),
            DepartmentRoute(department_id=UUID(int=12), chat_ids=("oc_later",)),
        ),
    })})
    service.client = lambda row: (config, client)
    await service.handle(Connection(), event, {})
    plan, _, card = service.repository.freeze_delivery.await_args.args[1:]
    assert plan.chat_id == "oc_original"
    text = json.dumps(card, ensure_ascii=False)
    assert "修改时的商机" in text and "销售甲" in text
    assert "1,000,000,000元" in text and "100,000元" in text
    assert "更晚" not in text
    service.repository.mark_planned.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [None, {"changes": []}, {"changes": [
    {"label": "商机名称", "before": "相同", "after": "相同"}]}])
async def test_legacy_or_empty_or_noop_update_does_not_notify(snapshot):
    service, event, client, _ = setup_update()
    event["notification_snapshot"] = (None if snapshot is None else {**event["notification_snapshot"], **snapshot})
    await service.handle(Connection(), event, {})
    client.update_record.assert_awaited_once()
    service.repository.freeze_delivery.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{"schema_version": 2}, {"change_id": "bad"},
    {"workspace_id": str(UUID(int=900))}, {"opportunity_id": str(UUID(int=900))},
    {"changes": "not-a-list"}, {"changes": [{"label": "x"}]}])
async def test_invalid_or_cross_tenant_snapshot_never_sends(invalid):
    service, event, client, _ = setup_update()
    event["notification_snapshot"].update(invalid)
    with pytest.raises(FeishuError, match="INVALID_UPDATE_SNAPSHOT"):
        await service.handle(Connection(), event, {})
    service.repository.freeze_delivery.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [{"enabled": False}, {"on_update": frozenset()}])
async def test_disabling_updates_also_stops_frozen_pending_delivery(disabled):
    service, event, client, config = setup_update()
    config = config.model_copy(update={"notification": config.notification.model_copy(update=disabled)})
    service.client = lambda row: (config, client)
    service.repository.deliveries.return_value = [
        {"status": state, "dedupe_key": state} for state in ("pending", "sent", "unknown")]
    await service.handle(Connection(), event, {})
    client.send_card.assert_not_awaited()
    assert service.repository.delivery_state.await_count == 1
    assert service.repository.delivery_state.await_args.args[1:] == ("pending", "failed")
    assert service.repository.delivery_state.await_args.kwargs == {"error": "NOTIFICATION_DISABLED"}


@pytest.mark.asyncio
async def test_update_retry_reuses_frozen_payload_and_key_then_sent_receipt_is_quiet():
    service, event, client, _ = setup_update()
    frozen = []

    async def freeze(connection, plan, workspace, card):
        frozen.append({"dedupe_key": plan.dedupe_key, "chat_id": plan.chat_id,
                       "payload": copy.deepcopy(card), "status": "pending"})

    async def state(connection, key, status, **kwargs):
        next(row for row in frozen if row["dedupe_key"] == key)["status"] = status

    async def planned(connection, current):
        current["notification_planned"] = True

    service.repository.freeze_delivery.side_effect = freeze
    service.repository.deliveries.side_effect = lambda *_: frozen
    service.repository.delivery_state.side_effect = state
    service.repository.mark_planned.side_effect = planned
    client.send_card.side_effect = [FeishuError("RATE_LIMITED", retryable=True), "om_sent"]
    with pytest.raises(FeishuError, match="RATE_LIMITED"):
        await service.handle(Connection(), event, {})
    service.repository.source.return_value["name"] = "重试时再改名"
    await service.handle(Connection(), event, {})
    await service.handle(Connection(), event, {})
    assert service.repository.freeze_delivery.await_count == 1
    assert client.send_card.await_count == 2
    assert client.send_card.await_args_list[0] == client.send_card.await_args_list[1]
    assert frozen[0]["status"] == "sent"


@pytest.mark.asyncio
async def test_unknown_update_send_result_is_not_retried():
    service, event, client, _ = setup_update()
    service.repository.deliveries.return_value = [{"status": "unknown"}]
    with pytest.raises(FeishuError, match="MESSAGE_RESULT_UNKNOWN"):
        await service.handle(Connection(), event, {})
    client.send_card.assert_not_awaited()
