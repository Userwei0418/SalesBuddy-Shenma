import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest

from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.integrations.feishu import FeishuClient, FeishuError
from sales_backend.repositories.feishu_sync import FeishuRepository
from tests.test_feishu_sync_policy import payload


def test_config_uses_json_object_for_database_codec():
    async def run():
        connection = SimpleNamespace(execute=AsyncMock(), fetchrow=AsyncMock(return_value=None))
        data = payload()
        data['enabled'] = False
        config = SyncConfig.model_validate(data)
        actor = SimpleNamespace(workspace_id=data['workspace_id'], user_id=str(UUID(int=9)))
        await FeishuRepository().save(connection, actor, config, 0)
        encoded = connection.execute.call_args.args[4]
        assert isinstance(json.loads(json.dumps(encoded)), dict)
        assert encoded['workspace_id'] == actor.workspace_id
    asyncio.run(run())


def test_delivery_payload_uses_json_object():
    async def run():
        connection = SimpleNamespace(execute=AsyncMock())
        plan = SimpleNamespace(dedupe_key='test', event_id=UUID(int=1), connection_id=UUID(int=2),
                               config_revision=1, chat_id='oc_test')
        card = {'header': {'title': {'tag': 'plain_text', 'content': '新增客户'}}}
        await FeishuRepository().freeze_delivery(connection, plan, UUID(int=3), card)
        assert connection.execute.call_args.args[-1] == card
    asyncio.run(run())


@pytest.mark.parametrize('write,unknown', [(False, False), (True, True)])
def test_timeout_distinguishes_unknown_write(write, unknown):
    async def run():
        def transport(request):
            raise httpx.ReadTimeout('sensitive provider details', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = FeishuClient('cli_test', 'secret', client=http)
            with pytest.raises(FeishuError) as caught:
                await client._send('POST', '/test', write=write)
            assert caught.value.unknown is unknown
            assert caught.value.retryable is (not write)
            assert 'sensitive' not in str(caught.value)
    asyncio.run(run())


def test_duplicate_system_id_is_not_silently_overwritten():
    async def run():
        client = FeishuClient('cli_test', 'secret')
        client.request = AsyncMock(return_value={'items': [{'record_id': 'a'}, {'record_id': 'b'}]})
        try:
            with pytest.raises(FeishuError, match='DUPLICATE_SYSTEM_ID'):
                await client.find_record('base', 'tbl', '系统ID', 'id')
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize('persistent', [False, True])
async def test_expired_tenant_token_refreshes_once_without_unbounded_retries(persistent):
    calls = []
    tokens = []
    def transport(request):
        if '/auth/' in request.url.path:
            token = f'fixture-{len(tokens)}'
            tokens.append(token)
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': token, 'expire': 7200})
        calls.append((request.headers['Authorization'], json.loads(request.content)))
        if persistent or len(calls) == 1:
            return httpx.Response(200, json={'code': 99991663, 'msg': 'invalid tenant token'})
        return httpx.Response(200, json={'code': 0, 'data': {'message_id': 'fixture-message'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        if persistent:
            with pytest.raises(FeishuError, match='FEISHU_99991663'):
                await client.send_card('oc_test', {'test': True}, 'stable-key')
        else:
            assert await client.send_card('oc_test', {'test': True}, 'stable-key') == 'fixture-message'
    assert len(calls) == len(tokens) == 2
    assert calls[0][0] != calls[1][0]
    assert calls[0][1] == calls[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code,expected', [
    (200, 1254290, 'RATE_LIMITED'), (200, 1254291, 'WRITE_CONFLICT'),
    (400, 99991400, 'RATE_LIMITED'), (400, 1254290, 'RATE_LIMITED'),
    (400, 1254291, 'WRITE_CONFLICT'),
])
async def test_explicit_provider_rejections_are_retryable_without_unknown_write(status, code, expected):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(status, json={'code': code, 'msg': 'private response'},
                              headers={'Retry-After': '3', 'x-ogw-ratelimit-reset': '7'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            await client._send('POST', '/test', write=True)
    assert caught.value.code == expected
    assert caught.value.retryable and not caught.value.unknown
    assert caught.value.retry_after == 7
    assert len(calls) == 1  # Native queue owns retry; transport does not replay writes.
    assert 'private' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,delay', [
    ({'x-ogw-ratelimit-reset': '11'}, 11),
    ({'Retry-After': '99999'}, 3600),
    ({'Retry-After': '-4', 'x-ogw-ratelimit-reset': 'invalid'}, 0),
])
async def test_http_rate_limit_respects_official_reset_header(headers, delay):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers=headers))) as http:
        with pytest.raises(FeishuError) as caught:
            await FeishuClient('cli_test', 'fixture-secret', client=http)._send('GET', '/test')
    assert caught.value.code == 'RATE_LIMITED' and caught.value.retry_after == delay


@pytest.mark.asyncio
async def test_unrecognized_provider_rejection_remains_non_retryable():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'code': 1254045, 'msg': 'bad field'}))) as http:
        with pytest.raises(FeishuError) as caught:
            await FeishuClient('cli_test', 'fixture-secret', client=http)._send('POST', '/test', write=True)
    assert caught.value.code == 'FEISHU_1254045'
    assert not caught.value.retryable and not caught.value.unknown


@pytest.mark.asyncio
async def test_fields_always_read_fresh_even_when_client_is_reused():
    client = FeishuClient('cli_test', 'fixture-secret')
    client.request = AsyncMock(side_effect=[
        {'items': [{'field_id': 'fldName', 'field_name': '名称'}]},
        {'items': [{'field_id': 'fldName', 'field_name': '新名称'}]},
    ])
    try:
        first = await client.fields('base', 'table')
        assert first['fldName']['field_name'] == '名称'
        assert (await client.fields('base', 'table'))['fldName']['field_name'] == '新名称'
        assert client.request.await_count == 2
    finally:
        await client.close()
    assert not client._token and not client._secret


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 400])
async def test_field_name_rejection_leaves_retry_to_native_queue_and_next_schema_is_fresh(status):
    names, writes = ['Old name'], []

    def transport(request):
        if '/auth/' in request.url.path:
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
        if request.url.path.endswith('/fields'):
            return httpx.Response(200, json={'code': 0, 'data': {'items': [
                {'field_id': 'fldName', 'field_name': names[0], 'type': 1}]}})
        writes.append(json.loads(request.content))
        return httpx.Response(status, json={'code': 1254045})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        assert (await client.fields('base', 'table'))['fldName']['field_name'] == 'Old name'
        names[0] = 'New name'
        with pytest.raises(FeishuError) as caught:
            await client.update_record('base', 'table', 'record', {'Old name': 'value'})
        assert caught.value.code == 'FEISHU_1254045' and caught.value.retryable
        assert not caught.value.unknown
        assert len(writes) == 1
        assert (await client.fields('base', 'table'))['fldName']['field_name'] == 'New name'
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 400])
@pytest.mark.parametrize('second_code,expected,retryable,unknown', [
    (1254009, 'FEISHU_1254009', True, False),
    (1254044, 'FEISHU_1254044', True, False),
    (1254045, 'FEISHU_1254045', True, False),
    (1254290, 'RATE_LIMITED', True, False),
    (1254291, 'WRITE_CONFLICT', True, False),
    (1254607, 'DATA_NOT_READY', True, False),
    (1255001, 'FEISHU_1255001', False, True),
    (99991663, 'FEISHU_99991663', False, False),
    ('timeout', 'TRANSPORT_UNKNOWN', False, True),
])
async def test_token_refresh_second_send_keeps_error_policy_without_third_write(
        status, second_code, expected, retryable, unknown):
    token_calls, writes = [], []

    def transport(request):
        if '/auth/' in request.url.path:
            token_calls.append(True)
            return httpx.Response(200, json={
                'code': 0, 'tenant_access_token': f'fixture-{len(token_calls)}', 'expire': 7200})
        writes.append(json.loads(request.content))
        if len(writes) == 1:
            return httpx.Response(status, json={'code': 99991663})
        if second_code == 'timeout':
            raise httpx.ReadTimeout('private detail', request=request)
        return httpx.Response(status, json={'code': second_code}, headers={'x-ogw-ratelimit-reset': '5'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            await client.update_record('base', 'table', 'record', {'名称': '客户'})
        await client.close()
    assert len(token_calls) == len(writes) == 2
    assert writes[0] == writes[1]
    assert caught.value.code == expected
    assert caught.value.retryable is retryable and caught.value.unknown is unknown
    if expected in {'RATE_LIMITED', 'WRITE_CONFLICT'}:
        assert caught.value.retry_after == 5
    elif expected in {'DATA_NOT_READY', 'FEISHU_1255001'}:
        assert caught.value.retry_after == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [1254009, 1254044, 1254045])
async def test_http400_field_rejection_uses_existing_bitable_queue_policy(code):
    writes = []

    def transport(request):
        if '/auth/' in request.url.path:
            return httpx.Response(200, json={
                'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
        writes.append(request)
        return httpx.Response(400, json={'code': code, 'msg': 'private customer detail'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            await client.create_record('base', 'table', {'名称': '客户'}, UUID(int=1))
    assert caught.value.code == f'FEISHU_{code}'
    assert caught.value.retryable and not caught.value.unknown
    assert len(writes) == 1  # No inline replay after a field rejection.
    assert 'private' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [1254009, 1254044, 1254045, 1999999])
async def test_http400_numeric_error_is_preserved_without_global_retry_policy(code):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(400, json={'code': code, 'msg': 'private provider detail'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            await client._send('POST', '/test', write=True)
    assert caught.value.code == f'FEISHU_{code}'
    assert not caught.value.retryable and not caught.value.unknown
    assert len(requests) == 1 and 'private' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'code': 0}, {'code': -1}, {'code': True}, {'code': 1254045.0},
    {'code': '1254045 private'}, {'code': {'private': 'customer'}},
    {'code': [1254045]}, {}, None, [],
])
async def test_http400_missing_or_noninteger_error_code_remains_plain_http_error(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json=payload))) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            await client._send('POST', '/test', write=True)
    assert caught.value.code == 'HTTP_400'
    assert not caught.value.retryable and not caught.value.unknown
    assert 'private' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,write,expected,retryable,unknown', [
    (429, True, 'RATE_LIMITED', True, False),
    (500, True, 'UPSTREAM_UNAVAILABLE', False, True),
    (503, False, 'UPSTREAM_UNAVAILABLE', True, False),
])
async def test_http_rate_limit_and_server_error_keep_precedence_over_field_codes(
        status, write, expected, retryable, unknown):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={'code': 1254045}))) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            await client._send('POST', '/test', write=write)
    assert caught.value.code == expected
    assert caught.value.retryable is retryable and caught.value.unknown is unknown


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 400])
@pytest.mark.parametrize('retry_after,expected_delay', [(None, 10), ('3', 10), ('20', 20)])
async def test_data_not_ready_defers_to_queue_and_keeps_stable_create_key(
        status, retry_after, expected_delay):
    calls, tokens = [], []
    stable_key = UUID('89f4fa22-3e76-4d48-9e19-7d68850623f7')

    def transport(request):
        if '/auth/' in request.url.path:
            tokens.append(True)
            return httpx.Response(200, json={
                'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
        calls.append(request)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'code': 0, 'data': {'items': []}})
        creates = [r for r in calls if r.url.path.endswith('/records')]
        if len(creates) == 1:
            return httpx.Response(status, json={'code': 1254607, 'msg': 'private provider detail'},
                                  headers={'Retry-After': retry_after} if retry_after else {})
        return httpx.Response(200, json={'code': 0, 'data': {'record': {'record_id': 'fixture-record'}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        assert await client.find_record('base', 'table', '系统ID', 'stable-system-id') is None
        with pytest.raises(FeishuError) as caught:
            await client.create_record('base', 'table', {'系统ID': 'stable-system-id'}, stable_key)
        assert caught.value.code == 'DATA_NOT_READY'
        assert caught.value.retryable and not caught.value.unknown
        assert caught.value.retry_after == expected_delay
        assert len(calls) == 2  # One lookup, one rejected create, no inline replay.
        assert 'private' not in str(caught.value)

        # A subsequent caller/queue attempt looks up the same identity again.
        # Neither this test nor the transport substitutes a new client token.
        assert await client.find_record('base', 'table', '系统ID', 'stable-system-id') is None
        assert await client.create_record(
            'base', 'table', {'系统ID': 'stable-system-id'}, stable_key) == 'fixture-record'
    assert len(tokens) == 1
    assert [r.url.path.rsplit('/', 1)[-1] for r in calls] == ['search', 'records', 'search', 'records']
    creates = [r for r in calls if r.url.path.endswith('/records')]
    assert [r.url.params['client_token'] for r in creates] == [str(stable_key), str(stable_key)]
    assert creates[0].content == creates[1].content


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 400])
@pytest.mark.parametrize('operation', ['create', 'update', 'search', 'fields'])
@pytest.mark.parametrize('retry_after,expected_delay', [('3', 10), ('20', 20)])
async def test_bitable_internal_error_keeps_write_result_unknown_without_inline_replay(
        status, operation, retry_after, expected_delay):
    requests = []

    def transport(request):
        if '/auth/' in request.url.path:
            return httpx.Response(200, json={
                'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
        requests.append(request)
        return httpx.Response(status, json={'code': 1255001, 'msg': 'private provider detail'},
                              headers={'Retry-After': retry_after})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = FeishuClient('cli_test', 'fixture-secret', client=http)
        with pytest.raises(FeishuError) as caught:
            if operation == 'create':
                await client.create_record('base', 'table', {'系统ID': 'fixture-id'}, UUID(int=1))
            elif operation == 'update':
                await client.update_record('base', 'table', 'record', {'系统ID': 'fixture-id'})
            elif operation == 'search':
                await client.find_record('base', 'table', '系统ID', 'fixture-id')
            else:
                await client.fields('base', 'table')
    write = operation in {'create', 'update'}
    assert caught.value.code == 'FEISHU_1255001'
    assert caught.value.unknown is write and caught.value.retryable is (not write)
    assert caught.value.retry_after == expected_delay
    assert len(requests) == 1 and 'private' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('path,code', [
    ('/im/v1/messages', 1255001),
    ('/bitable/v1/apps/base/tables/table/records', 1255002),
    ('/bitable/v1/apps/base/tables/table/records', '1255001'),
    ('/bitable/v1/apps/base/tables/table/records', 1255001.0),
])
async def test_internal_error_recovery_is_limited_to_exact_bitable_numeric_code(path, code):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'code': code}))) as http:
        with pytest.raises(FeishuError) as caught:
            await FeishuClient('cli_test', 'fixture-secret', client=http)._send('POST', path, write=True)
    assert not caught.value.retryable and not caught.value.unknown
