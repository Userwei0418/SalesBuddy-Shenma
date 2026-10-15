"""Exercise API upserts with real SCRAM roles, column ACLs and unchanged tenant RLS."""
# ruff: noqa: S608 -- SQL identifiers come exclusively from fixed test constants.

from __future__ import annotations

import copy
import getpass
import json
import secrets
from pathlib import Path
from uuid import uuid4

import asyncpg
import verify_legacy_adoption_postgres as harness
from runtime_roles import SERVICES, identifier, load_policy, verify
from upgrade_permissions import reconcile
from verify_legacy_definer_dependencies_postgres import settings

from sales_backend.contracts.visit_flow import canonical_fields
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, AgentMode, DataScope, RoleCode
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.collaboration import write_visit_participants
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.model_api import ModelApiRepository
from sales_backend.repositories.profile import ProfileRepository
from sales_backend.services.visit_archive import archive_visit

ROOT = Path(__file__).resolve().parents[3]
ADDED = {
    "activity.visit_participant": ["participant_role", "team_id_at_event", "role_code_at_event", "updated_at"],
    "config.model_api_current": ["version_no"],
    "insight.sales_competency_review": ["status", "error_code", "error_detail", "reviewed_at"],
}


async def denied(call, *, table=None):
    try:
        await call()
    except asyncpg.InsufficientPrivilegeError as exc:
        if table:
            assert table in str(exc), (table, str(exc))
        return
    raise AssertionError("Expected ACL/RLS rejection")


async def review_fixture(api, maintenance, actor, tenant):
    """Persist a synthetic accepted model result; never invoke a provider."""
    fields = canonical_fields(
        {
            "customer_name": "Synthetic API regression customer",
            "customer_type": "客户",
            "follow_up_record": "与合成测试联系人核对验收范围、参与人员及后续技术交流安排。",
            "next_action": "2026年9月30日17点前发送合成验收清单并确认交流时间。",
            "interaction_at": "2026-09-28",
            "created_date": "2026-09-28",
            "contact_name": "合成联系人",
        }
    )
    request = {
        "stage": "quality",
        "fields": fields,
        "opportunity_id": str(tenant["opportunity"]),
        "source_import_id": None,
        "collaborator_ids": [],
        "fde_participant_ids": [],
        "opportunity_mutation": None,
    }
    async with api.transaction(actor) as conn:
        repo = AssistantRepository()
        conversation = await repo.create_conversation(
            conn,
            actor,
            mode=AgentMode.VISIT_ENTRY,
            customer_id=str(tenant["customer"]),
            opportunity_id=str(tenant["opportunity"]),
        )
        run = await repo.enqueue_message(
            conn,
            actor,
            conversation_id=conversation["id"],
            text="合成归档权限回归",
            client_message_id=None,
            input_source="text",
            business_context={"visit_request": request},
        )
        policy = await CompanyRulesRepository().active(conn, "visit_admission")
    artifact = str(uuid4())
    await maintenance.execute("UPDATE agent.run SET status='waiting_human' WHERE id=$1::uuid", run)
    payload = {
        "visit_stage": "quality",
        "company_policy": policy,
        "quality_review": {
            "follow_up_score": 85,
            "next_action": {"passed": True, "time_found": True, "goal_or_plan_found": True},
        },
    }
    await maintenance.execute(
        "INSERT INTO agent.artifact(id,workspace_id,conversation_id,run_id,artifact_type,schema_code,"
        "schema_version,status,subject_type,subject_id,payload,created_by_user_ref_id) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,'visit_entry','visit_entry',1,'pending_confirm',"
        "'customer',$5::uuid,$6::jsonb,$7::uuid)",
        artifact,
        actor.workspace_id,
        conversation["id"],
        run,
        str(tenant["customer"]),
        json.dumps(payload, default=str),
        actor.user_id,
    )
    return artifact, {
        **fields,
        "opportunity_id": str(tenant["opportunity"]),
        "source_import_id": None,
        "collaborator_ids": [],
        "_fde_participant_ids": [],
        "_quality_review_run_id": run,
    }


async def model_test(maintenance, actor, version):
    test = {
        "id": str(uuid4()),
        "purpose": "tts",
        "expected_version": version,
        "config_snapshot": {"provider": "synthetic-offline"},
        "restored_from_version": None,
    }
    await maintenance.execute(
        "INSERT INTO config.model_api_test(id,workspace_id,purpose,actor_user_ref_id,expected_version,"
        "request_digest,config_snapshot,status) VALUES($1::uuid,$2::uuid,'tts',$3::uuid,$4,'synthetic',"
        "$5::jsonb,'passed')",
        test["id"],
        actor.workspace_id,
        actor.user_id,
        version,
        json.dumps(test["config_snapshot"]),
    )
    return test


async def exercise(root, host, _pg_bin):
    config = {"host": host, "user": getpass.getuser(), "port": 5432}
    admin = await asyncpg.connect(database="postgres", **config)
    await admin.execute("CREATE DATABASE api_upsert_fixture TEMPLATE template0")
    await admin.close()
    conn = await asyncpg.connect(database="api_upsert_fixture", **config)
    api = None
    try:
        await harness.fixture.migrate.migrate(conn, harness.fixture.archive_baseline(root / "historical"))
        tenants = await harness.fixture.fixture(conn)
        await harness.fixture.migrate.migrate(conn, ROOT / "database")
        policy = load_policy(ROOT / "tools/installation/runtime_permissions.json")
        original = copy.deepcopy(policy)
        for table, columns in ADDED.items():
            assert original["services"]["api"]["columns"].pop(table) == {"UPDATE": columns}
            assert "UPDATE" not in policy["services"]["api"]["tables"][table]
        roles = {service: "adoption_" + service for service in SERVICES}
        passwords = {service: secrets.token_urlsafe(48) for service in SERVICES}
        async with conn.transaction():
            for service, role in roles.items():
                literal = await conn.fetchval("SELECT quote_literal($1::text)", passwords[service])
                await conn.execute(
                    f"CREATE ROLE {identifier(role)} LOGIN NOSUPERUSER NOBYPASSRLS "
                    f"NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal}"
                )
                await conn.execute(f"GRANT CONNECT ON DATABASE api_upsert_fixture TO {identifier(role)}")
                for group in policy["services"][service]["memberships"]:
                    await conn.execute(f"GRANT {identifier(group)} TO {identifier(role)} WITH INHERIT FALSE, SET FALSE")
            await conn.execute("REVOKE CREATE ON DATABASE api_upsert_fixture FROM PUBLIC")
            await conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await reconcile(conn, roles, original)
        api = Database(
            settings(f"postgresql://adoption_api:{passwords['api']}@/api_upsert_fixture?host={host}&port=5432")
        )
        await api.connect()
        actors = [
            ActorContext(
                workspace_id=str(t["workspace"]),
                user_id=str(t["sales"]),
                role=RoleCode.SALES,
                data_scope=DataScope.SELF,
                team_ids=(str(t["team"]),),
            )
            for t in tenants
        ]
        admins = [
            ActorContext(
                workspace_id=str(t["workspace"]),
                user_id=str(t["administrator"]),
                role=RoleCode.ADMINISTRATOR,
                data_scope=DataScope.WORKSPACE,
                team_ids=(str(t["team"]),),
            )
            for t in tenants
        ]
        actor, tenant = actors[0], tenants[0]
        artifact, fields = await review_fixture(api, conn, actor, tenant)
        fixture_visit = await conn.fetchval(
            "SELECT id::text FROM activity.visit WHERE workspace_id=$1 LIMIT 1", tenant["workspace"]
        )
        test = await model_test(conn, admins[0], 0)

        async def archive():
            async with api.transaction(actor) as runtime:
                return await archive_visit(runtime, actor, customer_id=str(tenant["customer"]), fields=fields)

        async def participants():
            async with api.transaction(actor) as runtime:
                await write_visit_participants(runtime, actor, fixture_visit, [actor.user_id], [])

        async def publish():
            async with api.transaction(admins[0]) as runtime:
                return await ModelApiRepository().publish(runtime, admins[0], test)

        before = await conn.fetchval("SELECT count(*) FROM activity.visit")
        before_jobs = await conn.fetchval("SELECT count(*) FROM ops.job")
        await denied(archive, table="sales_competency_review")
        assert await conn.fetchval("SELECT count(*) FROM activity.visit") == before
        assert await conn.fetchval("SELECT count(*) FROM ops.job") == before_jobs
        assert await conn.fetchval("SELECT status FROM agent.artifact WHERE id=$1::uuid", artifact) == "pending_confirm"
        assert await conn.fetchval("SELECT count(*) FROM agent.confirmation WHERE artifact_id=$1::uuid", artifact) == 0
        await denied(participants, table="visit_participant")
        await denied(publish, table="model_api_current")
        assert (
            await conn.fetchval("SELECT count(*) FROM config.model_api_release WHERE test_id=$1::uuid", test["id"]) == 0
        )

        async with conn.transaction():
            await reconcile(conn, roles, policy)
            await verify(conn, roles, policy)
        for table, columns in ADDED.items():
            assert not await conn.fetchval("SELECT has_table_privilege('adoption_api',$1,'UPDATE')", table)
            for column in columns:
                assert await conn.fetchval("SELECT has_column_privilege('adoption_api',$1,$2,'UPDATE')", table, column)

        archived = await archive()
        assert archived["id"] and await conn.fetchval("SELECT count(*) FROM activity.visit") == before + 1
        jobs = await conn.fetchval("SELECT count(*) FROM ops.job")
        replay = await archive()
        assert replay["replayed"] and replay["id"] == archived["id"]
        assert await conn.fetchval("SELECT count(*) FROM activity.visit") == before + 1
        assert await conn.fetchval("SELECT count(*) FROM ops.job") == jobs
        assert await conn.fetchval("SELECT count(*) FROM agent.confirmation WHERE artifact_id=$1::uuid", artifact) == 1

        # Re-evaluation may reset queue/error metadata, never overwrite AI scores.
        reviews = []
        for subject in actors:
            async with api.transaction(subject) as runtime:
                reviews.append(await ProfileRepository().ensure_daily_competency_review(runtime, subject))
        review_id = reviews[0]["id"]
        for status, force, expected in [
            ("succeeded", False, "succeeded"),
            ("succeeded", True, "queued"),
            ("failed", False, "queued"),
            ("running", True, "running"),
        ]:
            await conn.execute(
                "UPDATE insight.sales_competency_review SET status=$2,overall_score=81,"
                "error_code='synthetic',error_detail='synthetic',reviewed_at=clock_timestamp() "
                "WHERE id=$1::uuid",
                review_id,
                status,
            )
            async with api.transaction(actor) as runtime:
                result = await ProfileRepository().ensure_daily_competency_review(runtime, actor, force=force)
            assert result["id"] == review_id and result["status"] == expected
            stored = await conn.fetchrow("SELECT * FROM insight.sales_competency_review WHERE id=$1::uuid", review_id)
            assert stored["overall_score"] == 81
            assert (stored["error_code"] is None) == (expected == "queued")
            assert (stored["reviewed_at"] is None) == (status == "succeeded" and force)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid AND job_type='sales_competency.review'",
                review_id,
            )
            == 1
        )

        await participants()
        await participants()
        person = {"id": actor.user_id, "team_id": str(tenant["team"]), "role": "fde"}
        async with api.transaction(actor) as runtime:
            await write_visit_participants(runtime, actor, fixture_visit, [], [person])
            # An unchanged participation role retains its original event snapshot.
            await write_visit_participants(runtime, actor, fixture_visit, [], [{**person, "role": "fde_lead"}])
            row = await runtime.fetchrow(
                "SELECT * FROM activity.visit_participant WHERE visit_id=$1::uuid AND user_ref_id=$2::uuid",
                fixture_visit,
                actor.user_id,
            )
            assert row["participant_role"] == "fde" and row["role_code_at_event"] == "fde"
        assert await publish() == 1
        test = await model_test(conn, admins[0], 1)
        assert await publish() == 2
        assert (
            await conn.fetchval(
                "SELECT version_no FROM config.model_api_current WHERE workspace_id=$1 AND purpose='tts'",
                tenant["workspace"],
            )
            == 2
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM config.model_api_release WHERE workspace_id=$1 AND purpose='tts'",
                tenant["workspace"],
            )
            == 2
        )

        # Verify denied business columns even when assigning their current value.
        for table, column, identity in [
            ("insight.sales_competency_review", "overall_score", actor),
            ("insight.sales_competency_review", "subject_user_ref_id", actor),
            ("activity.visit_participant", "created_by_user_ref_id", actor),
            ("config.model_api_current", "purpose", admins[0]),
        ]:

            async def forbidden(table=table, column=column, identity=identity):
                async with api.transaction(identity) as runtime:
                    await runtime.execute(f"UPDATE {table} SET {column}={column}")

            await denied(forbidden)

        # Real rows exist in the other tenant; all three permitted updates still obey RLS.
        other_visit = await conn.fetchval(
            "SELECT id::text FROM activity.visit WHERE workspace_id=$1 LIMIT 1", tenants[1]["workspace"]
        )
        async with api.transaction(actors[1]) as runtime:
            await write_visit_participants(runtime, actors[1], other_visit, [actors[1].user_id], [])
        other_test = await model_test(conn, admins[1], 0)
        async with api.transaction(admins[1]) as runtime:
            await ModelApiRepository().publish(runtime, admins[1], other_test)
        for table, column, identity in [
            ("insight.sales_competency_review", "status", actor),
            ("activity.visit_participant", "participant_role", actor),
            ("config.model_api_current", "version_no", admins[0]),
        ]:
            async with api.transaction(identity) as runtime:
                assert (
                    await runtime.fetchval(
                        f"SELECT count(*) FROM {table} WHERE workspace_id=$1::uuid", actors[1].workspace_id
                    )
                    == 0
                )
                assert (
                    await runtime.execute(
                        f"UPDATE {table} SET {column}={column} WHERE workspace_id=$1::uuid", actors[1].workspace_id
                    )
                    == "UPDATE 0"
                )

            async def foreign_insert(table=table, identity=identity):
                async with api.transaction(identity) as runtime:
                    if table == "insight.sales_competency_review":
                        await runtime.execute(
                            "INSERT INTO insight.sales_competency_review(workspace_id,subject_user_ref_id,"
                            "review_date,framework_version) VALUES($1::uuid,$2::uuid,'2099-01-01',1)",
                            actors[1].workspace_id,
                            actors[1].user_id,
                        )
                    elif table == "activity.visit_participant":
                        await runtime.execute(
                            "INSERT INTO activity.visit_participant(visit_id,user_ref_id,workspace_id) "
                            "VALUES($1::uuid,$2::uuid,$3::uuid)",
                            other_visit,
                            actor.user_id,
                            actors[1].workspace_id,
                        )
                    else:
                        await runtime.execute(
                            "INSERT INTO config.model_api_current(workspace_id,purpose,version_no) "
                            "VALUES($1::uuid,'tts',1)",
                            actors[1].workspace_id,
                        )

            await denied(foreign_insert)
        await verify(conn, roles, policy)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "schema": harness.fixture.TARGET,
                    "production_data": False,
                    "old_policy_upsert_failures_reproduced": 3,
                    "api_update_columns_added": 9,
                    "archive_transaction_rollback_and_replay": True,
                    "score_columns_unchanged": True,
                    "first_insert_and_conflict_update": 3,
                    "cross_tenant_rejected": 3,
                    "four_runtime_roles_exact": True,
                    "provider_calls": 0,
                },
                indent=2,
            )
        )
    finally:
        if api:
            await api.close()
        await conn.close()


if __name__ == "__main__":
    harness.exercise = exercise
    harness.main()
