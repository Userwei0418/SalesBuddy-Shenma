from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.ai_service_policy import AIServiceUnavailable, shared_allowed
from sales_backend.services.runtime_config import load_runtime_configuration
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.repositories.model_api import ModelApiRepository
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.security.runtime_credentials import CredentialCipher
from tests.test_model_api import ACTOR, SECRET, config
# Explicit re-export keeps the shared pytest fixture discoverable in this module.
from tests.test_model_api import settings as settings


class DB:
    def __init__(self, allowed):
        self.connection = SimpleNamespace(fetchval=AsyncMock(return_value={'shared_enabled': allowed}))

    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        yield self.connection


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, {}, {'shared_enabled': 'true'}, {'shared_enabled': 1}])
async def test_only_explicit_boolean_policy_grants_access(value):
    assert not await shared_allowed(SimpleNamespace(fetchval=AsyncMock(return_value=value)))


@pytest.mark.asyncio
@pytest.mark.parametrize('purpose', ['text', 'asr', 'tts'])
async def test_shared_off_missing_own_configuration_never_uses_global_key(settings, monkeypatch, purpose):
    monkeypatch.setattr(ModelApiRepository, 'current', AsyncMock(return_value=None))
    monkeypatch.setattr(AgentRuntimeConfigRepository, 'current', AsyncMock(return_value=None))
    with pytest.raises(AIServiceUnavailable):
        await load_runtime_configuration(DB(False), ACTOR, replace(settings, senseaudio_api_key='synthetic-global'), purpose=purpose)


@pytest.mark.asyncio
async def test_own_key_kept_but_company_execution_rule_cannot_reenable_shared_agents(settings, monkeypatch):
    cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, 'test')
    row = {'workspace_id': ACTOR.workspace_id, 'config_snapshot': config(), 'version_no': 1,
           'credential_owned': True, **cipher.encrypt(ACTOR.workspace_id, SECRET)}
    monkeypatch.setattr(ModelApiRepository, 'current', AsyncMock(return_value=row))
    monkeypatch.setattr(AgentRuntimeConfigRepository, 'current', AsyncMock(return_value=None))
    from sales_backend.domain.company_rules import policy_snapshot
    monkeypatch.setattr(CompanyRulesRepository, 'active', AsyncMock(side_effect=lambda _, code: policy_snapshot(code)))
    runtime = await load_runtime_configuration(DB(False), ACTOR, settings, capability='chatbi')
    assert runtime.settings.senseaudio_api_key == SECRET
    assert runtime.settings.agent_execution_policy['definition']['strategy'] == 'direct_only'
    assert not runtime.settings.agent_fde_chatbi_api_key
    assert runtime.settings.model_api_metadata['ai_service_source'] == 'tenant_purpose'
    row['credential_owned'] = False
    with pytest.raises(AIServiceUnavailable):
        await load_runtime_configuration(DB(False), ACTOR, settings)


@pytest.mark.asyncio
async def test_legacy_key_requires_complete_company_fields_when_shared_off(settings, monkeypatch):
    cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, 'test')
    row = {'workspace_id': ACTOR.workspace_id, 'enabled': True, 'provider_base_url': 'https://own.example',
           'llm_model': 'own-model', 'asr_model': '', 'tts_model': '', 'revision_no': 1, 'prompt_overrides': {},
           **cipher.encrypt(ACTOR.workspace_id, SECRET)}
    monkeypatch.setattr(ModelApiRepository, 'current', AsyncMock(return_value=None))
    monkeypatch.setattr(AgentRuntimeConfigRepository, 'current', AsyncMock(return_value=row))
    runtime = await load_runtime_configuration(DB(False), ACTOR, settings)
    assert runtime.settings.senseaudio_api_key == SECRET
    assert runtime.settings.model_api_metadata['ai_service_source'] == 'tenant_legacy'
    with pytest.raises(AIServiceUnavailable):
        await load_runtime_configuration(DB(False), ACTOR, settings, purpose='asr')
