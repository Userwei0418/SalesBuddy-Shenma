"""Managed credentials cannot depend on or escape through old company settings."""
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sales_backend.api import authorization
from sales_backend.config import get_settings
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.repositories.model_api import ModelApiRepository
from sales_backend.security import runtime_credentials, unified_model_key
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from sales_backend.services.runtime_config import _load_provider_configuration
from sales_backend.services.unified_model_key import write_private_json

OPERATOR = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
KEY = "sk-synthetic-deployment-credential"


@pytest.fixture
def managed(tmp_path):
    keyring = tmp_path / "keyring.json"
    write_private_json(keyring, {"keys": {"test": base64.b64encode(b"b" * 32).decode()}})
    settings = replace(get_settings(), unified_model_key_file=str(tmp_path / "key.json"),
        config_credential_keyring_file=str(keyring), config_credential_key_id="test",
        senseaudio_base_url="https://api.senseaudio.cn/v1", model_api_endpoint="",
        model_api_metadata={}, model_key_operator_ids=OPERATOR)
    write_private_json(settings.unified_model_key_file,
                       {**unified_model_key.credential(settings, KEY), "revision": 1})
    return settings


class Database:
    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        yield object()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["custom", "legacy", "disabled", "inherit"])
async def test_managed_runtime_does_not_decrypt_superseded_workspace_key(managed, monkeypatch, kind):
    snapshot = {"mode": kind, "provider_name": "fixture", "endpoint_url": "https://api.senseaudio.cn/v1/chat/completions",
                "timeout_seconds": 20, "max_retries": 0, "model": "company-model"}
    binding = None if kind == "legacy" else {"config_snapshot": snapshot, "version_no": 4}
    row = {"enabled": True, "api_key_ciphertext": b"unreadable-old-key", "provider_base_url": managed.senseaudio_base_url,
           "llm_model": "company-model", "asr_model": "company-asr", "tts_model": "company-tts",
           "prompt_overrides": {"instruction": "Keep approved company prompt"}}
    monkeypatch.setattr(ModelApiRepository, "current", AsyncMock(return_value=binding))
    monkeypatch.setattr(AgentRuntimeConfigRepository, "current", AsyncMock(return_value=row))
    old_key = AsyncMock(side_effect=RuntimeCredentialUnavailable())
    monkeypatch.setattr(runtime_credentials, "decrypt_credential", old_key)
    runtime = await _load_provider_configuration(Database(), SimpleNamespace(workspace_id="fixture"), managed)
    old_key.assert_not_awaited()
    assert runtime.prompt_overrides == row["prompt_overrides"]
    effective = unified_model_key.effective_settings(runtime.settings)
    assert effective.senseaudio_api_key == ("" if kind == "disabled" else KEY)
    if kind != "disabled":
        assert effective.llm_model == "company-model"


@pytest.mark.asyncio
async def test_unmanaged_runtime_still_uses_company_credential(managed, monkeypatch):
    row = {"enabled": True, "provider_base_url": "https://company-provider.example/v1", "llm_model": "custom",
           "asr_model": "asr", "tts_model": "tts", "prompt_overrides": {}}
    monkeypatch.setattr(ModelApiRepository, "current", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentRuntimeConfigRepository, "current", AsyncMock(return_value=row))
    old_key = AsyncMock(return_value="company-key")
    monkeypatch.setattr(runtime_credentials, "decrypt_credential", old_key)
    runtime = await _load_provider_configuration(Database(), SimpleNamespace(workspace_id="fixture"),
                                                 replace(managed, unified_model_key_file=""))
    old_key.assert_awaited_once()
    assert runtime.settings.senseaudio_api_key == "company-key"
    assert runtime.settings.senseaudio_base_url == row["provider_base_url"]


@pytest.mark.parametrize("endpoint", [
    "https://customer-provider.example/v1/chat/completions", "http://api.senseaudio.cn/v1",
    "https://api.senseaudio.cn.attacker.example/v1", "https://api.senseaudio.cn:444/v1",
    "https://user@api.senseaudio.cn/v1", "https://api.senseaudio.cn/v1#fragment",
    "https://api.senseaudio.cn\n/v1", "https://api.senseaudio.cn:invalid/v1",
])
@pytest.mark.parametrize("field", ["senseaudio_base_url", "model_api_endpoint"])
def test_managed_key_rejects_unapproved_target_before_decryption(managed, monkeypatch, endpoint, field):
    def forbidden(*args):
        pytest.fail("Do not even decrypt the global key for an unapproved target")
    monkeypatch.setattr(unified_model_key, "decrypt_state", forbidden)
    with pytest.raises(RuntimeCredentialUnavailable):
        unified_model_key.effective_settings(replace(managed, **{field: endpoint}))


@pytest.mark.parametrize("endpoint", ["https://api.senseaudio.cn/v1", "https://api.senseaudio.cn:443/v1/chat/completions"])
def test_managed_key_accepts_fixed_https_provider(managed, endpoint):
    assert unified_model_key.effective_settings(replace(managed, model_api_endpoint=endpoint)).senseaudio_api_key == KEY


def test_disabled_target_stays_disabled_without_decrypting_global_key(managed, monkeypatch):
    settings = replace(managed, senseaudio_api_key="", model_api_endpoint="https://old-provider.example/v1",
                       model_api_metadata={"connection_mode": "disabled"})
    monkeypatch.setattr(unified_model_key, "decrypt_state", lambda *args: pytest.fail("disabled purpose"))
    assert unified_model_key.effective_settings(settings) is settings


def test_account_permission_api_cannot_deny_or_replace_maintenance_authorization(managed, monkeypatch):
    import sales_backend.config as config
    monkeypatch.setattr(config, "get_settings", lambda: managed)
    write = AsyncMock(return_value={"saved": True})
    monkeypatch.setattr(authorization, "management_write", write)
    app = FastAPI()
    app.include_router(authorization.router)
    app.dependency_overrides[authorization.manage_accounts] = lambda: SimpleNamespace(actor=SimpleNamespace(user_id=OTHER))
    app.dependency_overrides[authorization.get_database] = lambda: Database()
    client = TestClient(app)
    body = {"roles": [], "overrides": [{"permission": "access.console", "effect": "deny"}],
            "version_no": 0, "reason": "synthetic test"}
    response = client.put(f"/api/v1/console/permissions/accounts/{OPERATOR}", json=body)
    assert response.status_code == 403
    write.assert_not_awaited()
    # The guard must not disable ordinary customer permission administration.
    response = client.put(f"/api/v1/console/permissions/accounts/{OTHER}", json=body)
    assert response.status_code == 200
    write.assert_awaited_once()
