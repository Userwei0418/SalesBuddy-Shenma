"""Every business route has an explicit action gate; object authorization is separate."""

from contextlib import asynccontextmanager, nullcontext
from uuid import UUID

from fastapi import HTTPException, Request

from sales_backend.api.dependencies import bearer, get_identity
from sales_backend.domain.route_permissions import (
    TASK_EVENTS,
    TASK_EXECUTION_ACTIONS,
    agent_permission,
    route_permission,
)
from sales_backend.db import json_value
from sales_backend.permission_context import current_feature
from sales_backend.read_budget import BOUNDED_READ_ENDPOINTS, read_budget
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.company_follow_up import available as company_follow_up_available, customer_available


# These entry points still validate the exact target, owner and requested action
# in their services/RLS. The capability never grants a general directory/read/AI
# permission and cannot be used for original downloads or opportunity edits.
COMPANY_FOLLOW_UP_ROUTES = frozenset({
    ("visit_flow", "structure"), ("visit_flow", "quality"),
    ("business", "visit_form_schema"), ("business", "create_visit"),
    ("business", "business_options"), ("business", "visit_colleagues"),
    ("collaboration", "directory_fde"),
    ("audio", "transcribe_audio"), ("audio", "get_audio_operation"),
    ("visit_imports", "upload_visit_file"), ("visit_imports", "upload_request_status"),
    ("visit_imports", "import_status"), ("visit_imports", "retry_import"),
})


async def own_visit_agent_resource(connection, identity, handler, params):
    """Narrow history fallback to the caller's visit-entry conversations/runs."""
    key = "run_id" if handler == "get_run" else "conversation_id"
    try:
        resource_id = UUID(str(params.get(key, "")))
    except ValueError:
        raise HTTPException(422, "请选择有效的跟进处理记录") from None
    return await connection.fetchval(
        "SELECT security.company_opportunity_follow_up_processing($1::uuid,$2::uuid)",
        resource_id if handler == "get_run" else None,
        None if handler == "get_run" else resource_id,
    ) is True


async def enforce_route_permission(request: Request):
    endpoint = request.scope.get("endpoint")
    bounded = request.method == "GET" and (
        endpoint.__module__.rsplit(".", 1)[-1], endpoint.__name__
    ) in BOUNDED_READ_ENDPOINTS
    # Run in the endpoint task: cancellation unwinds transaction contexts. This
    # is deliberately not a middleware timeout around streaming/background work.
    async with read_budget() if bounded else nullcontext():
        async with asynccontextmanager(_enforce_route_permission)(request):
            yield


async def _enforce_route_permission(request: Request):
    endpoint = request.scope.get("endpoint")
    module, handler = endpoint.__module__.rsplit(".", 1)[-1], endpoint.__name__
    permission = route_permission(module, handler,
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
    company_authorized = False
    if permission.startswith("$"):
        body = {}
        if request.method != "GET" and permission != "$knowledge_upload":
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(422, "请求内容格式无效") from None
            if not isinstance(body, dict):
                raise HTTPException(422, "请求内容必须为对象")
        if permission in {"$company_follow_up", "$company_customer_follow_up"}:
            async with database.transaction(identity.actor, readonly=True) as connection:
                company_authorized = await (customer_available(connection) if permission == "$company_customer_follow_up"
                                            else company_follow_up_available(connection))
            if not company_authorized:
                raise PermissionError("当前账号不可新增跟进")
            permission = "visit.create"
        elif permission == "$knowledge_upload":
            effective.require("knowledge.read")
            if not any(effective.allows(code) for code in ("knowledge.create", "knowledge.edit")):
                raise PermissionError("当前账号未获知识材料上传授权")
            permission = "knowledge.read"
        elif permission == "$team_directory":
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
            if body.get("mode") == "visit_entry":
                async with database.transaction(identity.actor, readonly=True) as connection:
                    company_authorized = await company_follow_up_available(connection)
            permission = "visit.structure" if company_authorized else agent_permission(body.get("mode"), effective)
        elif permission in {"$task", "$task_batch"}:
            if permission == "$task_batch":
                tasks = body.get("tasks")
                if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
                    raise HTTPException(422, "请指定待办和负责人")
                # The contract requires identical task scopes; the service also checks every item.
                body = tasks[0]
            permission = "task.create_daily" if body.get("association_kind") == "daily" or (not body.get("association_kind") and not body.get("customer_id") and not body.get("opportunity_id")) else "task.create_customer"
        elif permission == "$task_execution":
            codes = TASK_EXECUTION_ACTIONS.get(request.path_params.get("action"))
            if codes is None:
                raise HTTPException(404, {"code": "TASK_EXECUTION_UNKNOWN_ACTION"})
            effective.require("task.read")
            if not any(effective.allows(code) for code in codes):
                raise PermissionError("当前账号未获此任务操作授权")
            # Preserve the surface gate above; SQL rechecks action-specific scope.
            permission = "task.read"
        elif permission == "$task_event":
            permission = TASK_EVENTS.get(body.get("event_type"))
    if not company_authorized and not effective.allows(permission or ""):
        selected_route = (module, handler) in COMPANY_FOLLOW_UP_ROUTES
        visit_history = module == "assistant" and handler in {"get_run", "send_message", "list_messages"}
        if selected_route or visit_history:
            async with database.transaction(identity.actor, readonly=True) as connection:
                company_authorized = await company_follow_up_available(connection)
                if company_authorized and visit_history:
                    company_authorized = await own_visit_agent_resource(
                        connection, identity, handler, request.path_params,
                    )
    if not company_authorized and not effective.allows(permission or "") and (module, handler) in {
        ("business", "opportunities"), ("business", "opportunities_overview"), ("business", "opportunity_detail"),
        ("detail_reads", "opportunity_header"), ("detail_reads", "opportunity_overview"),
        ("detail_reads", "customer_opportunity_header"), ("detail_reads", "customer_opportunity_overview"),
        ("detail_reads", "customer_opportunities"),
        ("opportunity_views", "options"), ("opportunity_views", "query"),
        ("opportunity_views", "preference"), ("opportunity_views", "list_views"), ("opportunity_views", "detail"),
    }:
        from sales_backend.services.sales_opportunity_visibility import available as shared_read_available

        async with database.transaction(identity.actor, readonly=True) as connection:
            company_authorized = await shared_read_available(connection)
    if not company_authorized and not effective.allows(permission or "") and (module, handler) in {
        ("business", "visit_range"), ("business", "get_visit"), ("detail_reads", "visit_history"),
    }:
        from sales_backend.services.customer_claim_visit_read import available as claim_visit_read_available

        # This admits only the read entry point. Live claim ownership and the
        # formal-record predicate are checked for every returned row by RLS.
        async with database.transaction(identity.actor, readonly=True) as connection:
            company_authorized = await claim_visit_read_available(connection)
    if not company_authorized:
        effective.require(permission or "")
    # Opportunity sharing is a core-detail read. These existing auxiliary
    # endpoints retain the original object scope even for callers who already
    # had the generic opportunity.read feature before this change.
    if (module, handler) in {
        ("opportunity_access", "access"),
        ("opportunity_fields", "detail"), ("opportunity_fields", "change_history"),
        ("opportunity_fields", "intent_history"), ("operations_opportunities", "detail"),
        ("materials", "opportunity_materials"), ("materials", "material_forecast_preview"),
    } and request.path_params.get("opportunity_id"):
        async with database.transaction(identity.actor, readonly=True) as connection:
            context = json_value(await connection.fetchval(
                "SELECT security.opportunity_read_context($1::uuid)",
                UUID(str(request.path_params["opportunity_id"])),
            ))
            if context and context.get("shared_read_only"):
                raise PermissionError("当前商机可查看，但未获此附属信息的权限")
    token = current_feature.set(permission)
    try:
        yield
    finally:
        current_feature.reset(token)
