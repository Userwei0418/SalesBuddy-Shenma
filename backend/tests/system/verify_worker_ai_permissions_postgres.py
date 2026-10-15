"""Exercise real worker ACL/RLS and AI observation without any provider calls."""
# ruff: noqa: S608 -- SQL table/column substitutions are fixed test constants only.

from __future__ import annotations

import copy
import getpass
import json
import secrets
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import verify_legacy_adoption_postgres as harness
from runtime_roles import SERVICES, identifier, load_policy, verify
from upgrade_permissions import reconcile
from verify_legacy_definer_dependencies_postgres import settings

from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, AgentMode, DataScope, RoleCode
from sales_backend.job_context import JobLease, current_job_lease
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.jobs import JobRepository
from sales_backend.repositories.model_calls import ModelCallRepository
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.handler import AgentRunHandler
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.model_calls import DatabaseModelObserver

ROOT = Path(__file__).resolve().parents[3]
KINDS = (
    ("sales_competency.review", "sales_competency_review", "competency_review"),
    ("battle_map.review", "customer", "battle_map_review"),
    ("customer.risk.review", "customer_risk_assessment", "personal_risks"),
    ("business.advice", "business_advice", "customer_advice"),
    ("opportunity.change.review", "business_change", "opportunity_change"),
    ("ai.connectivity", "ai_connectivity", "connectivity_test"),
    ("visit.import", "visit_import", "visit_import"),
)


async def rejection(call, exceptions=(asyncpg.InsufficientPrivilegeError,)):
    try:
        await call()
    except exceptions:
        return
    raise AssertionError("Expected an ACL/RLS rejection")


async def exercise(root, host, _pg_bin):
    config = {"host": host, "user": getpass.getuser(), "port": 5432}
    admin = await asyncpg.connect(database="postgres", **config)
    await admin.execute("CREATE DATABASE worker_ai_fixture TEMPLATE template0")
    await admin.close()
    conn = await asyncpg.connect(database="worker_ai_fixture", **config)
    pools = []
    try:
        baseline = harness.fixture.archive_baseline(root / "historical")
        await harness.fixture.migrate.migrate(conn, baseline)
        tenants = await harness.fixture.fixture(conn)
        await harness.fixture.migrate.migrate(conn, ROOT / "database")
        policy = load_policy(ROOT / "tools/installation/runtime_permissions.json")
        roles = {service: "adoption_" + service for service in SERVICES}
        passwords = {service: secrets.token_urlsafe(48) for service in SERVICES}
        original = copy.deepcopy(policy)
        original["services"]["api"]["columns"].pop("agent.conversation")
        original["services"]["worker"]["tables"].pop("agent.conversation")
        original["services"]["worker"]["tables"]["agent.run"] = ["UPDATE"]
        async with conn.transaction():
            for service, role in roles.items():
                literal = await conn.fetchval("SELECT quote_literal($1::text)", passwords[service])
                await conn.execute(
                    f"CREATE ROLE {identifier(role)} LOGIN NOSUPERUSER NOBYPASSRLS "
                    f"NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal}"
                )
                await conn.execute(f"GRANT CONNECT ON DATABASE worker_ai_fixture TO {identifier(role)}")
                for group in policy["services"][service]["memberships"]:
                    await conn.execute(f"GRANT {identifier(group)} TO {identifier(role)} WITH INHERIT FALSE, SET FALSE")
            await conn.execute("REVOKE CREATE ON DATABASE worker_ai_fixture FROM PUBLIC")
            await conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            await reconcile(conn, roles, original)
        for service in ("api", "worker"):
            db = Database(
                settings(f"postgresql://{roles[service]}:{passwords[service]}@/worker_ai_fixture?host={host}&port=5432")
            )
            await db.connect()
            pools.append(db)
        api, worker = pools
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
        failed = DatabaseModelObserver(worker, actors[0], "competency_review")
        await rejection(lambda: failed.start("/v1/chat/completions", "synthetic", {}, 1))
        assert await conn.fetchval("SELECT count(*) FROM agent.model_invocation") == 0
        async with api.transaction(actors[0]) as runtime:
            repo = AssistantRepository()
            baseline_conversation = await repo.create_conversation(
                runtime, actors[0], mode=AgentMode.CHATBI, customer_id=None
            )

            async def old_api_enqueue():
                async with runtime.transaction():
                    await repo.enqueue_message(
                        runtime,
                        actors[0],
                        conversation_id=baseline_conversation["id"],
                        text="must fail without row lock privilege",
                        client_message_id=None,
                        input_source="text",
                    )

            await rejection(old_api_enqueue)
        async with conn.transaction():
            await reconcile(conn, roles, policy)
        await verify(conn, roles, policy)

        async def claim(kind, aggregate_type, actor):
            aggregate_id = uuid4()
            await conn.execute(
                "INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload) "
                "VALUES($1::uuid,$2,$3,$4,$5::jsonb)",
                actor.workspace_id,
                kind,
                aggregate_type,
                aggregate_id,
                actor.model_dump_json(),
            )
            async with worker.connection() as runtime:
                job = await JobRepository().claim(
                    runtime, worker_id="synthetic", lock_seconds=120, job_types=(kind,), exclude_types=False
                )
            assert job is not None and job.workspace_id == actor.workspace_id
            return job

        async def observe(actor, operation, *, run_id=None, endpoint="/v1/chat/completions", sent=True):
            operation_id = str(uuid4())
            audit = InferenceAudit(
                worker, actor, operation_id, mode=operation, run_id=run_id, facts={}, config={"synthetic": True}
            )
            await audit.start()
            observer = DatabaseModelObserver(worker, actor, operation, operation_id=operation_id, run_id=run_id)
            invocation = await observer.start(endpoint, "synthetic-no-network", {"synthetic": True}, 1)
            if sent:
                await observer.prepare_dispatch(invocation)
            response = httpx.Response(200, json={"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
            await observer.finish(
                invocation,
                response if sent else None,
                None if sent else RuntimeError("synthetic-before-dispatch"),
                network_sent=sent,
            )
            await audit.finish("accepted" if sent else "failed", trace={"synthetic": True})
            row = await conn.fetchrow(
                "SELECT i.status,i.input_tokens,i.output_tokens,a.state,o.status AS audit_status "
                "FROM agent.model_invocation i JOIN control.call_attempt a ON a.invocation_id=i.id "
                "JOIN agent.inference_operation o ON o.id=i.operation_id WHERE i.id=$1::uuid",
                invocation,
            )
            assert row["status"] == ("succeeded" if sent else "failed")
            assert row["state"] == ("settled" if sent else "released")
            assert row["audit_status"] == ("accepted" if sent else "failed")
            if sent:
                assert row["input_tokens"] == 7 and row["output_tokens"] == 3
            return invocation, observer

        observed = []
        for kind, aggregate_type, mode in KINDS:
            job = await claim(kind, aggregate_type, actors[0])
            token = current_job_lease.set(JobLease(job.id, job.lease_token))
            try:
                observed.append((await observe(actors[0], mode))[0])
            finally:
                current_job_lease.reset(token)
                await conn.execute("UPDATE ops.job SET status='succeeded' WHERE id=$1::uuid", job.id)
        # Explicitly cover ASR, platform transport, and the never-dispatched path.
        for endpoint, sent in (
            ("/v1/audio/transcriptions", True),
            ("agent-chat-messages", True),
            ("/v1/t2a_v2", False),
        ):
            observed.append((await observe(actors[0], "synthetic.transport", endpoint=endpoint, sent=sent))[0])

        # Add a real, independently authorized peer in the same company, plus
        # an FDE membership whose assigned project must remain tenant scoped.
        peer_id = uuid4()
        await conn.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
            "VALUES($1,$2,'PEER','Synthetic peer','PEER')",
            peer_id,
            tenants[0]["workspace"],
        )
        for role in ("fde",):
            await conn.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                "VALUES($1,$2,$3,'self',$4)",
                tenants[0]["workspace"],
                peer_id,
                role,
                tenants[0]["team"],
            )
        await conn.execute(
            "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
            "VALUES($1,$2,$3,'fde')",
            tenants[0]["workspace"],
            peer_id,
            tenants[0]["team"],
        )
        peer = actors[0].model_copy(update={"user_id": str(peer_id), "role": RoleCode.FDE})
        async with conn.transaction():
            await harness.fixture.context(conn, tenants[0], "administrator")
            await conn.execute(
                "INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,"
                "source_code,assigned_by_user_ref_id) VALUES($1,$2,$3,'fde','manual',$4)",
                tenants[0]["workspace"],
                tenants[0]["opportunity"],
                peer_id,
                tenants[0]["sales"],
            )
        for tenant in tenants:
            await conn.execute(
                "UPDATE activity.visit SET opportunity_id=$2,next_action='核对合成计划',follow_up_task_mode='legacy' "
                "WHERE workspace_id=$1",
                tenant["workspace"],
                tenant["opportunity"],
            )

        # API owns conversation/run creation. The worker only loads and completes
        # accepted runs, including persisted messages and review artifacts.
        repository = AssistantRepository()
        created = []
        for actor in [*actors, peer]:
            async with api.transaction(actor) as runtime:
                conversation = await repository.create_conversation(
                    runtime, actor, mode=AgentMode.CHATBI, customer_id=None
                )
                run_id = await repository.enqueue_message(
                    runtime,
                    actor,
                    conversation_id=conversation["id"],
                    text="合成权限回归",
                    client_message_id=None,
                    input_source="text",
                )
            created.append((conversation["id"], run_id))
        # The single updated_at column allows PostgreSQL's row lock, not
        # arbitrary mutation of conversation context/status.
        async with api.transaction(actors[0]) as runtime:
            for column in ("context", "status"):

                async def denied_column(column=column):
                    async with runtime.transaction():
                        await runtime.execute(
                            f"UPDATE agent.conversation SET {column}={column} WHERE id=$1::uuid", created[0][0]
                        )

                await rejection(denied_column)
            assert (
                await runtime.execute(
                    "UPDATE agent.conversation SET updated_at=updated_at WHERE id=$1::uuid", created[1][0]
                )
                == "UPDATE 0"
            )
        async with worker.transaction(actors[0], readonly=True) as runtime:
            for other in created[1:]:
                assert (
                    await runtime.fetchval("SELECT count(*) FROM agent.conversation WHERE id=$1::uuid", other[0]) == 0
                )
                assert await runtime.fetchval("SELECT count(*) FROM agent.run WHERE id=$1::uuid", other[1]) == 0
                assert (
                    await runtime.fetchval(
                        "SELECT count(*) FROM agent.message WHERE conversation_id=$1::uuid", other[0]
                    )
                    == 0
                )
            assert (
                await runtime.fetchval("SELECT count(*) FROM agent.conversation WHERE id=$1::uuid", created[1][0]) == 0
            )
            assert await runtime.fetchval("SELECT count(*) FROM agent.run WHERE id=$1::uuid", created[1][1]) == 0
        async with worker.connection() as runtime:
            assert await runtime.fetchval("SELECT count(*) FROM agent.conversation") == 0
            assert await runtime.fetchval("SELECT count(*) FROM agent.run") == 0
        async with worker.transaction(actors[0]) as runtime:

            async def foreign_insert():
                async with runtime.transaction():
                    await ModelCallRepository().start(
                        runtime,
                        actors[1],
                        operation="synthetic.cross_tenant",
                        operation_id=str(uuid4()),
                        run_id=created[1][1],
                        endpoint="/v1/chat/completions",
                        model="synthetic",
                        metadata={},
                        attempt=1,
                        request_id=None,
                        provider="senseaudio",
                    )

            await rejection(foreign_insert)
        run = await AgentRunHandler(worker, worker.settings)._load_and_start(created[0][1], actors[0])
        facts = await AgentFactsLoader(worker).load(run)
        invocation, observer = await observe(actors[0], "chatbi", run_id=run.run_id)
        await AgentRunStore(worker, worker.settings).persist_result(run, {"summary": "合成已完成"}, facts)
        assert await conn.fetchval("SELECT status FROM agent.run WHERE id=$1::uuid", run.run_id) == "succeeded"
        assert await conn.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run.run_id) == 1
        async with api.transaction(actors[0]) as runtime:
            draft_conversation = await repository.create_conversation(
                runtime, actors[0], mode=AgentMode.OPPORTUNITY_DRAFT, customer_id=str(tenants[0]["customer"])
            )
            draft_id = await repository.enqueue_message(
                runtime,
                actors[0],
                conversation_id=draft_conversation["id"],
                text="合成商机候选权限回归",
                client_message_id=None,
                input_source="text",
            )
        draft = await AgentRunHandler(worker, worker.settings)._load_and_start(draft_id, actors[0])
        draft_facts = await AgentFactsLoader(worker).load(draft)
        await AgentRunStore(worker, worker.settings).persist_result(draft, {"summary": "合成候选"}, draft_facts)
        assert await conn.fetchval("SELECT status FROM agent.run WHERE id=$1::uuid", draft_id) == "waiting_human"
        assert await conn.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", draft_id) == 1

        async def queued(actor, mode, customer=None):
            async with api.transaction(actor) as runtime:
                conversation = await repository.create_conversation(runtime, actor, mode=mode, customer_id=customer)
                queued_id = await repository.enqueue_message(
                    runtime,
                    actor,
                    conversation_id=conversation["id"],
                    text="合成受限角色验证",
                    client_message_id=None,
                    input_source="text",
                )
            return await AgentRunHandler(worker, worker.settings)._load_and_start(queued_id, actor)

        # Real source snapshots + FOR SHARE locks + INSERT/ON CONFLICT UPDATE.
        store = AgentRunStore(worker, worker.settings)
        risk_id = None
        for attempt in range(2):
            risk_run = await queued(actors[0], AgentMode.PERSONAL_RISKS)
            risk_facts = await AgentFactsLoader(worker).load(risk_run)
            assert len(risk_facts["visits"]) == 1
            source = risk_facts["visits"][0]
            risk_result = {
                "risks": [
                    {
                        "source_visit_id": source["source_visit_id"],
                        "risk_type": "schedule_risk",
                        "description": "合成计划回归",
                        "title": "Synthetic risk",
                        "severity": "medium",
                        "suggested_action": "核对计划",
                    }
                ]
            }
            await store.persist_result(risk_run, risk_result, risk_facts)
            row = await conn.fetchrow(
                "SELECT id,version_no FROM insight.risk WHERE workspace_id=$1 AND source_visit_id=$2::uuid",
                tenants[0]["workspace"],
                source["source_visit_id"],
            )
            assert row and row["version_no"] == attempt + 1
            if risk_id:
                assert row["id"] == risk_id
            risk_id = row["id"]
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM insight.risk_event WHERE risk_id=$1 AND event_type='reanalyzed'", risk_id
            )
            == 1
        )
        today_run = await queued(actors[0], AgentMode.TODAY_TASKS)
        today_facts = await AgentFactsLoader(worker).load(today_run)
        assert len(today_facts["follow_up_candidates"]) == 1
        await store.persist_result(today_run, {"ordered_items": []}, today_facts)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM workflow.task WHERE workspace_id=$1 AND source_code='today_task_agent'",
                tenants[0]["workspace"],
            )
            == 1
        )

        # Artifact SELECT is required for quality jobs queued before date_anchor
        # became explicit. Exercise the actual owned, durable source lookup.
        visit_run = await queued(actors[0], AgentMode.VISIT_ENTRY, str(tenants[0]["customer"]))
        visit_facts = await AgentFactsLoader(worker).load(visit_run)
        anchor = visit_facts["server_fields"]["created_date"]
        await store.persist_result(visit_run, {"fields": {"created_date": anchor}}, visit_facts)
        from sales_backend.services.visit_flow import structure_source

        async with worker.transaction(actors[0], readonly=True) as runtime:
            source_row = await structure_source(runtime, actors[0], visit_run.run_id)
            assert source_row["structure_created_date"] == anchor
        for other in (actors[1], peer):
            async with worker.transaction(other, readonly=True) as runtime:
                assert (
                    await runtime.fetchval(
                        "SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", visit_run.run_id
                    )
                    == 0
                )
                assert await structure_source(runtime, other, visit_run.run_id) is None

        # Full FDE fact loading exercises guarded history/demo/ranking/project
        # functions; raw cross-tenant project access is still invisible.
        fde = peer.model_copy(update={"role": RoleCode.FDE})
        fde_run = await queued(fde, AgentMode.CHATBI)
        fde_facts = await AgentFactsLoader(worker).load(fde_run)
        assert {item["id"] for item in fde_facts["opportunities"]} == {str(tenants[0]["opportunity"])}
        await store.persist_result(fde_run, {"summary": "合成 FDE 汇总"}, fde_facts)
        async with worker.transaction(fde, readonly=True) as runtime:
            assert not await runtime.fetchval(
                "SELECT security.authorization_opportunity_direct('agent.chatbi',$1)", tenants[1]["opportunity"]
            )

        # The historical fixture explicitly denies opportunity.update. Preserve
        # that policy: a read feature must not circumvent its UPDATE-policy gate.
        async with worker.transaction(actors[0]) as runtime:
            await runtime.execute("SELECT set_config('app.authorized_feature','agent.personal_risks',true)")
            assert (
                await runtime.fetchval(
                    "SELECT id FROM crm.opportunity WHERE id=$1 FOR SHARE", tenants[0]["opportunity"]
                )
                is None
            )
        await conn.execute(
            "DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 "
            "AND permission_code='opportunity.update'",
            tenants[0]["workspace"],
            tenants[0]["sales"],
        )

        # Row-lock grants are column scoped: substantive business fields remain
        # immutable for this role, and foreign company locks cannot see a row.
        for table, column, key in (
            ("crm.customer", "name", "customer"),
            ("crm.opportunity", "amount", "opportunity"),
            ("activity.visit", "follow_up_record", None),
        ):
            own = (
                tenants[0][key]
                if key
                else await conn.fetchval("SELECT id FROM activity.visit WHERE workspace_id=$1", tenants[0]["workspace"])
            )
            foreign = (
                tenants[1][key]
                if key
                else await conn.fetchval("SELECT id FROM activity.visit WHERE workspace_id=$1", tenants[1]["workspace"])
            )
            assert not await conn.fetchval("SELECT has_table_privilege($1,$2,'UPDATE')", roles["worker"], table)
            async with worker.transaction(actors[0]) as runtime:
                await runtime.execute("SELECT set_config('app.authorized_feature','agent.personal_risks',true)")
                locked = await runtime.fetchval(f"SELECT id FROM {table} WHERE id=$1 FOR SHARE", own)
                assert locked == own, (table, "owned row not lockable")
                assert await runtime.fetchval(f"SELECT id FROM {table} WHERE id=$1 FOR SHARE", foreign) is None

                async def denied_business_update(table=table, column=column, own=own):
                    async with runtime.transaction():
                        await runtime.execute(f"UPDATE {table} SET {column}={column} WHERE id=$1", own)

                await rejection(denied_business_update)

        # A changed source after load must reject the whole result transaction.
        changed_run = await queued(actors[0], AgentMode.PERSONAL_RISKS)
        changed_facts = await AgentFactsLoader(worker).load(changed_run)
        await conn.execute(
            "UPDATE crm.customer SET name='Changed synthetic source' WHERE id=$1", tenants[0]["customer"]
        )
        from sales_backend.services.agent_run.personal_risk_sources import PersonalRiskSourceChanged

        await rejection(
            lambda: store.persist_result(changed_run, risk_result, changed_facts), (PersonalRiskSourceChanged,)
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", changed_run.run_id)
            == 0
        )
        assert await conn.fetchval("SELECT version_no FROM insight.risk WHERE id=$1", risk_id) == 2

        # Permission revocation after facts load must prevent persistence.
        stale_run = await queued(actors[0], AgentMode.CHATBI)
        stale_facts = await AgentFactsLoader(worker).load(stale_run)
        await conn.execute(
            "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) "
            "VALUES($1,$2,'agent.chatbi','deny')",
            tenants[0]["workspace"],
            tenants[0]["sales"],
        )
        await rejection(
            lambda: store.persist_result(stale_run, {"summary": "must not persist"}, stale_facts), (PermissionError,)
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", stale_run.run_id)
            == 0
        )
        await conn.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", tenants[1]["workspace"])
        await rejection(lambda: observe(actors[1], "synthetic.closed"), (PermissionError,))

        async with worker.connection() as runtime:
            await rejection(
                lambda: runtime.fetchval(
                    "SELECT security.finish_model_call($1::uuid,$2::uuid,false,'{}'::jsonb)", invocation, str(uuid4())
                )
            )
        # The repair grants no conversation/run creation or control-plane access.
        for table, privilege in (
            ("agent.conversation", "INSERT"),
            ("agent.run", "INSERT"),
            ("control.call_attempt", "SELECT"),
            ("agent.model_invocation", "SELECT"),
        ):
            assert not await conn.fetchval("SELECT has_table_privilege($1,$2,$3)", roles["worker"], table, privilege)
        await verify(conn, roles, policy)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "observer_admission_dispatch_settlement": len(observed) + 1,
                    "prior_worker_and_api_acl_failures_reproduced": True,
                    "inference_audit_persisted": True,
                    "real_agent_load_facts_message_complete": True,
                    "real_draft_artifact_persisted": True,
                    "personal_risk_upsert_and_today_task_locks": True,
                    "owned_legacy_quality_artifact_read": True,
                    "fde_fact_loading": True,
                    "minimal_column_updates_and_revocation": True,
                    "source_change_rejected_atomically": True,
                    "cross_tenant_rejected": True,
                    "four_runtime_exact_acl_verified": True,
                    "external_provider_calls": 0,
                },
                indent=2,
            )
        )
    finally:
        for pool in pools:
            await pool.close()
        await conn.close()


if __name__ == "__main__":
    harness.exercise = exercise
    harness.main()
