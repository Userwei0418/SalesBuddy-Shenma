"""Synthetic tenants, current grants and real non-owner runtime connections."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sales_backend.api.dependencies import RequestIdentity, get_identity
from sales_backend.api.knowledge import CatalogWrite, router, service as knowledge_service_dependency
from sales_backend.config import Settings
from sales_backend.contracts.knowledge import KnowledgeContributionInput
from sales_backend.db import Database, _initialize_connection
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.identity import ActorRecord
from sales_backend.services.knowledge import KnowledgeService


def catalog_values():
    return dict(
        expected_revision=0,
        general_product_code="general",
        products=[
            {"code": "general", "label": "通用", "active": True},
            {"code": "test_product", "label": "测试产品", "active": True},
        ],
    )


def selection(*, files=(), name="测试材料", **updates):
    return KnowledgeContributionInput.model_validate(
        {
            "expected_catalog_revision": 1,
            "content_category": "sales_support",
            "content_scope": "public",
            "product_code": "general",
            "supplementary_text": name,
            "content": {"file_ids": list(files), "links": [] if files else ["https://example.com/source"]},
            **updates,
        }
    )


class Fixture:
    def __init__(self, admin, database):
        self.admin = admin
        self.database = database
        self.settings = database.settings
        self.service = KnowledgeService(database, self.settings)
        self.actors = {}
        self.workspace = uuid4()
        self.team = uuid4()

    async def user(self, name, *, workspace=None, scope="workspace", teams=(), permissions=None):
        workspace = workspace or self.workspace
        uid = uuid4()
        await self.admin.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,$3)",
            uid,
            workspace,
            str(uid),
        )
        await self.admin.execute(
            """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
               VALUES($1,$2,'manager','workspace');""",
            workspace,
            uid,
        )
        await self.admin.execute(
            "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", workspace, uid
        )
        for permission in (
            permissions if permissions is not None else ["read", "create", "edit", "manage", "catalog_manage"]
        ):
            await self.admin.execute(
                """INSERT INTO config.account_permission_override
                   (workspace_id,user_ref_id,permission_code,effect,scope_code,team_ids)
                   VALUES($1,$2,$3,'allow',$4,$5)""",
                workspace,
                uid,
                "knowledge." + permission,
                scope,
                list(teams),
            )
        actor = ActorContext(workspace_id=str(workspace), user_id=str(uid), role="manager", data_scope="workspace")
        self.actors[name] = actor
        return actor

    async def grant_scope(self, name, scope, teams=(), *, effect="allow", action="read"):
        actor = self.actors[name]
        await self.admin.execute(
            """UPDATE config.account_permission_override SET effect=$3,scope_code=$4,team_ids=$5
               WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code=$6""",
            UUID(actor.workspace_id),
            UUID(actor.user_id),
            effect,
            scope,
            list(teams),
            "knowledge." + action,
        )

    async def membership(self, name):
        await self.admin.execute(
            """INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role)
               VALUES($1,$2,$3,'manager')""",
            self.workspace,
            self.team,
            UUID(self.actors[name].user_id),
        )

    async def configure(self):
        return await self.service.save_catalog(self.actors["owner"], uuid4(), CatalogWrite(**catalog_values()))

    @asynccontextmanager
    async def client(self, name="owner"):
        actor = self.actors[name]
        app = FastAPI()
        app.state.database = self.database
        # API dependencies read the same settings object that the real app
        # installs during startup.  The isolated-Postgres harness used to
        # omit it, so every endpoint failed before reaching the knowledge
        # repository and masked the actual database acceptance result.
        app.state.settings = self.settings
        app.include_router(router)
        app.dependency_overrides[knowledge_service_dependency] = lambda: self.service
        app.dependency_overrides[get_identity] = lambda: RequestIdentity(
            actor,
            "synthetic-session",
            ActorRecord(actor, "test", "Test", ()),
            must_change_password=False,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def kb():
    configured = os.environ.get("KNOWLEDGE_TEST_CLUSTER")
    if not configured:
        pytest.skip("Run database/tests/verify_knowledge_postgres.py for an isolated real PG cluster")
    base = Path(configured)
    if (
        not base.name.startswith("sb-knowledge-")
        or (base / "PRIVATE_KNOWLEDGE_TEST").read_text() != "disposable-local-only\n"
    ):
        raise RuntimeError("Only the private knowledge test runner may supply this cluster")
    args = dict(host=str(base / "socket"), port=55438, database="postgres")
    admin = await asyncpg.connect(**args, user="kb_owner")
    await _initialize_connection(admin)
    database = Database(
        Settings(
            app_env="test",
            database_url="",
            senseaudio_base_url="",
            senseaudio_api_key="",
            asr_model="",
            tts_model="",
            llm_model="",
            timeout_seconds=1,
            max_retries=0,
            access_token_secret="",
            access_token_issuer="test",
            access_token_audience="test",
            access_token_minutes=1,
            refresh_token_days=1,
            auth_mode="demo",
            demo_workspace="",
            database_min_pool_size=1,
            database_max_pool_size=4,
            worker_poll_seconds=1,
            worker_lock_seconds=1,
            worker_id="test",
        )
    )
    database.pool = await asyncpg.create_pool(
        **args, user="kb_runtime", min_size=1, max_size=4, init=_initialize_connection
    )
    fixture = Fixture(admin, database)
    try:
        await admin.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'KB Test')",
            fixture.workspace,
            str(fixture.workspace),
        )
        await admin.execute(
            "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='knowledge'",
            fixture.workspace,
        )
        await admin.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'test','Synthetic team')",
            fixture.team,
            fixture.workspace,
        )
        await fixture.user("owner")
        await fixture.user("reader")
        other = uuid4()
        await admin.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Other KB Test')",
            other,
            str(other),
        )
        await admin.execute(
            "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='knowledge'", other
        )
        await fixture.user("other", workspace=other)
        yield fixture
    finally:
        await database.close()
        await admin.close()
