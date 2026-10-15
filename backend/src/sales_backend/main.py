from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import replace

from fastapi import Depends, FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sales_backend import __version__
from sales_backend.api.unified_model_key import router as unified_model_key_router
from sales_backend.api.weekly_reports import router as weekly_reports_router
from sales_backend.api.web_auth import router as web_auth_router
from sales_backend.api.work_reports import router as work_reports_router
from sales_backend.api.work_report_feishu import router as work_report_feishu_router
from sales_backend.api.material_reference_explanation import router as material_reference_explanation_router
from sales_backend.domain.work_report import WorkReportError
from sales_backend.api.admin import router as admin_router
from sales_backend.api.platform_control import router as platform_router
from sales_backend.services.platform_control import PlatformError, verify_platform_pool
from sales_backend.services.http_limits import TenantRateLimited
from sales_backend.api.model_api import router as model_api_router
from sales_backend.api.connectivity import router as connectivity_router
from sales_backend.api.advice import router as advice_router
from sales_backend.api.agent_audit import router as agent_audit_router
from sales_backend.api.assistant import router as assistant_router
from sales_backend.api.audio import router as audio_router
from sales_backend.api.auth import router as auth_router
from sales_backend.api.authorization import router as authorization_router
from sales_backend.api.permission_gate import enforce_route_permission
from sales_backend.api.business import router as business_router
from sales_backend.api.business_activity import router as business_activity_router
from sales_backend.api.collaboration import router as collaboration_router
from sales_backend.api.company_rules import router as company_rules_router
from sales_backend.api.console_auth import router as console_auth_router
from sales_backend.api.companies import router as companies_router
from sales_backend.api.customer_assets import router as customer_assets_router
from sales_backend.api.customers import router as customers_router
from sales_backend.api.demo_scenes import router as demo_scenes_router
from sales_backend.api.detail_reads import router as detail_reads_router
from sales_backend.api.fde_profile import router as fde_profile_router
from sales_backend.api.idempotency import idempotency_conflict_handler
from sales_backend.api.notifications import router as notifications_router
from sales_backend.api.operations_accounts import router as operations_accounts_router
from sales_backend.api.feishu_sync import router as feishu_sync_router
from sales_backend.api.operations_customers import router as operations_customers_router
from sales_backend.api.operations_opportunities import router as operations_opportunities_router
from sales_backend.api.opportunity_finance import router as opportunity_finance_router
from sales_backend.api.materials import router as materials_router
from sales_backend.api.opportunity_views import router as opportunity_views_router
from sales_backend.api.opportunity_creation_history import router as opportunity_creation_history_router
from sales_backend.api.opportunity_fields import router as opportunity_fields_router
from sales_backend.api.opportunity_initialization import router as opportunity_initialization_router
from sales_backend.api.operations_visits import router as operations_visits_router
from sales_backend.api.operations_reports import router as operations_reports_router
from sales_backend.api.operations_targets import router as operations_targets_router
from sales_backend.api.partners import router as partners_router
from sales_backend.api.profile import router as profile_router
from sales_backend.api.risks import router as risks_router
from sales_backend.api.static_assets import RevalidatingStaticFiles
from sales_backend.api.targets import router as targets_router
from sales_backend.api.review import router as review_router
from sales_backend.api.review_okr import router as review_okr_router
from sales_backend.api.review_overview import router as review_overview_router
from sales_backend.api.support_access import router as support_access_router
from sales_backend.api.tenant_export import router as tenant_export_router
from sales_backend.api.closed_company_export import router as closed_export_router
from sales_backend.api.tasks import router as tasks_router
from sales_backend.api.task_imports import router as task_imports_router
from sales_backend.api.visit_flow import router as visit_flow_router
from sales_backend.api.visit_imports import router as visit_imports_router
from sales_backend.config import get_settings
from sales_backend.db import Database, DatabaseBusy, TenantInactive
from sales_backend.domain.customer_creation_options import creation_choice_error
from sales_backend.observability import (
    configure_logging,
    request_id_middleware,
    unhandled_exception_handler,
)
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.operations import OperationsError
from sales_backend.services.system_events import DatabaseEventSink


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    settings = get_settings()
    settings.require_auth()
    database = Database(settings)
    application.state.settings = settings
    application.state.database = database
    await database.connect()
    application.state.platform_database = None
    application.state.platform_mfa_cipher = None
    if settings.platform_database_url:
        platform_db = Database(replace(settings, database_url=settings.platform_database_url,
                                       database_min_pool_size=1, database_max_pool_size=3))
        try:
            await platform_db.connect()
            await verify_platform_pool(platform_db)
            from sales_backend.security.platform_mfa import PlatformMFACipher
            cipher = PlatformMFACipher.from_file(settings.platform_mfa_keyring_file, settings.platform_mfa_key_id)
            cipher._key(settings.platform_mfa_key_id)
            application.state.platform_mfa_cipher = cipher
            application.state.platform_database = platform_db
        except Exception:
            await platform_db.close()
            await database.close()
            raise
    sink = DatabaseEventSink(database)
    logging.getLogger().addHandler(sink)
    logging.getLogger("sales_backend.api").info(
        "API service started", extra={"system_event": True, "event_type": "service_start"}
    )
    try:
        yield
    finally:
        logging.getLogger("sales_backend.api").info(
            "API service stopped", extra={"system_event": True, "event_type": "service_stop"}
        )
        await sink.shutdown()
        if application.state.platform_database:
            await application.state.platform_database.close()
        await database.close()


app = FastAPI(
    title="Raccoon SalesBuddy Sales SaaS API",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
    dependencies=[Depends(enforce_route_permission)],
)

app.middleware("http")(request_id_middleware)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request, exc):
    # Pydantic errors can retain the complete request body (including raw
    # passwords before SecretStr conversion). Even msg/ctx can embed inputs.
    # Preserve field locations and machine-readable types for existing clients.
    creation_request = (request is not None and request.method == "POST"
                        and request.url.path == "/api/v1/customers/creation-requests")
    detail = []
    for error in exc.errors():
        location = list(error["loc"])
        message = "请求字段不符合要求，请检查填写内容"
        if (creation_request and error["type"] == "literal_error"
                and location in (["body", "source"], ["body", "customer_type"])):
            message = creation_choice_error(location[1]) or message
        detail.append({"loc": location, "type": error["type"], "msg": message})
    return JSONResponse(
        status_code=422,
        content={"detail": detail},
        headers={"Cache-Control": "no-store"},
    )


@app.exception_handler(RuntimeCredentialUnavailable)
async def runtime_credential_unavailable_handler(request, exc):
    return JSONResponse(
        status_code=503, content={"detail": "模型凭据当前不可用，请联系管理员检查配置", "code": str(exc)}
    )


app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_exception_handler(IdempotencyConflict, idempotency_conflict_handler)


@app.exception_handler(OperationsError)
async def operations_error_handler(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(TenantInactive)
async def tenant_inactive_handler(request, exc):
    return JSONResponse(status_code=401, content={"detail": str(exc), "code": "TENANT_INACTIVE"},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(DatabaseBusy)
async def database_busy_handler(request, exc):
    return JSONResponse(status_code=503,
                        content={"detail": "系统繁忙，请稍后查看处理结果后再操作", "code": "DATABASE_BUSY"},
                        headers={"Cache-Control": "no-store", "Retry-After": "5"})


@app.exception_handler(PermissionError)
async def business_permission_handler(request, exc):
    """Service capability checks share the same transport contract across read models."""
    return JSONResponse(status_code=403, content={"detail": str(exc)})


app.mount(
    "/admin/assets",
    RevalidatingStaticFiles(directory=Path(__file__).parent / "web" / "assets"),
    name="console-assets",
)

@app.exception_handler(PlatformError)
async def platform_error_handler(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": exc.code}, headers={"Cache-Control": "no-store"})


@app.exception_handler(TenantRateLimited)
async def tenant_rate_limit_handler(request, exc):
    return JSONResponse(status_code=429,
                        content={'detail': str(exc), 'code': 'TENANT_RATE_LIMITED', 'retry_after': exc.retry_after},
                        headers={'Retry-After': str(exc.retry_after), 'Cache-Control': 'no-store'})


from sales_backend.services.model_calls import CallQuotaExceeded
from sales_backend.services.ai_service_policy import AIServiceUnavailable


@app.exception_handler(AIServiceUnavailable)
async def ai_service_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": "AI_SERVICE_NOT_CONFIGURED"},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(CallQuotaExceeded)
async def call_quota_error_handler(request, exc):
    return JSONResponse(status_code=409, content={'detail': str(exc), 'code': 'TENANT_CALL_QUOTA_EXCEEDED'},
                        headers={'Cache-Control': 'no-store'})


@app.exception_handler(WorkReportError)
async def work_report_error_handler(request, exc):
    body = {"detail": exc.detail, "code": exc.code, "retryable": exc.retryable}
    if exc.current_version is not None:
        body["current_version"] = exc.current_version
    return JSONResponse(status_code=exc.status, content=body)


app.include_router(unified_model_key_router)
app.include_router(weekly_reports_router)
app.include_router(web_auth_router)
app.include_router(work_reports_router)
app.include_router(work_report_feishu_router)
app.include_router(material_reference_explanation_router)
app.include_router(platform_router)
app.include_router(demo_scenes_router)
app.include_router(targets_router)
app.include_router(review_router)
app.include_router(review_okr_router)
app.include_router(review_overview_router)
app.include_router(operations_targets_router)
app.include_router(auth_router)
app.include_router(authorization_router)
app.include_router(console_auth_router)
app.include_router(companies_router)
app.include_router(operations_customers_router)
app.include_router(operations_accounts_router)
app.include_router(feishu_sync_router)
app.include_router(operations_reports_router)
app.include_router(operations_opportunities_router)
app.include_router(opportunity_fields_router)
app.include_router(opportunity_initialization_router)
app.include_router(opportunity_finance_router)
app.include_router(materials_router)
app.include_router(opportunity_views_router)
app.include_router(opportunity_creation_history_router)
app.include_router(operations_visits_router)
app.include_router(partners_router)
app.include_router(business_activity_router)
app.include_router(admin_router)
app.include_router(model_api_router)
app.include_router(connectivity_router)
app.include_router(advice_router)
app.include_router(agent_audit_router)
app.include_router(company_rules_router)
app.include_router(assistant_router)
app.include_router(audio_router)
app.include_router(business_router)
app.include_router(collaboration_router)
app.include_router(fde_profile_router)
app.include_router(customers_router)
app.include_router(detail_reads_router)
app.include_router(customer_assets_router)
app.include_router(notifications_router)
app.include_router(profile_router)
app.include_router(risks_router)
app.include_router(tasks_router)
app.include_router(task_imports_router)
app.include_router(tenant_export_router)
app.include_router(closed_export_router)
app.include_router(support_access_router)
app.include_router(visit_imports_router)
app.include_router(visit_flow_router)


@app.get("/api/v1/health/live", tags=["Ops"])
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/version", tags=["Ops"])
async def health_version() -> dict:
    """Imported release identity; expected_schema is not a database migration probe."""
    from sales_backend.release_identity import RELEASE_IDENTITY
    return RELEASE_IDENTITY


@app.get("/api/v1/health/ready", tags=["Ops"])
async def health_ready(response: Response) -> dict[str, object]:
    settings = get_settings()
    database_ready = False
    database = getattr(app.state, "database", None)
    if database is not None and database.pool is not None:
        try:
            async with database.connection() as connection:
                database_ready = bool(await connection.fetchval("SELECT true"))
        except Exception:
            database_ready = False
    checks = {
        "database": database_ready,
        "model_gateway_configured": settings.model_gateway_configured,
        "auth_configured": len(settings.access_token_secret) >= 32,
    }
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "degraded", "checks": checks}


@app.get("/connection-status", include_in_schema=False)
async def connection_status():
    response = Response()
    health = await health_ready(response)
    checks = health["checks"]
    return JSONResponse({"configured": bool(get_settings().database_url),
        "reachable": bool(checks["database"] and checks["auth_configured"]), "checks": checks,
        "previewOnly": False, "environment": "神码 SalesBuddy", "label": "神码 SalesBuddy",
        "apiPrefix": "/api/v1", "localQuickLogin": {"available": False}},
        headers={"Cache-Control": "no-store"})


@app.get("/web-capabilities", include_in_schema=False)
async def web_capabilities():
    return JSONResponse({"previewOnly": False, "adminUrl": "/admin"}, headers={"Cache-Control": "no-store"})


BUSINESS_WEB_DIST = Path(__file__).resolve().parents[3] / "frontend" / "web" / "dist"
if BUSINESS_WEB_DIST.is_dir():
    app.mount("/workspace", RevalidatingStaticFiles(directory=BUSINESS_WEB_DIST, html=True), name="business-workspace")

if BUSINESS_WEB_DIST.is_dir():
    app.mount("/web", RevalidatingStaticFiles(directory=BUSINESS_WEB_DIST, html=True), name="web-workspace")
