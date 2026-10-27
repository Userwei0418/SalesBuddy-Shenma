from uuid import uuid4

from sales_backend.auth.models import RestrictedSessionInfo, RestrictedSessionResponse
from sales_backend.auth.restricted import RestrictedIdentity, RestrictedTokens
from sales_backend.repositories.restricted import RestrictedRepository
from sales_backend.services.auth import AuthenticationFailed


class RestrictedAccessError(AuthenticationFailed):
    def __init__(self, code: str, status: int, message: str):
        self.code, self.status = code, status
        super().__init__(message)


class RestrictedService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.repository = RestrictedRepository()

    async def login_verified(self, candidate, *, channel, protocol, attempt_key):
        # Called only AFTER password verification, never reveals account type to
        # unauthenticated attempts. Account state must also pass the locked SQL check.
        if not candidate.get("enabled"):
            raise AuthenticationFailed("请核对账号和密码，或从所属公司的专属入口登录")
        if channel != "wechat-mini-program":
            raise RestrictedAccessError("RESTRICTED_CHANNEL_UNSUPPORTED", 403, "该账号暂不支持此端登录")
        if protocol != 1:
            raise RestrictedAccessError("RESTRICTED_CLIENT_UPDATE_REQUIRED", 409, "请更新小程序后重新登录")
        async with self.database.connection() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL statement_timeout = '5s'")
                await connection.execute("SET LOCAL lock_timeout = '1s'")
                row = await self.repository.create_session(connection, str(uuid4()), candidate, attempt_key)
                if not row:
                    raise AuthenticationFailed("账号状态已变化，请重新登录")
                # The restricted security function performs the credential race
                # check and clears no unrelated login/session families.
        info = RestrictedSessionInfo.model_validate(row)
        identity = RestrictedIdentity(candidate["workspace_id"], info.identity_id,
                                      info.session_id, candidate["permission_version"])
        return RestrictedSessionResponse(**info.model_dump(),
            access_token=RestrictedTokens(self.settings).issue(identity, info.expires_at))

    async def validate(self, connection, identity):
        row = await self.repository.session(connection, identity)
        if not row:
            raise RestrictedAccessError("RESTRICTED_SESSION_EXPIRED", 401, "登录已失效，请重新登录")
        return RestrictedSessionInfo.model_validate(row)

    async def admit(self, identity):
        # Commit bounded admission independently of readonly business queries.
        # Shared account devices have independent budgets; no normal actor context.
        async with self.database.connection() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL statement_timeout = '5s'")
                await connection.execute("SET LOCAL lock_timeout = '1s'")
                admitted = await self.repository.admit(connection, identity)
        # Rejected admissions still count, and business failures never roll back
        # the independent rate budget.
        if not admitted:
            raise RestrictedAccessError("RESTRICTED_RATE_LIMITED", 429, "请求较多，请稍后重试")

    async def read(self, identity, resource, resource_id=None, query="", offset=0, limit=20):
        async with self.database.connection() as connection:
            async with connection.transaction(readonly=True):
                await connection.execute("SET LOCAL statement_timeout = '5s'")
                await connection.execute("SET LOCAL lock_timeout = '1s'")
                await self.validate(connection, identity)
                result = await self.repository.read(connection, identity, resource, resource_id, query, offset, limit)
                if result is None:
                    # A concurrent disable/revoke wins even if it occurred after
                    # the first validation. Never fall back to ordinary reads.
                    await self.validate(connection, identity)
                    raise RestrictedAccessError("RESTRICTED_RECORD_UNAVAILABLE", 404, "该记录不存在或当前不可查看")
                return result
