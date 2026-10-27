from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from sales_backend.api.dependencies import (
    RequestIdentity,
    bind_login_identity,
    get_database,
    get_identity,
    get_settings,
)
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.auth.models import (
    ActorResponse,
    CompanyEntry,
    DemoLoginRequest,
    NativePasswordLogin,
    PasswordChange,
    RefreshRequest,
    RestrictedSessionResponse,
    SessionResponse,
)
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.auth import AuthenticationFailed, AuthService
from sales_backend.services.capabilities import capability_snapshot
from sales_backend.services.company_entry import CompanyEntryService
from sales_backend.services.password_auth import LoginThrottled, PasswordAuthService
from sales_backend.services.restricted import RestrictedAccessError

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.get("/company-entry", response_model=CompanyEntry)
async def company_entry(
    request: Request, response: Response, company_code: str = "",
    database: Database = Depends(get_database),
) -> CompanyEntry:
    response.headers["Cache-Control"] = "no-store"
    try:
        entry = await CompanyEntryService(database).lookup(
            company_code, request.client.host if request.client else "unknown",
        )
    except LoginThrottled as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"}) from exc
    if entry is None:
        raise HTTPException(404, "公司入口无效或暂不可用，请向公司管理员核对", headers={"Cache-Control": "no-store"})
    return CompanyEntry(**entry)


@router.post("/password/login", response_model=SessionResponse | RestrictedSessionResponse)
async def password_login(
    body: NativePasswordLogin, request: Request, response: Response,
    database: Database = Depends(get_database), settings: Settings = Depends(get_settings),
) -> SessionResponse | RestrictedSessionResponse:
    """Bearer sessions for native clients; shared password verification, no Web cookie dependency."""
    try:
        session, _ = await PasswordAuthService(database, settings).login(
            account=body.account_code, password=body.password.get_secret_value(),
            workspace=body.workspace, role=body.role, login_type=body.login_type,
            client_ip=request.client.host if request.client else "unknown", client_channel="wechat-mini-program",
            restricted_session_version=body.restricted_session_version,
        )
    except LoginThrottled as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except RestrictedAccessError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc
    except AuthenticationFailed as exc:
        raise HTTPException(401, {"code": "INVALID_CREDENTIALS"}) from exc
    if isinstance(session, SessionResponse):
        bind_login_identity(request, session)
    response.headers["Cache-Control"] = "no-store"
    return session


@router.post("/password")
async def password_change(
    body: PasswordChange,
    identity: RequestIdentity = Depends(get_password_identity),
    database: Database = Depends(get_database), settings: Settings = Depends(get_settings),
) -> dict:
    try:
        await PasswordAuthService(database, settings).change_password(
            identity, body.old_password.get_secret_value(), body.new_password.get_secret_value(),
        )
    except LoginThrottled as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except AuthenticationFailed as exc:
        raise HTTPException(401, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"changed": True}


@router.post("/session", response_model=SessionResponse)
@router.post("/wechat/login", response_model=SessionResponse)
async def create_session(
    body: DemoLoginRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    try:
        return await AuthService(database, settings).login_demo(
            account_code=body.account_code, workspace=body.workspace, client=body.client
        )
    except AuthenticationFailed as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.post("/refresh", response_model=SessionResponse)
async def refresh_session(
    body: RefreshRequest,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    try:
        return await AuthService(database, settings).refresh(body.refresh_token)
    except AuthenticationFailed as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.get("/me", response_model=ActorResponse)
async def get_me(
    response: Response, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
) -> ActorResponse:
    async with database.transaction(identity.actor, readonly=True) as connection:
        effective = await capability_snapshot(connection, identity.actor)
        effective.update(await IdentityRepository().company_identity(connection, identity.actor.workspace_id))
    response.headers["Cache-Control"] = "no-store"
    return AuthService.actor_response(identity.profile).model_copy(update=effective)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> Response:
    async with database.transaction(identity.actor) as connection:
        await IdentityRepository().revoke_session(connection, session_id=identity.session_id, actor=identity.actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
