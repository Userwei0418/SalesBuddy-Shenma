from sales_backend.repositories.authorization import AuthorizationRepository

LEGACY_ACTIONS = {
    "customer.read": ("customer.read",), "customer.create": ("customer.create",), "customer.edit": ("customer.update",),
    "customer.claim": ("customer.claim",), "opportunity.read": ("opportunity.read",),
    "opportunity.edit": ("opportunity.create", "opportunity.update"), "fde.members.manage": ("opportunity.fde_members",),
    "visit.create": ("visit.create",), "visit.supplement": ("visit.supplement",),
    "task.create": ("task.create_daily", "task.create_customer"),
    "task.respond": ("task.accept", "task.decline", "task.complete", "task.review"),
    "task.coordinate": ("task.coordinate",), "risk.resolve": ("risk.resolve",), "advice.decide": ("advice.decide",),
    "actual.manage": ("actual.create", "actual.void"), "console.access": ("access.console",),
}


def client_export_permissions(effective):
    """Whole client-generated exports require explicit workspace export scope.

    Narrow grants need a server export implementation with per-object checks;
    clients must not invent scope unions or filter protected historical records.
    """
    return {code: any(g.scope == "workspace" for g in effective.for_permission(code))
            for code in ("visit.export", "report.export")}


async def capability_snapshot(connection, actor):
    snapshot = await AuthorizationRepository().snapshot(connection)
    effective = AuthorizationRepository.from_snapshot(snapshot)
    permissions = effective.capabilities()
    capabilities = {name: any(permissions.get(code, False) for code in codes) for name, codes in LEGACY_ACTIONS.items()}
    capabilities["team.view"] = any(grant.scope in {"teams", "workspace"}
        for code in ("dashboard.read", "profile.sales_read", "profile.fde_read", "battle_map.read", "task.read")
        for grant in effective.for_permission(code))
    read_grants = effective.for_permission("overview.read")
    scopes = {grant.scope for grant in read_grants}
    scope_name = ("全公司" if "workspace" in scopes else "本部门" if scopes == {"teams"}
                  else "仅本人" if scopes == {"self"} else "关联业务" if scopes == {"assigned"}
                  else "已授权业务范围" if scopes else "无业务读取权限")
    return {
        "scope_name": scope_name,
        "client_export_permissions": client_export_permissions(effective),
        "capabilities": capabilities,
        "permissions": permissions,
        "permission_grants": snapshot["grants"],
        "permission_version": snapshot["permission_version"],
    }


async def effective_capabilities(connection, actor):
    return (await capability_snapshot(connection, actor))["capabilities"]


async def require_capability(connection, actor, capability):
    snapshot = await capability_snapshot(connection, actor)
    if not {**snapshot["capabilities"], **snapshot["permissions"]}.get(capability, False):
        message = "当前岗位未开启自主拜访录入" if capability.startswith("visit.") else "当前身份没有此操作权限"
        raise PermissionError(message)
