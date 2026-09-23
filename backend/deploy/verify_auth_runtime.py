"""Fail closed before stopping the current release; never print credentials."""
import json

from sales_backend.config import get_settings


def main():
    try:
        settings = get_settings()
        # This probe gates a production release. Generic Settings intentionally
        # supports explicit development/test demo mode; it is not valid here.
        if settings.app_env != "production" or settings.auth_mode != "password":
            raise RuntimeError("production deployment requires production/password")
        settings.require_auth()
    except (RuntimeError, ValueError, TypeError):
        raise SystemExit("Authentication configuration rejected: check APP_ENV=production, explicit AUTH_MODE=password and signing secret") from None
    print(json.dumps({"app_env": settings.app_env, "auth_mode": settings.auth_mode,
                      "signing_secret_configured": True}, sort_keys=True))


if __name__ == "__main__":
    main()
