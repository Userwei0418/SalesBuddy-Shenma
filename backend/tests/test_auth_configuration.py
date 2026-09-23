"""Production cannot start with implicit or demo authentication."""
import importlib.util
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI

from sales_backend.config import get_settings
from sales_backend.services.auth import AuthenticationFailed, AuthService
from tests.test_tokens import settings as token_settings


@pytest.fixture(autouse=True)
def clear_config_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("mode", [None, "", " ", "demo", "unknown"])
def test_production_requires_explicit_password_mode(monkeypatch, mode):
    monkeypatch.setenv("APP_ENV", "production")
    if mode is None:
        monkeypatch.delenv("AUTH_MODE", raising=False)
    else:
        monkeypatch.setenv("AUTH_MODE", mode)
    with pytest.raises(RuntimeError, match="explicit AUTH_MODE=password"):
        get_settings()


@pytest.mark.parametrize("environment", ["development", "test"])
def test_nonproduction_without_opt_in_defaults_to_password(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    assert get_settings().auth_mode == "password"


@pytest.mark.parametrize("environment", ["development", "test"])
def test_nonproduction_explicit_demo_remains_available(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("AUTH_MODE", "demo")
    assert get_settings().auth_mode == "demo"


def test_production_password_still_validates_signing_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("ACCESS_TOKEN_SECRET", "short")
    with pytest.raises(RuntimeError, match="ACCESS_TOKEN_SECRET"):
        get_settings().require_auth()
    replace(get_settings(), access_token_secret=token_settings().access_token_secret).require_auth()


@pytest.mark.parametrize("mode", ["demo", "unknown"])
def test_direct_settings_cannot_bypass_production_guard(mode):
    with pytest.raises(RuntimeError):
        replace(token_settings(), app_env="production", auth_mode=mode).require_auth()


def test_direct_test_demo_settings_remain_supported():
    token_settings().require_auth()


@pytest.mark.asyncio
async def test_password_mode_rejects_demo_login_before_reading_database():
    database = Mock()
    service = AuthService(database, replace(token_settings(), app_env="production", auth_mode="password"))
    with pytest.raises(AuthenticationFailed, match="disabled"):
        await service.login_demo(account_code="XS001", workspace=None, client={})
    database.connection.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "worker"])
async def test_service_startup_checks_direct_settings_before_database(monkeypatch, entrypoint):
    invalid = replace(token_settings(), app_env="production", auth_mode="demo")
    if entrypoint == "api":
        import sales_backend.main as module
    else:
        import sales_backend.worker as module
    database = Mock()
    monkeypatch.setattr(module, "get_settings", lambda: invalid)
    monkeypatch.setattr(module, "Database", database)
    with pytest.raises(RuntimeError, match="AUTH_MODE=password"):
        if entrypoint == "api":
            async with module.lifespan(FastAPI()):
                pytest.fail("Invalid configuration must not enter the lifespan")
        else:
            await module.main()
    database.assert_not_called()


@pytest.mark.parametrize("environment", ["production", "development", "test"])
@pytest.mark.parametrize("mode", ["password", "demo"])
def test_deployment_preflight_requires_production_password_and_reports_only_nonsecret_result(
    monkeypatch, capsys, environment, mode
):
    path = Path(__file__).parents[1] / "deploy/verify_auth_runtime.py"
    spec = importlib.util.spec_from_file_location("verify_auth_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = replace(token_settings(), app_env=environment, auth_mode=mode)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    if environment != "production" or mode != "password":
        with pytest.raises(SystemExit, match="Authentication configuration rejected"):
            module.main()
        assert not capsys.readouterr().out
    else:
        module.main()
        output = capsys.readouterr().out
        assert '"auth_mode": "password"' in output
        assert settings.access_token_secret not in output


def test_unknown_development_mode_is_not_silently_accepted(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "passwrod")
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        get_settings()
