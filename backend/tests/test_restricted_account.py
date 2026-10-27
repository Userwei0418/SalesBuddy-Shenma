"""Focused security contract for the independent Mini readonly session."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException

from sales_backend.api.auth import router as auth_router
from sales_backend.api.demo_view import router, CustomerView, FollowUpView, TaskView
from sales_backend.auth.models import RestrictedSessionResponse
from sales_backend.auth.restricted import RestrictedIdentity, RestrictedTokens
from sales_backend.auth.tokens import TokenError, TokenService
from sales_backend.error_contract import error_response, http_exception_handler
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.security.restricted_boundary import restricted_boundary
from sales_backend.services.auth import AuthenticationFailed
from sales_backend.services.password_auth import PasswordAuthService
from sales_backend.services.restricted import RestrictedAccessError
from tests.test_tokens import settings

WID, UID, SID = str(uuid4()), str(uuid4()), str(uuid4())
IDENTITY = RestrictedIdentity(WID, UID, SID, 1)
NOW = datetime.now(UTC)
INFO = dict(session_kind="demo_readonly", session_id=SID, identity_id=UID,
            workspace_id=WID, permission_version=1, account_code="FXS001",
            company=dict(id=WID, code="demo-sales-workspace", name="演示公司"),
            data_version="1", modules=["customers", "opportunities", "follow-ups", "tasks"],
            expires_at=NOW + timedelta(minutes=30))
CANDIDATE = {**INFO, "password_hash": "verified-only-by-test-double", "enabled": True}


class Connection:
    def __init__(self):
        self.calls = []
        self.active = True
        self.admitted = True
        self.candidate = CANDIDATE.copy()
        self.read_result = {"id": str(uuid4()), "name": "演示客户", "phone": "never-public",
                            "audio_url": "never-public", "ai_summary": "never-public"}

    @asynccontextmanager
    async def transaction(self, **kwargs):
        self.calls.append(("transaction", kwargs))
        yield

    async def execute(self, sql, *args):
        self.calls.append((sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "password_login_identifier" in sql:
            return {"workspace": "demo-sales-workspace", "account_code": "FXS001"}
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "is_demo_readonly_identity" in sql:
            return True
        if "login_attempt" in sql:
            return True
        if "demo_readonly_candidate" in sql:
            return self.candidate
        if "demo_readonly_create_session" in sql:
            return {**INFO, "session_id": args[0]}
        if "demo_readonly_session" in sql:
            return INFO.copy() if self.active else None
        if "demo_readonly_admit" in sql:
            return self.admitted
        if "demo_readonly_revoke_session" in sql:
            self.active = False
            return True
        if "demo_readonly_read" in sql:
            return self.read_result
        raise AssertionError(sql)


class DB:
    def __init__(self):
        self.conn = Connection()

    @asynccontextmanager
    async def connection(self):
        yield self.conn

    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        raise AssertionError("restricted flow must not create an ordinary actor context")
        yield


def app_for(db):
    app = FastAPI()
    app.state.settings = settings()
    app.state.database = db
    app.middleware("http")(restricted_boundary)
    app.add_exception_handler(HTTPException, http_exception_handler)

    @app.exception_handler(RestrictedAccessError)
    async def restricted_error(request, exc):
        return error_response(request, status_code=exc.status, code=exc.code)

    app.include_router(router)
    app.include_router(auth_router)

    @app.post("/api/v1/audio/transcribe")
    @app.post("/api/v1/future/write")
    async def forbidden_upload(request: Request):
        await request.body()
        raise AssertionError("restricted credentials reached a business handler")

    return app


def token(**kwargs):
    return RestrictedTokens(settings()).issue(IDENTITY, kwargs.get("expiry", NOW + timedelta(minutes=30)))


def test_separate_token_has_no_ordinary_authority_and_checks_purpose_expiry():
    restricted = token()
    assert RestrictedTokens(settings()).decode(restricted) == IDENTITY
    for decode in (TokenService(settings()).decode_access_token,
                   TokenService(settings()).decode_console_binding,
                   TokenService(settings()).identify_access_session_for_revocation):
        with pytest.raises(TokenError):
            decode(restricted)
    with pytest.raises(TokenError):
        RestrictedTokens(settings()).decode(token(expiry=NOW - timedelta(minutes=1)))
    claims = jwt.decode(restricted, settings().access_token_secret, algorithms=["HS256"],
                        options={"verify_aud": False})
    assert not {"role", "scope", "teams", "refresh_token"} & claims.keys()
    claims["purpose"] = "ordinary"
    with pytest.raises(TokenError):
        RestrictedTokens(settings()).decode(jwt.encode(claims, settings().access_token_secret, algorithm="HS256"))


@pytest.mark.asyncio
async def test_password_verified_before_native_split_and_no_normal_session(monkeypatch):
    db = DB()
    monkeypatch.setattr("sales_backend.services.password_auth.verify_password", lambda password, encoded: password == "test-only-correct")
    session, changed = await PasswordAuthService(db, settings()).login(
        account="fxs001", password="test-only-correct", client_channel="wechat-mini-program",
        restricted_session_version=1,
    )
    assert isinstance(session, RestrictedSessionResponse) and not changed
    assert session.company.code == "demo-sales-workspace"
    assert not {"role", "actor", "refresh_token"} & session.model_dump().keys()
    assert not any("password_candidate(" in call[0] or "create_auth_session(" in call[0] for call in db.conn.calls)
    created = [call for call in db.conn.calls if "demo_readonly_create_session" in call[0]]
    assert len(created) == 1 and len(created[0][1][-1]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("password,channel,version,status", [
    ("wrong", "web", None, None), ("test-only-correct", "web", 1, 403),
    ("test-only-correct", "wechat-mini-program", None, 409),
])
async def test_no_type_disclosure_before_password_web_and_old_clients_rejected(monkeypatch, password, channel, version, status):
    db = DB()
    monkeypatch.setattr("sales_backend.services.password_auth.verify_password", lambda password, encoded: password == "test-only-correct")
    with pytest.raises(AuthenticationFailed) as failure:
        await PasswordAuthService(db, settings()).login(account="fxs001", password=password,
            client_channel=channel, restricted_session_version=version)
    assert getattr(failure.value, "status", None) == status
    assert not any("create_session(" in call[0] for call in db.conn.calls)


@pytest.mark.asyncio
async def test_disabled_correct_credentials_never_get_a_session(monkeypatch):
    db = DB()
    db.conn.candidate["enabled"] = False
    monkeypatch.setattr("sales_backend.services.password_auth.verify_password", lambda *args: True)
    with pytest.raises(AuthenticationFailed):
        await PasswordAuthService(db, settings()).login(account="fxs001", password="synthetic",
            client_channel="wechat-mini-program", restricted_session_version=1)
    assert not any("create_session(" in call[0] for call in db.conn.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/audio/transcribe", "/api/v1/future/write", "/api/v1/auth/refresh",
                                  "/api/v1/auth/wechat/login", "/api/v1/console/auth/password"])
async def test_default_deny_before_receiving_body_or_any_database_work(path):
    db = DB()
    async def body():
        raise AssertionError("request body was consumed before authorization")
        yield b"never"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db)), base_url="http://test") as client:
        result = await client.post(path, headers={"Authorization": "Bearer " + token()}, content=body())
    assert result.status_code == 403 and result.json()["code"] == "RESTRICTED_ENDPOINT_DENIED"
    assert not db.conn.calls


@pytest.mark.asyncio
async def test_restore_read_projection_revoke_and_repeat_logout():
    db = DB()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db)), base_url="http://test",
                                headers={"Authorization": "Bearer " + token()}) as client:
        response = await client.get("/api/v1/demo-view/session")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert not {"password_hash", "permission_version", "workspace_id"} & response.json().keys()
        data = await client.get("/api/v1/demo-view/customers/" + db.conn.read_result["id"])
        assert data.status_code == 200 and data.json()["name"] == "演示客户"
        assert not {"phone", "audio_url", "ai_summary"} & data.json().keys()
        assert any(call == ("transaction", {"readonly": True}) for call in db.conn.calls)
        assert (await client.get("/api/v1/demo-view/customers?limit=1000")).status_code == 422
        assert (await client.get("/api/v1/demo-view/session", headers={"X-Company-ID": str(uuid4())})).status_code == 403
        assert (await client.post("/api/v1/demo-view/logout")).status_code == 204
        assert (await client.post("/api/v1/demo-view/logout")).status_code == 204
        assert (await client.get("/api/v1/demo-view/session")).status_code == 401


@pytest.mark.asyncio
async def test_rate_limit_and_hidden_record_do_not_fallback():
    db = DB()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db)), base_url="http://test",
                                headers={"Authorization": "Bearer " + token()}) as client:
        db.conn.read_result = None
        assert (await client.get("/api/v1/demo-view/tasks/" + str(uuid4()))).status_code == 404
        db.conn.admitted = False
        assert (await client.get("/api/v1/demo-view/overview")).status_code == 429


@pytest.mark.asyncio
async def test_ordinary_restore_and_session_creation_refuse_dedicated_identity():
    db = DB()
    repository = IdentityRepository()
    assert await repository.find_actor_by_id(db.conn, workspace_id=WID, user_id=UID) is None
    with pytest.raises(PermissionError):
        await repository.create_session(db.conn, session_id=SID,
            actor=SimpleNamespace(workspace_id=WID, user_id=UID), refresh_token_hash="synthetic",
            expires_at=NOW, client={})
    assert not any("create_auth_session" in call[0] for call in db.conn.calls)


def test_projection_models_keep_missing_progress_and_drop_unapproved_fields():
    followup = FollowUpView(id=str(uuid4()), display_text=None, ai_summary="never").model_dump()
    task = TaskView(id=str(uuid4()), title="任务", status="in_progress", raw_audio="never").model_dump()
    assert followup["display_text"] is None and "ai_summary" not in followup
    assert task["progress_percent"] is None and "raw_audio" not in task
    assert "email" not in CustomerView(id=str(uuid4()), name="客户", email="never").model_dump()


def test_restricted_lightweight_task_exposes_only_safe_state_and_no_percentage():
    row = TaskView(id=str(uuid4()), title="合成任务", status="needs_adjustment",
        execution_mode="execution_v3", processing_reason="awaiting_review", progress_percent=100,
        creator_email="never-public", completion_note="never-public", latest_feedback="never-public")
    result = row.model_dump()
    assert result["processing_reason"] == "awaiting_review" and result["progress_percent"] is None
    assert not {"creator_email", "completion_note", "latest_feedback"} & result.keys()
