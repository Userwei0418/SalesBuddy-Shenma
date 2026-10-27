"""Purpose-bound credentials with no ordinary actor, role or refresh authority."""
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt

from sales_backend.auth.tokens import TokenError


@dataclass(frozen=True, slots=True)
class RestrictedIdentity:
    workspace_id: str
    identity_id: str
    session_id: str
    permission_version: int


class RestrictedTokens:
    def __init__(self, settings):
        settings.require_auth()
        self.settings = settings

    def issue(self, identity: RestrictedIdentity, expires_at: datetime) -> str:
        now = datetime.now(UTC)
        return jwt.encode({
            "iss": self.settings.access_token_issuer,
            "aud": self.settings.access_token_audience + ":demo-readonly",
            "purpose": "demo-readonly", "sub": identity.identity_id,
            "wid": identity.workspace_id, "sid": identity.session_id,
            "pv": identity.permission_version, "iat": now, "nbf": now, "exp": expires_at,
        }, self.settings.access_token_secret, algorithm="HS256")

    def decode(self, token: str, *, verify_exp: bool = True) -> RestrictedIdentity:
        try:
            claims = jwt.decode(
                token, self.settings.access_token_secret, algorithms=["HS256"],
                audience=self.settings.access_token_audience + ":demo-readonly",
                issuer=self.settings.access_token_issuer,
                options={"verify_exp": verify_exp,
                         "require": ["exp", "iat", "nbf", "sub", "wid", "sid", "pv", "purpose"]},
            )
            if claims["purpose"] != "demo-readonly" or type(claims["pv"]) is not int or claims["pv"] < 1:
                raise ValueError("invalid purpose/version")
            return RestrictedIdentity(str(UUID(claims["wid"])), str(UUID(claims["sub"])),
                                      str(UUID(claims["sid"])), claims["pv"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise TokenError("restricted session is invalid or expired") from exc
