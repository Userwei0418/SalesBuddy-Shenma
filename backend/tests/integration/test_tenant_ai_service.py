"""Synthetic configuration and actual restricted admission, never real models."""
from dataclasses import replace
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ChatMessage
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.services.ai_service_policy import AIServiceUnavailable
from sales_backend.services.model_calls import DatabaseModelObserver
from sales_backend.services.runtime_config import load_runtime_configuration
from tests.integration.feishu_fixtures import fixture_owner, seed_fetchval
from tests.integration.test_operations_api import TransactionDatabase
from tests.integration.test_platform_control import BASE
# Explicit re-export keeps the shared pytest fixture discoverable in this module.
from tests.integration.test_platform_control import platform as platform
from tests.integration.test_platform_configuration import model_release
from tests.integration.test_tenant_storage_quota import company
from tests.test_senseaudio import settings

pytestmark = pytest.mark.asyncio


async def policy(client, actor):
    response = await client.get(f'{BASE}/tenants/{actor.workspace_id}/ai-service')
    assert response.status_code == 200, response.text
    return response.json()


async def sharing(client, actor, enabled):
    before = await policy(client, actor)
    response = await client.post(f'{BASE}/tenants/{actor.workspace_id}/ai-service', json={
        'version_no': before['version_no'], 'shared_enabled': enabled, 'reason': '合成共享服务验收'})
    assert response.status_code == 200, response.text
    return before, response.json()


async def test_policy_scope_cas_audit_and_no_direct_company_write(platform, connection):
    client, _ = platform
    a = await company(client, connection); b = await company(client, connection)
    old, new = await sharing(client, a, False)
    assert old['shared_enabled'] and not new['shared_enabled']
    assert new['version_no'] == old['version_no'] + 1
    assert (await policy(client, b))['shared_enabled']
    stale = await client.post(f'{BASE}/tenants/{a.workspace_id}/ai-service', json={
        'version_no': old['version_no'], 'shared_enabled': True, 'reason': '过期版本'})
    assert stale.status_code == 409
    await set_request_context(connection, a)
    assert await connection.fetchval('SELECT security.ai_service_policy()') == {'shared_enabled': False}
    for sql in ['UPDATE control.tenant_ai_service SET shared_enabled=true',
                "SELECT control.ai_service_settings('invalid',NULL)",
                'SELECT security.assert_ai_service_source(NULL)']:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)
    assert await seed_fetchval(connection, "SELECT count(*) FROM control.audit WHERE workspace_id=$1::uuid AND action='tenant.ai_service'", a.workspace_id) == 1


async def test_shared_revocation_blocks_stale_settings_network_and_agent_attempts(platform, connection):
    client, _ = platform
    a = await company(client, connection); b = await company(client, connection)
    db = TransactionDatabase(connection)
    runtime = await load_runtime_configuration(db, a, settings())
    # A previously admitted request is checked again at the dispatch boundary.
    observer = DatabaseModelObserver(db, a, 'synthetic')
    attempt = await observer.start('/v1/chat/completions', 'synthetic', runtime.settings.model_api_metadata, 1)
    await sharing(client, a, False)
    with pytest.raises(AIServiceUnavailable):
        await observer.prepare_dispatch(attempt)
    await observer.finish(attempt, None, AIServiceUnavailable(), network_sent=False)
    sent = []
    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    async with httpx.AsyncClient(base_url='https://api.senseaudio.cn', transport=httpx.MockTransport(transport)) as http:
        model = SenseAudioClient(runtime.settings, client=http, observer=DatabaseModelObserver(db, a, 'synthetic'))
        with pytest.raises(AIServiceUnavailable):
            await model.chat_json(messages=[ChatMessage(role='user', content='synthetic')])
        assert not sent
        model = SenseAudioClient(settings(), client=http, observer=DatabaseModelObserver(db, b, 'synthetic'))
        assert await model.chat_json(messages=[ChatMessage(role='user', content='synthetic')]) == {'ok': True}
    assert len(sent) == 1
    with pytest.raises(AIServiceUnavailable):
        await DatabaseModelObserver(db, a, 'synthetic', provider='agent_platform').start('agent-chat-messages', 'synthetic', {}, 1)
    with pytest.raises(AIServiceUnavailable):
        await load_runtime_configuration(db, a, settings())


async def test_own_release_allowed_but_wrong_version_tenant_and_inherited_credential_denied(platform, connection):
    client, _ = platform
    a = await company(client, connection); b = await company(client, connection)
    async with fixture_owner(connection):
        for actor in [a, b]:
            await model_release(connection, UUID(actor.workspace_id), 'text', {'mode': 'custom'}, credential=True)
    await sharing(client, a, False)
    db = TransactionDatabase(connection)
    metadata = {'ai_service_source': 'tenant_purpose', 'connection_purpose': 'text', 'connection_version': 1}
    observer = DatabaseModelObserver(db, a, 'synthetic')
    attempt = await observer.start('/v1/chat/completions', 'synthetic', metadata, 1)
    await observer.prepare_dispatch(attempt)
    await observer.finish(attempt, httpx.Response(200), None)
    for change in [{'connection_version': 2}, {'connection_purpose': 'asr'}, {'ai_service_source': 'platform'}]:
        with pytest.raises(AIServiceUnavailable):
            await DatabaseModelObserver(db, a, 'synthetic').start('/v1/chat/completions', 'synthetic', {**metadata, **change}, 1)
    async with fixture_owner(connection):
        # Historical copied credentials carry a source guard and remain shared.
        await connection.execute("UPDATE config.model_api_test SET source_guard='legacy-origin' WHERE workspace_id=$1", UUID(a.workspace_id))
    with pytest.raises(AIServiceUnavailable):
        await DatabaseModelObserver(db, a, 'synthetic').start('/v1/chat/completions', 'synthetic', metadata, 1)
    # Another company's legitimate release cannot supply this tenant's source.
    async with fixture_owner(connection):
        await connection.execute('DELETE FROM config.model_api_current WHERE workspace_id=$1', UUID(a.workspace_id))
    with pytest.raises(AIServiceUnavailable):
        await DatabaseModelObserver(db, a, 'synthetic').start('/v1/chat/completions', 'synthetic', metadata, 1)


async def test_missing_policy_fails_closed_without_mutating_other_company(platform, connection):
    client, _ = platform
    a = await company(client, connection)
    async with fixture_owner(connection):
        await connection.execute('DELETE FROM control.tenant_ai_service WHERE workspace_id=$1', UUID(a.workspace_id))
    db = TransactionDatabase(connection)
    with pytest.raises(AIServiceUnavailable):
        await load_runtime_configuration(db, a, replace(settings(), senseaudio_api_key='synthetic-shared'))
    await sharing(client, a, True)
    runtime = await load_runtime_configuration(db, a, settings())
    assert runtime.settings.model_api_metadata['shared_ai_allowed'] is True


async def test_company_can_test_publish_and_use_own_key_with_shared_service_disabled(platform, connection, tmp_path):
    import base64
    import json
    from sales_backend.domain.model_api import ConnectionTest, ConnectionPublish
    from sales_backend.services.model_api import ModelApiService
    from sales_backend.services.connectivity import ConnectivityService
    client, _ = platform
    actor = await company(client, connection)
    await sharing(client, actor, False)
    db = TransactionDatabase(connection)
    keyring = tmp_path / 'synthetic-keyring.json'
    keyring.write_text(json.dumps({'keys': {'test': base64.b64encode(b'Q' * 32).decode()}}))
    keyring.chmod(0o600)
    defaults = replace(settings(), config_credential_keyring_file=str(keyring), config_credential_key_id='test',
                       senseaudio_api_key='synthetic-platform-key')
    attempts = []
    async def probe(purpose, configured, snapshot):
        assert configured.senseaudio_api_key == 'synthetic-company-key'
        observer = DatabaseModelObserver(db, actor, 'model_connection_test',
                                         operation_id=configured.model_api_metadata['connection_test_id'])
        attempt = await observer.start('/v1/chat/completions', 'synthetic', configured.model_api_metadata, 1)
        await observer.prepare_dispatch(attempt)
        await observer.finish(attempt, httpx.Response(200), None)
        attempts.append(attempt)
    service = ModelApiService(db, defaults, probe=probe)
    response = await service.list(actor)
    assert not response['service_policy']['shared_enabled']
    assert all(not x['service_authorized'] and not x['has_api_key'] for x in response['items'])
    body = ConnectionTest(request_id=uuid4(), expected_version=0, api_key='synthetic-company-key', configuration={
        'mode': 'custom', 'provider_name': 'Synthetic', 'endpoint_url': 'https://own.example/v1/chat/completions',
        'protocol': 'chat_completions', 'model': 'synthetic', 'timeout_seconds': 5, 'max_retries': 0, 'voice_id': ''})
    receipt = await service.test(actor, 'text', body)
    assert receipt['status'] == 'passed' and len(attempts) == 1
    published = await service.publish(actor, 'text', ConnectionPublish(test_id=body.request_id, expected_version=0))
    assert published['published_version'] == 1
    runtime = await load_runtime_configuration(db, actor, defaults)
    assert runtime.settings.senseaudio_api_key == 'synthetic-company-key'
    assert runtime.settings.agent_execution_policy['definition']['strategy'] == 'direct_only'
    observer = DatabaseModelObserver(db, actor, 'synthetic-business')
    attempt = await observer.start('/v1/chat/completions', 'synthetic', runtime.settings.model_api_metadata, 1)
    await observer.prepare_dispatch(attempt)
    await observer.finish(attempt, httpx.Response(200), None)
    assert (await service.list(actor))['items'][0]['service_authorized']
    # The existing-current-connection test uses the same owned source authority.
    original_probe = ModelApiService._probe
    async def current_probe(self, purpose, configured, snapshot, *, observer):
        attempt = await observer.start('/v1/chat/completions', 'synthetic', configured.model_api_metadata, 1)
        await observer.prepare_dispatch(attempt)
        await observer.finish(attempt, httpx.Response(200), None)
    ModelApiService._probe = current_probe
    try:
        result = {'id': str(uuid4())}
        await ConnectivityService(db, defaults).direct(actor, 'text', result)
        assert result['status'] == 'passed'
    finally:
        ModelApiService._probe = original_probe
    # This deliberately does not enable business AI for a newly created company.
    assert await seed_fetchval(connection, "SELECT enabled FROM control.tenant_module WHERE workspace_id=$1::uuid AND module_code='ai'", actor.workspace_id) is False
    await set_request_context(connection, actor)
    assert await connection.fetchval("SELECT security.workspace_job_enabled($1::uuid,'ai.connectivity')", actor.workspace_id)
    assert not await connection.fetchval("SELECT security.workspace_job_enabled($1::uuid,'agent.run')", actor.workspace_id)


async def test_reconcile_keeps_non_superuser_function_owner_execution(connection):
    name = 'synthetic_ai_owner_' + uuid4().hex[:12]
    async with fixture_owner(connection):
        original = await connection.fetchval('SELECT current_user')
        await connection.execute(f'CREATE ROLE {name} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        try:
            await connection.execute(f'GRANT USAGE,CREATE ON SCHEMA security TO {name}')
            await connection.execute(f'ALTER FUNCTION security.assert_ai_service_source(uuid) OWNER TO {name}')
            await connection.fetchval('SELECT security.reconcile_runtime_grants()')
            assert await connection.fetchval("SELECT has_function_privilege($1,'security.assert_ai_service_source(uuid)','EXECUTE')", name)
        finally:
            await connection.execute(f'ALTER FUNCTION security.assert_ai_service_source(uuid) OWNER TO "{original}"')
            await connection.execute(f'DROP OWNED BY {name}')
            await connection.execute(f'DROP ROLE {name}')
