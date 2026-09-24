from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sales_backend.auth.tokens import TokenError, TokenService
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.identity import ActorRecord, IdentityRepository
from sales_backend.repositories.passwords import PasswordRepository
from sales_backend.request_metadata import current_actor

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    actor: ActorContext
    session_id: str
    profile: ActorRecord
    auth_method: str = ""
    must_change_password: bool = True
    authenticated_profile: ActorRecord | None = None
    client_channel: str = "wechat-mini-program"


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def bind_login_identity(request: Request, session) -> None:
    """Attribute login auditing to the server-issued identity, never client role/name fields."""
    actor, session_id = TokenService(request.app.state.settings).decode_access_token(session.access_token)
    profile = ActorRecord(
        actor, session.actor.account_code, session.actor.display_name, tuple(session.actor.team_names),
    )
    request.state.identity = RequestIdentity(actor, session_id, profile)
    current_actor.set(actor)


async def get_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> RequestIdentity:
    verified = getattr(request.state, "verified_identity", None)
    if verified is not None:
        return verified
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "AUTH_REQUIRED")
    try:
        actor, session_id = TokenService(settings).decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID") from exc
    repository = IdentityRepository()
    async with database.transaction(actor, readonly=True) as connection:
        if not await repository.session_is_active(connection, session_id=session_id, actor=actor):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SESSION_EXPIRED")
        profile = await repository.find_actor_by_id(
            connection, workspace_id=actor.workspace_id, user_id=actor.user_id, role=actor.role.value
        )
        session_credentials = await PasswordRepository().session_credentials(connection, session_id)
    if profile is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "ACTOR_INACTIVE")
    if profile.context != actor:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "ACTOR_SCOPE_CHANGED")
    identity = RequestIdentity(
        actor=actor, session_id=session_id, profile=profile,
        auth_method=session_credentials.get("auth_method", ""),
        must_change_password=bool(session_credentials.get("must_change_password", True)),
        client_channel=session_credentials.get("client_channel", "wechat-mini-program"),
    )
    request.state.identity = identity
    current_actor.set(actor)
    # Initial-password sessions may only inspect identity, change password or log out.
    if session_credentials.get("auth_method") == "password" and session_credentials.get("must_change_password"):
        allowed = {"/api/v1/auth/me", "/api/v1/auth/password", "/api/v1/auth/logout",
                   "/api/v1/console/auth/me", "/api/v1/console/auth/password", "/api/v1/console/auth/logout"}
        if request.url.path not in allowed:
            raise HTTPException(403, "PASSWORD_CHANGE_REQUIRED")
    # Selection is never authority: validate the original session first, then resolve
    # an explicitly delegated administrator identity for every selected-company request.
    target = request.headers.get("X-Company-ID")
    auth_path = request.url.path.startswith(("/api/v1/auth/", "/api/v1/console/auth/"))
    directory_path = request.url.path in {"/api/v1/console/companies", "/api/v1/console/companies/select"}
    if target and not auth_path and not directory_path:
        try:
            target = str(UUID(target))
        except ValueError as exc:
            raise HTTPException(400, "公司标识无效") from exc
        if target != actor.workspace_id:
            if identity.auth_method != "password" or actor.role.value != "administrator":
                raise HTTPException(403, "没有此公司的管理权限")
            async with database.transaction(actor, readonly=True) as connection:
                selected_profile = await repository.find_company_management_actor(connection, target)
                if selected_profile is None:
                    raise HTTPException(403, "没有此公司的管理权限，或公司已停用")
            identity = replace(identity, actor=selected_profile.context, profile=selected_profile,
                               authenticated_profile=profile)
            request.state.identity = identity
            current_actor.set(identity.actor)
    request.state.verified_identity = identity
    return identity
