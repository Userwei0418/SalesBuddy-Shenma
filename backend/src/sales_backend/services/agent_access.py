"""Authorize FDE analysis against the current database state at every write boundary."""

from sales_backend.domain.agent import AgentMode
from sales_backend.domain.capabilities import FDE_ROLES
from sales_backend.domain.policy import assert_mode_allowed
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.services.capabilities import require_capability
from sales_backend.services.visit_access import require_visit_recording_scope


async def require_agent_access(
    connection, actor, mode, customer_id=None, *, opportunity_id=None, permission_version=None
):
    assert_mode_allowed(actor.role, AgentMode(mode))
    if mode == "visit_entry" and not (customer_id or "").strip():
        raise ValueError("录入拜访必须先选择客户")
    if mode == "customer_chatbi" and not (customer_id or "").strip():
        raise PermissionError("客户问数必须先选择客户")
    if actor.role.value not in FDE_ROLES:
        return
    if mode == "visit_entry":
        await require_capability(connection, actor, "visit.create")
        await require_visit_recording_scope(connection, actor, customer_id, opportunity_id)
    repo = CapabilityRepository()
    if customer_id and not await repo.has_customer_access(connection, customer_id):
        raise PermissionError("客户已不在当前授权范围内")
    if permission_version is not None:
        current = await repo.analysis_identity(connection, actor)
        if current.get("permission_version") != permission_version:
            raise PermissionError("FDE权限已变化，请重新发起分析")
