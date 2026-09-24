"""V098 contract against disposable PostgreSQL databases and non-bypass roles.

Uses only PGHOST/PGPORT/PGUSER and the postgres maintenance database. It never
reads application credentials or an existing business database. All fixtures,
databases and roles are removed in finally.
"""

import asyncio
import importlib.util
import io
import json
import os
import sys
from contextlib import asynccontextmanager, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
from verify_fde_postgres import context, fixture_before_upgrade, forbidden
from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database/scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))
from migrate import migrate
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository

spec = importlib.util.spec_from_file_location("customer_risk_cli", ROOT / "backend/scripts/customer_risk_assessments.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class TestDatabase:
    def __init__(self, connection):
        self.conn = connection

    @asynccontextmanager
    async def connection(self):
        yield self.conn

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        async with self.conn.transaction(readonly=readonly):
            await set_request_context(self.conn, actor)
            yield self.conn


async def rejects(connection, error_type, query, *args):
    transaction = connection.transaction()
    await transaction.start()
    try:
        try:
            await connection.execute(query, *args)
        except error_type:
            return
        raise AssertionError("Expected rejected database mutation")
    finally:
        await transaction.rollback()


async def runtime_grants(connection, role):
    await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    for schema in ("common", "security", "platform", "crm", "activity", "insight", "ops", "agent", "workflow", "config"):
        await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
        await connection.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
    await connection.execute(f'GRANT INSERT,UPDATE ON ops.job TO "{role}"')
    await connection.execute(f'GRANT INSERT ON insight.risk,ops.job_effect,agent.inference_operation TO "{role}"')


async def main():
    config = {"host": os.environ.get("PGHOST", "/tmp"), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:12]
    database = "salegent_verify_customer_risk_" + suffix
    runtime, late = ["salegent_risk_" + name + "_" + suffix for name in ("runtime", "late")]
    connection = None
    roles = []
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0 ENCODING \'UTF8\'')
        connection = await asyncpg.connect(database=database, **config)
        for kind in ("json", "jsonb"):
            await connection.set_type_codec(kind, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        await migrate_through(connection, 68)
        workspace, team, owner, customer, visit, _ = await fixture_before_upgrade(connection)
        await migrate_through(connection, 97)
        fde, peer, operator, administrator, project, other_customer, other_workspace = [uuid4() for _ in range(7)]
        for user, code, role, scope in ((fde, "FDE", "fde", "self"), (peer, "PEER", "sales", "self"),
                                       (operator, "OPS", "operations", "workspace"),
                                       (administrator, "ADMIN", "administrator", "workspace")):
            await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$4,$4)", user, workspace, str(user), code)
            await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", workspace, user, role, scope, team)
            await connection.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)", workspace, user, team, role)
        await connection.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,created_by_user_ref_id) VALUES($1,$2,'Other customer','other customer',$3,$4,$3)", other_customer, workspace, peer, team)
        await connection.execute("INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", workspace, customer, peer)
        await connection.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,'Scoped project',$4,$5)", project, workspace, customer, owner, team)
        await connection.execute("INSERT INTO crm.opportunity_participant(opportunity_id,workspace_id,user_ref_id,participant_role,assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')", project, workspace, fde, owner)
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Other workspace')", other_workspace, str(other_workspace))
        await runtime_grants(connection, runtime)
        roles.append(runtime)
        await migrate(connection)
        await connection.execute("SET row_security=on")
        assert await connection.fetchval("SELECT data_type FROM information_schema.columns WHERE table_schema='insight' AND table_name='customer_risk_assessment' AND column_name='fact_scope_version'") == "smallint"
        assert not await connection.fetchval("SELECT has_table_privilege($1,'insight.customer_risk_assessment','DELETE')", runtime)
        await connection.execute(f'SET ROLE "{runtime}"')
        assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        checks.append("upgrade_grants_nonsuper_nonbypass_runtime_without_delete")

        await context(connection, workspace, fde, "fde", team)
        assert await connection.fetchval("SELECT security.profile_customer_owner($1)", customer) == owner
        assert await connection.fetchval("SELECT security.customer_risk_assessment_owner($1)", customer) == owner
        assert await connection.fetchval("SELECT security.customer_risk_assessment_owner($1)", other_customer) is None
        actual_owner = await IdentityRepository().find_actor_by_id(connection, workspace_id=str(workspace), user_id=str(owner), role="sales")
        assert actual_owner and actual_owner.context.user_id == str(owner)
        identity = actual_owner.context.model_dump(mode="json")
        assert set(identity["team_ids"]) == {str(team)}
        checks.append("fde_can_resolve_only_visible_claimed_owner_and_its_actual_identity")

        async def enqueue(*, actor=owner, initiator=fde, target=customer, key=None, job_type="customer.risk.review"):
            assessment_id, job_id = uuid4(), uuid4()
            await connection.execute("INSERT INTO ops.job(id,workspace_id,job_type,aggregate_type,aggregate_id,payload) VALUES($1,$2,$3,'customer_risk_assessment',$4,$5)", job_id, workspace, job_type, assessment_id, identity)
            await connection.execute("INSERT INTO insight.customer_risk_assessment(id,workspace_id,customer_id,actor_user_ref_id,actor_role_code,initiated_by_user_ref_id,job_id,trigger_type,trigger_id,idempotency_key,identity_snapshot) VALUES($1,$2,$3,$4,'sales',$5,$6,'visit.archived',$7,$8,$9)", assessment_id, workspace, target, actor, initiator, job_id, str(visit), key or str(assessment_id), {**identity, "user_id": str(actor)})
            return assessment_id, job_id

        assessment, job = await enqueue(key="one-archive")
        assert await connection.fetchval("SELECT status FROM insight.customer_risk_assessment WHERE id=$1", assessment) == "queued"
        await forbidden(connection, "UPDATE insight.customer_risk_assessment SET status='running' WHERE id=$1", assessment)
        await forbidden(connection, "INSERT INTO insight.risk(workspace_id,customer_id,risk_type_code,title,severity_code,owner_user_ref_id) VALUES($1,$2,'budget','Forbidden FDE write','low',$3)", workspace, customer, owner)
        for overrides in ({"actor": peer}, {"target": other_customer}, {"initiator": owner}, {"key": "one-archive"}, {"job_type": "battle_map.review"}):
            transaction = connection.transaction()
            await transaction.start()
            try:
                try:
                    await enqueue(**overrides)
                except (asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError, asyncpg.UniqueViolationError):
                    pass
                else:
                    raise AssertionError(f"Invalid request accepted: {overrides}")
            finally:
                await transaction.rollback()
        checks.append("fde_can_request_but_cannot_execute_or_write_risks_and_duplicate_or_forged_requests_fail")

        await context(connection, workspace, owner, "sales", team)
        assert await connection.execute("UPDATE insight.customer_risk_assessment SET status='running',started_at=clock_timestamp() WHERE id=$1", assessment) == "UPDATE 1"
        await rejects(connection, asyncpg.CheckViolationError, "UPDATE insight.customer_risk_assessment SET initiated_by_user_ref_id=$2 WHERE id=$1", assessment, owner)
        await rejects(connection, asyncpg.CheckViolationError, "UPDATE insight.customer_risk_assessment SET identity_snapshot='{}' WHERE id=$1", assessment)
        await connection.execute("UPDATE ops.job SET status='failed',attempts=1,last_error_code='TimeoutError' WHERE id=$1", job)
        row = await connection.fetchrow("SELECT status,error_code,completed_at FROM insight.customer_risk_assessment WHERE id=$1", assessment)
        assert dict(row) == {"status": "queued", "error_code": "TimeoutError", "completed_at": None}
        await connection.execute("UPDATE insight.customer_risk_assessment SET status='running' WHERE id=$1", assessment)
        await connection.execute("UPDATE ops.job SET status='running',attempts=max_attempts,locked_until=clock_timestamp()-interval '1 second',lease_token=gen_random_uuid() WHERE id=$1", job)
        await connection.fetch("SELECT * FROM ops.claim_job('risk-fixture',30,ARRAY['customer.risk.review'],false)")
        row = await connection.fetchrow("SELECT status,error_code,completed_at FROM insight.customer_risk_assessment WHERE id=$1", assessment)
        assert row["status"] == "failed" and row["error_code"] == "LEASE_ATTEMPTS_EXHAUSTED" and row["completed_at"]
        checks.append("owner_executes_retry_requeues_and_real_typed_claim_lease_exhaustion_closes_receipt")

        await context(connection, workspace, fde, "fde", team)
        invalid, invalid_job = await enqueue()
        lease = uuid4()
        await connection.execute("UPDATE ops.job SET status='running',lease_token=$2,locked_until=clock_timestamp()+interval '1 minute' WHERE id=$1", invalid_job, lease)
        await context(connection, "", "", "")
        assert await connection.fetchval("SELECT ops.reject_invalid_job_actor($1,$2)", invalid_job, lease) == "dead_letter"
        await context(connection, workspace, fde, "fde", team)
        assert await connection.fetchval("SELECT error_code FROM insight.customer_risk_assessment WHERE id=$1", invalid) == "INVALID_JOB_ACTOR"
        rollback_id, rollback_job = await enqueue()
        transaction = connection.transaction()
        await transaction.start()
        await connection.execute("UPDATE ops.job SET status='cancelled' WHERE id=$1", rollback_job)
        assert await connection.fetchval("SELECT status FROM insight.customer_risk_assessment WHERE id=$1", rollback_id) == "failed"
        await transaction.rollback()
        assert await connection.fetchval("SELECT status FROM insight.customer_risk_assessment WHERE id=$1", rollback_id) == "queued"
        await connection.execute("UPDATE ops.job SET status='cancelled' WHERE id=$1", rollback_job)
        checks.append("invalid_actor_closes_under_empty_context_and_queue_receipt_changes_rollback_together")

        success, success_job = await enqueue()
        await context(connection, workspace, owner, "sales", team)
        operation = uuid4()
        await connection.execute("INSERT INTO agent.inference_operation(id,workspace_id,actor_user_ref_id,actor_role_code,capability,job_id) VALUES($1,$2,$3,'sales','customer_risk',$4)", operation, workspace, owner, success_job)
        await connection.execute("UPDATE insight.customer_risk_assessment SET status='succeeded',outcome='no_risk_identified',completed_at=clock_timestamp(),inference_operation_id=$2,coverage=$3,result=$4 WHERE id=$1", success, operation, {"visits": {"included_count": 1, "has_more": False}}, {"risks": [], "private_marker": "not_audit_content"})
        await connection.execute("INSERT INTO ops.job_effect(job_id,workspace_id,lease_token) VALUES($1,$2,$3)", success_job, workspace, uuid4())
        await connection.execute("UPDATE ops.job SET status='dead_letter',last_error_code='SHOULD_NOT_REPLACE_SUCCESS' WHERE id=$1", success_job)
        assert await connection.fetchval("SELECT status FROM insight.customer_risk_assessment WHERE id=$1", success) == "succeeded"
        await context(connection, workspace, peer, "sales", team)
        assert await connection.fetchval("SELECT outcome FROM insight.customer_risk_assessment WHERE id=$1", success) == "no_risk_identified"
        await forbidden(connection, "UPDATE insight.customer_risk_assessment SET status='failed' WHERE id=$1", success)
        await context(connection, other_workspace, owner, "sales", team)
        assert await connection.fetchval("SELECT count(*) FROM insight.customer_risk_assessment") == 0
        await context(connection, workspace, operator, "operations", team)
        now = datetime.now(timezone.utc)
        rows = await connection.fetch("SELECT * FROM security.agent_operation_rows($1,$2)", now-timedelta(days=1), now+timedelta(days=1))
        item = next(row for row in rows if row["operation_id"] == operation)
        assert item["case_id"] == success and item["record"]["customer_id"] == str(customer)
        assert item["record"]["business_status"] == "succeeded" and item["record"]["risk_assessment"]["outcome"] == "no_risk_identified"
        assert "not_audit_content" not in str(item) and "result" not in item["record"]["risk_assessment"]
        await context(connection, workspace, peer, "sales", team)
        assert await connection.fetchval("SELECT count(*) FROM security.agent_operation_rows($1,$2)", now-timedelta(days=1), now+timedelta(days=1)) == 0
        checks.append("successful_receipt_survives_queue_failure_customer_metadata_read_and_management_only_audit_are_scoped")

        # Exercise the real CLI service against native RLS without a subprocess,
        # environment credentials or starting a worker/model client.
        request_id = str(uuid4())
        cli_args = ["--workspace", str(workspace), "--admin-account", "ADMIN", "--customer-id", str(customer),
                    "--request-id", request_id]
        database_adapter = TestDatabase(connection)
        before_jobs = await connection.fetchval("SELECT count(*) FROM ops.job")
        preview = await cli.execute(database_adapter, cli.parse_args(cli_args))
        assert preview["mode"] == "preview" and preview["selected_count"] == 1
        assert preview["assessment_ids"] == [] and preview["customers"][0]["owner_user_ref_id"] == str(owner)
        assert before_jobs == await connection.fetchval("SELECT count(*) FROM ops.job")
        applied = await cli.execute(database_adapter, cli.parse_args(cli_args + ["--apply"]))
        repeated = await cli.execute(database_adapter, cli.parse_args(cli_args + ["--apply"]))
        assert applied["assessment_ids"] == repeated["assessment_ids"] and applied["job_ids"] == repeated["job_ids"]
        assert before_jobs + 1 == await connection.fetchval("SELECT count(*) FROM ops.job")
        cli_receipt = await connection.fetchrow("SELECT initiated_by_user_ref_id,trigger_type FROM insight.customer_risk_assessment WHERE id=$1::uuid", applied["assessment_ids"][0])
        assert cli_receipt["initiated_by_user_ref_id"] == administrator and cli_receipt["trigger_type"] == "maintenance.customer_risk"
        await connection.execute("UPDATE ops.job SET status='dead_letter',last_error_code='CLI_RETRY_FIXTURE' WHERE id=$1::uuid", applied["job_ids"][0])
        replay_failed = await cli.execute(database_adapter, cli.parse_args(cli_args + ["--apply"]))
        assert replay_failed["assessment_ids"] == applied["assessment_ids"]
        new_args = cli.parse_args(cli_args + ["--apply"])
        new_args.request_id = str(uuid4())
        retry = await cli.execute(database_adapter, new_args)
        assert retry["assessment_ids"] != applied["assessment_ids"]
        assert before_jobs + 2 == await connection.fetchval("SELECT count(*) FROM ops.job")
        for bad_args in (["--workspace", str(workspace), "--admin-account", "ADMIN"],
                         cli_args + ["--limit", "101"], cli_args + ["--all-authorized"],
                         cli_args + ["--request-id", "not-a-uuid"]):
            with redirect_stderr(io.StringIO()):
                try:
                    cli.parse_args(bad_args)
                except SystemExit as exc:
                    assert exc.code == 2
                else:
                    raise AssertionError("CLI accepted unsafe arguments")
        checks.append("maintenance_cli_preview_is_readonly_explicit_apply_replays_and_new_request_retries_under_real_administrator")

        await context(connection, workspace, fde, "fde", team)
        transferred, transferred_job = await enqueue()
        await connection.execute("RESET ROLE")
        await connection.execute("UPDATE crm.customer_ownership SET owner_user_ref_id=$2 WHERE customer_id=$1", customer, peer)
        await connection.execute(f'SET ROLE "{runtime}"')
        await context(connection, workspace, owner, "sales", team)
        await forbidden(connection, "UPDATE insight.customer_risk_assessment SET status='succeeded' WHERE id=$1", transferred)
        await connection.execute("UPDATE ops.job SET status='dead_letter',last_error_code='OWNER_CHANGED' WHERE id=$1", transferred_job)
        assert await connection.fetchval("SELECT error_code FROM insight.customer_risk_assessment WHERE id=$1", transferred) == "OWNER_CHANGED"
        await connection.execute("RESET ROLE")
        await connection.execute("UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),ended_by_user_ref_id=$2,end_reason='isolated removal' WHERE opportunity_id=$1 AND user_ref_id=$3", project, owner, fde)
        await connection.execute(f'SET ROLE "{runtime}"')
        await context(connection, workspace, fde, "fde", team)
        assert await connection.fetchval("SELECT security.customer_risk_assessment_owner($1)", customer) is None
        assert await connection.fetchval("SELECT count(*) FROM insight.customer_risk_assessment") == 0
        checks.append("ownership_transfer_blocks_old_executor_but_terminal_sync_survives_and_removed_fde_loses_read")

        await connection.execute("RESET ROLE")
        await runtime_grants(connection, late)
        roles.append(late)
        assert all(step["status"] == "unchanged" for step in await migrate(connection))
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            assert await connection.fetchval("SELECT has_table_privilege($1,'insight.customer_risk_assessment',$2)", late, privilege)
        assert not await connection.fetchval("SELECT has_table_privilege($1,'insight.customer_risk_assessment','DELETE')", late)
        assert not await connection.fetchval("SELECT has_function_privilege($1,'security.reconcile_runtime_grants()','EXECUTE')", late)
        assert not await connection.fetchval("SELECT has_function_privilege($1,'ops.sync_customer_risk_assessment_job()','EXECUTE')", late)
        checks.append("late_runtime_role_reconciliation_is_idempotent_without_private_function_or_delete_grants")
        print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))
    finally:
        if connection:
            await connection.execute("RESET ROLE")
            await connection.close()
        assert database.startswith("salegent_verify_customer_risk_")
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    async def bounded():
        async with asyncio.timeout(120):
            await main()
    asyncio.run(bounded())
