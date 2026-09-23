"""Zero remote reads/writes for unchanged sources, without losing notifications."""

from unittest.mock import AsyncMock

import pytest
from sales_backend.domain.feishu_sync.config import TableMapping
from tests.test_feishu_sync_service import setup, Connection


async def acknowledged(service, event, client):
    event["historical"] = True
    await service.handle(Connection(), event, {})
    args = service.repository.remember.await_args.args
    saved = dict(table_id=args[2], record_id=args[3], projection_hash=args[4], source_fingerprint=args[5])
    service.repository.mapped = AsyncMock(return_value=saved)
    for name in ("fields", "find_record", "create_record", "update_record", "send_card"):
        getattr(client, name).reset_mock()
    return saved


@pytest.mark.asyncio
async def test_repeat_and_process_restart_do_not_call_feishu():
    service, event, client = setup()
    saved = await acknowledged(service, event, client)
    for restart in (False, True):
        if restart:
            service, event, client = setup()
            event["historical"] = True
            service.repository.mapped.return_value = saved
        await service.handle(Connection(), event, {})
        for name in ("fields", "find_record", "create_record", "update_record", "send_card"):
            getattr(client, name).assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_timestamp_is_not_a_source_change():
    service, event, client = setup()
    config, _ = service.client({})
    mapping = config.mappings["customer"].model_copy(
        update={"fields": {**config.mappings["customer"].fields, "synced_at": "fldSynced"}}
    )
    config = config.model_copy(update={"mappings": {"customer": mapping}})
    service.client = lambda row: (config, client)
    client.fields.return_value["fldSynced"] = {"type": 1, "field_name": "同步时间"}
    await acknowledged(service, event, client)
    await service.handle(Connection(), event, {})
    client.fields.assert_not_awaited()
    client.update_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "mapping", "force", "retry"])
async def test_changes_force_and_retry_still_recheck_remote(change):
    service, event, client = setup()
    await acknowledged(service, event, client)
    if change == "source":
        service.repository.source.return_value["name"] = "changed"
    elif change == "mapping":
        config, _ = service.client({})
        mapping = config.mappings["customer"].model_copy(update={"fields": {"record_status": "fldStatus"}})
        config = config.model_copy(update={"mappings": {"customer": mapping}})
        service.client = lambda row: (config, client)
    elif change == "force":
        event["force_remote_check"] = True
    else:
        event["attempts"] = 2
    await service.handle(Connection(), event, {})
    client.fields.assert_awaited()
    client.find_record.assert_awaited_once()
    client.update_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_unchanged_source_does_not_consume_first_notification():
    service, event, client = setup()
    await acknowledged(service, event, client)
    event["historical"] = False
    await service.handle(Connection(), event, {})
    client.update_record.assert_not_awaited()
    service.repository.freeze_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_acknowledged_projection_bootstraps_without_remote_write():
    service, event, client = setup()
    saved = await acknowledged(service, event, client)
    saved["source_fingerprint"] = None
    service._comparison_schemas.clear()
    await service.handle(Connection(), event, {})
    client.fields.assert_awaited_once()
    client.find_record.assert_not_awaited()
    client.update_record.assert_not_awaited()
    assert service.repository.remember.await_args.args[-1]


@pytest.mark.asyncio
async def test_changed_linked_remote_record_invalidates_fingerprint():
    service, event, client = setup()
    config, _ = service.client({})
    mapping = TableMapping(
        enabled=True,
        table_id="tblCustomers",
        id_field_id="fldSystemId",
        fields={"record_status": "fldStatus", "owner_id": "fldOwner"},
    )
    config = config.model_copy(update={"mappings": {"customer": mapping}})
    service.client = lambda row: (config, client)
    service.repository.source.return_value["owner_user_ref_id"] = "00000000-0000-0000-0000-000000000088"
    client.fields.return_value["fldOwner"] = {"type": 18, "field_name": "负责人"}
    parent = {"record_id": "recOwnerA"}
    saved = {}
    service.repository.mapped = AsyncMock(
        side_effect=lambda c, cid, kind, oid: parent if kind == "member" else saved.get("row")
    )
    event["historical"] = True
    await service.handle(Connection(), event, {})
    args = service.repository.remember.await_args.args
    saved["row"] = dict(table_id=args[2], record_id=args[3], projection_hash=args[4], source_fingerprint=args[5])
    parent["record_id"] = "recOwnerB"
    client.update_record.reset_mock()
    await service.handle(Connection(), event, {})
    assert client.update_record.await_args.args[-1]["负责人"] == ["recOwnerB"]
