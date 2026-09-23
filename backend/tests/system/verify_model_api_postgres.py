"""Disposable real PostgreSQL checks: encrypted test/publish, tenant RLS and concurrent versions."""

import asyncio
import base64
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "database/scripts"), str(ROOT / "backend/src"), str(Path(__file__).parent)]
from migrate import migrate
from verify_runtime_config_postgres import workspace_fixture
from sales_backend.config import get_settings
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.model_api import ConnectionTest, ConnectionPublish
from sales_backend.security.runtime_credentials import CredentialCipher
from sales_backend.services.model_api import ModelApiError, ModelApiService
from sales_backend.services.runtime_config import load_runtime_configuration


async def main():
    pg = {
        "host": os.environ.get("PGHOST", "/tmp"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "user": os.environ.get("PGUSER", "postgres"),
    }
    admin = await asyncpg.connect(database="postgres", **pg)
    suffix = uuid4().hex[:10]
    name = "verify_model_api_" + suffix
    role = "model_api_" + suffix
    conn = None
    created_role = False
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(database=name, **pg)
        await _initialize_connection(conn)
        await migrate(conn)
        assert all(r["status"] == "unchanged" for r in await migrate(conn))
        first, second = await workspace_fixture(conn), await workspace_fixture(conn)
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role = True
        for schema in ("config", "common", "security", "platform", "ops"):
            await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        await conn.execute(f'GRANT SELECT ON platform.user_ref,config.agent_runtime_config TO "{role}"')
        await migrate(conn)
        assert await conn.fetchval("SELECT has_table_privilege($1,'config.model_api_release','SELECT')", role)
        assert not await conn.fetchval("SELECT has_table_privilege($1,'config.model_api_release','DELETE')", role)
        # Grant mutation deliberately to prove the append-only trigger also rejects it.
        await conn.execute(f'GRANT UPDATE,DELETE ON config.model_api_release TO "{role}"')
        checks.append("late_runtime_grants_reconciled_without_delete")
        await conn.execute(f'GRANT SELECT,INSERT ON ops.audit_log TO "{role}"')

        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False):
                c = await asyncpg.connect(database=name, **pg)
                await _initialize_connection(c)
                try:
                    await c.execute(f'SET ROLE "{role}"')
                    async with c.transaction(readonly=readonly):
                        await set_request_context(c, actor)
                        yield c
                finally:
                    await c.close()

        db = DB()
        actor = first["administrator"]
        calls = []

        async def probe(purpose, settings, config):
            # Prove no DB transaction is held while the external sample runs.
            calls.append((purpose, settings.llm_model))
            assert settings.senseaudio_api_key == "fixture-api-key"

        with tempfile.TemporaryDirectory() as directory:
            ring = Path(directory) / "ring.json"
            ring.write_text(json.dumps({"keys": {"test": base64.b64encode(b"x" * 32).decode()}}))
            ring.chmod(0o600)
            settings = replace(
                get_settings(),
                senseaudio_api_key="legacy-fixture",
                config_credential_key_id="test",
                config_credential_keyring_file=str(ring),
            )
            svc = ModelApiService(db, settings, probe=probe)
            initial = await svc.list(actor)
            assert len(initial["items"]) == 3 and all(i["version"] == 0 for i in initial["items"])
            config = {
                "mode": "custom",
                "provider_name": "Fixture",
                "endpoint_url": "https://fixture.example/v1/chat/completions",
                "protocol": "chat_completions",
                "model": "fixture-model",
                "timeout_seconds": 30,
                "max_retries": 1,
            }
            body = ConnectionTest(
                request_id=uuid4(), expected_version=0, configuration=config, api_key="fixture-api-key"
            )
            result = await svc.test(actor, "text", body)
            assert result["status"] == "passed" and result["result"]["network_sent"]
            assert "fixture-api-key" not in json.dumps(result, default=str)
            assert await svc.test(actor, "text", body) == result and len(calls) == 1
            changed = body.model_copy(update={"api_key": None})
            try:
                await svc.test(actor, "text", changed)
                raise AssertionError("changed request accepted")
            except ModelApiError:
                pass
            checks.append("test_idempotency_and_encryption")
            other = ConnectionTest(
                request_id=uuid4(), expected_version=0, configuration=config, api_key="fixture-api-key"
            )
            await svc.test(actor, "text", other)
            releases = await asyncio.gather(
                *(
                    svc.publish(actor, "text", ConnectionPublish(test_id=b.request_id, expected_version=0))
                    for b in (body, other)
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(r, dict) for r in releases) == 1
            assert sum(isinstance(r, ModelApiError) for r in releases) == 1
            winner = (body, other)[next(i for i, r in enumerate(releases) if isinstance(r, dict))]
            assert (await svc.publish(actor, "text", ConnectionPublish(test_id=winner.request_id, expected_version=0)))[
                "replayed"
            ]
            checks.append("concurrent_publish_exactly_one_wins_and_retry_is_idempotent")
            runtime = await load_runtime_configuration(db, first["sales"], settings)
            assert (
                runtime.settings.llm_model == "fixture-model"
                and runtime.settings.senseaudio_api_key == "fixture-api-key"
            )
            asr = await load_runtime_configuration(db, first["sales"], settings, purpose="asr")
            assert asr.settings.asr_model == settings.asr_model and not asr.settings.model_api_endpoint
            assert not (await load_runtime_configuration(db, second["sales"], settings)).settings.model_api_endpoint
            checks.append("real_runtime_uses_published_purpose_tenant_and_legacy_fallback")
            for outsider in (second["administrator"], first["sales"], first["operations"]):
                async with db.transaction(outsider) as c:
                    assert await svc.repo.test(c, outsider, body.request_id) is None
                try:
                    await svc.publish(
                        outsider, "text", ConnectionPublish(test_id=winner.request_id, expected_version=0)
                    )
                    raise AssertionError("outside publish")
                except ModelApiError:
                    pass
            checks.append("ordinary_role_RLS_rejects_other_tenant_sales_and_operations")
            async with db.transaction(actor, readonly=True) as c:
                current = await svc.repo.current(c, actor, "text")
                history = await svc.repo.releases(c, actor, "text")
                assert "ciphertext" not in json.dumps(history, default=str) and "fixture-api-key" not in json.dumps(
                    history, default=str
                )
                assert (
                    CredentialCipher.from_file(str(ring), "test").decrypt(
                        actor.workspace_id, "test", current["api_key_ciphertext"]
                    )
                    == "fixture-api-key"
                )
            try:
                async with db.transaction(actor) as c:
                    await c.execute("UPDATE config.model_api_release SET version_no=version_no")
                raise AssertionError("append-only bypass")
            except (asyncpg.RaiseError, asyncpg.InsufficientPrivilegeError):
                pass
            checks.append("history_masked_append_only")
            restore = ConnectionTest(
                request_id=uuid4(), expected_version=1, configuration={**config, "mode": "disabled"}
            )
            disabled = await svc.test(actor, "text", restore)
            assert disabled["status"] == "passed" and not disabled["result"]["network_sent"] and len(calls) == 2
            await svc.publish(actor, "text", ConnectionPublish(test_id=restore.request_id, expected_version=1))
            assert not (
                await load_runtime_configuration(db, first["sales"], settings)
            ).settings.model_gateway_configured
            restore = ConnectionTest(
                request_id=uuid4(), expected_version=2, configuration={**config, "mode": "inherit"}
            )
            await svc.test(actor, "text", restore)
            await svc.publish(actor, "text", ConnectionPublish(test_id=restore.request_id, expected_version=2))
            assert (
                await load_runtime_configuration(db, first["sales"], settings)
            ).settings.senseaudio_api_key == "legacy-fixture"
            checks.append("disabled_and_inherit_are_local_checks_with_correct_runtime_binding")
        print(json.dumps({"passed": checks}, ensure_ascii=False, indent=2))
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if created_role:
            await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
