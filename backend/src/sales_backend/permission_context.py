"""Trusted server route context; never populated from request headers or payloads."""
from contextvars import ContextVar

current_feature: ContextVar[str] = ContextVar("authorized_feature", default="")
