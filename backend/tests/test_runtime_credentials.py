import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sales_backend.config import get_settings
from sales_backend.contracts.models import AgentRuntimeConfigRollback, AgentRuntimeConfigUpdate
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.security.runtime_credentials import (
    CredentialCipher,
    RuntimeCredentialUnavailable,
    decrypt_credential,
)
from sales_backend.services.runtime_config import _load_provider_configuration
from sales_backend.services.runtime_config_management import ConfigVersionConflict, RuntimeConfigService


@pytest.fixture
def actor():
    return ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role="administrator", data_scope="workspace")


@pytest.fixture
def settings(tmp_path):
    file = tmp_path / "keyring.json"
    file.write_text(json.dumps({"keys": {"new": base64.b64encode(b"A" * 32).decode(),
                                        "old": base64.b64encode(b"B" * 32).decode()}}))
    file.chmod(0o600)
    return replace(get_settings(), config_credential_keyring_file=str(file), config_credential_key_id="new",
                   config_credential_legacy_key_file="", access_token_secret="unrelated-login-signature",
                   senseaudio_api_key="fixture-environment-provider")


def test_gcm_roundtrip_random_nonce_and_key_rotation(settings, actor):
    cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, "old")
    first = cipher.encrypt(actor.workspace_id, "fixture-provider-credential")
    second = cipher.encrypt(actor.workspace_id, "fixture-provider-credential")
    assert first["api_key_ciphertext"] != second["api_key_ciphertext"]
    rotated = CredentialCipher.from_file(settings.config_credential_keyring_file, "new")
    assert rotated.decrypt(actor.workspace_id, "old", first["api_key_ciphertext"]) == "fixture-provider-credential"
    for workspace, key, ciphertext in [
        (str(uuid4()), "old", first["api_key_ciphertext"]),
        (actor.workspace_id, "new", first["api_key_ciphertext"]),
        (actor.workspace_id, "absent", first["api_key_ciphertext"]),
        (actor.workspace_id, "old", first["api_key_ciphertext"][:-1] + bytes([first["api_key_ciphertext"][-1] ^ 1])),
        (actor.workspace_id, "old", b"broken"),
    ]:
        with pytest.raises(RuntimeCredentialUnavailable, match="^RUNTIME_CREDENTIAL_UNAVAILABLE$"):
            rotated.decrypt(workspace, key, ciphertext)


@pytest.mark.parametrize("content", ['{}', 'null', '{"keys": []}', '{"keys": {"new": "bad"}}'])
def test_keyring_invalid_data_is_controlled(settings, content):
    from pathlib import Path
    Path(settings.config_credential_keyring_file).write_text(content)
    with pytest.raises(RuntimeCredentialUnavailable):
        CredentialCipher.from_file(settings.config_credential_keyring_file, "new")


def test_keyring_requires_private_permissions(settings):
    from pathlib import Path
    Path(settings.config_credential_keyring_file).chmod(0o644)
    with pytest.raises(RuntimeCredentialUnavailable):
        CredentialCipher.from_file(settings.config_credential_keyring_file, "new")


@pytest.mark.asyncio
async def test_legacy_never_implicitly_uses_login_secret(settings, actor):
    connection = SimpleNamespace(fetchval=AsyncMock())
    with pytest.raises(RuntimeCredentialUnavailable):
        await decrypt_credential(connection, {"api_key_ciphertext": b"historical",
          "cipher_format": "pgp-v1", "encryption_key_id": "legacy-pgp", "workspace_id": actor.workspace_id}, settings)
    connection.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_get_masks_without_loading_keyring_or_ciphertext(settings, actor):
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value={"has_api_key": True, "api_key_tail": "tail",
       "cipher_format": "aes256gcm-v1", "encryption_key_id": "lost", "revision_no": 7}))
    settings = replace(settings, config_credential_keyring_file="/missing")
    result = await RuntimeConfigService(settings).get(connection, actor)
    assert result["revision_no"] == 7 and result["api_key_masked"] == "••••••••tail"
    sql = connection.fetchrow.await_args.args[0]
    assert "pgp_sym_decrypt" not in sql and "cfg.api_key_ciphertext," not in sql
    assert settings.senseaudio_api_key not in json.dumps(result)


@pytest.mark.asyncio
async def test_runtime_bad_credential_does_not_switch_provider_or_erase_prompt(settings, actor, monkeypatch):
    row = {"workspace_id": actor.workspace_id, "enabled": True, "provider_base_url": "https://configured.invalid",
           "api_key_ciphertext": b"bad", "cipher_format": "aes256gcm-v1", "encryption_key_id": "new",
           "prompt_overrides": {"visit_entry": "Configured instruction"}}
    from sales_backend.repositories.model_api import ModelApiRepository
    monkeypatch.setattr(ModelApiRepository, "current", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentRuntimeConfigRepository, "current", AsyncMock(return_value=row))
    class DB:
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            yield object()
    with pytest.raises(RuntimeCredentialUnavailable):
        await _load_provider_configuration(DB(), actor, settings)
    row["enabled"] = False
    runtime = await _load_provider_configuration(DB(), actor, settings)
    assert runtime.settings is settings and runtime.prompt_overrides == {}


@pytest.mark.asyncio
async def test_stale_save_or_rollback_never_writes(settings, actor):
    repo = SimpleNamespace(lock=AsyncMock(return_value={"revision_no": 3}), publish=AsyncMock(), release=AsyncMock())
    service = RuntimeConfigService(settings, repo)
    data = {"expected_version": 2, "provider_base_url": "https://provider.invalid", "llm_model": "llm",
            "asr_model": "asr", "tts_model": "tts", "prompt_overrides": {}, "enabled": True}
    with pytest.raises(ConfigVersionConflict):
        await service.save(None, actor, data)
    with pytest.raises(ConfigVersionConflict):
        await service.rollback(None, actor, 1, 2)
    repo.publish.assert_not_awaited()
    repo.release.assert_not_awaited()


def test_expected_version_is_required_and_bounded():
    for model in (AgentRuntimeConfigRollback, AgentRuntimeConfigUpdate):
        with pytest.raises(ValidationError):
            model.model_validate({})
    for version in (-1, 2147483647):
        with pytest.raises(ValidationError):
            AgentRuntimeConfigRollback(expected_version=version)


def test_runtime_credential_error_has_sanitized_503():
    from fastapi import FastAPI

    from sales_backend.main import runtime_credential_unavailable_handler
    test_app = FastAPI()
    test_app.add_exception_handler(RuntimeCredentialUnavailable, runtime_credential_unavailable_handler)
    @test_app.get("/")
    def fail():
        raise RuntimeCredentialUnavailable()
    response = TestClient(test_app).get("/")
    assert response.status_code == 503 and response.json()["code"] == "RUNTIME_CREDENTIAL_UNAVAILABLE"


@pytest.mark.parametrize("path,payload", [
    ("/api/v1/admin/agent-config", {"provider_base_url": "https://provider.invalid", "llm_model": "llm",
                                 "asr_model": "asr", "tts_model": "tts"}),
    ("/api/v1/admin/agent-config/releases/1/rollback", {}),
    ("/api/v1/admin/agent-config/releases/2147483647/rollback", {"expected_version": 1}),
])
def test_admin_api_requires_cas_version_before_database(actor, settings, path, payload, monkeypatch):
    from sales_backend.api.dependencies import get_database
    from sales_backend.api.dependencies import get_settings as settings_dependency
    from sales_backend.api.management_dependencies import get_system_identity
    from sales_backend.main import app
    from tests.authorization_fixtures import install_http_authorization
    install_http_authorization(monkeypatch, app, actor, {p: "workspace" for p in ("access.console", "ai.config_publish", "ai.config_rollback")})
    class NoDatabase:
        def transaction(self, *args, **kwargs):
            raise AssertionError("invalid input reached database")
    app.dependency_overrides[get_system_identity] = lambda: SimpleNamespace(actor=actor)
    app.dependency_overrides[get_database] = lambda: NoDatabase()
    app.dependency_overrides[settings_dependency] = lambda: settings
    try:
        response = TestClient(app).request("POST" if path.endswith("rollback") else "PUT", path, json=payload)
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_admin_api_version_conflict_is_409(actor, settings, monkeypatch):
    from sales_backend.api.dependencies import get_database
    from sales_backend.api.dependencies import get_settings as settings_dependency
    from sales_backend.api.management_dependencies import get_system_identity
    from sales_backend.main import app
    from tests.authorization_fixtures import install_http_authorization
    install_http_authorization(monkeypatch, app, actor, {p: "workspace" for p in ("access.console", "ai.config_publish", "ai.config_rollback")})
    class DB:
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            yield object()
    monkeypatch.setattr(RuntimeConfigService, "rollback", AsyncMock(side_effect=ConfigVersionConflict()))
    app.dependency_overrides[get_system_identity] = lambda: SimpleNamespace(actor=actor)
    app.dependency_overrides[get_database] = lambda: DB()
    app.dependency_overrides[settings_dependency] = lambda: settings
    try:
        response = TestClient(app).post("/api/v1/admin/agent-config/releases/1/rollback", json={"expected_version": 2})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "CONFIG_VERSION_CONFLICT"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_maintenance_top_level_never_prints_exception_or_secret(capsys, monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / "scripts/runtime_config_credentials.py"
    spec = importlib.util.spec_from_file_location("runtime_config_maintenance_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "run", AsyncMock(side_effect=RuntimeError("secret-bearing-db-parameter")))
    assert await module.bounded_run(SimpleNamespace()) == 1
    output = capsys.readouterr()
    assert "secret-bearing" not in output.err
    assert json.loads(output.err) == {"status": "failed", "code": "RUNTIME_CONFIG_MAINTENANCE_FAILED"}


@pytest.mark.asyncio
async def test_maintenance_lookup_uses_formal_administrator_not_demo():
    from sales_backend.repositories.identity import IdentityRepository
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    assert await IdentityRepository().find_maintenance_administrator(
        connection, workspace_external_id="fixture", account_code=" admin001 ") is None
    query, workspace, account = connection.fetchrow.await_args.args
    assert "resolve_account_actor" in query and "'administrator'" in query
    assert "resolve_demo_actor" not in query and account == "ADMIN001" and workspace == "fixture"


@pytest.mark.asyncio
async def test_config_mutation_lock_requires_real_transaction():
    conn = SimpleNamespace(is_in_transaction=lambda: False, execute=AsyncMock())
    with pytest.raises(RuntimeError, match="explicit transaction"):
        await AgentRuntimeConfigRepository().lock(conn, SimpleNamespace(workspace_id=str(uuid4())))
    conn.execute.assert_not_awaited()
