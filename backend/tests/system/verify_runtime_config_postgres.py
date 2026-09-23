"""Disposable PostgreSQL upgrade, independent-connection CAS and credential recovery.

Only PGHOST/PGUSER local maintenance connection, no application DSN or live keys.
Every credential below is generated test material in a temporary private directory.
"""
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
sys.path.insert(0, str(ROOT / "database/tests"))
sys.path.insert(0, str(ROOT / "database/scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))
from migrate import migrate
from verify_runtime_grants_postgres import migrate_through

from sales_backend.config import get_settings
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable, decrypt_credential
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.runtime_config_management import ConfigVersionConflict, RuntimeConfigService


def snapshot(model="fixture-model"):
    return {"provider_base_url": "https://fixture-provider.invalid", "llm_model": model,
            "asr_model": "fixture-asr", "tts_model": "fixture-tts",
            "prompt_overrides": {"visit_entry": "fixture instruction"}, "enabled": True}


async def workspace_fixture(connection):
    workspace, team = uuid4(), uuid4()
    await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Config fixture')",
                             workspace, str(workspace))
    await connection.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'CONFIG','Config')", team, workspace)
    actors = {}
    for role in ("administrator", "operations", "sales"):
        user = uuid4()
        await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code)
          VALUES($1,$2,$3,$3,$3)""", user, workspace, str(user).upper())
        await connection.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id)
          VALUES($1,$2,$3,$4,$5)""", workspace, user, role, "self" if role == "sales" else "workspace", team)
        await connection.execute("""INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
          VALUES($1,$2,$3,$4)""", workspace, user, team, role)
        actors[role] = ActorContext(workspace_id=str(workspace), user_id=str(user), role=role,
                                  data_scope="self" if role == "sales" else "workspace", team_ids=(str(team),))
    return actors


async def main():
    pg = {"host": os.environ.get("PGHOST", "/tmp"), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **pg)
    suffix = uuid4().hex[:12]
    name, role = "salegent_verify_config_" + suffix, "salegent_config_runtime_" + suffix
    connection, created_role = None, False
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **pg)
        await _initialize_connection(connection)
        await migrate_through(connection, 79)
        legacy, matching, fresh = [await workspace_fixture(connection) for _ in range(3)]
        for actors, current_model in ((legacy, "old-rollback-effective"), (matching, "fixture-model")):
            who = actors["administrator"]
            await connection.execute("""INSERT INTO config.agent_runtime_config(workspace_id,provider_base_url,
              api_key_ciphertext,llm_model,asr_model,tts_model,prompt_overrides,enabled,updated_by_user_ref_id)
              VALUES($1::uuid,'https://fixture-provider.invalid',public.pgp_sym_encrypt('fixture-api-key','explicit-legacy-fixture'),
               $2,'fixture-asr','fixture-tts',$3::jsonb,true,$4::uuid)""",
              who.workspace_id, current_model, snapshot()["prompt_overrides"], who.user_id)
            await connection.execute("""INSERT INTO config.agent_runtime_release(workspace_id,version_no,config_snapshot,
              created_by_user_ref_id) VALUES($1::uuid,1,$2::jsonb,$3::uuid)""", who.workspace_id, snapshot(), who.user_id)
        steps = await migrate(connection)
        assert next(item for item in steps if item["key"] == "V080")["status"] == "applied"
        assert await connection.fetchval("SELECT revision_no FROM config.agent_runtime_config WHERE workspace_id=$1::uuid",
                                        legacy["administrator"].workspace_id) == 2
        assert await connection.fetchval("SELECT revision_no FROM config.agent_runtime_config WHERE workspace_id=$1::uuid",
                                        matching["administrator"].workspace_id) == 1
        assert await connection.fetchval("SELECT operation FROM config.agent_runtime_release WHERE workspace_id=$1::uuid AND version_no=2",
                                        legacy["administrator"].workspace_id) == "migration_baseline"
        assert all(item["status"] == "unchanged" for item in await migrate(connection))
        checks.append("legacy_upgrade_reconciles_effective_rollback_and_matching_head_repeat_is_noop")
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role = True
        for schema in ("config", "common", "security", "platform", "ops"):
            await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        await connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON config.agent_runtime_config,config.agent_runtime_release TO "{role}"')
        await connection.execute(f'GRANT SELECT ON platform.user_ref TO "{role}"')
        await connection.execute(f'GRANT SELECT,INSERT ON ops.audit_log,ops.mutation_receipt TO "{role}"')

        @asynccontextmanager
        async def runtime(actor):
            # Every concurrent operation gets a genuine independent connection.
            conn = await asyncpg.connect(database=name, **pg)
            await _initialize_connection(conn)
            try:
                await conn.execute(f'SET ROLE "{role}"')
                await conn.execute("SET row_security=on")
                assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                async with conn.transaction():
                    await set_request_context(conn, actor)
                    yield conn
            finally:
                await conn.close()

        with tempfile.TemporaryDirectory() as directory:
            ring, old_key = Path(directory) / "keyring.json", Path(directory) / "legacy.key"
            ring.write_text(json.dumps({"keys": {"new": base64.b64encode(os.urandom(32)).decode(),
                                                "next": base64.b64encode(os.urandom(32)).decode()}}))
            ring.chmod(0o600)
            old_key.write_text("explicit-legacy-fixture")
            old_key.chmod(0o600)
            settings = replace(get_settings(), config_credential_keyring_file=str(ring),
                config_credential_key_id="new", config_credential_legacy_key_file=str(old_key),
                access_token_secret="not-the-legacy-key", senseaudio_api_key="environment-fixture")
            # Real CLI preflight under password mode and a restricted connection
            # pool; the administrator is deliberately unavailable to demo login.
            import importlib.util
            from types import SimpleNamespace

            from sales_backend.db import Database
            from sales_backend.repositories.identity import IdentityRepository
            cli_path = ROOT / "backend/scripts/runtime_config_credentials.py"
            spec = importlib.util.spec_from_file_location("fixture_runtime_config_cli", cli_path)
            cli = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cli)
            cli.get_settings = lambda: replace(settings, app_env="production", auth_mode="password")
            class RuntimeDatabase(Database):
                async def connect(self):
                    async def setup(conn):
                        await conn.execute(f'SET ROLE "{role}"')
                        await conn.execute("SET row_security=on")
                    self.pool = await asyncpg.create_pool(database=name, min_size=1, max_size=2,
                                                         init=_initialize_connection, setup=setup, **pg)
            cli.Database = RuntimeDatabase
            async with runtime(legacy["administrator"]) as conn:
                formal = await IdentityRepository().find_maintenance_administrator(conn,
                    workspace_external_id=legacy["administrator"].workspace_id,
                    account_code=legacy["administrator"].user_id)
                assert formal.context == legacy["administrator"]
                assert not await conn.fetchrow("SELECT * FROM security.resolve_demo_actor($1,$2)",
                    legacy["administrator"].workspace_id, legacy["administrator"].user_id)
            assert await cli.bounded_run(SimpleNamespace(action="check", workspace=legacy["administrator"].workspace_id,
                                                        administrator=legacy["administrator"].user_id)) == 0
            checks.append("maintenance_cli_uses_formal_administrator_when_demo_login_is_unavailable")
            service, repo = RuntimeConfigService(settings), AgentRuntimeConfigRepository()
            actor = legacy["administrator"]
            async with runtime(actor) as conn:
                before = await repo.current(conn, actor)
                assert await decrypt_credential(conn, before, settings) == "fixture-api-key"
                assert "fixture-api-key" not in json.dumps(await service.get(conn, actor), default=str)
                result = await service.reencrypt(conn, actor)
                after = await repo.current(conn, actor)
                assert result["status"] == "rotated" and after["encryption_revision"] == 2
                assert after["revision_no"] == before["revision_no"] and after["updated_at"] == before["updated_at"]
                assert after["prompt_overrides"] == before["prompt_overrides"]
                assert await decrypt_credential(conn, after, replace(settings, access_token_secret="rotated-login-key")) == "fixture-api-key"
                assert (await service.reencrypt(conn, actor))["status"] == "already_current"
            checks.append("explicit_legacy_recovery_and_resumable_aes_rotation_preserve_business_version_timestamp_prompt")
            next_service = RuntimeConfigService(replace(settings, config_credential_key_id="next"))
            async with runtime(actor) as conn:
                assert (await next_service.reencrypt(conn, actor))["encryption_revision"] == 3
                current = await repo.current(conn, actor)
                assert await decrypt_credential(conn, current, settings) == "fixture-api-key"
                broken = {**current, "api_key_ciphertext": current["api_key_ciphertext"][:-1] + bytes([current["api_key_ciphertext"][-1] ^ 1])}
                try:
                    await decrypt_credential(conn, broken, settings)
                except RuntimeCredentialUnavailable:
                    pass
                else:
                    raise AssertionError("corrupt tag accepted")
            checks.append("independent_keyring_rotation_and_corruption_failure")

            actor = fresh["administrator"]
            async def save(data, key=None, repository=None):
                async with runtime(actor) as conn:
                    return await RuntimeConfigService(settings, repository).save(conn, actor, data, key=key)
            async def rollback(version, expected, key=None):
                async with runtime(actor) as conn:
                    return await service.rollback(conn, actor, version, expected, key=key)
            created = await asyncio.gather(save({**snapshot("one"), "expected_version": 0}),
                                           save({**snapshot("two"), "expected_version": 0}), return_exceptions=True)
            assert sum(isinstance(result, ConfigVersionConflict) for result in created) == 1, created
            assert sum(isinstance(result, dict) and result["revision_no"] == 1 for result in created) == 1
            updated = await asyncio.gather(save({**snapshot("three"), "expected_version": 1}),
                                           save({**snapshot("four"), "expected_version": 1}), return_exceptions=True)
            assert sum(isinstance(result, ConfigVersionConflict) for result in updated) == 1, updated
            raced = await asyncio.gather(save({**snapshot("five"), "expected_version": 2}), rollback(1, 2), return_exceptions=True)
            assert sum(isinstance(result, ConfigVersionConflict) for result in raced) == 1, raced
            checks.append("independent_connections_create_save_and_save_vs_rollback_have_one_winner_no_lost_updates")
            body = {**snapshot("idempotent"), "api_key": "fixture-new-api-key", "expected_version": 3}
            key = uuid4()
            results = await asyncio.gather(save(body, key), save(body, key))
            assert results[0] == results[1] and results[0]["revision_no"] == 4
            try:
                await save({**body, "api_key": "different-fixture-key"}, key)
            except IdempotencyConflict:
                pass
            else:
                raise AssertionError("same request key accepted different credential")
            async with runtime(actor) as conn:
                versions = await repo.releases(conn, actor)
                assert [r["version_no"] for r in versions] == [4, 3, 2, 1]
                receipts = await conn.fetchval("SELECT jsonb_agg(to_jsonb(r)) FROM ops.mutation_receipt r WHERE workspace_id=$1::uuid", actor.workspace_id)
                assert "fixture-new-api-key" not in json.dumps(receipts, default=str)
                assert "fixture-new-api-key" not in json.dumps(versions, default=str)
                audit = await conn.fetchval("SELECT jsonb_agg(to_jsonb(r)) FROM ops.audit_log r WHERE workspace_id=$1::uuid", actor.workspace_id)
                assert "fixture-new-api-key" not in json.dumps(audit, default=str)
            restored = await rollback(1, 4)
            assert restored["revision_no"] == 5
            async with runtime(actor) as conn:
                row = await repo.current(conn, actor)
                assert await decrypt_credential(conn, row, settings) == "fixture-new-api-key"
                releases = await repo.releases(conn, actor)
                assert releases[0]["operation"] == "rollback" and releases[0]["restored_from_version"] == 1
            checks.append("idempotent_replay_redaction_and_rollback_publishes_new_release_retaining_current_credential")
            class AuditFailure(AgentRuntimeConfigRepository):
                async def audit(self, *args, **kwargs):
                    raise RuntimeError("fixture-audit-failure")
            failed_key = uuid4()
            try:
                await save({**snapshot("must-not-survive"), "expected_version": 5}, failed_key, AuditFailure())
            except RuntimeError as error:
                assert str(error) == "fixture-audit-failure"
            else:
                raise AssertionError("audit failure did not propagate")
            async with runtime(actor) as conn:
                assert (await repo.current(conn, actor))["revision_no"] == 5
                assert len(await repo.releases(conn, actor)) == 5
            retry = await save({**snapshot("must-not-survive"), "expected_version": 5}, failed_key)
            assert retry["revision_no"] == 6
            checks.append("audit_failure_rolls_back_current_release_receipt_and_same_request_can_retry")
            async def rotate_concurrently():
                async with runtime(actor) as conn:
                    return await next_service.reencrypt(conn, actor)
            concurrent_key = "fixture-concurrent-replacement"
            await asyncio.gather(save({**snapshot("rotating"), "expected_version": 6, "api_key": concurrent_key}),
                                 rotate_concurrently())
            async with runtime(actor) as conn:
                row = await repo.current(conn, actor)
                assert row["revision_no"] == 7
                assert await decrypt_credential(conn, row, settings) == concurrent_key
            checks.append("credential_rotation_and_admin_replacement_share_lock_no_stale_credential_overwrite")
            await connection.execute("""CREATE FUNCTION config.fixture_reject_release() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN RAISE EXCEPTION 'fixture release failure' USING ERRCODE='40001'; END $$;
              CREATE TRIGGER fixture_reject_release BEFORE INSERT ON config.agent_runtime_release
              FOR EACH STATEMENT EXECUTE FUNCTION config.fixture_reject_release();""")
            release_key = uuid4()
            try:
                try:
                    await save({**snapshot("release-failure"), "expected_version": 7}, release_key)
                except asyncpg.SerializationError:
                    pass
                else:
                    raise AssertionError("release failure accepted")
            finally:
                await connection.execute("DROP TRIGGER fixture_reject_release ON config.agent_runtime_release; "
                                         "DROP FUNCTION config.fixture_reject_release()")
            async with runtime(actor) as conn:
                assert (await repo.current(conn, actor))["revision_no"] == 7
                assert len(await repo.releases(conn, actor)) == 7
            assert (await save({**snapshot("release-failure"), "expected_version": 7}, release_key))["revision_no"] == 8
            checks.append("actual_release_insert_failure_rolls_back_current_and_receipt_then_recovers")
            for forbidden in (fresh["operations"], fresh["sales"]):
                try:
                    async with runtime(forbidden) as conn:
                        await service.save(conn, forbidden, {**snapshot(), "expected_version": 0})
                except asyncpg.InsufficientPrivilegeError:
                    pass
                else:
                    raise AssertionError("non-admin wrote runtime config")
            async with runtime(actor) as conn:
                for query in ("DELETE FROM config.agent_runtime_release", "UPDATE config.agent_runtime_release SET operation='save'"):
                    try:
                        async with conn.transaction():
                            await conn.execute(query)
                    except asyncpg.InsufficientPrivilegeError:
                        pass
                    else:
                        raise AssertionError("immutable history mutated")
                assert not await conn.fetchval("SELECT 1 FROM config.agent_runtime_config WHERE workspace_id=$1::uuid",
                                               legacy["administrator"].workspace_id)
            forged = actor.model_copy(update={"workspace_id": legacy["administrator"].workspace_id})
            try:
                async with runtime(forged) as conn:
                    await service.save(conn, forged, {**snapshot(), "expected_version": 0})
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError("foreign-workspace administrator binding accepted")
            checks.append("nonbypass_roles_cannot_write_as_operations_sales_or_foreign_workspace_history_is_immutable")
        print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if created_role:
            await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=150))
