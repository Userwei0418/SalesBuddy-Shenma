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
    DemoLoginRequest,
    PasswordChange,
    PasswordLogin,
    RefreshRequest,
    SessionResponse,
)
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.auth import AuthenticationFailed, AuthService
from sales_backend.services.capabilities import capability_snapshot
from sales_backend.services.password_auth import LoginThrottled, PasswordAuthService

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.post("/password/login", response_model=SessionResponse)
async def password_login(
    body: PasswordLogin, request: Request, response: Response,
    database: Database = Depends(get_database), settings: Settings = Depends(get_settings),
) -> SessionResponse:
    """Bearer sessions for native clients; shared password verification, no Web cookie dependency."""
    try:
        session, _ = await PasswordAuthService(database, settings).login(
            account=body.account_code, password=body.password.get_secret_value(),
            workspace=body.workspace, role=body.role,
            client_ip=request.client.host if request.client else "unknown", client_channel="wechat-mini-program",
        )
    except LoginThrottled as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except AuthenticationFailed as exc:
        raise HTTPException(401, str(exc)) from exc
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
