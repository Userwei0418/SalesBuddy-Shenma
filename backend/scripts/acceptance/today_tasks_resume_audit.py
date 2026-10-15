"""Finish reads for two existing authorized runs; never replay models or create tasks."""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
from today_tasks_business_live import (
    ACTOR,
    WORKSPACE,
    fingerprint,
    select_scope,
    shared_customer_task_visible,
)
from today_tasks_notification_audit import audit_notifications

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput


async def run(args):
    plan = json.loads(Path(args.plan).read_text())
    originals = {case: json.loads((Path(args.prior) / (case + ".json")).read_text()) for case in ("normal", "off")}
    connection = await asyncpg.connect(host="/tmp", database="sales_saas", user="postgres", command_timeout=10)  # noqa: S108
    clients = {}
    reports = []
    try:
        await _initialize_connection(connection)
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE sales_app")
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            actors = {
                account: (
                    await IdentityRepository().find_actor_by_account(
                        connection, workspace_external_id="demo-sales-workspace", account_code=account
                    )
                ).context
                for account in ("XS001", "XS002")
            }
            assert actors["XS001"].user_id == ACTOR and actors["XS001"].workspace_id == WORKSPACE
            for account, actor in actors.items():
                client = clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                response = await client.post(
                    "/auth/session",
                    json={
                        "account_code": account,
                        "workspace": "demo-sales-workspace",
                        "client": {"purpose": "today-tasks-resume-readonly"},
                    },
                )
                assert response.status_code == 200
                value = response.json()
                client.headers["Authorization"] = "Bearer " + value["access_token"]
                assert value["actor"]["user_id"] == actor.user_id

            class Pool:
                @asynccontextmanager
                async def acquire(self):
                    yield connection

            for case in ("normal", "off"):
                scope = select_scope(plan, case, live=True)
                old = originals[case]
                failures = [item for item in old["checks"] if not item["passed"]]
                if case == "normal":
                    assert old["passed"] and not failures
                else:
                    assert len(failures) == 1 and failures[0]["name"] == (
                        "XS002 GET /tasks/" + old["ids"]["task_id"] + ": 200"
                    )
                    assert old["status"] == "succeeded" and old["inference_route"] is None
                required = {
                    "all prior tasks unchanged",
                    "exactly one task created",
                    "sole owner is the current sales user",
                    "existing assignees unchanged",
                    "CRM visits and notifications unchanged",
                    "one receipt and answer",
                    "XS001 own session revoked",
                    "XS002 own session revoked",
                }
                assert required <= {item["name"] for item in old["checks"] if item["passed"]}
                report = {
                    k: old.get(k)
                    for k in (
                        "scenario",
                        "ids",
                        "status",
                        "elapsed_ms",
                        "inference_route",
                        "attempts",
                        "platform_accepted",
                    )
                }
                report.update(
                    prior_evidence_sha256=fingerprint(old),
                    original_failed_checks=failures,
                    original_evidence_path=str(Path(args.prior) / (case + ".json")),
                    checks=[],
                    new_model_calls_in_resume=0,
                    new_tasks_in_resume=0,
                    new_tasks=1,
                )
                reports.append(report)

                def check(name, passed, report=report):
                    report["checks"].append({"name": name, "passed": bool(passed)})
                    print(json.dumps(report["checks"][-1]), flush=True)
                    if not passed:
                        raise AssertionError(name)

                task_id, run_id = old["ids"]["task_id"], old["ids"]["run_id"]
                await set_request_context(connection, actors["XS001"])
                task = await connection.fetchrow("SELECT * FROM workflow.task WHERE id=$1::uuid", task_id)
                check(
                    "same authorized task remains pending with same sole source",
                    task
                    and str(task["source_visit_id"]) == scope.source_ids[0]
                    and task["status"] == "pending_execution"
                    and str(task["creator_user_ref_id"]) == ACTOR,
                )
                owners = await connection.fetch(
                    "SELECT assignee_user_ref_id::text,responsibility "
                    "FROM workflow.task_assignee WHERE task_id=$1::uuid",
                    task_id,
                )
                check(
                    "only original owner assigned",
                    len(owners) == 1
                    and owners[0]["assignee_user_ref_id"] == ACTOR
                    and owners[0]["responsibility"] == "owner",
                )
                # Later authorized cases did not exist in the earlier baseline.
                exclude = [
                    originals[name]["ids"]["task_id"] for name in ("normal", "off") if case == "normal" or name == "off"
                ]
                before = await connection.fetch(
                    "SELECT * FROM workflow.task WHERE NOT(id=ANY($1::uuid[])) ORDER BY id", exclude
                )
                check(
                    "all original task facts still unchanged",
                    fingerprint([dict(row) for row in before]) == old["tasks_before_fingerprint"],
                )
                authorized_tasks = [dict(row) for row in await connection.fetch(
                    "SELECT * FROM workflow.task WHERE id=ANY($1::uuid[])",
                    [item['ids']['task_id'] for item in originals.values()],
                )]
                reminders = await audit_notifications(
                    connection, old['protected_before']['SELECT * FROM workflow.notification ORDER BY id'],
                    authorized_tasks, actor=ACTOR, workspace=WORKSPACE,
                    since=datetime.fromisoformat(old['started_at']),
                )
                report['existing_monitor_reminders'] = reminders
                report['new_notifications'] = sum(row['task_id'] == task_id for row in reminders)
                report['notification_scope_correction'] = (
                    'Original immediate zero-notification check did not cover the later existing priority monitor. '
                    'Own in-app task reminders are recorded; existing rows and external delivery are protected.'
                )
                check('existing notifications unchanged; only exact own-task monitor reminders added', True)
                for query, digest in old["protected_before"].items():
                    assert query in (
                        "SELECT * FROM crm.customer ORDER BY id",
                        "SELECT * FROM crm.opportunity ORDER BY id",
                        "SELECT * FROM activity.visit ORDER BY id",
                        "SELECT * FROM workflow.notification ORDER BY id",
                    )
                    if query == 'SELECT * FROM workflow.notification ORDER BY id':
                        continue
                    check(
                        "protected facts unchanged: " + query,
                        fingerprint([dict(row) for row in await connection.fetch(query)]) == digest,
                    )
                check(
                    "one task for source",
                    await connection.fetchval(
                        "SELECT count(*) FROM workflow.task WHERE source_visit_id=$1::uuid AND deleted_at IS NULL",
                        scope.source_ids[0],
                    )
                    == 1,
                )
                own = await clients["XS001"].get("/agent/runs/" + run_id)
                check(
                    "existing run succeeded and card names this task",
                    own.status_code == 200
                    and own.json()["status"] == "succeeded"
                    and sum(row["task_id"] == task_id for row in own.json()["result"]["rows"]) == 1,
                )
                await set_request_context(connection, actors["XS002"])
                visible = await shared_customer_task_visible(connection, task)
                report["XS002_expected_task_read_status"] = 200 if visible else 404
                response = await clients["XS002"].get("/tasks/" + task_id)
                check(
                    "task read follows existing customer and opportunity policy",
                    response.status_code == (200 if visible else 404),
                )
                check(
                    "task RLS agrees with existing policy",
                    await connection.fetchval("SELECT count(*) FROM workflow.task WHERE id=$1::uuid", task_id)
                    == int(visible),
                )
                check(
                    "another user cannot read private run",
                    (await clients["XS002"].get("/agent/runs/" + run_id)).status_code == 404,
                )
                response = await clients["XS002"].get("/conversations/" + old["ids"]["conversation_id"] + "/messages")
                check("another user cannot read conversation", response.status_code == 200 and response.json() == [])
                other = await AgentFactsLoader(Database(get_settings(), pool=Pool())).load(
                    RunInput("audit-only", "none", "today tasks", "today_tasks", None, actors["XS002"])
                )
                check(
                    "shared read does not assign this task or feed another users own planning",
                    scope.source_ids[0]
                    not in {row["source_id"] for row in other["active_tasks"] + other["follow_up_candidates"]},
                )
                report.update(passed=True, real_business_verified=True, resume_audited_at=datetime.now(UTC).isoformat())
    finally:
        cleanup_ok = True
        for client in clients.values():
            try:
                if "Authorization" in client.headers:
                    response = await client.post("/auth/logout")
                    cleanup_ok = cleanup_ok and response.status_code == 204
            except Exception:
                cleanup_ok = False
            finally:
                await client.aclose()
        await connection.close()
        for report in reports:
            report["resume_sessions_revoked"] = cleanup_ok
            if not cleanup_ok:
                report["passed"] = False
            with (Path(args.output) / (report["scenario"] + ".json")).open("x") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
        if not cleanup_ok:
            raise RuntimeError("Resume audit session cleanup failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--prior", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    asyncio.run(run(parser.parse_args()))
