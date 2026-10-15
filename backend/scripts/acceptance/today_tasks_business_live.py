"""Four bounded today-task cases through the real API/queue/Worker.

Preflight performs only RLS SELECTs. Live mode requires an approved plan and the
same operator scope installed in the Worker. Evidence contains hashes and IDs,
never credentials, source prose or model answers. Existing tasks are protected.
"""

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import httpx
from today_tasks_notification_audit import audit_notifications

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.today_scope import TodayTasksScope, load_today_scope

WORKSPACE = "00000000-0000-0000-0000-000000000001"
ACTOR = "02000000-0000-0000-0000-000000000004"
SCENARIOS = ("normal", "off", "blocked", "production")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def shared_customer_task_visible(connection, candidate):
    """Existing task_scope AND task_opportunity_boundary for a non-assignee.

    Customer access alone permits customer-linked task reads, but never bypasses
    a private opportunity. This does not grant assignment or transition rights.
    """
    customer = await connection.fetchval('SELECT security.has_customer_access($1::uuid)', candidate['customer_id'])
    opportunity = candidate.get('opportunity_id')
    return bool(customer) and (opportunity is None or bool(await connection.fetchval(
        'SELECT security.has_opportunity_access($1::uuid)', opportunity)))


def select_scope(plan, scenario, *, live, now=None):
    """Reject scope expansion or stale authorization before any application login."""
    now = now or datetime.now(UTC)
    assert plan["account"] == "XS001" and plan["actor_id"] == ACTOR and plan["workspace_id"] == WORKSPACE
    assert plan["maximum_new_tasks_if_auto_materialized"] == 4
    assert [item["scenario"] for item in plan["cases"]] == list(SCENARIOS)
    if live:
        assert plan["approval_status"] == "approved"
        assert plan["business_rule"] == "auto_own_pending_execution_backend_future_due"
        assert plan["publication_api_live_enable_authorized"] is True
    scopes, seen = {}, set()
    for item in plan["cases"]:
        assert item["expected_candidate_count"] == item["new_task_limit_if_auto_materialized"] == 1
        assert any(marker in item["customer_name"] for marker in ("演示", "验收", "测试", "联调"))
        value = item["scope_file_content"][WORKSPACE][ACTOR]
        assert len(value["sources"]) == 1
        source, digest = next(iter(value["sources"].items()))
        assert str(UUID(source)) == source and source not in seen
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        seen.add(source)
        expires = now + timedelta(minutes=10)
        if live:
            assert value["approval_status"] == "approved" and value.get("enabled", True) is True
            expires = datetime.fromisoformat(value["expires_at"])
            assert expires.tzinfo is not None and expires > now
        scopes[item["scenario"]] = TodayTasksScope(WORKSPACE, ACTOR, expires, ((source, digest),))
    return scopes[scenario]


async def run(args):
    plan = json.loads(Path(args.plan).read_text())
    scope = select_scope(plan, args.scenario, live=not args.preflight_only)
    output = Path(args.output)
    with output.open("x") as stream:
        stream.write("{}\n")
    state = {
        "scenario": args.scenario,
        "preflight_only": args.preflight_only,
        "started_at": datetime.now(UTC).isoformat(),
        "checks": [],
        "ids": {},
    }
    clients, connection = {}, None

    def save():
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n")

    def check(name, passed):
        state["checks"].append({"name": name, "passed": bool(passed)})
        save()
        print(json.dumps(state["checks"][-1]), flush=True)
        if not passed:
            raise AssertionError(name)

    async def request(account, method, path, expected=200, **kwargs):
        response = await clients[account].request(method, path, **kwargs)
        check(f"{account} {method} {path}: {response.status_code}", response.status_code == expected)
        return response.json() if response.content else None

    try:
        connection = await asyncpg.connect(host="/tmp", database="sales_saas", user="postgres", command_timeout=10)  # noqa: S108
        await _initialize_connection(connection)
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE " + args.role)
            check(
                "database role obeys RLS",
                not await connection.fetchval(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ),
            )
            actors = {}
            for account in ("XS001", "XS002"):
                actors[account] = (
                    await IdentityRepository().find_actor_by_account(
                        connection, workspace_external_id="demo-sales-workspace", account_code=account
                    )
                ).context
            actor = actors["XS001"]
            check(
                "exact authorized sales actor",
                actor.user_id == ACTOR and actor.workspace_id == WORKSPACE and actor.role.value == "sales",
            )
            await set_request_context(connection, actor)

            class Pool:
                @asynccontextmanager
                async def acquire(self):
                    yield connection

            facts = await AgentFactsLoader(Database(get_settings(), pool=Pool())).load(
                RunInput("preflight", "none", "整理今日待办", "today_tasks", None, actor), today_tasks_scope=scope
            )
            candidates = facts["follow_up_candidates"]
            check(
                "exactly one unchanged authorized demo source",
                len(candidates) == 1 and candidates[0]["source_id"] == scope.source_ids[0],
            )
            candidate = candidates[0]
            await set_request_context(connection, actors['XS002'])
            shared_visible = await shared_customer_task_visible(connection, candidate)
            state['XS002_expected_task_read_status'] = 200 if shared_visible else 404
            await set_request_context(connection, actor)
            expected_name = next(item["customer_name"] for item in plan["cases"] if item["scenario"] == args.scenario)
            check("approved demo customer name", candidate["customer_name"] == expected_name)
            state.update(
                active_tasks_before=len(facts["active_tasks"]),
                candidate_count=1,
                source_fingerprint=scope.sources[0][1],
            )

            async def tasks():
                return [dict(row) for row in await connection.fetch("SELECT * FROM workflow.task ORDER BY id")]

            async def protected():
                result = {}
                for query in (
                    "SELECT * FROM crm.customer ORDER BY id",
                    "SELECT * FROM crm.opportunity ORDER BY id",
                    "SELECT * FROM activity.visit ORDER BY id",
                ):
                    rows = await connection.fetch(query)
                    result[query] = fingerprint([dict(row) for row in rows])
                return result

            before_tasks, before_formal = await tasks(), await protected()
            before_notifications = [dict(row) for row in await connection.fetch(
                'SELECT * FROM workflow.notification ORDER BY id')]
            before_ids = [row["id"] for row in before_tasks]
            before_assignees = fingerprint(
                [
                    dict(row)
                    for row in await connection.fetch(
                        "SELECT * FROM workflow.task_assignee WHERE task_id=ANY($1::uuid[]) "
                        "ORDER BY task_id,assignee_user_ref_id,responsibility",
                        before_ids,
                    )
                ]
            )
            state.update(tasks_before_fingerprint=fingerprint(before_tasks), protected_before=before_formal)
            state['notifications_before_fingerprint'] = fingerprint(before_notifications)
            if args.preflight_only:
                state.update(preflight_passed=True, application_sessions_created=0, model_calls=0, business_writes=0)
                return
            check(
                "Worker scope equals approved case",
                load_today_scope(
                    SimpleNamespace(
                        agent_today_tasks_acceptance_target=WORKSPACE + ":" + ACTOR,
                        agent_today_tasks_acceptance_path=args.installed_scope,
                    ),
                    actor,
                )
                == scope,
            )
            for account in actors:
                clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                session = await request(
                    account,
                    "POST",
                    "/auth/session",
                    json={
                        "account_code": account,
                        "workspace": "demo-sales-workspace",
                        "client": {"purpose": "today-tasks-acceptance", "label": args.scenario},
                    },
                )
                clients[account].headers["Authorization"] = "Bearer " + session["access_token"]
                check(account + " session belongs to own actor", session["actor"]["user_id"] == actors[account].user_id)
            conversation = await request("XS001", "POST", "/conversations", 201, json={"mode": "today_tasks"})
            cid = state["ids"]["conversation_id"] = conversation["id"]
            path = f"/conversations/{cid}/messages"
            body = {
                "text": "请结合已有任务和跟进记录整理我的今日待办。",
                "client_message_id": "today-acceptance-" + uuid4().hex,
                "input_source": "text",
            }
            started, submitted = monotonic(), datetime.now(UTC)
            accepted = await request("XS001", "POST", path, 202, json=body)
            run_id = state["ids"]["run_id"] = accepted["run_id"]
            save()
            replay = await request("XS001", "POST", path, 202, json=body)
            check("message replay returns same run", replay["run_id"] == run_id)
            await request("XS001", "POST", path, 409, json={**body, "text": body["text"] + "不同正文"})
            public = {}
            while monotonic() - started < 75:
                response = await clients["XS001"].get(f"/agent/runs/{run_id}")
                assert response.status_code == 200
                public = response.json()
                if public["status"] in {"succeeded", "failed", "cancelled", "waiting_human"}:
                    break
                await asyncio.sleep(1)
            state.update(elapsed_ms=round((monotonic() - started) * 1000), status=public.get("status"))
            check("automatic task planning completed", public.get("status") == "succeeded")
            after_tasks = await tasks()
            new_tasks = [row for row in after_tasks if row["id"] not in before_ids]
            check("all prior tasks unchanged", [row for row in after_tasks if row["id"] in before_ids] == before_tasks)
            check("exactly one task created", len(new_tasks) == 1)
            task = new_tasks[0]
            task_id = state["ids"]["task_id"] = str(task["id"])
            check(
                "task uses only backend source and own pending status",
                str(task["source_visit_id"]) == candidate["source_id"]
                and str(task["customer_id"]) == candidate["customer_id"]
                and (str(task["opportunity_id"]) if task["opportunity_id"] else None) == candidate["opportunity_id"]
                and task["description"] == candidate["next_action"].strip()
                and str(task["creator_user_ref_id"]) == ACTOR
                and task["task_type"] == "visit_follow_up"
                and task["source_code"] == "today_task_agent"
                and task["status"] == "pending_execution"
                and task["due_at"] > submitted
                and task["priority_code"] in {"normal", "medium", "high", "urgent"},
            )
            owners = await connection.fetch("SELECT * FROM workflow.task_assignee WHERE task_id=$1::uuid", task_id)
            check(
                "sole owner is the current sales user",
                len(owners) == 1
                and str(owners[0]["assignee_user_ref_id"]) == ACTOR
                and owners[0]["responsibility"] == "owner",
            )
            check(
                "one creation event for this run",
                await connection.fetchval(
                    "SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid "
                    "AND event_type='created' AND payload->>'run_id'=$2",
                    task_id,
                    run_id,
                )
                == 1,
            )
            check(
                "existing assignees unchanged",
                before_assignees
                == fingerprint(
                    [
                        dict(row)
                        for row in await connection.fetch(
                            "SELECT * FROM workflow.task_assignee WHERE task_id=ANY($1::uuid[]) "
                        "ORDER BY task_id,assignee_user_ref_id,responsibility",
                            before_ids,
                        )
                    ]
                ),
            )
            check("CRM and visits unchanged", before_formal == await protected())
            authorized_source_ids = [select_scope(plan, mode, live=True).source_ids[0] for mode in SCENARIOS]
            authorized_tasks = [dict(row) for row in await connection.fetch(
                'SELECT * FROM workflow.task WHERE source_visit_id=ANY($1::uuid[])', authorized_source_ids)]
            reminders = await audit_notifications(
                connection, before_notifications, authorized_tasks, actor=ACTOR, workspace=WORKSPACE)
            state['existing_monitor_reminders'] = reminders
            state['new_notifications'] = len(reminders)
            check('existing notifications unchanged; only exact own-task monitor reminders added', True)
            result = public["result"]
            check(
                "existing card contains persisted task",
                sum(row["task_id"] == task_id for row in result["rows"]) == 1
                and len(result["rows"]) == len(facts["active_tasks"]) + 1,
            )
            check(
                "no provider metadata in card",
                not set(result) & {"provider", "inference_route", "fallback_reason", "operation_id"},
            )
            check(
                "automatic mode has no pending artifact",
                await connection.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 0,
            )
            jobs = []
            for _ in range(20):
                jobs = await connection.fetch(
                    "SELECT id::text,status,attempts FROM ops.job WHERE aggregate_id=$1::uuid", run_id
                )
                if len(jobs) == 1 and jobs[0]["status"] == "succeeded":
                    break
                await asyncio.sleep(0.1)
            check(
                "one completed Worker job",
                len(jobs) == 1 and jobs[0]["status"] == "succeeded" and jobs[0]["attempts"] == 1,
            )
            check(
                "one receipt and answer",
                await connection.fetchval("SELECT count(*) FROM ops.job_effect WHERE job_id=$1::uuid", jobs[0]["id"])
                == 1
                and await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id)
                == 1,
            )
            context = await connection.fetchval("SELECT business_context FROM agent.run WHERE id=$1::uuid", run_id)
            trace = state["inference_route"] = context.get("inference_route")
            attempts = await connection.fetch(
                "SELECT provider_code,status,http_status,error_code,operation_id::text, "
                "request_snapshot->>'network_dispatch_suppressed' AS dispatch_suppressed "
                "FROM agent.model_invocation WHERE run_id=$1::uuid "
                "AND record_kind='provider_attempt' ORDER BY started_at",
                run_id,
            )
            state["attempts"] = [dict(row) for row in attempts]
            check(
                "same operation final real API success",
                attempts
                and len({row["operation_id"] for row in attempts}) == 1
                and attempts[-1]["status"] == "succeeded"
                and attempts[-1]["http_status"] == 200,
            )
            if args.scenario == "off":
                check(
                    "off uses original API only",
                    trace is None and all(row["provider_code"] == "senseaudio" for row in attempts),
                )
            elif args.scenario == "blocked":
                check(
                    "blocked request falls back in same operation",
                    trace
                    and trace["provider"] == "senseaudio"
                    and trace["fallback_reason"] == "ControlledPlatformBlock"
                    and attempts[0]["dispatch_suppressed"] == "true"
                    and attempts[0]["http_status"] is None,
                )
            else:
                check(
                    "real platform dispatch",
                    trace
                    and attempts[0]["provider_code"] == "agent_platform"
                    and attempts[0]["dispatch_suppressed"] == "false",
                )
                state["platform_accepted"] = trace["provider"] == "agent_platform"
                check(
                    "platform result or automatic original fallback",
                    state["platform_accepted"]
                    or (trace["provider"] == "senseaudio" and bool(trace.get("fallback_reason"))),
                )
            await request("XS001", "GET", f"/tasks/{task_id}")
            await request("XS002", "GET", f"/tasks/{task_id}", 200 if shared_visible else 404)
            await request("XS002", "GET", f"/agent/runs/{run_id}", 404)
            check("other sales cannot read conversation", await request("XS002", "GET", path) == [])
            await set_request_context(connection, actors["XS002"])
            check('task RLS follows existing customer and private opportunity boundaries',
                  await connection.fetchval('SELECT count(*) FROM workflow.task WHERE id=$1::uuid', task_id)
                  == int(shared_visible))
            other_facts = await AgentFactsLoader(Database(get_settings(), pool=Pool())).load(
                RunInput('other-user-readonly', 'none', 'today tasks', 'today_tasks', None, actors['XS002']))
            check('shared read does not add this task to another users own planning facts',
                  candidate['source_id'] not in {row['source_id'] for row in
                      other_facts['active_tasks'] + other_facts['follow_up_candidates']})
            state.update(new_tasks=1, real_business_verified=True, passed=True)
    except Exception as exc:
        state.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        cleanup_ok = True
        for account, client in clients.items():
            try:
                if "Authorization" in client.headers:
                    check(account + " own session revoked", (await client.post("/auth/logout")).status_code == 204)
            except Exception:
                cleanup_ok = False
                state["passed"] = False
            finally:
                await client.aclose()
        if connection:
            await connection.close()
        state["finished_at"] = datetime.now(UTC).isoformat()
        save()
        if not cleanup_ok:
            raise RuntimeError("Own session cleanup failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--installed-scope", default="/etc/sales-today-tasks-acceptance.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--role", choices=("sales_app", "sales_runtime"), default="sales_app")
    asyncio.run(run(parser.parse_args()))
