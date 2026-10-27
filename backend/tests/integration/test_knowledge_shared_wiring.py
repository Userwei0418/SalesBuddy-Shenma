"""Combined V258 runtime policy and actual shared route gate, synthetic identity."""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from sales_backend.api import permission_gate
from sales_backend.api.dependencies import RequestIdentity, get_identity
from sales_backend.api.knowledge import router
from sales_backend.domain.permission_catalog import CATALOG
from sales_backend.repositories.identity import ActorRecord

from .knowledge_fixtures import catalog_values, selection
from .knowledge_fixtures import kb as kb


@asynccontextmanager
async def gated_client(kb, monkeypatch, name="owner"):
    actor = kb.actors[name]
    identity = RequestIdentity(
        actor,
        "synthetic-session",
        ActorRecord(actor, "test", "Test", ()),
        auth_method="password",
        client_channel="wechat-mini-program",
    )

    async def selected_identity(*args, **kwargs):
        return identity

    monkeypatch.setattr(permission_gate, "get_identity", selected_identity)
    app = FastAPI(dependencies=[Depends(permission_gate.enforce_route_permission)])
    app.state.database = kb.database
    app.state.settings = kb.database.settings
    app.include_router(router)
    app.dependency_overrides[get_identity] = selected_identity

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    # FastAPI must not treat the direct gate's varargs helper as dependency inputs.
    app.dependency_overrides[get_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic") as client:
        yield client


def key():
    return {"Idempotency-Key": str(uuid4())}


@pytest.mark.asyncio
async def test_real_registration_keeps_module_off_and_all_roles_ungranted(kb):
    workspace = uuid4()
    await kb.admin.execute(
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Disabled fixture')",
        workspace,
        str(workspace),
    )
    assert (
        await kb.admin.fetchval(
            "SELECT enabled FROM control.tenant_module WHERE workspace_id=$1 AND module_code='knowledge'", workspace
        )
        is False
    )
    assert (
        await kb.admin.fetchval(
            "SELECT count(*) FROM config.permission_role_grant WHERE permission_code LIKE 'knowledge.%'"
        )
        == 0
    )
    rows = await kb.admin.fetch(
        "SELECT code,label,module,scopes,sensitive FROM config.permission_catalog WHERE code LIKE 'knowledge.%'"
    )
    assert len(rows) == 5
    for row in rows:
        definition = CATALOG[row["code"]]
        assert (row["label"], row["module"], tuple(row["scopes"]), row["sensitive"]) == (
            definition.label,
            definition.module,
            definition.scopes,
            definition.sensitive,
        )
    assert CATALOG["knowledge.catalog_manage"].scopes == ("workspace",)
    assert not await kb.admin.fetchval("SELECT has_schema_privilege('kb_worker','knowledge','USAGE')")
    assert not await kb.admin.fetchval(
        "SELECT has_function_privilege('kb_worker','security.knowledge_save_material(uuid,integer,jsonb)','EXECUTE')"
    )


@pytest.mark.asyncio
async def test_shared_gate_multipart_roundtrip_and_real_stats(kb, monkeypatch):
    async with gated_client(kb, monkeypatch) as client:
        response = await client.put("/api/v1/knowledge/catalog", json=catalog_values(), headers=key())
        assert response.status_code == 200, response.text
        response = await client.post(
            "/api/v1/knowledge/files", files={"file": ("actual.txt", b"actual bytes", "text/plain")}, headers=key()
        )
        assert response.status_code == 201, response.text
        fid = response.json()["file_id"]
        response = await client.post(
            "/api/v1/knowledge/materials", json=selection(files=[fid]).model_dump(mode="json"), headers=key()
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        content = await client.get(
            f"/api/v1/knowledge/files/{fid}/download", params={"version_id": saved["version_id"]}
        )
        assert content.status_code == 200 and content.content == b"actual bytes", content.text
        stats = await client.get("/api/v1/knowledge/stats")
        assert stats.status_code == 200, stats.text
        assert stats.json()["metrics"]["total"]["count"] == 1
        assert stats.json()["metrics"]["retrievable"]["count"] == 0
        assert (await client.get("/api/v1/knowledge/count")).json()["count"] is None
        await kb.admin.execute(
            "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='knowledge'",
            kb.workspace,
        )
        denied = await client.get("/api/v1/knowledge/materials")
        assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize("keep,expected", [("read", 403), ("edit", 201), ("create", 201)])
async def test_upload_requires_read_and_create_or_edit(kb, monkeypatch, keep, expected):
    await kb.admin.execute(
        "DELETE FROM config.account_permission_override WHERE user_ref_id=$1 "
        "AND permission_code LIKE 'knowledge.%' AND permission_code<>ALL($2::text[])",
        UUID(kb.actors["owner"].user_id),
        ["knowledge.read", "knowledge." + keep],
    )
    async with gated_client(kb, monkeypatch) as client:
        response = await client.post(
            "/api/v1/knowledge/files", files={"file": ("test.txt", b"data", "text/plain")}, headers=key()
        )
        assert response.status_code == expected, response.text
        await kb.grant_scope("owner", None, effect="deny")
        denied = await client.post(
            "/api/v1/knowledge/files", files={"file": ("test.txt", b"data", "text/plain")}, headers=key()
        )
        assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_platform_startup_rejects_unexpected_knowledge_table_read(kb):
    from types import SimpleNamespace

    from sales_backend.services.platform_control import verify_platform_pool

    @asynccontextmanager
    async def connection():
        async with kb.admin.transaction():
            await kb.admin.execute("SET LOCAL ROLE kb_platform")
            yield kb.admin

    database = SimpleNamespace(connection=connection)
    await verify_platform_pool(database)
    transaction = kb.admin.transaction()
    await transaction.start()
    try:
        await kb.admin.execute("GRANT SELECT ON knowledge.file TO kb_platform")
        with pytest.raises(RuntimeError, match="must not directly read or write"):
            await verify_platform_pool(database)
    finally:
        await transaction.rollback()
