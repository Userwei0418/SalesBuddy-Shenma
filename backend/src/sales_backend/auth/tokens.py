from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from sales_backend.config import Settings
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode


class TokenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionReference:
    """Signed session identifier for comparison/revocation, never authentication."""

    workspace_id: str
    user_id: str
    session_id: str

    @classmethod
    def from_actor(cls, actor: ActorContext, session_id: str) -> SessionReference:
        return cls(actor.workspace_id, actor.user_id, session_id)


class TokenService:
    def __init__(self, settings: Settings):
        settings.require_auth()
        self.settings = settings

    def issue(self, actor: ActorContext, *, session_id: str, refresh_token: str | None = None) -> IssuedTokens:
        now = datetime.now(UTC)
        access_expires_at = now + timedelta(minutes=self.settings.access_token_minutes)
        refresh_expires_at = now + timedelta(days=self.settings.refresh_token_days)
        claims: dict[str, Any] = {
            "iss": self.settings.access_token_issuer,
            "aud": self.settings.access_token_audience,
            "sub": actor.user_id,
            "wid": actor.workspace_id,
            "role": actor.role.value,
            "scope": actor.data_scope.value,
            "teams": list(actor.team_ids),
            "sid": session_id,
            "iat": now,
            "nbf": now,
            "exp": access_expires_at,
        }
        access_token = jwt.encode(claims, self.settings.access_token_secret, algorithm="HS256")
        return IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token or secrets.token_urlsafe(48),
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )

    def decode_access_token(self, token: str) -> tuple[ActorContext, str]:
        try:
            claims = jwt.decode(
                token,
                self.settings.access_token_secret,
                algorithms=["HS256"],
                audience=self.settings.access_token_audience,
                issuer=self.settings.access_token_issuer,
                options={"require": ["exp", "iat", "sub", "wid", "role", "scope", "sid"]},
            )
            actor = ActorContext(
                workspace_id=claims["wid"],
                user_id=claims["sub"],
                role=RoleCode(claims["role"]),
                data_scope=DataScope(claims["scope"]),
                team_ids=tuple(claims.get("teams") or ()),
            )
            return actor, str(claims["sid"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise TokenError("access token is invalid or expired") from exc

    def issue_console_binding(self, reference: SessionReference) -> str:
        # No authorization lifetime: this is only an immutable identifier. Refresh
        # still requires a live, unexpired DB session and its rotating secret.
        now = datetime.now(UTC)
        return jwt.encode({
            "iss": self.settings.access_token_issuer,
            "aud": self.settings.access_token_audience + ":console-binding",
            "purpose": "console-session-binding",
            "wid": reference.workspace_id, "sub": reference.user_id,
            "sid": reference.session_id, "iat": now, "nbf": now,
        }, self.settings.access_token_secret, algorithm="HS256")

    def decode_console_binding(self, token: str) -> SessionReference:
        try:
            claims = jwt.decode(
                token, self.settings.access_token_secret, algorithms=["HS256"],
                audience=self.settings.access_token_audience + ":console-binding",
                issuer=self.settings.access_token_issuer,
                options={"require": ["iat", "sub", "wid", "sid", "purpose"]},
            )
            if claims["purpose"] != "console-session-binding":
                raise ValueError("incorrect token purpose")
            return self._session_reference(claims)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise TokenError("console session binding is invalid") from exc

    def identify_access_session_for_revocation(self, token: str) -> SessionReference:
        """Expired, correctly signed access may revoke ONLY its own session.

        Returns no role, scope or capability; normal API authentication continues
        to use decode_access_token with mandatory expiry verification.
        """
        try:
            claims = jwt.decode(
                token, self.settings.access_token_secret, algorithms=["HS256"],
                audience=self.settings.access_token_audience,
                issuer=self.settings.access_token_issuer,
                options={"verify_exp": False,
                         "require": ["exp", "iat", "sub", "wid", "role", "scope", "sid"]},
            )
            return self._session_reference(claims)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise TokenError("session revocation proof is invalid") from exc

    @staticmethod
    def _session_reference(claims: dict[str, Any]) -> SessionReference:
        return SessionReference(
            str(UUID(claims["wid"])), str(UUID(claims["sub"])), str(UUID(claims["sid"])),
        )

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
