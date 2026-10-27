"""Static setup gates through real SQL/HTTP; no live providers or Feishu calls."""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.main import app
from sales_backend.services.platform_control import digest
from tests.integration.feishu_fixtures import fixture_owner, seed_fetchval
from tests.integration.test_operations_api import client_for
from tests.integration.test_platform_control import BASE, PASSWORD, tenant, transition
# Explicit re-export keeps the shared pytest fixture discoverable in this module.
from tests.integration.test_platform_control import platform as platform
from tests.integration.test_platform_configuration import model_release, read
from tests.integration.test_tenant_ai_service import sharing
from tests.integration.test_tenant_capabilities import change, settings as modules
from tests.integration.test_tenant_storage_quota import company

pytestmark = pytest.mark.asyncio


def shared(monkeypatch, configured):
    monkeypatch.setattr(app.state, 'settings', replace(app.state.settings,
        senseaudio_base_url='https://synthetic-provider.invalid',
        senseaudio_api_key='synthetic-shared-key' if configured else '',
        llm_model='synthetic', asr_model='', tts_model=''))


def checks(report):
    return {c['code']: c for c in report['configuration_readiness']['checks']}


async def own_model(connection, actor):
    async with fixture_owner(connection):
        await model_release(connection, UUID(actor.workspace_id), 'text', dict(
            mode='custom', provider_name='synthetic', endpoint_url='https://private.invalid/v1/chat/completions',
            model='synthetic', protocol='chat_completions'), credential=True)


async def test_basic_activation_and_atomic_enable_refusal_then_shared_configuration(platform, connection, monkeypatch):
    client, _ = platform
    shared(monkeypatch, False)
    actor = await company(client, connection)
    report = await read(client, actor.workspace_id)
    assert report['configuration_readiness']['required_ready']
    assert not checks(report)['text']['configuration_ready']
    before = await modules(client, actor.workspace_id)
    audits = await seed_fetchval(connection, 'SELECT count(*) FROM control.audit WHERE workspace_id=$1::uuid', actor.workspace_id)
    denied = await change(client, before, 'ai', True)
    assert denied.status_code == 409 and denied.json()['detail'] == 'AI_CONFIGURATION_REQUIRED'
    assert await modules(client, actor.workspace_id) == before
    assert await seed_fetchval(connection, 'SELECT count(*) FROM control.audit WHERE workspace_id=$1::uuid', actor.workspace_id) == audits
    forged = await client.post(f'{BASE}/tenants/{actor.workspace_id}/modules', json={
        'version_no': before['version_no'], 'modules': [], 'reason': 'synthetic', 'instance_presence': {'text': True}})
    assert forged.status_code == 422
    shared(monkeypatch, True)
    allowed = await change(client, before, 'ai', True)
    assert allowed.status_code == 200, allowed.text
    report = await read(client, actor.workspace_id)
    facts = checks(report)
    assert facts['text']['required'] and facts['text']['configuration_ready']
    assert not facts['asr']['required'] and not facts['asr']['configuration_ready']
    assert not facts['tts']['required'] and not facts['tts']['configuration_ready']
    assert report['configuration_readiness']['required_ready']
    assert 'synthetic-shared-key' not in str(report) and 'synthetic-provider' not in str(report)


async def test_own_model_is_tenant_scoped_and_resume_can_recover_by_disabling_module(platform, connection, monkeypatch):
    client, _ = platform
    shared(monkeypatch, False)
    a = await company(client, connection); b = await company(client, connection)
    await sharing(client, a, False)
    await own_model(connection, a)
    assert (await change(client, await modules(client, a.workspace_id), 'ai', True)).status_code == 200
    assert (await change(client, await modules(client, b.workspace_id), 'ai', True)).status_code == 409
    assert (await transition(client, await tenant(client, a.workspace_id), 'suspended')).status_code == 200
    async with fixture_owner(connection):
        await connection.execute('DELETE FROM config.model_api_current WHERE workspace_id=$1::uuid', a.workspace_id)
    paused = await tenant(client, a.workspace_id)
    denied = await transition(client, paused, 'active')
    assert denied.status_code == 409 and denied.json()['detail'] == 'AI_CONFIGURATION_REQUIRED'
    assert await tenant(client, a.workspace_id) == paused
    assert not (await read(client, a.workspace_id))['configuration_readiness']['required_ready']
    assert (await change(client, await modules(client, a.workspace_id), 'ai', False)).status_code == 200
    assert (await transition(client, await tenant(client, a.workspace_id), 'active')).status_code == 200
    assert (await tenant(client, b.workspace_id))['status'] == 'active'


async def test_expiry_renewal_and_old_sql_entry_cannot_bypass_shared_configuration(platform, connection, monkeypatch):
    client, database = platform
    shared(monkeypatch, False)
    actor = await company(client, connection)
    past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    assert (await change(client, await modules(client, actor.workspace_id), 'ai', True, past)).status_code == 200
    before = await modules(client, actor.workspace_id)
    assert (await change(client, before, 'ai', True)).status_code == 409
    assert await modules(client, actor.workspace_id) == before
    shared(monkeypatch, True)
    assert (await change(client, before, 'ai', True)).status_code == 200
    assert (await transition(client, await tenant(client, actor.workspace_id), 'suspended')).status_code == 200
    row = await tenant(client, actor.workspace_id)
    token = digest(client.cookies.get('sales_platform_session'))
    async with database.connection() as conn:
        for sql, args in [
            ("SELECT control.transition($1,$2,$3,'active','synthetic')", [token, UUID(actor.workspace_id), row['version_no']]),
            ("SELECT control.transition($1,$2,$3,'active','synthetic',$4::jsonb)", [token, UUID(actor.workspace_id), row['version_no'], {'text': 'true'}]),
        ]:
            with pytest.raises(asyncpg.PostgresError, match='AI_CONFIGURATION_REQUIRED'):
                async with conn.transaction(): await conn.fetchval(sql, *args)
        for signature in ['control.transition_before_integration_readiness(text,uuid,integer,text,text)',
                          'control.save_modules_before_integration_readiness(text,uuid,integer,jsonb,text)']:
            assert not await conn.fetchval('SELECT has_function_privilege(current_user,$1,\'EXECUTE\')', signature)
    assert await tenant(client, actor.workspace_id) == row


async def test_company_can_save_feishu_before_module_but_cannot_enable_or_sync(platform, connection, monkeypatch, tmp_path):
    client, _ = platform
    actor = await company(client, connection)
    initial = await modules(client, actor.workspace_id)
    response = await change(client, initial, 'feishu', True)
    assert response.status_code == 409 and response.json()['detail'] == 'FEISHU_CONFIGURATION_REQUIRED'
    keyring = tmp_path/'synthetic-keys.json'
    keyring.write_text(json.dumps({'keys': {'test': base64.b64encode(b'R'*32).decode()}})); keyring.chmod(0o600)
    company_code = (await tenant(client, actor.workspace_id))['company_code']
    async with await client_for(connection) as admin:
        monkeypatch.setattr(app.state, 'settings', replace(app.state.settings,
            config_credential_keyring_file=str(keyring), config_credential_key_id='test'))
        login = await admin.post('/api/v1/console/auth/login', json={'workspace': company_code,'account_code':'ADMIN','password':PASSWORD})
        assert login.status_code == 200, login.text
        admin.headers['Authorization'] = 'Bearer '+login.json()['access_token']
        path = '/api/v1/console/feishu-sync'
        assert (await admin.get(path)).status_code == 200
        body = {'expected_revision': 0, 'app_secret': 'synthetic-feishu-secret', 'config': {
            'workspace_id': actor.workspace_id,'connection_id':str(uuid4()),'revision':1,'enabled':False,
            'app_id':'syntheticapp','base_token':'syntheticbase','credential_ref':str(uuid4()),
            'direction':'system_to_base','mappings':{'customer': {'enabled':True,'table_id':'tblSynthetic',
              'id_field_id':'fldId','fields':{'record_status':'fldStatus'}}}}}
        saved = await admin.put(path, json=body)
        assert saved.status_code == 200, saved.text
        assert (await admin.post(path+'/validate', json={'expected_revision':1})).status_code == 403
        report = await read(client, actor.workspace_id)
        assert checks(report)['feishu']['configuration_ready'] and not checks(report)['feishu']['required']
        assert not report['feishu']['validated'] and not report['feishu']['enabled']
        assert (await change(client, await modules(client, actor.workspace_id), 'feishu', True)).status_code == 200
        assert (await admin.post(path+'/enable', json={'expected_revision':1})).status_code == 422
        # No validation was queued, and no external synchronization was enabled.
        async with fixture_owner(connection):
            row = await connection.fetchrow('SELECT enabled,validation_requested FROM config.feishu_connection WHERE workspace_id=$1::uuid', actor.workspace_id)
            assert not row['enabled'] and not row['validation_requested']
            assert await connection.fetchval('SELECT count(*) FROM ops.feishu_event WHERE workspace_id=$1::uuid', actor.workspace_id)==0
        assert (await change(client, await modules(client, actor.workspace_id), 'feishu', False)).status_code == 200
        assert (await admin.get(path)).status_code == 200
        assert (await admin.post(path+'/enable', json={'expected_revision':1})).status_code == 403


@pytest.mark.parametrize('mode', ['disabled', 'unexpected'])
async def test_explicitly_unavailable_model_is_not_overridden_by_shared_defaults(platform, connection, monkeypatch, mode):
    client, _ = platform
    shared(monkeypatch, True)
    actor = await company(client, connection)
    async with fixture_owner(connection):
        await model_release(connection, UUID(actor.workspace_id), 'text', {'mode':mode})
    report = await read(client, actor.workspace_id)
    assert not checks(report)['text']['configuration_ready']
    assert (await change(client, await modules(client, actor.workspace_id), 'ai', True)).status_code == 409


async def test_missing_feishu_mapping_fields_block_enable_and_projection_is_readonly(platform, connection):
    client, _ = platform
    actor = await company(client, connection)
    cid = uuid4()
    async with fixture_owner(connection):
        await connection.execute('''INSERT INTO config.feishu_connection(id,workspace_id,revision,settings,updated_by)
            VALUES($1,$2::uuid,1,$3,$4::uuid)''', cid, actor.workspace_id, {
            'workspace_id': actor.workspace_id, 'connection_id': str(cid),
            'app_id':'private-marker','base_token':'private-marker','credential_ref':str(uuid4()),
            'direction':'system_to_base','mappings':{'customer':{'enabled':True}}}, actor.user_id)
        await connection.execute('INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) VALUES($1,$2::uuid,$3,$4)',
                                 cid, actor.workspace_id, bytes(29),'synthetic')
    report = await read(client, actor.workspace_id)
    assert report['feishu']['enabled_mappings'] == 1
    assert checks(report)['feishu']['issues'] == ['mapping_fields_missing']
    assert 'private-marker' not in str(report)
    before = await modules(client, actor.workspace_id)
    assert (await change(client, before, 'feishu', True)).status_code == 409
    assert await modules(client, actor.workspace_id) == before
    assert not await seed_fetchval(connection, 'SELECT validation_requested FROM config.feishu_connection WHERE id=$1', cid)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE workspace_id=$1::uuid', actor.workspace_id) == 0
