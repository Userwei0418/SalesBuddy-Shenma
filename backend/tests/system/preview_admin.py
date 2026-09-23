"""Loopback-only admin preview backed by a disposable DB and non-bypass role.

No AI calls or worker are started. Fixtures are explicitly synthetic scenarios,
not evidence of provider availability. Ctrl-C drops the database and role.
"""

import asyncio
import signal
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import asyncpg
import uvicorn
from run_integration_postgres import main

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection
from sales_backend.main import app
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_platform.audit import InferenceAudit


async def serve(config, name, role):
    async def setup(connection):
        # Pool.reset resets ROLE; setup must run on every acquire, not just init.
        await connection.execute(f'SET ROLE "{role}"')
        assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")

    pool = await asyncpg.create_pool(
        database=name, **config, min_size=1, max_size=4, init=_initialize_connection, setup=setup
    )
    settings = replace(
        get_settings(),
        database_url="",
        senseaudio_api_key="",
        access_token_secret=uuid4().hex + uuid4().hex,
        demo_workspace="demo-sales-workspace",
    )
    database = Database(settings, pool)
    try:
        async with database.connection() as connection:
            record = await IdentityRepository().find_actor_by_account(
                connection, workspace_external_id=settings.demo_workspace, account_code="XS001"
            )
        actor = record.context
        for mode, provider, fallback, state in [
            ("opportunity_draft", "agent_platform", None, "waiting_human"),
            ("today_tasks", "senseaudio", "TimeoutError", "succeeded"),
            ("operating_report", None, "TimeoutError", "failed"),
        ]:
            async with database.transaction(actor) as connection:
                conversation = await connection.fetchval(
                    "INSERT INTO agent.conversation(workspace_id,user_ref_id,role_code,data_scope_snapshot) "
                    "VALUES($1::uuid,$2::uuid,'sales','{}') RETURNING id",
                    actor.workspace_id,
                    actor.user_id,
                )
                run = await connection.fetchval(
                    "INSERT INTO agent.run(workspace_id,conversation_id,status,identity_context,business_context) "
                    "VALUES($1::uuid,$2,$3,$5::jsonb,$4::jsonb) RETURNING id",
                    actor.workspace_id,
                    conversation,
                    state,
                    {"mode": mode, "fixture": "isolated admin preview"},
                    actor.model_dump(mode="json"),
                )
            operation = str(uuid4())
            audit = InferenceAudit(
                database,
                actor,
                operation,
                mode=mode,
                run_id=str(run),
                facts={"visits": [{"text": "隔离演示正文"}]},
                config={
                    "platform_seconds": 12,
                    "total_seconds": 45,
                    "agent_id": "isolated-preview",
                    "direct_model": "fixture",
                },
            )
            await audit.start()
            audit.event("route_started", "agent_platform")
            if fallback:
                audit.event("route_failed", "agent_platform", error_code=fallback)
                audit.event("fallback_requested", "agent_platform", reason=fallback)
                audit.event("route_started", "senseaudio")
            audit.event("contract_accepted" if provider else "route_failed", provider or "senseaudio")
            await audit.finish(
                "accepted" if provider else "failed",
                trace={"operation_id": operation, "provider": provider, "fallback_reason": fallback},
            )

        @asynccontextmanager
        async def preview_lifespan(application):
            application.state.settings, application.state.database = settings, database
            yield

        app.router.lifespan_context = preview_lifespan
        print("ISOLATED_ADMIN_PREVIEW http://127.0.0.1:18155/admin — synthetic fixtures only", flush=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18155, log_level="warning"))
        # Consume Uvicorn's replayed signal so the outer runner can drop the
        # disposable database and role before the process terminates.
        previous = {sig: signal.signal(sig, lambda *_: None) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            await server.serve()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main(serve))
