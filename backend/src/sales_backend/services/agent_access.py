"""Authorize FDE analysis against the current database state at every write boundary."""

from sales_backend.domain.route_permissions import AGENT_PERMISSIONS
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.services.capabilities import require_capability
from sales_backend.services.visit_access import require_visit_recording_scope
from sales_backend.services.authorization import require_permission


async def require_agent_access(
    connection, actor, mode, customer_id=None, *, opportunity_id=None, permission_version=None
):
    await require_permission(connection, AGENT_PERMISSIONS.get(mode, ""))
    if mode == "opportunity_draft":
        await require_permission(connection, "opportunity.create")
    if mode == "visit_entry" and not (customer_id or "").strip():
        raise ValueError("录入拜访必须先选择客户")
    if mode == "customer_chatbi" and not (customer_id or "").strip():
        raise PermissionError("客户问数必须先选择客户")
    if mode == "visit_entry":
        await require_capability(connection, actor, "visit.create")
        await require_visit_recording_scope(connection, actor, customer_id, opportunity_id)
    repo = CapabilityRepository()
    if customer_id and mode not in {'visit_entry','opportunity_draft','customer_create'}:
        await require_permission(connection, AGENT_PERMISSIONS[mode], customer_id=customer_id)
    if permission_version is not None:
        current = await repo.analysis_identity(connection, actor)
        if current.get("permission_version") != permission_version:
            raise PermissionError("权限已变化，请重新发起分析")
