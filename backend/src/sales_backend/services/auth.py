from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sales_backend.auth.models import ActorResponse, SessionResponse
from sales_backend.auth.tokens import SessionReference, TokenService
from sales_backend.config import Settings
from sales_backend.db import Database, set_request_context
from sales_backend.domain.capabilities import permission_version, role_capabilities
from sales_backend.repositories.identity import (
    ROLE_NAMES,
    SCOPE_NAMES,
    ActorRecord,
    IdentityRepository,
)
from sales_backend.repositories.passwords import PasswordRepository
from sales_backend.services.capabilities import capability_snapshot


class AuthenticationFailed(PermissionError):
    pass


class AuthService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.tokens = TokenService(settings)
        self.repository = IdentityRepository()

    async def login_demo(self, *, account_code: str, workspace: str | None, client: dict[str, str]) -> SessionResponse:
        if self.settings.auth_mode != "demo":
            raise AuthenticationFailed("demo login is disabled")
        async with self.database.connection() as connection:
            async with connection.transaction():
                record = await self.repository.find_actor_by_account(
                    connection,
                    workspace_external_id=workspace or self.settings.demo_workspace,
                    account_code=account_code,
                )
                if record is None or record.context.role.value not in {"sales", "supervisor", "manager"}:
                    raise AuthenticationFailed("account is not available")
                session_id = str(uuid.uuid4())
                issued = self.tokens.issue(record.context, session_id=session_id)
                await set_request_context(connection, record.context)
                await self.repository.create_session(
                    connection,
                    session_id=session_id,
                    actor=record.context,
                    refresh_token_hash=self.tokens.hash_refresh_token(issued.refresh_token),
                    expires_at=issued.refresh_expires_at,
                    client=client,
                )
        return self._response(record, issued)

    async def refresh(
        self, refresh_token: str, *, require_password: bool = False,
        expected_session: SessionReference | None = None,
    ) -> SessionResponse:
        new_refresh_token = secrets.token_urlsafe(48)
        new_refresh = self.tokens.hash_refresh_token(new_refresh_token)
        new_expires_at = datetime.now(UTC) + timedelta(days=self.settings.refresh_token_days)
        async with self.database.connection() as connection:
            async with connection.transaction():
                refreshed = await self.repository.rotate_refresh_token(
                    connection,
                    refresh_token=refresh_token,
                    new_hash=new_refresh,
                    new_expires_at=new_expires_at,
                )
                if refreshed is None:
                    raise AuthenticationFailed("refresh session is invalid or expired")
                actual = SessionReference.from_actor(refreshed.actor.context, refreshed.session_id)
                if expected_session is not None and actual != expected_session:
                    # This check is inside rotation's transaction, so a stale cookie
                    # cannot consume another session's refresh hash on rejection.
                    raise AuthenticationFailed("refresh session does not match browser login")
                issued = self.tokens.issue(
                    refreshed.actor.context,
                    session_id=refreshed.session_id,
                    refresh_token=new_refresh_token,
                )
                await set_request_context(connection, refreshed.actor.context)
                credentials = await PasswordRepository().session_credentials(connection, refreshed.session_id)
                if require_password and credentials.get("auth_method") != "password":
                    raise AuthenticationFailed("password session is required")
                effective = await capability_snapshot(connection, refreshed.actor.context)
                session = self._response(refreshed.actor, issued)
                # Do not commit rotation until every required response read succeeds.
                result = session.model_copy(update={
                    "actor": session.actor.model_copy(update=effective),
                    "auth_method": "password" if credentials.get("auth_method") == "password" else "demo",
                    "must_change_password": bool(credentials.get("must_change_password", False)),
                })
        return result

    async def revoke_browser_sessions(
        self, references: tuple[SessionReference, ...], refresh_token: str | None,
    ) -> tuple[ActorRecord, str] | None:
        """Same-origin logout does not require an unexpired access token.

        A verified session reference identifies the original audit actor. No rotated secret
        or access credential is returned. Signed references revoke the family even
        when a concurrent refresh has already rotated its hash.
        """
        if not references and not refresh_token:
            return None
        audit_identity = None
        async with self.database.connection() as connection:
            async with connection.transaction():
                targets = set(references)
                for reference in references:
                    profile = await self.repository.session_audit_identity(connection, reference)
                    if profile and audit_identity is None:
                        audit_identity = (profile, reference.session_id)
                if not references and refresh_token:
                    # Legacy browsers may only carry refresh. Use DB time and
                    # immediately revoke in this transaction; return no new secret.
                    expiry = await connection.fetchval(
                        "SELECT clock_timestamp()+$1::int*interval '1 day'", self.settings.refresh_token_days,
                    )
                    refreshed = await self.repository.rotate_refresh_token(
                        connection, refresh_token=refresh_token,
                        new_hash=self.tokens.hash_refresh_token(secrets.token_urlsafe(48)),
                        new_expires_at=expiry,
                    )
                    if refreshed:
                        actual = SessionReference.from_actor(refreshed.actor.context, refreshed.session_id)
                        audit_identity = (refreshed.actor, refreshed.session_id)
                        targets.add(actual)
                if audit_identity:
                    await set_request_context(connection, audit_identity[0].context)
                for reference in sorted(targets, key=lambda ref: ref.session_id):
                    await self.repository.revoke_identified_session(connection, reference)
        return audit_identity

    @staticmethod
    def actor_response(record: ActorRecord) -> ActorResponse:
        context = record.context
        capabilities = role_capabilities(context.role.value)
        return ActorResponse(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            account_code=record.account_code,
            display_name=record.display_name,
            role=context.role.value,
            role_name=record.role_title or ROLE_NAMES[context.role.value],
            data_scope=context.data_scope.value,
            scope_name=("参与客户" if context.role.value == "fde" else "FDE部门参与客户"
                        if context.role.value == "fde_lead" else SCOPE_NAMES[context.data_scope.value]),
            team_ids=list(context.team_ids),
            team_names=list(record.team_names),
            capabilities=capabilities,
            permission_version=permission_version(context, capabilities),
        )

    def _response(self, record: ActorRecord, issued: object) -> SessionResponse:
        return SessionResponse(
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            expires_at=issued.access_expires_at,
            actor=self.actor_response(record),
        )
