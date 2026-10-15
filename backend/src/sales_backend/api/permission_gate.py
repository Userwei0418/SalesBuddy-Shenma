"""Every business route has an explicit action gate; object authorization is separate."""

from fastapi import HTTPException, Request

from sales_backend.api.dependencies import bearer, get_identity
from sales_backend.domain.route_permissions import AGENT_PERMISSIONS, TASK_EVENTS, route_permission
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.permission_context import current_feature


async def enforce_route_permission(request: Request):
    endpoint = request.scope.get("endpoint")
    permission = route_permission(endpoint.__module__.rsplit(".", 1)[-1], endpoint.__name__,
                                  exporting=request.url.path.endswith("/export"))
    if permission == "*":
        yield
        return
    database = request.app.state.database
    identity = await get_identity(request, await bearer(request), database, request.app.state.settings)
    async with database.transaction(identity.actor, readonly=True) as connection:
        snapshot = await AuthorizationRepository().snapshot(connection)
    effective = AuthorizationRepository.from_snapshot(snapshot)
    request.state.permissions = effective
    console_path = request.url.path.startswith(("/api/v1/console/", "/api/v1/admin/"))
    # Only the persisted, server-chosen session channel can select a surface.
    # Headers and request bodies never turn a mini-program session into a console session.
    if console_path or identity.client_channel == "web":
        if identity.auth_method != "password":
            raise HTTPException(403, "请使用正式账号密码登录")
        effective.require("access.console")
    elif identity.client_channel == "business_web":
        effective.require("access.business_web")
    else:
        effective.require("access.mini_program")
    if permission.startswith("$"):
        body = {}
        if request.method != "GET":
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(422, "请求内容格式无效") from None
            if not isinstance(body, dict):
                raise HTTPException(422, "请求内容必须为对象")
        if permission == "$team_directory":
            from sales_backend.domain.team_directory import ASSIGNMENT_PERMISSIONS, team_directory_permission

            try:
                purpose = request.query_params.get('purpose', 'browse')
                selected = team_directory_permission(purpose, request.query_params.get('permission'))
                permission = selected if purpose == 'assignment' and selected in ASSIGNMENT_PERMISSIONS else 'directory.read'
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        elif permission == "$opportunity":
            permission = "opportunity.update" if body.get("action") == "update" else "opportunity.create"
        elif permission == "$review_target":
            if not any(effective.allows(code) for code in ("target.submit", "target.manage")):
                raise PermissionError("当前账号未获目标维护授权")
            # PostgreSQL checks the exact person/team/department scope again.
            yield
            return
        elif permission == "$task_alignment":
            if not any(effective.allows(code) for code in ("task.coordinate", "task.cancel")):
                raise PermissionError("当前账号未获任务关联维护授权")
            # The database also requires task.read and the exact coordinator or
            # creator-with-cancel permission for this specific task.
            yield
            return
        elif permission in {"$opportunity_name", "$task_choices"}:
            codes = ("opportunity.create", "opportunity.update") if permission == "$opportunity_name" else (
                "task.create_daily", "task.create_customer")
            if not any(effective.allows(code) for code in codes):
                raise PermissionError("当前账号未获此功能授权")
            yield
            return
        elif permission == "$agent":
            permission = AGENT_PERMISSIONS.get(body.get("mode"))
        elif permission in {"$task", "$task_batch"}:
            if permission == "$task_batch":
                tasks = body.get("tasks")
                if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
                    raise HTTPException(422, "请指定待办和负责人")
                # The contract requires identical task scopes; the service also checks every item.
                body = tasks[0]
            permission = "task.create_daily" if body.get("association_kind") == "daily" or (not body.get("association_kind") and not body.get("customer_id") and not body.get("opportunity_id")) else "task.create_customer"
        elif permission == "$task_event":
            permission = TASK_EVENTS.get(body.get("event_type"))
    effective.require(permission or "")
    token = current_feature.set(permission)
    try:
        yield
    finally:
        current_feature.reset(token)
