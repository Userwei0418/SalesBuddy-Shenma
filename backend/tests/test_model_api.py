import asyncio
import base64
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from sales_backend.api.dependencies import get_database
from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.config import get_settings
from sales_backend.domain.agent import ActorContext, ChatMessage
from sales_backend.domain.model_api import ConnectionPublish, ConnectionSnapshot, ConnectionTest, public_endpoint
from sales_backend.integrations.public_model_transport import PublicModelTransport
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError, shared_breaker
from sales_backend.main import app
from sales_backend.security.runtime_credentials import CredentialCipher
from sales_backend.services.model_api import ModelApiError, ModelApiService, connection_settings
from sales_backend.services.runtime_config import _load_provider_configuration

ACTOR = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role="administrator", data_scope="workspace")
SECRET = "synthetic-test-api-secret"


def config(**kw):
    return ConnectionSnapshot(
        provider_name="Fixture",
        endpoint_url="https://public.example/custom/chat",
        protocol="chat_completions",
        model="fixture-model",
        **kw,
    ).model_dump()


@pytest.fixture
def settings(tmp_path):
    ring = tmp_path / "ring.json"
    ring.write_text(json.dumps({"keys": {"test": base64.b64encode(b"x" * 32).decode()}}))
    ring.chmod(0o600)
    return replace(
        get_settings(),
        senseaudio_api_key=SECRET,
        config_credential_key_id="test",
        config_credential_keyring_file=str(ring),
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest",
        "https://[::1]/v1",
        "https://user:secret@public.example/v1",
        "https://public.example/v1?key=secret",
        "https://public.example/v1#secret",
        "https://public.example/",
        "https://public.example:0/v1",
        "https://public.example\\@127.0.0.1/v1",
        "https://10.0.0.1/v1",
    ],
)
def test_unsafe_addresses(url):
    with pytest.raises(ValueError):
        public_endpoint(url)


@pytest.mark.asyncio
async def test_dns_pin_and_rebind_block(monkeypatch):
    transport = PublicModelTransport()
    transport.transport = SimpleNamespace(handle_async_request=AsyncMock(return_value=httpx.Response(200)))
    resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    await transport.handle_async_request(httpx.Request("POST", "https://public.example/v1", content=b"fixture"))
    req = transport.transport.handle_async_request.call_args.args[0]
    assert req.url.host == "8.8.8.8" and req.headers["host"] == "public.example"
    assert req.extensions["sni_hostname"] == "public.example"
    resolver.return_value.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)))
    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(httpx.Request("POST", "https://public.example/v1"))
    assert transport.transport.handle_async_request.await_count == 1


@pytest.mark.asyncio
async def test_full_url_model_and_secret_safe_error(settings):
    s = connection_settings(settings, ACTOR, "text", config(), SECRET, 3)
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(401, json={"error": {"message": SECRET}}, request=req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SenseAudioClient(s, client=http)
        with pytest.raises(SenseAudioError) as exc:
            await client.chat_json(messages=[ChatMessage(role="user", content="fixture")])
    assert SECRET not in str(exc.value)
    assert str(seen[0].url) == "https://public.example/custom/chat"
    assert json.loads(seen[0].content)["model"] == "fixture-model"
    assert shared_breaker(s) is not shared_breaker(
        replace(s, model_api_metadata={**s.model_api_metadata, "connection_workspace": "other"})
    )


@pytest.mark.asyncio
async def test_redirect_and_wrong_purpose_not_followed(settings):
    s = connection_settings(settings, ACTOR, "text", config(), SECRET, 3)
    handler = AsyncMock(return_value=httpx.Response(302, headers={"location": "https://elsewhere.example/v1"}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SenseAudioClient(s, client=http)
        with pytest.raises(SenseAudioError):
            await client.chat_json(messages=[ChatMessage(role="user", content="fixture")])
        assert handler.await_count == 1
        with pytest.raises(SenseAudioError):
            await client.transcribe(filename="a.wav", content=b"data", mime_type="audio/wav")
        assert handler.await_count == 1


class FakeDB:
    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        yield SimpleNamespace()


def service(settings):
    svc = ModelApiService(FakeDB(), settings, probe=AsyncMock())
    svc.repo = SimpleNamespace(
        lock=AsyncMock(),
        test=AsyncMock(return_value=None),
        current=AsyncMock(return_value=None),
        reserve=AsyncMock(),
        finish=AsyncMock(),
        audit=AsyncMock(),
        publish=AsyncMock(return_value=1),
        recent_tests=AsyncMock(return_value=0),
        published_test=AsyncMock(return_value=None),
        release_snapshot=AsyncMock(),
    )
    return svc


def receipt_row(body, **kw):
    return {
        "id": body.request_id,
        "purpose": "text",
        "expected_version": body.expected_version,
        "status": "passed",
        "result": {},
        "expires_at": "later",
        "actor_user_ref_id": ACTOR.user_id,
        "unexpired": True,
        "source_guard": None,
        "config_snapshot": body.configuration.model_dump(),
        "restored_from_version": None,
        **kw,
    }


@pytest.mark.asyncio
async def test_test_stores_encrypted_key_publishes_exact_candidate(settings):
    svc = service(settings)
    body = ConnectionTest(request_id=uuid4(), expected_version=0, configuration=config(), api_key=SECRET)
    row = receipt_row(body)
    svc.repo.test.side_effect = [None, row]
    result = await svc.test(ACTOR, "text", body)
    assert SECRET not in json.dumps(result, default=str)
    credential = svc.repo.reserve.call_args.args[-2]
    assert credential["api_key_ciphertext"] != SECRET.encode()
    assert (
        CredentialCipher.from_file(settings.config_credential_keyring_file, "test").decrypt(
            ACTOR.workspace_id, "test", credential["api_key_ciphertext"]
        )
        == SECRET
    )
    svc.repo.test.side_effect = None
    svc.repo.test.return_value = row
    await svc.publish(ACTOR, "text", ConnectionPublish(test_id=body.request_id, expected_version=0))
    assert svc.repo.publish.call_args.args[-1] is row
    svc.probe.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed"},
        {"status": "running"},
        {"unexpired": False},
        {"actor_user_ref_id": str(uuid4())},
        {"purpose": "asr"},
        {"expected_version": 2},
    ],
)
async def test_unpublishable_receipt(settings, changes):
    svc = service(settings)
    body = ConnectionTest(request_id=uuid4(), expected_version=0, configuration=config())
    svc.repo.test.return_value = receipt_row(body, **changes)
    with pytest.raises(ModelApiError):
        await svc.publish(ACTOR, "text", ConnectionPublish(test_id=body.request_id, expected_version=0))
    svc.repo.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_change_without_new_key_rejected_before_request(settings):
    svc = service(settings)
    svc._legacy = AsyncMock(return_value=({"endpoint_url": "https://old.example/v1"}, SECRET))
    with pytest.raises(ModelApiError, match="重新填写"):
        await svc.test(ACTOR, "text", ConnectionTest(request_id=uuid4(), expected_version=0, configuration=config()))
    svc.probe.assert_not_awaited()
    svc.repo.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_error_does_not_echo_secrets(settings):
    svc = service(settings)
    svc.probe.side_effect = SenseAudioError(SECRET, status_code=403)
    body = ConnectionTest(request_id=uuid4(), expected_version=0, configuration=config(), api_key=SECRET)
    svc.repo.test.side_effect = [None, receipt_row(body, status="failed")]
    await svc.test(ACTOR, "text", body)
    status, result = svc.repo.finish.call_args.args[-2:]
    assert status == "failed" and SECRET not in str(result) and "鉴权" in result["message"]


@pytest.mark.asyncio
async def test_custom_runtime_preserves_agent_route_prompts_without_old_key(settings, monkeypatch):
    from sales_backend.repositories.admin import AgentRuntimeConfigRepository
    from sales_backend.repositories.model_api import ModelApiRepository

    cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, "test")
    monkeypatch.setattr(
        ModelApiRepository,
        "current",
        AsyncMock(
            return_value={
                "config_snapshot": config(),
                "version_no": 2,
                "workspace_id": ACTOR.workspace_id,
                **cipher.encrypt(ACTOR.workspace_id, SECRET),
            }
        ),
    )
    monkeypatch.setattr(
        AgentRuntimeConfigRepository,
        "current",
        AsyncMock(
            return_value={
                "enabled": True,
                "api_key_ciphertext": b"broken-old-key",
                "prompt_overrides": {"visit_entry": "preserve"},
            }
        ),
    )
    runtime = await _load_provider_configuration(FakeDB(), ACTOR, settings)
    assert runtime.settings.agent_platform_bindings_json == settings.agent_platform_bindings_json
    assert runtime.settings.model_api_endpoint == config()["endpoint_url"]
    assert runtime.prompt_overrides == {"visit_entry": "preserve"}


def test_validation_never_echoes_invalid_key(monkeypatch):
    from tests.authorization_fixtures import install_http_authorization
    install_http_authorization(monkeypatch, app, ACTOR, {p: "workspace" for p in ("access.console", "ai.config_test")})
    from sales_backend.api.dependencies import get_settings as api_settings

    app.dependency_overrides[api_settings] = get_settings
    app.dependency_overrides[get_system_identity] = lambda: SimpleNamespace(actor=ACTOR)
    app.dependency_overrides[get_database] = lambda: FakeDB()
    try:
        response = TestClient(app).post(
            "/api/v1/admin/model-apis/text/tests",
            json={
                "request_id": str(uuid4()),
                "expected_version": 0,
                "configuration": config(),
                "api_key": "secret with spaces",
            },
        )
        assert response.status_code == 422 and "secret with spaces" not in response.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_disabling_direct_fallback_does_not_block_platform_client_construction(settings):
    cfg = {**config(), "mode": "disabled"}
    s = connection_settings(settings, ACTOR, "text", cfg, None, 4)
    client = SenseAudioClient(s)
    try:
        with pytest.raises(RuntimeError, match="停用"):
            await client.chat_json(messages=[ChatMessage(role="user", content="fixture")])
    finally:
        await client.close()
