"""Hold original storage I/O while independently changing current authorization."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from starlette.requests import ClientDisconnect, Request

from sales_backend.api import live_identity, tenant_export, visit_imports
from sales_backend.api.dependencies import RequestIdentity
from sales_backend.auth.tokens import TokenService
from sales_backend.db import TenantInactive
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.domain.authorization import Authorization, Grant
from sales_backend.repositories.identity import ActorRecord
from sales_backend.services.tenant_export import Package

WORKSPACE = "00000000-0000-4000-a000-000000000001"
USER = "00000000-0000-4000-a000-000000000002"
SESSION = "00000000-0000-4000-a000-000000000003"
IMPORT = UUID("00000000-0000-4000-a000-000000000004")
OTHER_WORKSPACE = "00000000-0000-4000-a000-000000000005"


@pytest.fixture
def harness(monkeypatch):
    actor = ActorContext(workspace_id=WORKSPACE, user_id=USER, role=RoleCode.SALES, data_scope=DataScope.SELF)
    profile = ActorRecord(actor, "SYNTHETIC", "Synthetic", ())
    identity = RequestIdentity(actor, SESSION, profile, auth_method="password", must_change_password=False)
    settings = SimpleNamespace(
        require_auth=lambda: None, access_token_secret="synthetic-download-token-secret-00000000000",
        access_token_issuer="test", access_token_audience="test", access_token_minutes=10, refresh_token_days=1,
    )
    state = SimpleNamespace(
        identity=identity, current=profile, selected=None, active=True, closed=set(), checks=0,
        session={"auth_method": "password", "must_change_password": False, "client_channel": "wechat-mini-program"},
        permissions={"access.console", "access.mini_program", "visit.download_original",
                     "company.export", "customer.read", "customer.export"},
        row={"id": IMPORT, "filename": "synthetic.txt", "file_size": 17, "file_path": "",
             "storage_profile": "test", "storage_driver": "s3", "storage_key": "test/original",
             "content_sha256": "synthetic"},
        locations=0, transactions=[], contexts=[], permission_reads=0, after_permission_read=None,
        started=asyncio.Event(), release=asyncio.Event(), settings=settings,
    )

    class Database:
        @asynccontextmanager
        async def transaction(self, selected_actor, *, readonly=False):
            state.transactions.append(selected_actor)
            if selected_actor.workspace_id in state.closed:
                raise TenantInactive("synthetic closed company")
            yield SimpleNamespace(actor=selected_actor)

    class Identities:
        async def session_is_active(self, connection, *, session_id, actor):
            state.checks += 1
            assert session_id == SESSION
            return state.active

        async def find_actor_by_id(self, connection, **kwargs):
            return state.current

        async def find_company_management_actor(self, connection, target):
            return state.selected

    class Passwords:
        async def session_credentials(self, connection, session_id):
            return dict(state.session)

    class Permissions:
        async def effective(self, connection):
            state.permission_reads += 1
            if state.after_permission_read:
                change, state.after_permission_read = state.after_permission_read, None
                change(state)
            return Authorization.combine(connection.actor.workspace_id, connection.actor.user_id,
                                         [Grant(code, "workspace") for code in state.permissions])

    async def set_context(connection, selected_actor):
        state.contexts.append((id(connection), selected_actor))
        if selected_actor.workspace_id in state.closed:
            raise TenantInactive("synthetic closed company")
        connection.actor = selected_actor

    class Storage:
        async def read(self, database, actor, row):
            state.started.set()
            await state.release.wait()
            return b"synthetic-content"

    async def location(connection, actor, import_id):
        state.locations += 1
        assert import_id == IMPORT
        if "visit.download_original" not in state.permissions or state.row is None:
            return None
        return dict(state.row)

    def request(path=f"/api/v1/visit-imports/{IMPORT}/original", *, expired=False):
        token_settings = SimpleNamespace(**vars(settings))
        if expired:
            token_settings.access_token_minutes = -1
        original = state.identity.authenticated_profile or state.identity.profile
        token = TokenService(token_settings).issue(original.context, session_id=SESSION).access_token
        value = Request({"type": "http", "method": "GET", "path": path, "query_string": b"",
                         "headers": [(b"authorization", f"Bearer {token}".encode())],
                         "app": SimpleNamespace(state=SimpleNamespace(settings=settings))})
        # Deliberately stale caches: neither may authorize the late result.
        value.state.identity = value.state.verified_identity = state.identity
        value.state.permissions = SimpleNamespace(allows=lambda _: True, require=lambda _: None)
        return value

    state.database, state.request = Database(), request
    monkeypatch.setattr(live_identity, "IdentityRepository", Identities)
    monkeypatch.setattr(live_identity, "PasswordRepository", Passwords)
    monkeypatch.setattr(live_identity, "AuthorizationRepository", Permissions)
    monkeypatch.setattr(live_identity, "set_request_context", set_context)
    monkeypatch.setattr(tenant_export, "AuthorizationRepository", Permissions)
    monkeypatch.setattr(visit_imports, "original_location", location)
    monkeypatch.setattr(visit_imports, "MaterialStorage", Storage)
    return state


async def download(state, change=None):
    task = asyncio.create_task(visit_imports.download_original(IMPORT, state.request(), state.identity, state.database))
    await asyncio.wait_for(state.started.wait(), 1)
    if change:
        change(state)
    state.release.set()
    return await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda s: setattr(s, "active", False),
    lambda s: setattr(s, "current", None),
    lambda s: s.closed.add(WORKSPACE),
    lambda s: s.permissions.remove("visit.download_original"),
    lambda s: s.permissions.remove("access.mini_program"),
    lambda s: setattr(s, "current", replace(s.current, context=s.current.context.model_copy(
        update={"role": RoleCode.FDE}))),
    lambda s: setattr(s, "current", replace(s.current, context=s.current.context.model_copy(
        update={"team_ids": (OTHER_WORKSPACE,)}))),
    lambda s: s.session.update(must_change_password=True),
    lambda s: s.session.update(auth_method="demo"),
    lambda s: s.session.update(client_channel="web"),
    lambda s: setattr(s, "row", None),
], ids=["logout", "disabled-account", "closed-company", "object-revoked", "surface-revoked",
        "role-changed", "scope-changed", "password-reset", "auth-method-changed", "channel-changed", "deleted"])
async def test_late_original_never_escapes_after_identity_or_permission_changes(harness, change):
    with pytest.raises(PermissionError):
        await download(harness, change)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("storage_key", "replacement"), ("storage_driver", "postgres"), ("storage_profile", "other"),
    ("file_path", "/different/legacy-original"), ("file_size", 18), ("content_sha256", "changed"),
    ("filename", "renamed.txt"),
])
async def test_original_location_or_version_change_requires_new_download(harness, field, value):
    with pytest.raises(HTTPException) as error:
        await download(harness, lambda state: state.row.update({field: value}))
    assert error.value.status_code == 409 and harness.locations == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method,channel", [("password", "wechat-mini-program"), ("password", "web"),
                                           ("demo", "wechat-mini-program")])
async def test_valid_existing_login_methods_and_surfaces_can_still_download(harness, method, channel):
    harness.identity = replace(harness.identity, auth_method=method, client_channel=channel)
    harness.session.update(auth_method=method, client_channel=channel, must_change_password=method == "demo")
    response = await download(harness)
    assert response.body == b"synthetic-content"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert harness.checks == 2 and harness.locations == 2


@pytest.mark.asyncio
async def test_web_original_download_rechecks_console_surface_without_substituting_mini_grant(harness):
    harness.identity = replace(harness.identity, client_channel="web")
    harness.session.update(client_channel="web")
    with pytest.raises(PermissionError):
        await download(harness, lambda state: state.permissions.remove("access.console"))


def delegated(state):
    original = replace(state.current, context=state.current.context.model_copy(update={"role": RoleCode.ADMINISTRATOR}))
    actor = original.context.model_copy(update={"workspace_id": OTHER_WORKSPACE})
    profile = replace(original, context=actor)
    state.identity = replace(state.identity, actor=actor, profile=profile, authenticated_profile=original)
    state.current, state.selected = original, profile


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [lambda s: setattr(s, "selected", None),
                                  lambda s: s.closed.add(OTHER_WORKSPACE),
                                  lambda s: s.closed.add(WORKSPACE)],
                         ids=["delegation-revoked", "target-closed", "original-company-closed"])
async def test_cross_company_download_rechecks_original_session_and_delegation(harness, change):
    delegated(harness)
    with pytest.raises(PermissionError):
        await download(harness, change)


@pytest.mark.asyncio
async def test_valid_cross_company_identity_is_kept_and_target_grants_are_reloaded(harness):
    delegated(harness)
    response = await download(harness)
    assert response.body == b"synthetic-content"
    assert [actor.workspace_id for actor in harness.transactions] == [OTHER_WORKSPACE, WORKSPACE]
    assert len({connection for connection, _ in harness.contexts}) == 1
    assert harness.contexts[-1][1].workspace_id == OTHER_WORKSPACE


@pytest.mark.asyncio
async def test_fresh_check_ignores_cached_identity_and_checks_token_expiration(harness):
    with pytest.raises(PermissionError, match="登录已过期"):
        async with live_identity.live_identity_connection(harness.request(expired=True), harness.identity,
                                                        harness.database):
            pytest.fail("expired token allowed")
    assert harness.transactions == []


@pytest.mark.asyncio
async def test_a_different_valid_token_cannot_resume_the_original_request(harness):
    request = harness.request()
    different = TokenService(harness.settings).issue(harness.identity.actor, session_id=OTHER_WORKSPACE)
    request.scope["headers"] = [(b"authorization", f"Bearer {different.access_token}".encode())]
    with pytest.raises(PermissionError, match="身份已变化"):
        async with live_identity.live_identity_connection(request, harness.identity, harness.database):
            pytest.fail("different session allowed")
    assert harness.transactions == []


@pytest.mark.asyncio
async def test_export_still_requires_password_even_when_demo_has_console_permission(harness):
    harness.identity = replace(harness.identity, auth_method="demo")
    harness.session.update(auth_method="demo")
    with pytest.raises(PermissionError):
        async with live_identity.live_identity_connection(
            harness.request("/api/v1/console/company-data/export"), harness.identity,
            harness.database, require_password=True,
        ):
            pytest.fail("demo identity allowed to export")


@pytest.mark.asyncio
async def test_real_export_route_rechecks_shared_live_identity_on_every_chunk(harness, monkeypatch, tmp_path):
    folder = tmp_path / "export"
    folder.mkdir()
    path = folder / "data.zip"
    path.write_bytes(b"x" * (3 * 1024 * 1024))
    package = Package(folder, path, "synthetic", {"workspace_id": WORKSPACE, "record_count": 1}, "digest")
    monkeypatch.setattr(tenant_export, "build_package", AsyncMock(return_value=package))
    monkeypatch.setattr(tenant_export, "record_prepared", AsyncMock())
    request = harness.request("/api/v1/console/company-data/export")
    response = await tenant_export.export_business_data(
        tenant_export.ExportRequest(sections=["customers"], reason="合成验收"),
        request, harness.identity, harness.database,
    )
    chunks = []

    async def send(message):
        if message["type"] == "http.response.body":
            chunks.append(message["body"])
            harness.active = False

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert sum(map(len, chunks)) == 1024 * 1024
    assert harness.checks == 5 and WORKSPACE not in tenant_export._active
    assert not folder.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda s: setattr(s, "active", False),
    lambda s: s.session.update(must_change_password=True),
    lambda s: setattr(s, "current", None),
    lambda s: setattr(s, "selected", None),
    lambda s: s.closed.add(WORKSPACE),
    lambda s: s.closed.add(OTHER_WORKSPACE),
], ids=["logout", "password-reset", "disabled-account", "delegation-revoked", "original-closed", "target-closed"])
async def test_final_identity_check_covers_permission_lookup_window_on_one_connection(harness, change):
    delegated(harness)
    harness.after_permission_read = change
    with pytest.raises(PermissionError):
        async with live_identity.live_identity_connection(harness.request(), harness.identity, harness.database):
            pytest.fail("revoked identity allowed after permissions were read")
    assert len(harness.transactions) == 1  # No release/reacquire pool wait between checks.
    assert len({connection for connection, _ in harness.contexts}) == 1
