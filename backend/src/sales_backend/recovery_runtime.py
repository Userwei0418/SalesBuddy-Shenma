"""Opt-in adapter used only by the frozen FINANCE001 emergency read package.

Normal main.py does not import or install this adapter. It is not a database
rollback and does not provide the complete application's business workflows.
"""
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import wraps
import logging

import asyncpg
from starlette.requests import Request
from starlette.routing import Match

from sales_backend.db import Database
from sales_backend.error_contract import error_response

_technical_write = ContextVar("finance_recovery_technical_write", default=False)
_isolation = ContextVar("finance_recovery_isolation", default="read_committed")

CODE = "FINANCE_RECOVERY_READ_ONLY"
DETAIL = "当前为财务应急只读模式，仅支持已开放的查询；业务保存和后台任务暂不可用，请联系管理员恢复服务。"
HEADERS = {"Cache-Control": "no-store", "X-SalesBuddy-Recovery": "finance-read-only"}

# Exact existing endpoint identities, not user-supplied paths or role labels.
# Authentication continues to enforce passwords, origin, sessions and grants.
AUTH_ENDPOINTS = frozenset({
    ("auth", "company_entry"), ("auth", "password_login"),
    ("auth", "password_change"), ("auth", "refresh_session"), ("auth", "logout"),
    ("console_auth", "login"), ("console_auth", "refresh"),
    ("console_auth", "change_password"), ("console_auth", "logout"),
})
READ_ENDPOINTS = {
    "auth": {"get_me"}, "console_auth": {"me"},
    "main": {"health_live", "health_ready", "health_version"},
    "admin": {"admin_page"},
    "authorization": {"own_permissions", "console_permissions", "available_modules"},
    "companies": {"get_role_labels", "directory"},
    "materials": {"materials", "management_materials", "product_lines",
                  "management_product_lines", "opportunity_materials"},
    "opportunity_finance": {"read_financial_overview", "management_financial_overview"},
    "operations_opportunities": {"opportunities", "amount_policy", "detail"},
    "customers": {"customer_portfolio", "reference", "list_customers", "customer_detail"},
    "customer_assets": {"map_customers", "read_assets"},
    "detail_reads": {"customer_overview", "customer_header", "opportunity_header",
        "customer_opportunity_header", "opportunity_overview", "customer_opportunities",
        "customer_opportunity_overview", "actual_quarters"},
    "business": {"business_options", "directory_teams", "directory_members", "dashboard",
        "dashboard_options", "dashboard_ranking_view", "opportunities", "opportunity_detail",
        "opportunities_overview", "visit_range", "get_visit"},
    "profile": {"profile_scope_options", "scoped_performance"},
    "fde_profile": {"profile"},
    "operations_customers": {"summary", "customers", "customer", "customer_overview"},
    "opportunity_fields": {"options", "directory", "detail"},
    "opportunity_views": {"options", "preference", "list_views", "detail"},
    "opportunity_creation_history": {"history", "options"},
    "opportunity_initialization": {"options"},
    "visit_imports": {"upload_request_status", "import_status"},
    "operations_visits": {"visits", "recorders", "detail"},
}
PREVIEW_ENDPOINT = ("materials", "material_forecast_preview")
READ_POST_ENDPOINTS = frozenset({PREVIEW_ENDPOINT, ("opportunity_views", "query"),
                               ("opportunity_initialization", "preview")})


@contextmanager
def technical_write_scope():
    """Called only at reviewed authentication/admission/audit boundaries."""
    token = _technical_write.set(True)
    try:
        yield
    finally:
        _technical_write.reset(token)


def technical_operation(function):
    """Recovery builder decorates three exact technical functions, no services broadly."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with technical_write_scope():
            return await function(*args, **kwargs)
    return wrapped


class RecoveryDatabase(Database):
    @asynccontextmanager
    async def connection(self):
        async with super().connection() as connection:
            if _technical_write.get():
                yield connection
            else:
                # Covers raw connection users as well as actor transactions.
                # A read handler accidentally writing through a DB function is
                # rejected by PostgreSQL, not by a SQL-text heuristic.
                async with connection.transaction(readonly=True, isolation=_isolation.get()):
                    yield connection

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False, isolation="read_committed"):
        token = _isolation.set(isolation)
        try:
            async with super().transaction(actor, readonly=readonly or not _technical_write.get(),
                                           isolation=isolation) as connection:
                yield connection
        finally:
            _isolation.reset(token)


def blocked_response(request=None):
    return error_response(request, status_code=503, detail=DETAIL, code=CODE, headers=HEADERS)


async def readonly_violation_handler(request, exc):
    return blocked_response(request)


class RecoveryMiddleware:
    def __init__(self, app, *, routes):
        self.app = app
        self.routes = routes

    def operation(self, scope):
        for route in self.routes:
            match, child = route.matches(scope)
            if match != Match.FULL:
                continue
            # This pinned FastAPI runtime keeps included routers lazy: their
            # public matches() returns FULL with an empty child scope. Resolve
            # the same selected effective route (including include prefixes)
            # instead of treating the router container as the endpoint. Unknown
            # router implementations remain closed until independently checked.
            while match == Match.FULL and "endpoint" not in child and hasattr(route, "_match"):
                match, child, route, _ = route._match(scope)
            if match != Match.FULL or route is None:
                return "blocked"
            if getattr(route, "name", None) == "console-assets":
                return "read" if scope["method"] in {"GET", "HEAD"} else "blocked"
            endpoint = child.get("endpoint")
            module = getattr(endpoint, "__module__", "").rsplit(".", 1)[-1]
            key = (module, getattr(endpoint, "__name__", ""))
            if key in AUTH_ENDPOINTS:
                return "authentication"
            if scope["method"] in {"GET", "HEAD"} and key[1] in READ_ENDPOINTS.get(module, set()):
                return "read"
            if scope["method"] == "POST" and key in READ_POST_ENDPOINTS:
                return "read"
            return "blocked"
        return "blocked"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            return
        operation = self.operation(scope)
        if operation == "blocked":
            logging.getLogger("sales_backend.recovery").warning("recovery operation blocked")
            return await blocked_response(Request(scope))(scope, receive, send)

        async def send_headers(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in {b"cache-control", b"x-salesbuddy-recovery"}]
                message = {**message, "headers": headers +
                           [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()]}
            await send(message)

        token = _technical_write.set(operation == "authentication")
        try:
            await self.app(scope, receive, send_headers)
        finally:
            _technical_write.reset(token)


def install_recovery(app):
    app.add_exception_handler(asyncpg.ReadOnlySQLTransactionError, readonly_violation_handler)
    app.add_middleware(RecoveryMiddleware, routes=app.routes)
    app.state.finance_recovery = "read_only"
