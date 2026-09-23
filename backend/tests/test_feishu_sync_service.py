from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.integrations.feishu import FeishuError
from sales_backend.services.feishu_sync import FeishuSyncService
from tests.test_feishu_sync_policy import payload


class Connection:
    async def execute(self, sql, *args):
        return "SELECT 1"

    @asynccontextmanager
    async def transaction(self):
        yield self


def setup():
    config = SyncConfig.model_validate(payload())
    event = {
        "id": UUID(int=10),
        "workspace_id": config.workspace_id,
        "connection_id": config.connection_id,
        "object_kind": "customer",
        "object_id": UUID(int=20),
        "first_formal_create": True,
        "historical": False,
        "notification_planned": False,
        "lease_token": UUID(int=21),
    }
    client = SimpleNamespace(
        close=AsyncMock(),
        fields=AsyncMock(
            return_value={
                "fldSystemId": {"field_name": "系统ID", "type": 1},
                "fldName": {"field_name": "名称", "type": 1},
                "fldStatus": {"field_name": "状态", "type": 1},
            }
        ),
        find_record=AsyncMock(return_value="recExisting"),
        create_record=AsyncMock(return_value="recNew"),
        update_record=AsyncMock(),
        send_card=AsyncMock(return_value="om_test"),
    )
    service = FeishuSyncService(None)
    service.client = lambda row: (config, client)
    service.repository = SimpleNamespace(
        guard=AsyncMock(),
        source=AsyncMock(return_value={"id": str(event["object_id"]), "name": "客户", "data_kind": "production"}),
        remember=AsyncMock(),
        mapped=AsyncMock(return_value=None),
        deliveries=AsyncMock(return_value=[]),
        freeze_delivery=AsyncMock(),
        mark_planned=AsyncMock(),
        delivery_state=AsyncMock(),
    )
    return service, event, client


@pytest.mark.asyncio
async def test_existing_remote_record_is_repaired_without_duplicate_create():
    service, event, client = setup()
    event["historical"] = True
    await service.handle(Connection(), event, {})
    client.update_record.assert_awaited_once()
    client.create_record.assert_not_awaited()
    service.repository.freeze_delivery.assert_not_awaited()
    client.send_card.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('recovery_lookup', ['found', 'absent', 'duplicate'])
async def test_unknown_internal_create_recovers_only_after_stable_id_lookup(recovery_lookup):
    service, event, client = setup()
    event['historical'] = True
    client.find_record.side_effect = [None, {
        'found': 'recCommitted', 'absent': None,
        'duplicate': FeishuError('DUPLICATE_SYSTEM_ID'),
    }[recovery_lookup]]
    client.create_record.side_effect = [
        FeishuError('FEISHU_1255001', unknown=True, retry_after=10), 'recRecovered',
    ]
    with pytest.raises(FeishuError, match='FEISHU_1255001'):
        await service.handle(Connection(), event, {})
    client.create_record.assert_awaited_once()
    client.update_record.assert_not_awaited()
    service.repository.remember.assert_not_awaited()
    client.send_card.assert_not_awaited()

    # A later native queue attempt starts the normal handler again. It cannot
    # assume the failed response means the remote create did not commit.
    if recovery_lookup == 'duplicate':
        with pytest.raises(FeishuError, match='DUPLICATE_SYSTEM_ID'):
            await service.handle(Connection(), event, {})
        client.create_record.assert_awaited_once()
        client.update_record.assert_not_awaited()
        service.repository.remember.assert_not_awaited()
    else:
        await service.handle(Connection(), event, {})
        service.repository.remember.assert_awaited_once()
        if recovery_lookup == 'found':
            client.create_record.assert_awaited_once()
            client.update_record.assert_awaited_once()
            assert client.update_record.await_args.args[2] == 'recCommitted'
        else:
            client.update_record.assert_not_awaited()
            assert client.create_record.await_count == 2
            first, second = client.create_record.await_args_list
            assert first.args[-1] == second.args[-1] and first.args[-1].version == 4
    assert client.find_record.await_count == 2
    first_lookup, second_lookup = client.find_record.await_args_list
    assert first_lookup.args == second_lookup.args
    assert second_lookup.args[-1] == str(event['object_id'])
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_missing", [False, True])
async def test_historical_visit_keeps_all_links_and_waits_for_every_parent(dependency_missing):
    from sales_backend.domain.feishu_sync.config import TableMapping
    service, event, client = setup()
    config, _ = service.client({})
    mapping = TableMapping(enabled=True, table_id="tblVisits", id_field_id="fldSystemId",
        fields={"record_status": "fldStatus", "opportunity_ids": "fldOpportunities"})
    config = config.model_copy(update={"mappings": {**config.mappings, "visit": mapping}})
    service.client = lambda row: (config, client)
    event.update(object_kind="visit", historical=True)
    service.repository.source.return_value = {"id": str(event["object_id"]),
        "archived_at": "2026-09-22T00:00:00Z",
        "opportunity_ids": [str(UUID(int=30)), str(UUID(int=31)), str(UUID(int=30))]}
    service.repository.mapped = AsyncMock(side_effect=[{"record_id": "recFirst"},
        None if dependency_missing else {"record_id": "recSecond"}, None])
    client.fields.return_value["fldOpportunities"] = {"field_name": "关联商机", "type": 18}
    if dependency_missing:
        with pytest.raises(FeishuError, match="DEPENDENCY_PENDING"):
            await service.handle(Connection(), event, {})
        client.update_record.assert_not_awaited()
    else:
        await service.handle(Connection(), event, {})
        assert client.update_record.call_args.args[-1]["关联商机"] == ["recFirst", "recSecond"]
    client.send_card.assert_not_awaited()
    service.repository.freeze_delivery.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_notification_does_not_consume_planning_marker():
    service, event, client = setup()
    config, _ = service.client({})
    disabled = config.model_copy(update={
        "notification": config.notification.model_copy(update={"enabled": False}),
    })
    service.client = lambda row: (disabled, client)
    await service.handle(Connection(), event, {})
    service.repository.freeze_delivery.assert_not_awaited()
    service.repository.mark_planned.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_test_data_never_leaves_system():
    service, event, client = setup()
    service.repository.source.return_value = {"id": str(event["object_id"]), "data_kind": "test"}
    await service.handle(Connection(), event, {})
    client.fields.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_notification_is_not_blindly_resent():
    service, event, client = setup()
    service.repository.deliveries.return_value = [{"status": "unknown"}]
    with pytest.raises(FeishuError, match="MESSAGE_RESULT_UNKNOWN"):
        await service.handle(Connection(), event, {})
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_lease_stops_before_remote_io():
    service, event, client = setup()
    service.repository.guard.side_effect = RuntimeError("lease expired")
    with pytest.raises(RuntimeError, match="lease expired"):
        await service.handle(Connection(), event, {})
    client.fields.assert_not_awaited()
    client.create_record.assert_not_awaited()
    client.send_card.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_excluded_existing_record_only_updates_status_without_body_or_notification():
    service, event, client = setup()
    service.repository.source.return_value = {'id': str(event['object_id']), 'excluded': True}
    await service.handle(Connection(), event, {})
    fields = client.update_record.await_args.args[-1]
    assert fields == {'系统ID': str(event['object_id']), '状态': '已归档'}
    client.create_record.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_excluded_unmapped_record_is_never_created():
    service, event, client = setup()
    service.repository.source.return_value = {'id': str(event['object_id']), 'excluded': True}
    client.find_record.return_value = None
    await service.handle(Connection(), event, {})
    client.update_record.assert_not_awaited()
    client.create_record.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('source', [{'excluded': True}, {'deleted': True}, {'status': 'withdrawn'}])
async def test_pending_frozen_notice_is_terminated_when_source_becomes_ineligible(source):
    service, event, client = setup()
    service.repository.source.return_value = {'id': str(event['object_id']), **source}
    service.repository.deliveries.return_value = [
        {'status': 'pending', 'dedupe_key': 'pending'},
        {'status': 'sent', 'dedupe_key': 'sent'},
        {'status': 'unknown', 'dedupe_key': 'unknown'},
    ]
    await service.handle(Connection(), event, {})
    client.send_card.assert_not_awaited()
    service.repository.delivery_state.assert_awaited_once()
    args = service.repository.delivery_state.await_args
    assert args.args[1:] == ('pending', 'failed')
    assert args.kwargs == {'error': 'SOURCE_NO_LONGER_ELIGIBLE'}


@pytest.mark.asyncio
async def test_terminal_failed_notice_is_not_retried():
    service, event, client = setup()
    service.repository.deliveries.return_value = [{'status': 'failed', 'dedupe_key': 'closed'}]
    await service.handle(Connection(), event, {})
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["customer", "refresh"])
async def test_stored_bidirectional_connection_cannot_process_events(kind):
    service, event, client = setup()
    config, _ = service.client({})
    service.client = lambda row: (config.model_copy(update={"direction": "bidirectional"}), client)
    service.repository.reconcile = AsyncMock()
    event["object_kind"] = kind
    with pytest.raises(FeishuError, match="SYNC_DIRECTION_UNSUPPORTED"):
        await service.handle(Connection(), event, {})
    service.repository.source.assert_not_awaited()
    service.repository.reconcile.assert_not_awaited()
    for method in (client.fields, client.find_record, client.create_record, client.update_record, client.send_card):
        method.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_reserved_direction_validation_fails_before_remote_io():
    service, _, client = setup()
    config, _ = service.client({})
    service.client = lambda row: (config.model_copy(update={"direction": "bidirectional"}), client)
    service.repository.validation_result = AsyncMock()
    connection, row = Connection(), {"id": "stored-reserved-connection"}
    await service.validate(connection, row)
    service.repository.validation_result.assert_awaited_once_with(connection, row, "SYNC_DIRECTION_UNSUPPORTED")
    client.fields.assert_not_awaited()
    client.send_card.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_deleted_absent_record_is_never_recreated():
    service, event, client = setup()
    event["historical"] = True
    service.repository.source.return_value = {"id": str(event["object_id"]),
        "deleted_at": "2026-09-19T00:00:00Z", "name": "旧客户"}
    client.find_record.return_value = None
    await service.handle(Connection(), event, {})
    client.create_record.assert_not_awaited()
    client.update_record.assert_not_awaited()
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_soft_deleted_existing_record_only_archives_status():
    service, event, client = setup()
    service.repository.source.return_value = {"id": str(event["object_id"]),
        "deleted_at": "2026-09-19T00:00:00Z", "name": "旧客户"}
    await service.handle(Connection(), event, {})
    fields = client.update_record.call_args.args[-1]
    assert fields == {"系统ID": str(event["object_id"]), "状态": "已归档"}
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_forecast_sync_uses_link_maps_and_never_sends_create_card():
    from sales_backend.domain.feishu_sync.config import TableMapping
    service, event, client = setup()
    config, _ = service.client({})
    mapping = TableMapping(enabled=True, table_id='tblForecast', id_field_id='fldSystemId',
        fields={'record_status':'fldStatus','opportunity_id':'fldOpportunity',
                'recognized_amount':'fldRecognized','collection_amount':'fldCollection'})
    config = config.model_copy(update={'mappings':{**config.mappings,'forecast':mapping}})
    service.client = lambda row: (config, client)
    event['object_kind'] = 'forecast'
    service.repository.source.return_value = {'id':str(event['object_id']),
        'opportunity_id':str(UUID(int=30)), 'recognized_amount':0, 'collection_amount':None}
    service.repository.mapped = AsyncMock(side_effect=lambda c, cid, kind, oid: {'record_id':'recOpportunity'} if kind=='opportunity' else None)
    client.fields.return_value.update({
        'fldOpportunity':{'field_name':'关联商机','type':18},
        'fldRecognized':{'field_name':'预测确收','type':2},
        'fldCollection':{'field_name':'预测回款','type':2}})
    await service.handle(Connection(), event, {})
    fields = client.update_record.call_args.args[-1]
    assert fields['关联商机'] == ['recOpportunity']
    assert fields['预测确收'] == 0 and fields['预测回款'] is None
    client.send_card.assert_not_awaited()
    service.repository.freeze_delivery.assert_not_awaited()
