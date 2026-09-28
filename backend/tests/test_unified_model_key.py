import asyncio
import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.api.unified_model_key import router, service
from sales_backend.config import get_settings
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from sales_backend.security.unified_model_key import credential, decrypt_state, effective_settings, read_state
from sales_backend.services.model_api import ModelApiError
from sales_backend.services.unified_model_key import UnifiedModelKeyService, write_private_json

OLD_KEY = "sk-old-fixture-credential"
NEW_KEY = "sk-new-fixture-credential"
OWNER = SimpleNamespace(user_id="00000000-0000-0000-0000-000000000001")
OTHER = SimpleNamespace(user_id="00000000-0000-0000-0000-000000000002")


@pytest.fixture
def settings(tmp_path):
    keyring = tmp_path / "keyring.json"
    write_private_json(keyring, {"keys": {"fixture": base64.b64encode(b"a" * 32).decode()}})
    settings = replace(get_settings(), unified_model_key_file=str(tmp_path / "unified.json"),
                       model_key_operator_ids=OWNER.user_id, config_credential_keyring_file=str(keyring),
                       config_credential_key_id="fixture", senseaudio_api_key="stale-env-key")
    write_private_json(settings.unified_model_key_file, {**credential(settings, OLD_KEY), "revision": 1})
    return settings


async def no_probe(key):
    assert key == NEW_KEY


@pytest.mark.asyncio
async def test_authorized_rotation_updates_both_targets_and_masks_output(settings):
    requests = []
    async def remote(body):
        requests.append(body)
        return {"status": "applied" if body["action"] == "apply" else "prepared"}
    manager = UnifiedModelKeyService(settings, remote=remote, probe=no_probe)
    result = await manager.rotate(OWNER, NEW_KEY, 1)
    assert [r["action"] for r in requests] == ["prepare", "apply"]
    assert requests[1]["api_key"] == NEW_KEY
    assert result["revision"] == 2 and result["can_manage"]
    assert NEW_KEY not in json.dumps(result)
    assert NEW_KEY not in manager.path.read_text()
    assert manager.path.stat().st_mode & 0o777 == 0o600
    assert decrypt_state(settings, read_state(settings)) == NEW_KEY
    assert not manager.journal.exists()


@pytest.mark.asyncio
async def test_customer_cannot_rotate_even_with_configuration_permission(settings):
    manager = UnifiedModelKeyService(settings)
    assert manager.status(OTHER)["can_manage"] is False
    with pytest.raises(ModelApiError) as exc:
        await manager.rotate(OTHER, NEW_KEY, 1)
    assert exc.value.status == 403
    assert decrypt_state(settings, read_state(settings)) == OLD_KEY


@pytest.mark.asyncio
async def test_failed_probe_preserves_local_and_does_not_touch_agent(settings):
    async def probe(key):
        raise ModelApiError("验证失败", 422)
    async def remote(body):
        pytest.fail("failed key must not reach Agent configuration")
    manager = UnifiedModelKeyService(settings, remote=remote, probe=probe)
    with pytest.raises(ModelApiError):
        await manager.rotate(OWNER, NEW_KEY, 1)
    assert decrypt_state(settings, read_state(settings)) == OLD_KEY
    assert not manager.journal.exists()


@pytest.mark.asyncio
async def test_agent_failure_is_compensated_and_local_key_is_preserved(settings):
    actions = []
    async def remote(body):
        actions.append(body["action"])
        if body["action"] == "apply":
            raise ModelApiError("同步失败", 503)
    manager = UnifiedModelKeyService(settings, remote=remote, probe=no_probe)
    with pytest.raises(ModelApiError):
        await manager.rotate(OWNER, NEW_KEY, 1)
    assert actions == ["prepare", "apply", "rollback"]
    assert decrypt_state(settings, read_state(settings)) == OLD_KEY
    assert not manager.journal.exists()


@pytest.mark.asyncio
async def test_interrupted_rotation_recovers_before_next_publish(settings):
    actions = []
    async def remote(body):
        actions.append(body["action"])
    manager = UnifiedModelKeyService(settings, remote=remote, probe=no_probe)
    write_private_json(manager.journal, {"rotation_id": "interrupted"})
    await manager.rotate(OWNER, NEW_KEY, 1)
    assert actions == ["rollback", "prepare", "apply"]
    assert decrypt_state(settings, read_state(settings)) == NEW_KEY


@pytest.mark.asyncio
async def test_local_write_failure_rolls_agent_back(settings, monkeypatch):
    import sales_backend.services.unified_model_key as module
    actions = []
    async def remote(body):
        actions.append(body["action"])
    original = module.write_private_json
    def fail_publish(path, value):
        if str(path) == settings.unified_model_key_file:
            raise OSError("simulated disk failure")
        original(path, value)
    monkeypatch.setattr(module, "write_private_json", fail_publish)
    manager = UnifiedModelKeyService(settings, remote=remote, probe=no_probe)
    with pytest.raises(OSError):
        await manager.rotate(OWNER, NEW_KEY, 1)
    assert actions == ["prepare", "apply", "rollback"]
    assert decrypt_state(settings, read_state(settings)) == OLD_KEY


@pytest.mark.asyncio
async def test_failure_after_atomic_activation_does_not_undo_agent(settings, monkeypatch):
    import sales_backend.services.unified_model_key as module
    actions = []
    async def remote(body):
        actions.append(body["action"])
    original = module.write_private_json
    def fail_after_publish(path, value):
        original(path, value)
        if str(path) == settings.unified_model_key_file:
            raise OSError("simulated directory fsync failure")
    monkeypatch.setattr(module, "write_private_json", fail_after_publish)
    manager = UnifiedModelKeyService(settings, remote=remote, probe=no_probe)
    with pytest.raises(OSError):
        await manager.rotate(OWNER, NEW_KEY, 1)
    assert actions == ["prepare", "apply"]
    assert decrypt_state(settings, read_state(settings)) == NEW_KEY
    assert manager.journal.exists()
    await manager._recover()
    assert actions == ["prepare", "apply"] and not manager.journal.exists()


@pytest.mark.asyncio
async def test_failed_compensation_preserves_recovery_intent(settings):
    async def remote(body):
        if body["action"] in {"apply", "rollback"}:
            raise ModelApiError("network unavailable", 503)
    manager = UnifiedModelKeyService(settings, remote=remote, probe=no_probe)
    with pytest.raises(ModelApiError):
        await manager.rotate(OWNER, NEW_KEY, 1)
    assert manager.journal.exists()
    assert manager.status(OWNER)["rotation_status"] == "pending"
    assert NEW_KEY not in manager.journal.read_text()
    assert decrypt_state(settings, read_state(settings)) == OLD_KEY


@pytest.mark.asyncio
async def test_concurrent_rotation_and_stale_revision_cannot_overwrite(settings):
    entered, release = asyncio.Event(), asyncio.Event()
    async def probe(key):
        entered.set()
        await release.wait()
    async def remote(body):
        return {}
    manager = UnifiedModelKeyService(settings, remote=remote, probe=probe)
    active = asyncio.create_task(manager.rotate(OWNER, NEW_KEY, 1))
    await entered.wait()
    with pytest.raises(ModelApiError) as exc:
        await UnifiedModelKeyService(settings, remote=remote, probe=no_probe).rotate(OWNER, NEW_KEY, 1)
    assert exc.value.status == 409
    release.set()
    await active
    with pytest.raises(ModelApiError) as exc:
        await manager.rotate(OWNER, NEW_KEY, 1)
    assert exc.value.status == 409


def test_unified_credential_overrides_cached_env_and_workspace_keys_but_not_disabled(settings):
    assert effective_settings(settings).senseaudio_api_key == OLD_KEY
    settings = replace(settings, senseaudio_api_key="workspace-custom-key", model_api_metadata={"connection_mode": "custom"})
    client = SenseAudioClient(settings)
    assert client._settings.senseaudio_api_key == OLD_KEY
    asyncio.run(client.close())
    disabled = replace(settings, senseaudio_api_key="", model_api_metadata={"connection_mode": "disabled"})
    assert effective_settings(disabled).senseaudio_api_key == ""


def test_missing_or_public_unified_file_fails_closed(settings):
    from pathlib import Path
    path = Path(settings.unified_model_key_file)
    path.chmod(0o644)
    with pytest.raises(RuntimeCredentialUnavailable):
        effective_settings(settings)
    path.unlink()
    with pytest.raises(RuntimeCredentialUnavailable):
        effective_settings(settings)


def test_http_contract_rejects_customer_and_never_echoes_invalid_secret(settings):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_system_identity] = lambda: SimpleNamespace(actor=OTHER)
    app.dependency_overrides[service] = lambda: UnifiedModelKeyService(settings)
    client = TestClient(app)
    response = client.post("/api/v1/admin/unified-model-key", json={"api_key": NEW_KEY, "expected_revision": 1})
    assert response.status_code == 403 and NEW_KEY not in response.text
    response = client.post("/api/v1/admin/unified-model-key", json={"api_key": NEW_KEY, "expected_revision": "bad"})
    assert response.status_code == 422 and NEW_KEY not in response.text
    assert client.get("/api/v1/admin/unified-model-key").json()["can_manage"] is False
