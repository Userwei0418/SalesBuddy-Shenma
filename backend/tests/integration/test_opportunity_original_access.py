"""V275 originals: real password HTTP, PostgreSQL RLS and original-byte storage.

The isolated runner installs the exact product roles. Only synthetic actors and
archived records are seeded by its owner; requests use kb_runtime with no auth,
permission, storage or download adapter replaced. No model/provider is called.
"""

import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from sales_backend.api.auth import router as auth_router
from sales_backend.api.permission_gate import enforce_route_permission
from sales_backend.api.visit_imports import router as import_router
from sales_backend.auth.passwords import encode_password
from sales_backend.db import set_request_context
from sales_backend.permission_context import current_feature
from sales_backend.services.material_storage import MaterialStorage

from .knowledge_fixtures import kb as kb

pytestmark = pytest.mark.asyncio
CONTENT = b"Synthetic archived collaborator original; no customer content.\n"
PASSWORD = "Isolated-Original-2026"


@pytest_asyncio.fixture
async def originals(kb):
    kb.database.settings = replace(
        kb.settings, auth_mode="password",
        access_token_secret="isolated-original-test-signing-secret-never-production",
    )
    await kb.user("manager", permissions=[])
    await kb.user("principal", permissions=[])
    await kb.user("unrelated", permissions=[])
    # Explicitly scoped fixture grants, not the default manager read-all role.
    await kb.admin.execute(
        "DELETE FROM config.permission_role_grant WHERE workspace_id=$1", kb.workspace
    )
    kb.accounts, kb.teams = {}, {}
    password_hash = encode_password(PASSWORD)
    for name, actor in tuple(kb.actors.items()):
        ws, uid, team = UUID(actor.workspace_id), UUID(actor.user_id), uuid4()
        account = "ORIG" + uuid4().hex[:14].upper()
        kb.accounts[name], kb.teams[name] = account, team
        await kb.admin.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'Original test team')",
            team, ws, str(team),
        )
        await kb.admin.execute(
            "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) "
            "VALUES($1,$2,$3,'manager')", ws, team, uid,
        )
        kb.actors[name] = actor.model_copy(update={"team_ids": (str(team),)})
        await kb.admin.execute("UPDATE platform.user_ref SET account_code=$2 WHERE id=$1", uid, account)
        await kb.admin.execute(
            "INSERT INTO platform.password_credential(workspace_id,user_ref_id,password_hash,must_change_password) "
            "VALUES($1,$2,$3,false)", ws, uid, password_hash,
        )
        for permission in ("access.mini_program", "opportunity.read", "visit.read", "visit.download_original"):
            scope = "workspace" if name in {"manager", "other"} or permission.startswith("access.") else "teams"
            if name == "principal" and not permission.startswith("access."):
                scope = "self"
            await kb.admin.execute(
                "INSERT INTO config.account_permission_override"
                "(workspace_id,user_ref_id,permission_code,effect,scope_code,team_ids) "
                "VALUES($1,$2,$3,'allow',$4,$5)",
                ws, uid, permission, scope, [team] if scope == "teams" else [],
            )
    # Owner-only fixture setup still obeys tenant finance/audit trigger context.
    # This connection is never used by HTTP; the product pool stays kb_runtime.
    await kb.admin.execute(
        "SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false)",
        str(kb.workspace), kb.actors["manager"].user_id,
    )
    kb.customer = await kb.admin.fetchval(
        "INSERT INTO crm.customer(workspace_id,name,owner_user_ref_id,owner_team_id,created_by_user_ref_id) "
        "VALUES($1,'Original access synthetic customer',$2,$3,$2) RETURNING id",
        kb.workspace, UUID(kb.actors["manager"].user_id), kb.teams["manager"],
    )
    kb.opportunity = await create_opportunity(kb, "principal", "Original access synthetic opportunity")
    for name in ("owner", "reader"):
        await kb.admin.execute(
            "INSERT INTO crm.opportunity_collaborator(workspace_id,opportunity_id,user_ref_id,added_by_user_ref_id) "
            "VALUES($1,$2,$3,$4)", kb.workspace, kb.opportunity,
            UUID(kb.actors[name].user_id), UUID(kb.actors["manager"].user_id),
        )
    kb.import_id = await kb.admin.fetchval(
        "INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,"
        "status,storage_profile,storage_driver,storage_key,content_sha256) "
        "VALUES(gen_random_uuid(),$1,$2,'synthetic-original.txt','',$3,"
        "'succeeded','postgres','postgres','synthetic',$4) RETURNING id",
        kb.workspace, UUID(kb.actors["owner"].user_id), len(CONTENT), hashlib.sha256(CONTENT).hexdigest(),
    )
    await kb.admin.execute(
        "INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)",
        kb.import_id, kb.workspace, CONTENT,
    )
    yield kb


async def create_opportunity(kb, name, title):
    identifier = uuid4()
    async with kb.admin.transaction():
        await set_request_context(kb.admin, kb.actors[name])
        await kb.admin.execute("SELECT set_config('app.native_opportunity_create',$1,true)", str(identifier))
        await kb.admin.execute(
            "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,"
            "created_by_user_ref_id,source_code) VALUES($1,$2,$3,$4,$5,$6,$5,'manual')",
            identifier, kb.workspace, kb.customer, title, UUID(kb.actors[name].user_id), kb.teams[name],
        )
    return identifier


async def archive(kb):
    kb.visit = await kb.admin.fetchval(
        "INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,"
        "recorder_team_id,created_by_user_ref_id,confirmed_by_user_ref_id,form_version_id,status,"
        "source_import_id,follow_up_record,next_action,archived_fields,interaction_at) "
        "VALUES($1,$2,$3,$4,$5,$4,$4,"
        "(SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),"
        "'archived',$6,'Synthetic follow-up','Synthetic next step','{}',clock_timestamp()) RETURNING id",
        kb.workspace, kb.customer, kb.opportunity, UUID(kb.actors["owner"].user_id),
        kb.teams["owner"], kb.import_id,
    )


@asynccontextmanager
async def client(kb, name="owner"):
    app = FastAPI(dependencies=[Depends(enforce_route_permission)])
    app.state.database, app.state.settings = kb.database, kb.database.settings
    app.include_router(auth_router)
    app.include_router(import_router)

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://isolated.invalid") as c:
        response = await c.post("/api/v1/auth/password/login", json={
            "account_code": kb.accounts[name], "password": PASSWORD,
            "workspace": kb.actors[name].workspace_id,
        })
        assert response.status_code == 200, response.text
        c.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        yield c


def path(kb):
    return f"/api/v1/visit-imports/{kb.import_id}/original"


async def revoke(kb, name):
    await kb.admin.execute(
        "DELETE FROM crm.opportunity_collaborator WHERE opportunity_id=$1 AND user_ref_id=$2",
        kb.opportunity, UUID(kb.actors[name].user_id),
    )


async def denied_action(kb, name, action):
    await kb.admin.execute(
        "UPDATE config.account_permission_override SET effect='deny',scope_code=NULL,team_ids='{}' "
        "WHERE user_ref_id=$1 AND permission_code=$2", UUID(kb.actors[name].user_id), action,
    )


async def sql_permissions(kb, name, *, feature="visit.download_original"):
    token = current_feature.set(feature)
    try:
        async with kb.database.transaction(kb.actors[name], readonly=True) as conn:
            assert not await conn.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            return dict(await conn.fetchrow(
                "SELECT security.authorization_import('visit.read',$1) AS can_read,"
                "security.authorization_import('visit.download_original',$1) AS can_download,"
                "security.authorization_visit('visit.supplement',$2) AS can_supplement,"
                "security.authorization_opportunity('opportunity.update',$3) AS can_update,"
                "security.authorization_customer('customer.read',$4) AS can_read_customer,"
                "(SELECT count(*) FROM activity.visit_import_content WHERE import_id=$1) AS chunks",
                kb.import_id, kb.visit, kb.opportunity, kb.customer,
            ))
    finally:
        current_feature.reset(token)


async def test_actual_original_bytes_survive_archival_for_author_and_current_collaborator(originals):
    kb = originals
    async with client(kb, "reader") as c:
        assert (await c.get(path(kb))).status_code == 404  # Unbound upload is private.
    async with client(kb) as c:
        before = await c.get(path(kb))
        assert before.status_code == 200 and before.content == CONTENT, before.text
        await archive(kb)
        after = await c.get(path(kb))
        assert after.status_code == 200 and after.content == CONTENT, after.text
        assert after.headers["cache-control"] == "private, no-store"
    async with client(kb, "reader") as c:
        response = await c.get(path(kb))
        assert response.status_code == 200 and response.content == CONTENT, response.text
    permissions = await sql_permissions(kb, "reader")
    assert permissions == dict(can_read=True, can_download=True, can_supplement=False,
                               can_update=False, can_read_customer=False, chunks=1)


async def test_current_cross_team_collaborator_with_self_action_grants(originals):
    kb = originals
    await archive(kb)
    await kb.admin.execute(
        "UPDATE config.account_permission_override SET scope_code='self',team_ids='{}' "
        "WHERE user_ref_id=$1 AND permission_code IN ('visit.read','visit.download_original','opportunity.read')",
        UUID(kb.actors["reader"].user_id),
    )
    async with client(kb, "reader") as c:
        response = await c.get(path(kb))
        assert response.status_code == 200 and response.content == CONTENT, response.text
        await revoke(kb, "reader")
        assert (await c.get(path(kb))).status_code == 404


async def test_same_customer_sibling_does_not_inherit_collaboration_or_original_access(originals):
    kb = originals
    original = kb.import_id
    kb.opportunity = await create_opportunity(kb, "manager", "Sibling without collaborator")
    kb.import_id = await kb.admin.fetchval(
        "INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,"
        "status,storage_profile,storage_driver,storage_key,content_sha256) "
        "SELECT gen_random_uuid(),workspace_id,$2,filename,file_path,file_size,status,storage_profile,storage_driver,"
        "storage_key,content_sha256 FROM activity.visit_import WHERE id=$1 RETURNING id",
        original, UUID(kb.actors["manager"].user_id),
    )
    await kb.admin.execute(
        "INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)",
        kb.import_id, kb.workspace, CONTENT,
    )
    await archive(kb)
    for name in ("reader", "principal"):
        async with client(kb, name) as c:
            assert (await c.get(path(kb))).status_code == 404
        state = await sql_permissions(kb, name)
        assert not state["can_read"] and not state["can_download"] and state["chunks"] == 0


async def test_current_opportunity_owner_self_scope_can_download_collaborator_original(originals):
    kb = originals
    await archive(kb)
    async with client(kb, "principal") as c:
        response = await c.get(path(kb))
        assert response.status_code == 200 and response.content == CONTENT, response.text
        permissions = await sql_permissions(kb, "principal")
        assert permissions == dict(can_read=True, can_download=True, can_supplement=False,
                                   can_update=False, can_read_customer=False, chunks=1)
        await kb.admin.execute(
            "UPDATE crm.opportunity SET owner_user_ref_id=$2,owner_team_id=$3 WHERE id=$1",
            kb.opportunity, UUID(kb.actors["manager"].user_id), kb.teams["manager"],
        )
        assert (await c.get(path(kb))).status_code == 404


async def test_revoked_collaborator_and_author_return_to_existing_scoped_authority(originals):
    kb = originals
    await archive(kb)
    for name in ("owner", "reader"):
        async with client(kb, name) as c:
            assert (await c.get(path(kb))).status_code == 200
            await revoke(kb, name)
            assert (await c.get(path(kb))).status_code == 404
        permissions = await sql_permissions(kb, name)
        assert not permissions["can_download"] and not permissions["can_read"] and permissions["chunks"] == 0
    # An existing workspace-authorized manager still reads the historical file.
    async with client(kb, "manager") as c:
        response = await c.get(path(kb))
        assert response.status_code == 200 and response.content == CONTENT, response.text
    # Explicit self authority of the original uploader remains the V226 branch;
    # removal of collaboration does not revoke an independent permission grant.
    await kb.admin.execute(
        "UPDATE config.account_permission_override SET scope_code='self',team_ids='{}' "
        "WHERE user_ref_id=$1 AND permission_code IN ('visit.read','visit.download_original')",
        UUID(kb.actors["owner"].user_id),
    )
    async with client(kb) as c:
        assert (await c.get(path(kb))).content == CONTENT


@pytest.mark.parametrize("name", ["unrelated", "other"])
async def test_unrelated_and_other_company_cannot_read_original_metadata_or_bytes(originals, name):
    kb = originals
    await archive(kb)
    async with client(kb, name) as c:
        assert (await c.get(path(kb))).status_code == 404
    permissions = await sql_permissions(kb, name)
    assert not permissions["can_read"] and not permissions["can_download"] and permissions["chunks"] == 0


@pytest.mark.parametrize("name", ["reader", "principal"])
@pytest.mark.parametrize("action", ["visit.read", "visit.download_original", "opportunity.read"])
async def test_collaboration_or_ownership_never_overrides_current_action_denies(originals, action, name):
    kb = originals
    await archive(kb)
    async with client(kb, name) as c:
        assert (await c.get(path(kb))).status_code == 200
        await denied_action(kb, name, action)
        assert (await c.get(path(kb))).status_code == (403 if action == "visit.download_original" else 404)
    permissions = await sql_permissions(kb, name)
    assert not permissions["can_read"] and not permissions["can_download"] and permissions["chunks"] == 0


async def test_revocation_during_real_storage_read_releases_no_bytes(originals, monkeypatch):
    kb = originals
    await archive(kb)
    real_read = MaterialStorage.read

    async def read_then_revoke(storage, *args, **kwargs):
        content = await real_read(storage, *args, **kwargs)
        assert content == CONTENT
        await revoke(kb, "reader")
        return content

    monkeypatch.setattr(MaterialStorage, "read", read_then_revoke)
    async with client(kb, "reader") as c:
        response = await c.get(path(kb))
        assert response.status_code == 403 and CONTENT not in response.content


async def test_read_authority_does_not_supply_missing_download_grant_or_other_action(originals):
    kb = originals
    await archive(kb)
    for name in ("reader", "principal"):
        state = await sql_permissions(kb, name, feature="visit.retry_import")
        assert not state["can_read"] and not state["can_download"] and state["chunks"] == 0
        await kb.admin.execute(
            "DELETE FROM config.account_permission_override WHERE user_ref_id=$1 "
            "AND permission_code='visit.download_original'", UUID(kb.actors[name].user_id),
        )
        async with client(kb, name) as c:
            assert (await c.get(path(kb))).status_code == 403
        state = await sql_permissions(kb, name)
        assert not state["can_read"] and not state["can_download"] and state["chunks"] == 0


@pytest.mark.parametrize("change", ["inactive_actor", "deleted_opportunity", "deleted_visit"])
async def test_current_actor_and_live_objects_required(originals, change):
    kb = originals
    await archive(kb)
    async with client(kb, "reader") as c:
        assert (await c.get(path(kb))).status_code == 200
        if change == "inactive_actor":
            await kb.admin.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1",
                                   UUID(kb.actors["reader"].user_id))
        elif change == "deleted_opportunity":
            await kb.admin.execute(
                "UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1", kb.opportunity
            )
        else:
            await kb.admin.execute("UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1", kb.visit)
        response = await c.get(path(kb))
        assert response.status_code in ({401, 403} if change == "inactive_actor" else {404}), response.text
    permissions = await sql_permissions(kb, "reader")
    assert not permissions["can_download"] and not permissions["can_read"] and permissions["chunks"] == 0
