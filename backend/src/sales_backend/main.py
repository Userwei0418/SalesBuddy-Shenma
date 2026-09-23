from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse

from sales_backend import __version__
from sales_backend.api.admin import router as admin_router
from sales_backend.api.model_api import router as model_api_router
from sales_backend.api.connectivity import router as connectivity_router
from sales_backend.api.advice import router as advice_router
from sales_backend.api.agent_audit import router as agent_audit_router
from sales_backend.api.assistant import router as assistant_router
from sales_backend.api.audio import router as audio_router
from sales_backend.api.auth import router as auth_router
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
from sales_backend.api.operations_reports import router as operations_reports_router
from sales_backend.api.operations_targets import router as operations_targets_router
from sales_backend.api.partners import router as partners_router
from sales_backend.api.profile import router as profile_router
from sales_backend.api.risks import router as risks_router
from sales_backend.api.static_assets import RevalidatingStaticFiles
from sales_backend.api.targets import router as targets_router
from sales_backend.api.tasks import router as tasks_router
from sales_backend.api.visit_flow import router as visit_flow_router
from sales_backend.api.visit_imports import router as visit_imports_router
from sales_backend.config import get_settings
from sales_backend.db import Database
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
        await database.close()


app = FastAPI(
    title="销售智助 Sales SaaS API",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.middleware("http")(request_id_middleware)


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


@app.exception_handler(PermissionError)
async def business_permission_handler(request, exc):
    """Service capability checks share the same transport contract across read models."""
    return JSONResponse(status_code=403, content={"detail": str(exc)})


app.mount(
    "/admin/assets",
    RevalidatingStaticFiles(directory=Path(__file__).parent / "web" / "assets"),
    name="console-assets",
)

app.include_router(demo_scenes_router)
app.include_router(targets_router)
app.include_router(operations_targets_router)
app.include_router(auth_router)
app.include_router(console_auth_router)
app.include_router(companies_router)
app.include_router(operations_customers_router)
app.include_router(operations_accounts_router)
app.include_router(feishu_sync_router)
app.include_router(operations_reports_router)
app.include_router(operations_opportunities_router)
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
