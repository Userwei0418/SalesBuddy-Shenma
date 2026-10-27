from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.company_follow_up import available as company_follow_up_available, customer_available, RECORDING_ACTIONS

LEGACY_ACTIONS = {
    "customer.read": ("customer.read",), "customer.create": ("customer.create",), "customer.edit": ("customer.update",),
    "customer.claim": ("customer.claim",), "opportunity.read": ("opportunity.read",),
    "opportunity.edit": ("opportunity.create", "opportunity.update"),
    "fde.members.manage": ("opportunity.fde_members",),
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


async def capability_snapshot(connection, actor, *, include_execution=True):
    snapshot = await AuthorizationRepository().snapshot(connection)
    effective = AuthorizationRepository.from_snapshot(snapshot)
    permissions = effective.capabilities()
    capabilities = {name: any(permissions.get(code, False) for code in codes) for name, codes in LEGACY_ACTIONS.items()}
    capabilities["company_opportunity_follow_up"] = await company_follow_up_available(connection)
    capabilities["company_customer_follow_up"] = await customer_available(connection)
    from sales_backend.services.sales_opportunity_visibility import available as shared_read_available

    capabilities["sales_shared_opportunities"] = await shared_read_available(connection)
    from sales_backend.services.customer_claim_visit_read import available as claim_visit_read_available

    capabilities["customer_claim_visit_read"] = await claim_visit_read_available(connection)
    if include_execution:
        available = await connection.fetchval("""SELECT bool_and(COALESCE(
            has_function_privilege(current_user,to_regprocedure(signature),'EXECUTE'),false))
            FROM unnest(ARRAY['workflow.task_execution_read(uuid)',
              'workflow.task_execution_summary(uuid)',
              'workflow.task_execution_command(uuid,text,jsonb,uuid)',
              'workflow.task_execution_initialize(uuid,jsonb)',
              'workflow.task_execution_candidates(uuid,text,text,integer,integer)']) signature""")
        creation_enabled = await connection.fetchval(
            "SELECT security.workspace_module_enabled(common.current_workspace_id(),'task') "
            "AND security.workspace_module_enabled(common.current_workspace_id(),'task_execution_v1')"
        )
        v2_available = await connection.fetchval("""SELECT COALESCE(has_function_privilege(
            current_user,to_regprocedure('workflow.task_execution_initialize_v2(uuid,jsonb,boolean)'),
            'EXECUTE'),false)""")
        v2_creation = await connection.fetchval(
            "SELECT security.workspace_module_enabled(common.current_workspace_id(),'task')"
        ) if v2_available is True else False
        # Rollout governs new V1 tasks only. Existing tasks route by their persisted mode.
        capabilities["task_execution"] = {
            "protocol_version": 1,
            "available": available is True,
            "legacy_available": True,
            "default_creation_mode": "legacy",
            "creation_enabled": available is True and creation_enabled is True,
        }
        if available is True and v2_available is True:
            capabilities["task_execution"].update({
                # Existing Mini clients require protocol_version=1 for their V1
                # executor and interpret creation_enabled as the V1 pilot gate.
                "supported_versions": [1, 2],
                "default_creation_mode": "execution_v2",
                "v2_creation_enabled": v2_creation is True,
                "v1_creation_enabled": creation_enabled is True,
                "checklist_owner_editable": True,
            })
        v3_available = await connection.fetchval("""SELECT bool_and(COALESCE(
            has_function_privilege(current_user,to_regprocedure(signature),'EXECUTE'),false))
            FROM unnest(ARRAY['workflow.task_execution_initialize_v3(uuid,jsonb)',
              'workflow.task_execution_command_v3(uuid,text,jsonb,uuid)',
              'workflow.task_execution_adopt_v3(uuid,jsonb,uuid)']) signature""")
        if available is True and v3_available is True:
            v3_creation = await connection.fetchval(
                "SELECT security.workspace_module_enabled(common.current_workspace_id(),'task')")
            capabilities["task_execution"].update({
                "supported_versions": [1, 2, 3], "default_creation_mode": "execution_v3",
                "v3_creation_enabled": v3_creation is True,
                # Existing tasks remain readable/writable by their persisted protocol.
                "creation_enabled": False, "v1_creation_enabled": False, "v2_creation_enabled": False,
            })
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
    snapshot = await capability_snapshot(connection, actor, include_execution=False)
    if capability in RECORDING_ACTIONS and snapshot["capabilities"]["company_opportunity_follow_up"]:
        return
    if not {**snapshot["capabilities"], **snapshot["permissions"]}.get(capability, False):
        message = "当前岗位未开启自主拜访录入" if capability.startswith("visit.") else "当前身份没有此操作权限"
        raise PermissionError(message)
