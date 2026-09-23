"""Self-host workspace provisioning restricted to explicitly configured accounts.

Reuses TenantService's owner/RBAC/key initialization. The global registration and
workspace-creation switches stay unchanged. Redis serializes each creator so a
retried same-name request returns their existing workspace instead of a duplicate.
"""
from sqlalchemy import select
from werkzeug.exceptions import Conflict, Forbidden
from configs import dify_config
from extensions.ext_redis import redis_client
from models.account import Tenant, TenantAccountJoin, TenantAccountRole
from services.account_service import TenantService


def can_create_workspace(account_id: str) -> bool:
    return str(account_id) in {
        value.strip() for value in dify_config.SUPER_FDE_WORKSPACE_CREATOR_IDS.split(',') if value.strip()
    }


def create_workspace_for_account(account, name, *, session):
    if not can_create_workspace(account.id):
        raise Forbidden('This account cannot create workspaces.')
    lock = redis_client.lock(f'super-fde:workspace-create:{account.id}', timeout=120, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        raise Conflict('A workspace is already being created. Please retry shortly.')
    try:
        existing = session.scalar(select(Tenant).join(TenantAccountJoin, TenantAccountJoin.tenant_id == Tenant.id).where(
            TenantAccountJoin.account_id == account.id,
            TenantAccountJoin.role == TenantAccountRole.OWNER,
            Tenant.name == name,
        ).limit(1))
        if existing is not None:
            TenantService.switch_tenant(account, existing.id, session=session)
            return existing, False
        # This privileged path is gated by the account allowlist above. Never
        # expose is_from_dashboard as a client-supplied field.
        tenant = TenantService.create_owner_tenant(account, name=name, is_from_dashboard=True, session=session)
        return tenant, True
    finally:
        lock.release()
