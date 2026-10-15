"""Fresh identity checks before releasing data produced by a long-running read.

Unlike request dependencies, this never reuses request.state or admits/rate-limits
the HTTP request again. The yielded transaction has current tenant/object grants.
"""

from contextlib import asynccontextmanager

from fastapi import Request

from sales_backend.api.dependencies import RequestIdentity, bearer
from sales_backend.auth.tokens import TokenError, TokenService
from sales_backend.db import Database, set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.passwords import PasswordRepository


@asynccontextmanager
async def live_identity_connection(
    request: Request, identity: RequestIdentity, database: Database, *, require_password: bool = False,
):
    credentials = await bearer(request)
    try:
        token_actor, token_session = TokenService(request.app.state.settings).decode_access_token(
            credentials.credentials
        )
    except (TokenError, AttributeError):
        raise PermissionError("登录已过期，请重新登录后重试") from None
    original = identity.authenticated_profile or identity.profile
    actor = identity.actor
    if (token_actor != original.context or token_session != identity.session_id
            or actor != identity.profile.context):
        raise PermissionError("身份已变化，请重新发起请求")
    repository = IdentityRepository()
    async def verify_original(connection):
        if not await repository.session_is_active(connection, session_id=identity.session_id, actor=original.context):
            raise PermissionError("登录已撤销，请重新登录后重试")
        current = await repository.find_actor_by_id(
            connection, workspace_id=original.context.workspace_id,
            user_id=original.context.user_id, role=original.context.role.value,
        )
        session = await PasswordRepository().session_credentials(connection, identity.session_id)
        method = session.get("auth_method")
        channel = session.get("client_channel", "wechat-mini-program")
        if (current is None or current.context != original.context
                or not method or method != identity.auth_method or channel != identity.client_channel
                or (require_password and method != "password")
                or (method == "password" and session.get("must_change_password", True))):
            raise PermissionError("账号状态已变化，请重新登录后重试")
        if actor.workspace_id != original.context.workspace_id:
            if method != "password" or original.context.role.value != "administrator":
                raise PermissionError("公司授权已变化，请重新发起请求")
            selected = await repository.find_company_management_actor(connection, actor.workspace_id)
            if selected is None or selected.context != actor:
                raise PermissionError("公司授权已变化，请重新发起请求")
        elif actor != original.context:
            raise PermissionError("身份已变化，请重新发起请求")
        return method, channel

    async with database.transaction(original.context, readonly=True) as connection:
        await verify_original(connection)
        if actor != original.context:
            await set_request_context(connection, actor)
        # Keep the connection while obtaining permissions. Releasing it here
        # would open a pool-wait window in which logout/reset could go unnoticed.
        await AuthorizationRepository().effective(connection)
        await set_request_context(connection, original.context)
        method, channel = await verify_original(connection)
        if actor != original.context:
            await set_request_context(connection, actor)
        effective = await AuthorizationRepository().effective(connection)
        console_path = request.url.path.startswith(("/api/v1/console/", "/api/v1/admin/"))
        if console_path or channel == "web":
            if method != "password":
                raise PermissionError("请使用正式账号密码登录")
            effective.require("access.console")
        else:
            effective.require("access.mini_program")
        yield connection, effective
