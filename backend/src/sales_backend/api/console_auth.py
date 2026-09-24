"""Same-origin Web authentication. Access token stays in memory; refresh is HttpOnly."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from sales_backend.api.dependencies import RequestIdentity, bind_login_identity, get_database, get_settings
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.auth.models import PasswordChange, PasswordLogin
from sales_backend.auth.tokens import SessionReference, TokenError, TokenService
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.request_metadata import current_actor
from sales_backend.services.auth import AuthenticationFailed, AuthService
from sales_backend.services.password_auth import LoginThrottled, PasswordAuthService

router = APIRouter(prefix="/api/v1/console/auth", tags=["Console authentication"])
COOKIE = "sales_console_refresh"
BINDING_COOKIE = "sales_console_session"
COOKIE_PATH = "/api/v1/console/auth"


def same_origin(request: Request):
    # Do not trust X-Forwarded-Host or a client-supplied origin allowlist.
    origin = urlsplit(request.headers.get("origin", ""))
    if origin.scheme != request.url.scheme or origin.netloc != request.url.netloc:
        raise HTTPException(403, "ORIGIN_NOT_ALLOWED")


def browser_session_references(request: Request, tokens: TokenService) -> tuple[SessionReference, ...]:
    """Verify opaque browser proofs; absent/invalid proof grants no authority."""
    references = []
    binding = request.cookies.get(BINDING_COOKIE)
    if binding:
        try:
            references.append(tokens.decode_console_binding(binding))
        except TokenError:
            pass
    scheme, _, access = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and access:
        try:
            reference = tokens.identify_access_session_for_revocation(access)
            if reference not in references:
                references.append(reference)
        except TokenError:
            pass
    return tuple(references)


def session_response(request, response, session, must_change, *, new_login=False):
    # Authenticate the audit actor from the server-issued session, never request fields.
    bind_login_identity(request, session)
    response.set_cookie(
        COOKIE,
        session.refresh_token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path=COOKIE_PATH,
    )
    if new_login:
        tokens = TokenService(request.app.state.settings)
        actor, session_id = tokens.decode_access_token(session.access_token)
        response.set_cookie(
            BINDING_COOKIE, tokens.issue_console_binding(SessionReference.from_actor(actor, session_id)),
            httponly=True, secure=request.url.scheme == "https", samesite="strict", path=COOKIE_PATH,
        )
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": session.access_token,
        "expires_at": session.expires_at,
        "actor": session.actor,
        "must_change_password": must_change,
    }


@router.post("/login", dependencies=[Depends(same_origin)])
async def login(
    body: PasswordLogin,
    request: Request,
    response: Response,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    try:
        session, must_change = await PasswordAuthService(database, settings).login(
            account=body.account_code,
            password=body.password.get_secret_value(),
            workspace=body.workspace,
            role=body.role,
            client_ip=request.client.host if request.client else "unknown",
            previous_sessions=browser_session_references(request, TokenService(settings)),
        )
    except LoginThrottled as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except AuthenticationFailed as exc:
        raise HTTPException(401, str(exc)) from exc
    return session_response(request, response, session, must_change, new_login=True)


@router.post("/refresh", dependencies=[Depends(same_origin)])
async def refresh(
    request: Request,
    response: Response,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    token = request.cookies.get(COOKIE)
    binding = request.cookies.get(BINDING_COOKIE)
    if not token or not binding:
        # Sessions established before the binding protocol need a one-time login.
        raise HTTPException(401, "AUTH_REQUIRED")
    service = AuthService(database, settings)
    try:
        reference = service.tokens.decode_console_binding(binding)
        session = await service.refresh(token, require_password=True, expected_session=reference)
    except (AuthenticationFailed, TokenError) as exc:
        raise HTTPException(401, "SESSION_EXPIRED") from exc
    return session_response(request, response, session, session.must_change_password)


@router.get("/me")
async def me(
    response: Response,
    identity: RequestIdentity = Depends(get_password_identity),
    database: Database = Depends(get_database),
):
    response.headers["Cache-Control"] = "no-store"
    from sales_backend.services.capabilities import capability_snapshot
    async with database.transaction(identity.actor, readonly=True) as connection:
        effective = await capability_snapshot(connection, identity.actor)
    return {
        "actor": AuthService.actor_response(identity.profile).model_copy(update=effective),
        "must_change_password": identity.must_change_password,
    }


@router.post("/password", dependencies=[Depends(same_origin)])
async def change_password(
    body: PasswordChange,
    identity: RequestIdentity = Depends(get_password_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    try:
        await PasswordAuthService(database, settings).change_password(
            identity, body.old_password.get_secret_value(), body.new_password.get_secret_value()
        )
    except LoginThrottled as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except AuthenticationFailed as exc:
        raise HTTPException(401, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"changed": True}


@router.post("/logout", dependencies=[Depends(same_origin)])
async def logout(
    request: Request,
    response: Response,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    service = AuthService(database, settings)
    audit_identity = await service.revoke_browser_sessions(
        browser_session_references(request, service.tokens), request.cookies.get(COOKIE),
    )
    if audit_identity:
        profile, session_id = audit_identity
        # For attribution only: logout has already revoked the session. This is
        # not an authorization dependency and does not grant a capability.
        request.state.identity = RequestIdentity(profile.context, session_id, profile)
        current_actor.set(profile.context)
    for cookie in (COOKIE, BINDING_COOKIE):
        response.delete_cookie(cookie, path=COOKIE_PATH, httponly=True,
                               secure=request.url.scheme == "https", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"logged_out": True}
