from sales_backend.domain.capabilities import FDE_ROLES, permission_version, role_capabilities
from sales_backend.repositories.capabilities import CapabilityRepository


async def capability_snapshot(connection, actor):
    state = await CapabilityRepository().fde_state(connection) if actor.role.value in FDE_ROLES else {}
    capabilities = role_capabilities(actor.role.value, visit_entry_enabled=bool(state.get("visit_entry_enabled")))
    return {
        "capabilities": capabilities,
        "permission_version": permission_version(actor, capabilities, str(state.get("permission_version", ""))),
    }


async def effective_capabilities(connection, actor):
    return (await capability_snapshot(connection, actor))["capabilities"]


async def require_capability(connection, actor, capability):
    if not (await effective_capabilities(connection, actor)).get(capability, False):
        message = "当前岗位未开启自主拜访录入" if capability.startswith("visit.") else "当前身份没有此操作权限"
        raise PermissionError(message)
