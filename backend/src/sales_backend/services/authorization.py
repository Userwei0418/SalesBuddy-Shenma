"""Business services share repository-backed authorization checks."""

from sales_backend.repositories.authorization_checks import opportunity_creation_owner, require_permission

__all__ = ["opportunity_creation_owner", "require_permission"]
