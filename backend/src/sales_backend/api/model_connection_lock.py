"""Deployment policy independent of company roles and delegated permissions."""

from fastapi import Depends, HTTPException, Request

from sales_backend.api.dependencies import get_settings
from sales_backend.config import Settings

LOCK_MESSAGE = "模型连接由维护方统一管理，当前不允许修改或测试配置。"


def connection_policy(settings: Settings) -> dict:
    return {
        "locked": settings.model_connections_locked,
        "lock_message": LOCK_MESSAGE if settings.model_connections_locked else "",
    }


def require_model_connection_unlocked(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if settings.model_connections_locked and request.method not in {"GET", "HEAD", "OPTIONS"}:
        raise HTTPException(403, LOCK_MESSAGE)


def require_direct_connection_unlocked(
    request: Request, kind: str, settings: Settings = Depends(get_settings),
) -> None:
    if kind == "direct":
        require_model_connection_unlocked(request, settings)
