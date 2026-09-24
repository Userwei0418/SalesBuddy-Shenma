"""Explicit server snapshots for isolated tests; real scope enforcement uses PostgreSQL tests."""


def permission_snapshot(actor, permissions, *, version="rbac-test:1"):
    return {
        "workspace_id": actor.workspace_id, "user_id": actor.user_id,
        "permission_version": version,
        "grants": [{"permission_code": code, "effect": "allow", "scope_code": scope,
                    "team_ids": list(actor.team_ids) if scope == "teams" else [],
                    "source_code": "fixture", "source_id": "configured-role"}
                   for code, scope in permissions.items()],
    }


def visit_authorization_query(actor, *, customer_id="customer", opportunity_id=None, version="rbac-test:1"):
    snapshot = permission_snapshot(actor, {"visit.create": "self", "visit.structure": "self"}, version=version)

    async def read(sql, *args):
        if "security.authorization_snapshot" in sql:
            return snapshot
        if "security.authorization_visit_target" in sql:
            return args == ("visit.create", customer_id, opportunity_id, actor.user_id, None)
        raise AssertionError(f"Unexpected authorization query: {sql}")
    return read


def configured_database(actor, permissions, *, version="rbac-test:1"):
    """Only authorization reads are faked; unexpected business queries fail."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def read(sql, *args):
        if "security.authorization_snapshot" in sql:
            return permission_snapshot(actor, permissions, version=version)
        if "security.authorization_has" in sql:
            return args[0] in permissions if args else any(code in sql for code in permissions)
        raise AssertionError(f"Unexpected fixture SQL: {sql}")
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=read))
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection
    return SimpleNamespace(transaction=transaction, connection=connection, permissions=permissions)


def install_http_authorization(monkeypatch, app, actor, permissions):
    """Authenticate a fixture identity while keeping the real per-route gate."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from sales_backend.config import get_settings
    db = configured_database(actor, permissions)
    identity = SimpleNamespace(actor=actor, auth_method="password", client_channel="web", must_change_password=False)
    monkeypatch.setattr("sales_backend.api.permission_gate.get_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(app.state, "database", db, raising=False)
    monkeypatch.setattr(app.state, "settings", get_settings(), raising=False)
    return db, identity
