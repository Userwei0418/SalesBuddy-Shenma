import asyncio
import hashlib
from uuid import uuid4

from sales_backend.auth.tokens import SessionReference
from sales_backend.auth.passwords import DUMMY_HASH, encode_password, verify_password
from sales_backend.repositories.passwords import PasswordRepository
from sales_backend.services.auth import AuthenticationFailed, AuthService
from sales_backend.services.capabilities import capability_snapshot


class LoginThrottled(AuthenticationFailed):
    def __init__(self, scope, retry_after):
        self.scope = scope
        self.retry_after = max(1, int(retry_after))
        message = {
            "account": "该账号尝试较多",
            "network": "当前网络尝试较多",
            "account_network": "该账号和当前网络尝试较多",
            "password_change": "当前密码校验次数较多",
        }[scope]
        super().__init__(f"{message}，请 {self.retry_after} 秒后重试")


class PasswordAuthService(AuthService):
    async def login(
        self, *, account, password, workspace=None, role=None, client_ip="unknown", client_channel="web",
        previous_sessions: tuple[SessionReference, ...] = (),
    ):
        workspace = (workspace.strip() or None) if workspace else None
        account = account.strip().upper()
        def digest(text):
            return hashlib.sha256(text.encode()).hexdigest()
        ip_key = digest(f"ip:{client_ip}")
        repository = PasswordRepository()
        # Commit the attempt before authentication: failure must not roll it back.
        async with self.database.connection() as connection:
            identifier = await repository.login_identifier(connection, workspace, account)
            throttle_workspace = identifier["workspace"] if identifier else workspace or "unresolved"
            canonical_account = identifier["account_code"] if identifier else account
            account_key = digest(f"account:{throttle_workspace}:{canonical_account}")
            account_allowed = await repository.consume_attempt(connection, account_key, 5)
            ip_allowed = await repository.consume_attempt(connection, ip_key, 50)
            if not account_allowed or not ip_allowed:
                blocked = []
                if not account_allowed:
                    blocked.append(await repository.limit_status(connection, account_key, 5))
                if not ip_allowed:
                    blocked.append(await repository.limit_status(connection, ip_key, 50))
                scope = "network" if account_allowed else "account" if ip_allowed else "account_network"
                raise LoginThrottled(scope, max(item["retry_after_seconds"] for item in blocked))
            candidate = await repository.candidate(connection, workspace, account, role)
            # Console/FDE duties must not mask this person's active sales appointment
            # in the mini-program. Explicit role selection and console login stay unchanged.
            if (
                role is None and client_channel in {"wechat-mini-program", "business_web"} and candidate
                and candidate["role_code"] in {"administrator", "operations", "fde_lead", "fde"}
            ):
                for business_role in ("manager", "supervisor", "sales", "fde_lead", "fde"):
                    business = await repository.candidate(connection, workspace, account, business_role)
                    if business and (business["workspace_id"], business["user_id"]) == (candidate["workspace_id"], candidate["user_id"]):
                        candidate = business
                        break
        valid = await asyncio.to_thread(
            verify_password, password, candidate["password_hash"] if candidate else DUMMY_HASH
        )
        if not candidate or not valid:
            raise AuthenticationFailed("账号或密码错误")
        record = self.repository._actor(candidate)
        session_id = str(uuid4())
        issued = self.tokens.issue(record.context, session_id=session_id)
        async with self.database.transaction(record.context) as connection:
            if not await repository.lock_login_identifier(connection, account):
                raise AuthenticationFailed("登录账号已变更，请重新登录")
            if client_channel == "business_web":
                from sales_backend.services.authorization import require_permission
                await require_permission(connection, "access.business_web")
            await self.repository.create_session(
                connection,
                session_id=session_id,
                actor=record.context,
                refresh_token_hash=self.tokens.hash_refresh_token(issued.refresh_token),
                expires_at=issued.refresh_expires_at,
                client={"channel": client_channel},
            )
            await repository.complete_login(connection, session_id, candidate["password_hash"], account_key)
            record = await self.repository._with_role_title(connection, record)
            effective = await capability_snapshot(connection, record.context)
            # Close the previous browser family atomically with the new login. An
            # already-rotated but delayed response cannot reactivate the old family.
            for previous in previous_sessions:
                await self.repository.revoke_identified_session(connection, previous)
        session = self._response(record, issued).model_copy(update={
            "auth_method": "password", "must_change_password": candidate["must_change_password"],
        })
        session = session.model_copy(update={"actor": session.actor.model_copy(update=effective)})
        return session, candidate["must_change_password"]

    async def change_password(self, identity, old_password, new_password):
        repository = PasswordRepository()
        key = hashlib.sha256(
            f"password-change:{identity.actor.workspace_id}:{identity.actor.user_id}".encode()
        ).hexdigest()
        async with self.database.connection() as connection:
            if not await repository.consume_attempt(connection, key, 5):
                state = await repository.limit_status(connection, key, 5)
                raise LoginThrottled("password_change", state["retry_after_seconds"])
        async with self.database.transaction(identity.actor, readonly=True) as connection:
            # Workspace external ID is resolved by a narrowly scoped security function.
            candidate = await repository.candidate_for_self(connection)
        if not candidate or not await asyncio.to_thread(verify_password, old_password, candidate["password_hash"]):
            raise AuthenticationFailed("当前密码错误")
        if old_password == new_password:
            raise ValueError("新密码需要与当前密码不同")
        encoded = await asyncio.to_thread(encode_password, new_password)
        async with self.database.transaction(identity.actor) as connection:
            if not await repository.change_own(connection, candidate["password_hash"], encoded, identity.session_id):
                raise AuthenticationFailed("密码或会话已变化，请重新登录")
