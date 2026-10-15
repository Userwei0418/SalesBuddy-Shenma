"""Offline old/new request regression; real RLS/row-lock scheduling needs a DB window."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import risks
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.risks import RiskRepository

NOTE = "负责人已复核风险处理完毕"


class ResolveConnection:
    def __init__(self, *, version=1, status="pending", visible=True, permission=(True, True), update_count=1):
        self.risk = dict(id=str(uuid4()), title="合成风险", status=status, version_no=version,
                         resolution_note="原解除说明" if status == "resolved" else None)
        self.visible = visible
        self.permission = iter(permission)
        self.update_count = update_count
        self.events, self.updates, self.locks = [], [], []

    async def fetchval(self, sql, *args):
        if sql.startswith("SELECT EXISTS"):
            return self.visible
        assert "security.authorization_risk" in sql
        assert "'risk.resolve'" in sql or args[0] == "risk.resolve"
        return next(self.permission)

    async def fetchrow(self, sql, *args):
        assert "FOR UPDATE" in sql and args == (self.risk["id"],)
        self.locks.append(sql)
        return deepcopy(self.risk)

    async def execute(self, sql, *args):
        if "UPDATE insight.risk" in sql:
            assert "version_no = $3" in sql and "status <> 'resolved'" in sql
            assert args[0] == self.risk["id"] and args[2] == self.risk["version_no"]
            if not self.update_count:
                return "UPDATE 0"
            self.updates.append(args)
            self.risk.update(status="resolved", resolution_note=args[1], version_no=args[2] + 1)
            return "UPDATE 1"
        assert "INSERT INTO insight.risk_event" in sql and "'resolved'" in sql
        self.events.append(deepcopy(args))
        return "INSERT 0 1"


def actor():
    return SimpleNamespace(user_id=str(uuid4()), workspace_id=str(uuid4()))


def detail_from(monkeypatch, connection):
    async def detail(self, *args, **kwargs):
        return deepcopy(connection.risk)
    monkeypatch.setattr(RiskRepository, "detail", detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("version,expected", [(1, None), (4, 4)])
async def test_legacy_initial_and_explicit_current_write_once_with_audit(monkeypatch, version, expected):
    conn = ResolveConnection(version=version)
    detail_from(monkeypatch, conn)
    person = actor()
    result = await RiskRepository().resolve(conn, actor=person, risk_id=conn.risk["id"],
                                          resolution_note=NOTE, expected_version=expected)
    assert result["status"] == "resolved" and result["version_no"] == version + 1
    assert result["resolution_note"] == NOTE
    assert len(conn.locks) == len(conn.updates) == len(conn.events) == 1
    assert conn.events[0] == (person.workspace_id, conn.risk["id"], "pending", person.user_id, {
        "resolution_note": NOTE,
        "version_check": "legacy_initial_version" if expected is None else "client_version",
        "risk_version_before": version, "risk_version_after": version + 1,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("version,expected,status", [
    (2, None, "pending"), (4, None, "accepted"), (2, 1, "pending"), (3, 2, "resolved"),
])
async def test_changed_open_legacy_and_stale_explicit_requests_never_write(monkeypatch, version, expected, status):
    conn = ResolveConnection(version=version, status=status)
    detail_from(monkeypatch, conn)
    before = deepcopy(conn.risk)
    with pytest.raises(VersionConflict) as caught:
        await RiskRepository().resolve(conn, actor=actor(), risk_id=conn.risk["id"],
                                       resolution_note=NOTE, expected_version=expected)
    if expected is None:
        assert "升级小程序" in str(caught.value)
    assert conn.risk == before and not conn.updates and not conn.events


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [None, 7])
async def test_resolved_read_only_response_does_not_replace_note_or_audit(monkeypatch, expected):
    conn = ResolveConnection(version=7, status="resolved")
    detail_from(monkeypatch, conn)
    before = deepcopy(conn.risk)
    result = await RiskRepository().resolve(conn, actor=actor(), risk_id=conn.risk["id"],
                                          resolution_note="不能覆盖先前解除说明", expected_version=expected)
    assert result == conn.risk == before and not conn.updates and not conn.events


@pytest.mark.asyncio
async def test_second_legacy_close_preserves_first_writer(monkeypatch):
    # Sequential observations model the state seen after a competing row lock
    # commits. This proves terminal handling, not PostgreSQL lock scheduling.
    conn = ResolveConnection(permission=(True, True, True, True))
    detail_from(monkeypatch, conn)
    repo = RiskRepository()
    person = actor()
    first = await repo.resolve(conn, actor=person, risk_id=conn.risk["id"], resolution_note=NOTE)
    second = await repo.resolve(conn, actor=person, risk_id=conn.risk["id"], resolution_note="另一人稍后解除说明")
    assert first == second and second["resolution_note"] == NOTE
    assert len(conn.updates) == len(conn.events) == 1


@pytest.mark.asyncio
async def test_failed_conditional_update_cannot_append_resolution_event(monkeypatch):
    conn = ResolveConnection(update_count=0)
    detail_from(monkeypatch, conn)
    before = deepcopy(conn.risk)
    with pytest.raises(VersionConflict):
        await RiskRepository().resolve(conn, actor=actor(), risk_id=conn.risk["id"], resolution_note=NOTE)
    assert conn.risk == before and not conn.events and not conn.updates


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected_status", [
    ("legacy", 200), ("null", 200), ("evolved", 409), ("explicit", 200), ("stale", 409),
    ("resolved", 200), ("capability", 403), ("scope", 403), ("revoked_after_lock", 403),
    ("missing", 404), ("bad_version", 422), ("short_note", 422),
])
async def test_old_and_new_payloads_use_same_route_permissions_and_write_guards(monkeypatch, case, expected_status):
    permissions = (False, True) if case == "scope" else (True, False) if case == "revoked_after_lock" else (True, True)
    conn = ResolveConnection(version=2 if case in {"evolved", "explicit", "stale", "resolved"} else 1,
                             status="resolved" if case == "resolved" else "pending",
                             visible=case != "missing", permission=permissions)
    detail_from(monkeypatch, conn)
    person = actor()
    transactions, grants = [], []

    @asynccontextmanager
    async def transaction(current_actor):
        assert current_actor is person
        transactions.append(True)
        yield conn

    async def capability(connection, current_actor, name):
        assert connection is conn and current_actor is person and name == "risk.resolve"
        grants.append(name)
        if case == "capability":
            raise PermissionError("denied")

    app = FastAPI()
    app.include_router(risks.router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=person)
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(transaction=transaction)
    monkeypatch.setattr(risks, "require_capability", capability)
    data = {"resolution_note": "已好" if case == "short_note" else NOTE}
    if case in {"explicit", "stale", "bad_version", "null"}:
        data["version_no"] = {"explicit": 2, "stale": 1, "bad_version": "1", "null": None}[case]
    # ASGITransport calls the Python app in process; no HTTP socket or DB is opened.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/risks/{conn.risk['id']}/resolve", json=data)
    assert response.status_code == expected_status
    writes = case in {"legacy", "null", "explicit"}
    assert len(conn.events) == len(conn.updates) == int(writes)
    if case in {"short_note", "bad_version"}:
        assert not transactions and not grants
    elif case == "evolved":
        assert "升级小程序" in response.json()["detail"]
    elif case == "resolved":
        assert response.json()["resolution_note"] == "原解除说明"
    if case in {"capability", "scope", "missing"}:
        assert not conn.locks


def test_openapi_documents_optional_version_and_limited_legacy_behavior():
    app = FastAPI()
    app.include_router(risks.router)
    schema = app.openapi()
    resolve = schema["components"]["schemas"]["RiskResolve"]
    assert resolve["required"] == ["resolution_note"]
    assert "版本为1" in resolve["properties"]["version_no"]["description"]
    endpoint = schema["paths"]["/api/v1/risks/{risk_id}/resolve"]["post"]
    assert "升级客户端" in endpoint["description"] and "leader" in endpoint["description"]
