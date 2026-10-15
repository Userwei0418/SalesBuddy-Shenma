"""Worker-owned caches and independent object writes; synthetic data only."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest

from sales_backend.integrations.feishu import FeishuClient, FeishuError
from tests.test_feishu_sync_service import Connection, setup


def worker_row(config):
    return {'id': config.connection_id, 'workspace_id': config.workspace_id,
            'revision': config.revision, 'credential_updated_at': 'version-one',
            'encryption_key_id': 'key-one'}


@pytest.mark.asyncio
async def test_worker_reuses_token_and_http_but_fresh_schema_prevents_name_reuse_misrouting():
    service, event, _ = setup()
    config, _ = service.client({})
    row = worker_row(config)
    requests = []
    names = {'fldName': '名称', 'fldOther': '其他文本字段'}
    writes = []

    def transport(request):
        path = request.url.path
        requests.append((request.method, path))
        if '/auth/' in path:
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
        if path.endswith('/fields'):
            return httpx.Response(200, json={'code': 0, 'data': {'items': [
                {'field_id': 'fldSystemId', 'field_name': '系统ID', 'type': 1},
                *[{'field_id': fid, 'field_name': name, 'type': 1} for fid, name in names.items()],
                {'field_id': 'fldStatus', 'field_name': '状态', 'type': 1}]}})
        if path.endswith('/search'):
            return httpx.Response(200, json={'code': 0, 'data': {'items': []}})
        assert path.endswith('/records')
        fields = json.loads(request.content)['fields']
        # The provider accepts both names: stale schema silently targets fldOther.
        writes.append({fid: fields[name] for fid, name in names.items() if name in fields})
        names.update(fldName='客户名称已改名', fldOther='名称')
        return httpx.Response(200, json={'code': 0, 'data': {'record': {'record_id': 'recFixture'}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_fixture', 'fixture-secret', client=http)
        service.reuse_client = True
        service.client = Mock(return_value=(config, client))
        event['historical'] = True
        await service.handle(Connection(), event, row)
        await service.handle(Connection(), {**event, 'id': UUID(int=99)}, row)
        assert service.client.call_count == 1
        assert sum('/auth/' in p for _, p in requests) == 1
        assert sum(p.endswith('/fields') for _, p in requests) == 2
        assert sum(p.endswith('/search') for _, p in requests) == 2
        assert sum(p.endswith('/records') for _, p in requests) == 2
        assert writes == [{'fldName': '客户'}, {'fldName': '客户'}]
        assert client._token
        await service.close()
        assert service._cached_client is None and not client._token and not client._secret


@pytest.mark.asyncio
@pytest.mark.parametrize('field,new_value', [
    ('id', UUID(int=999)), ('workspace_id', UUID(int=888)), ('revision', 2),
    ('credential_updated_at', 'version-two'), ('encryption_key_id', 'key-two'),
])
async def test_worker_cache_closes_before_config_or_credential_identity_changes(field, new_value):
    service, _, first = setup()
    config, _ = service.client({})
    row = worker_row(config)
    second = SimpleNamespace(close=AsyncMock())
    service.reuse_client = True
    service.client = Mock(side_effect=[(config, first), (config, second)])
    async with service._client(row):
        pass
    first.close.assert_not_awaited()
    async with service._client({**row, field: new_value}) as (_, actual):
        first.close.assert_awaited_once()
        assert actual is second
    await service.close()
    await service.close()
    second.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_cache_has_bounded_lifetime(monkeypatch):
    from sales_backend.services import feishu_sync
    now = [0.0]
    monkeypatch.setattr(feishu_sync, 'monotonic', lambda: now[0])
    service, _, first = setup()
    config, _ = service.client({})
    second = SimpleNamespace(close=AsyncMock())
    service.reuse_client = True
    service.client = Mock(side_effect=[(config, first), (config, second)])
    row = worker_row(config)
    async with service._client(row):
        pass
    now[0] = 600
    async with service._client(row) as (_, actual):
        assert actual is second
    first.close.assert_awaited_once()
    await service.close()


@pytest.mark.asyncio
async def test_validation_discards_cached_client_and_reads_fresh_schema():
    service, _, first = setup()
    config, _ = service.client({})
    _, _, fresh = setup()
    config = config.model_copy(update={'notification': config.notification.model_copy(update={'enabled': False})})
    service.reuse_client = True
    service.client = Mock(side_effect=[(config, first), (config, fresh)])
    service.repository.validation_result = AsyncMock()
    row = worker_row(config)
    async with service._client(row):
        pass
    await service.validate(Connection(), row)
    first.close.assert_awaited_once()
    fresh.fields.assert_awaited()
    fresh.close.assert_awaited_once()
    assert service._cached_client is None



class AdvisoryConnection(Connection):
    """Model session locks so restoring a table lock breaks the concurrency test."""

    def __init__(self, locks):
        self.locks = locks

    async def execute(self, sql, key):
        if 'pg_advisory_unlock(' in sql:
            self.locks[key].release()
        elif 'pg_advisory_lock(' in sql:
            await self.locks.setdefault(key, asyncio.Lock()).acquire()
        return 'SELECT 1'


@pytest.mark.asyncio
async def test_separate_objects_can_write_same_table_concurrently():
    entered_a, entered_b, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first, event_a, client_a = setup()
    second, event_b, client_b = setup()
    event_a["historical"] = event_b["historical"] = True
    event_b.update(id=UUID(int=98), object_id=UUID(int=99))
    client_b.find_record.return_value = "recSecond"
    second.repository.source.return_value = {
        "id": str(event_b["object_id"]), "name": "第二客户", "data_kind": "production"}

    async def write_a(*args):
        entered_a.set()
        await release.wait()

    async def write_b(*args):
        entered_b.set()
        await release.wait()

    client_a.update_record.side_effect = write_a
    client_b.update_record.side_effect = write_b
    locks = {}
    task_a = asyncio.create_task(first.handle(AdvisoryConnection(locks), event_a, {}))
    task_b = None
    try:
        await asyncio.wait_for(entered_a.wait(), 1)
        task_b = asyncio.create_task(second.handle(AdvisoryConnection(locks), event_b, {}))
        # Both remote writes must enter before either finishes, even in one table.
        await asyncio.wait_for(entered_b.wait(), 1)
        assert not task_a.done() and not task_b.done()
        assert client_a.update_record.await_args.args[:2] == client_b.update_record.await_args.args[:2]
        assert client_a.update_record.await_args.args[-1]["系统ID"] != (
            client_b.update_record.await_args.args[-1]["系统ID"])
    finally:
        release.set()
        await asyncio.gather(*[task for task in (task_a, task_b) if task is not None])
    first.repository.remember.assert_awaited_once()
    second.repository.remember.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_write_conflict_does_not_remember_or_notify(operation):
    service, event, client = setup()
    client.find_record.return_value = None if operation == "create" else "recExisting"
    write = client.create_record if operation == "create" else client.update_record
    write.side_effect = FeishuError("WRITE_CONFLICT", retryable=True, retry_after=7)
    with pytest.raises(FeishuError, match="WRITE_CONFLICT") as caught:
        await service.handle(Connection(), event, {})
    assert caught.value.retryable and caught.value.retry_after == 7
    service.repository.remember.assert_not_awaited()
    service.repository.freeze_delivery.assert_not_awaited()
    client.send_card.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    FeishuError("WRITE_CONFLICT", retryable=True),
    FeishuError("TRANSPORT_UNKNOWN", unknown=True),
])
async def test_same_object_creation_retry_keeps_key_across_event_and_revision(error):
    service, event, client = setup()
    event["historical"] = True
    config, _ = service.client({})
    client.find_record.return_value = None
    client.create_record.side_effect = [error, "recCreated", "recOther"]
    with pytest.raises(FeishuError):
        await service.handle(Connection(), event, {})
    service.repository.remember.assert_not_awaited()
    service.client = lambda row: (config.model_copy(update={"revision": config.revision + 1}), client)
    await service.handle(Connection(), {**event, "id": UUID(int=90), "lease_token": UUID(int=91)}, {})
    other_event = {**event, "id": UUID(int=92), "object_id": UUID(int=93)}
    service.repository.source.return_value = {
        "id": str(other_event["object_id"]), "name": "另一客户", "data_kind": "production"}
    await service.handle(Connection(), other_event, {})
    keys = [call.args[-1] for call in client.create_record.await_args_list]
    assert keys[0] == keys[1] and keys[2] != keys[0]
    assert all(isinstance(key, UUID) and key.version == 4 for key in keys)
    assert service.repository.remember.await_count == 2
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_or_config_change_before_write_prevents_write():
    service, event, client = setup()
    service.repository.guard.side_effect = [None, None, RuntimeError("lease changed")]
    with pytest.raises(RuntimeError, match="lease changed"):
        await service.handle(Connection(), event, {})
    client.update_record.assert_not_awaited()
    client.create_record.assert_not_awaited()
    service.repository.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_remote_write_is_not_remembered_or_notified():
    service, event, client = setup()
    writing = asyncio.Event()

    async def update(*args):
        writing.set()
        await asyncio.Event().wait()

    client.update_record.side_effect = update
    task = asyncio.create_task(service.handle(Connection(), event, {}))
    try:
        await asyncio.wait_for(writing.wait(), 1)
    finally:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    service.repository.remember.assert_not_awaited()
    client.send_card.assert_not_awaited()
    client.close.assert_awaited_once()
