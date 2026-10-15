"""Reproduce V152 mixed-owner authentication and scheduler failures in private PG16.

Only synthetic data in the disposable cluster harness. No application instance,
production environment, credentials, model or notification service is contacted.
"""

from __future__ import annotations

import getpass
import json
import secrets
from pathlib import Path

import asyncpg
import verify_legacy_adoption_postgres as harness
from legacy_adoption_state import AdoptionError, fingerprint
from legacy_definer_dependencies import CALLEES, CALLERS, READ_TABLES, WRITE_TABLES, apply_plan, build_plan
from runtime_roles import SERVICES, identifier, load_policy, verify
from upgrade_permissions import cluster_identity, reconcile

from sales_backend.auth.passwords import encode_password
from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.services.password_auth import PasswordAuthService

ROOT = Path(__file__).resolve().parents[3]
OWNER = "synthetic_legacy_owner"


def settings(dsn):
    return Settings(
        app_env="test",
        database_url=dsn,
        senseaudio_base_url="https://synthetic.invalid",
        senseaudio_api_key="",
        asr_model="",
        tts_model="",
        llm_model="",
        timeout_seconds=1,
        max_retries=0,
        access_token_secret=secrets.token_urlsafe(48),
        access_token_issuer="mixed-owner-test",
        access_token_audience="mixed-owner-test",
        access_token_minutes=30,
        refresh_token_days=1,
        auth_mode="password",
        demo_workspace="",
        database_min_pool_size=1,
        database_max_pool_size=2,
        worker_poll_seconds=1,
        worker_lock_seconds=30,
        worker_id="synthetic",
    )


async def rejected(call, exception):
    try:
        await call()
    except exception:
        return
    raise AssertionError("Expected mixed-owner rejection")


async def exercise(root, host, pg_bin):
    config = {"host": host, "user": getpass.getuser(), "port": 5432}
    admin = await asyncpg.connect(database="postgres", **config)
    await admin.execute("CREATE DATABASE mixed_owner_fixture TEMPLATE template0")
    await admin.close()
    conn = await asyncpg.connect(database="mixed_owner_fixture", **config)
    database = None
    try:
        historical = harness.fixture.archive_baseline(root / "historical")
        await harness.fixture.migrate.migrate(conn, historical)
        actors = await harness.fixture.fixture(conn)
        await conn.execute(f"CREATE ROLE {OWNER} LOGIN BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE")
        for schema in harness.fixture.SCHEMAS:
            await conn.execute(f"ALTER SCHEMA {identifier(schema)} OWNER TO {OWNER}")
        invokers = (
            "common.current_workspace_id()",
            "common.current_user_ref_id()",
            "common.current_role_code()",
            "common.touch_updated_at()",
            "common.touch_runtime_updated_at()",
        )
        for signature in (*CALLERS, *invokers):
            await conn.execute(f"ALTER FUNCTION {signature} OWNER TO {OWNER}")
        for table, privileges in {**{t: ("SELECT",) for t in READ_TABLES}, **WRITE_TABLES}.items():
            await conn.execute(f"GRANT {','.join(privileges)} ON {table} TO {OWNER}")
        # Row locking by the historical scheduler also needs one UPDATE column.
        await conn.execute(f"GRANT UPDATE ON platform.workspace TO {OWNER}")
        original_owners = dict(
            await conn.fetch(
                "SELECT p.oid::regprocedure::text,pg_get_userbyid(p.proowner) "
                "FROM pg_proc p WHERE p.proowner=$1::regrole",
                OWNER,
            )
        )
        await harness.fixture.migrate.migrate(conn, ROOT / "database")
        assert await conn.fetchval("SELECT max(version) FROM ops.schema_migration") == harness.fixture.TARGET
        # These four explicit legacy grants were present in the observed mixed
        # ownership layout; the newly referenced workspace predicate was not.
        for signature in CALLEES:
            if signature != "security.workspace_is_active(uuid)":
                await conn.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {OWNER}")
        roles = {s: "adoption_" + s for s in SERVICES}
        policy = load_policy(ROOT / "tools/installation/runtime_permissions.json")
        passwords = {s: secrets.token_urlsafe(48) for s in SERVICES}
        async with conn.transaction():
            for service, role in roles.items():
                literal = await conn.fetchval("SELECT quote_literal($1::text)", passwords[service])
                await conn.execute(
                    f"CREATE ROLE {identifier(role)} LOGIN NOSUPERUSER NOBYPASSRLS "
                    f"NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal}"
                )
                await conn.execute(f"GRANT CONNECT ON DATABASE mixed_owner_fixture TO {identifier(role)}")
                for group in policy["services"][service]["memberships"]:
                    await conn.execute(f"GRANT {identifier(group)} TO {identifier(role)} WITH INHERIT FALSE, SET FALSE")
            await conn.execute("REVOKE CREATE ON DATABASE mixed_owner_fixture FROM PUBLIC")
            await conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await reconcile(conn, roles, policy)
        password = secrets.token_urlsafe(24)
        encoded = encode_password(password)
        for actor in actors:
            await conn.execute(
                "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) "
                "VALUES($1,$2,$3,false)",
                actor["administrator"],
                actor["workspace"],
                encoded,
            )
        conf = settings(f"postgresql://{roles['api']}:{passwords['api']}@/mixed_owner_fixture?host={host}&port=5432")
        database = Database(conf)
        await database.connect()
        auth = PasswordAuthService(database, conf)

        async def login():
            return await auth.login(account="ADMINISTRATOR", password=password, workspace="upgrade-company-0")

        await rejected(login, asyncpg.InsufficientPrivilegeError)
        job_type = "mixed-owner.synthetic"
        job_id = await conn.fetchval(
            "INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1,$2,'{}') RETURNING id",
            actors[0]["workspace"],
            job_type,
        )
        claimed_before = await conn.fetchval(
            "SELECT last_claimed_at::text FROM control.tenant_job_schedule WHERE workspace_id=$1",
            actors[0]["workspace"],
        )

        async def scheduler():
            worker = await asyncpg.connect(
                database="mixed_owner_fixture", host=host, port=5432, user=roles["worker"], password=passwords["worker"]
            )
            try:
                return await worker.fetch("SELECT * FROM ops.claim_job('mixed-owner',30,$1::text[],false)", [job_type])
            finally:
                await worker.close()

        await rejected(scheduler, asyncpg.InsufficientPrivilegeError)
        plan = await build_plan(conn, roles, policy)
        assert sorted(key for _, key in plan["grants"]) == [
            "control_usage",
            "schedule_select",
            "schedule_update",
            "workspace_active_execute",
        ]
        # Both function body and owner drift invalidate a previously reviewed plan.
        for sql in (
            "CREATE OR REPLACE FUNCTION security.has_active_role(p_role_code text) RETURNS boolean "
            "LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT false'",
            f"ALTER FUNCTION security.has_active_role(text) OWNER TO {identifier(getpass.getuser())}",
        ):
            transaction = conn.transaction()
            await transaction.start()
            try:
                await conn.execute(sql)
                await rejected(lambda: apply_plan(conn, plan, roles, policy), AdoptionError)
            finally:
                await transaction.rollback()
        before_hash = fingerprint(plan)
        transaction = conn.transaction()
        await transaction.start()
        await apply_plan(conn, plan, roles, policy)
        await transaction.rollback()
        assert fingerprint(await build_plan(conn, roles, policy)) == before_hash
        async with conn.transaction():
            result = await apply_plan(conn, plan, roles, policy)
        session, change = await login()
        assert not change and session.actor.user_id == str(actors[0]["administrator"])
        refreshed = await auth.refresh(session.refresh_token, require_password=True)
        assert refreshed.actor.user_id == session.actor.user_id
        claimed = await scheduler()
        assert len(claimed) == 1 and claimed[0]["id"] == job_id and claimed[0]["status"] == "running"
        assert claimed[0]["attempts"] == 1 and claimed[0]["lease_token"] is not None
        assert await conn.fetchval(
            "SELECT last_claimed_at > $2::text::timestamptz FROM control.tenant_job_schedule WHERE workspace_id=$1",
            actors[0]["workspace"],
            claimed_before,
        )
        async with database.transaction(
            auth.tokens.decode_access_token(refreshed.access_token)[0], readonly=True
        ) as runtime:
            assert await runtime.fetchval("SELECT security.has_customer_access($1)", actors[0]["customer"])
            assert not await runtime.fetchval("SELECT security.has_customer_access($1)", actors[1]["customer"])
        assert (
            dict(
                await conn.fetch(
                    "SELECT p.oid::regprocedure::text,pg_get_userbyid(p.proowner) "
                    "FROM pg_proc p WHERE p.proowner=$1::regrole",
                    OWNER,
                )
            )
            == original_owners
        )
        await verify(conn, roles, policy)
        assert not (await build_plan(conn, roles, policy))["grants"]
        assert fingerprint(await build_plan(conn, roles, policy)) == result["verified_plan_sha256"]
        # The commit-unknown resume path validates retained-owner dependencies,
        # not just the four runtime identities.
        adoption_spec = {
            "database": {"name": "mixed_owner_fixture", "administrator": getpass.getuser()},
            "new_release": str(ROOT),
            "roles": roles,
            "retire_roles": [],
        }
        journal = {
            "spec": adoption_spec,
            "database_identity": await harness.database.identity(conn, adoption_spec),
            "before_ledger": {},
            "after_ledger": await harness.database.ledger(conn),
            "after_cluster": fingerprint(await cluster_identity(conn)),
            "legacy_definer_dependencies": result,
        }
        assert await harness.database.state(conn, journal) == "new"
        transaction = conn.transaction()
        await transaction.start()
        try:
            await conn.execute(f"REVOKE EXECUTE ON FUNCTION security.workspace_is_active(uuid) FROM {OWNER}")
            await rejected(lambda: harness.database.state(conn, journal), AdoptionError)
        finally:
            await transaction.rollback()
        assert not await conn.fetchval("SELECT has_table_privilege($1,'control.tenant_job_schedule','UPDATE')", OWNER)
        assert await conn.fetchval(
            "SELECT has_column_privilege($1,'control.tenant_job_schedule','last_claimed_at','UPDATE')", OWNER
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "schema": harness.fixture.TARGET,
                    "mixed_owner_count": 18,
                    "login_failure_reproduced": True,
                    "scheduler_failure_reproduced": True,
                    "login_refresh_customer_scope_passed": True,
                    "restricted_worker_claim_and_schedule_update_passed": True,
                    "definition_and_owner_drift_rejected": True,
                    "rollback_preserved": True,
                    "resume_dependency_drift_rejected": True,
                    "legacy_owners_preserved": True,
                    "runtime_exact_acl_verified": True,
                    "grant_count": len(result["grants"]),
                    "production_data": False,
                },
                indent=2,
            )
        )
    finally:
        if database is not None:
            await database.close()
        await conn.close()


if __name__ == "__main__":
    harness.exercise = exercise
    harness.main()
