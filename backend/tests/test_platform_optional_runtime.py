"""An omitted platform pool preserves company API startup, but never grants platform access.

These tests cover startup wiring and the real platform HTTP gate without network
or database access. Existing-company migration/login is covered by the separate
V152 upgrade PostgreSQL acceptance, not inferred from these mocked resources.
"""

import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import sales_backend.main as module
from sales_backend.api.platform_control import COOKIE
from sales_backend.security.platform_mfa import PlatformMFACipher
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from tests.test_tokens import settings as token_settings


class QuietSink(logging.NullHandler):
    def __init__(self, database):
        super().__init__()

    async def shutdown(self):
        logging.getLogger().removeHandler(self)


@pytest.fixture
def runtime(monkeypatch):
    configured = replace(token_settings(), app_env="production", auth_mode="password",
                         database_url="postgresql://synthetic-api/fixture",
                         platform_database_url="", platform_mfa_keyring_file="",
                         platform_mfa_key_id="")
    databases = []

    def database(settings):
        result = SimpleNamespace(settings=settings, connect=AsyncMock(), close=AsyncMock())
        databases.append(result)
        return result

    previous_state = dict(module.app.state._state)
    monkeypatch.setattr(module, "get_settings", lambda: configured)
    monkeypatch.setattr(module, "Database", database)
    monkeypatch.setattr(module, "DatabaseEventSink", QuietSink)
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    validate = AsyncMock()
    keyring = Mock()
    monkeypatch.setattr(module, "verify_platform_pool", validate)
    monkeypatch.setattr(PlatformMFACipher, "from_file", keyring)
    yield SimpleNamespace(settings=configured, databases=databases, validate=validate, keyring=keyring)
    module.app.state._state.clear()
    module.app.state._state.update(previous_state)


@pytest.mark.asyncio
async def test_unconfigured_platform_does_not_prevent_company_api_startup(runtime):
    async with module.lifespan(module.app):
        assert len(runtime.databases) == 1
        runtime.databases[0].connect.assert_awaited_once()
        assert module.app.state.database is runtime.databases[0]
        assert module.app.state.platform_database is None
        assert module.app.state.platform_mfa_cipher is None
        runtime.validate.assert_not_awaited()
        runtime.keyring.assert_not_called()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                    base_url="https://fixture.invalid", cookies={COOKIE: "a" * 64}) as client:
            assert (await client.get("/api/v1/health/live")).status_code == 200
            assert (await client.get("/admin")).status_code == 200
            response = await client.get("/api/v1/platform/auth/me")
            assert response.status_code == 503
            assert response.json() == {"detail": "PLATFORM_NOT_CONFIGURED"}
    runtime.databases[0].close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["connection", "permissions", "mfa"])
async def test_configured_but_invalid_platform_never_silently_starts_unprotected(runtime, monkeypatch, fault):
    configured = replace(runtime.settings, platform_database_url="postgresql://synthetic-platform/fixture")
    monkeypatch.setattr(module, "get_settings", lambda: configured)
    original_factory = module.Database

    def database(settings):
        result = original_factory(settings)
        if fault == "connection" and settings.database_url == configured.platform_database_url:
            result.connect.side_effect = RuntimeError("synthetic connection failure")
        return result

    monkeypatch.setattr(module, "Database", database)
    if fault == "permissions":
        runtime.validate.side_effect = RuntimeError("synthetic elevated runtime role")
    if fault == "mfa":
        runtime.keyring.side_effect = RuntimeCredentialUnavailable()
    expected = RuntimeCredentialUnavailable if fault == "mfa" else RuntimeError
    with pytest.raises(expected):
        async with module.lifespan(module.app):
            pytest.fail("A configured platform pool must not bypass validation")
    assert len(runtime.databases) == 2
    for database in runtime.databases:
        database.close.assert_awaited_once()
    assert module.app.state.platform_database is None
    assert module.app.state.platform_mfa_cipher is None


@pytest.mark.asyncio
async def test_valid_platform_uses_separate_pool_and_mfa(runtime, monkeypatch):
    configured = replace(runtime.settings, platform_database_url="postgresql://synthetic-platform/fixture",
                         platform_mfa_keyring_file="synthetic-keyring", platform_mfa_key_id="synthetic")
    monkeypatch.setattr(module, "get_settings", lambda: configured)
    async with module.lifespan(module.app):
        assert len(runtime.databases) == 2
        api, platform = runtime.databases
        assert api.settings.database_url != platform.settings.database_url
        assert module.app.state.database is api
        assert module.app.state.platform_database is platform
        assert module.app.state.platform_mfa_cipher is runtime.keyring.return_value
        runtime.validate.assert_awaited_once_with(platform)
        runtime.keyring.assert_called_once_with("synthetic-keyring", "synthetic")
        runtime.keyring.return_value._key.assert_called_once_with("synthetic")
    for database in runtime.databases:
        database.close.assert_awaited_once()
