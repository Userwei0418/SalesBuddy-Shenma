"""Bounded report acceptance through the existing business API; no formal mutations.

Preflight is RLS SELECT only. Live execution requires separate publication/API
and exact three-account authorization. Evidence keeps counts, hashes and IDs.
"""

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
import httpx
from pydantic_core import to_jsonable_python

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.contract import INSTANT_SUMMARY_CONTRACT
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput

WORKSPACE = "00000000-0000-0000-0000-000000000001"
AGENT = "01a09022-d0ec-7703-b59f-0699150d63a7"
ACCOUNTS = {"XS001": "sales", "ZJ001": "supervisor", "ZJL001": "manager"}
CASES = {
    "normal-sales": "XS001",
    "normal-supervisor": "ZJ001",
    "normal-manager": "ZJL001",
    "off": "XS001",
    "blocked": "XS001",
    "production": "XS001",
}
TEXT = "请根据当前权限范围的客户经营事实生成即时总结，行动方案只提供建议。"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def validate_plan(plan, scenario, *, live):
    assert scenario in CASES and plan["agent_id"] == AGENT and plan["workspace_id"] == WORKSPACE
    assert plan["cases"] == list(CASES) and plan["maximum_report_cards"] == 6
    assert set(plan["actors"]) == set(ACCOUNTS)
    ids = [str(UUID(plan["actors"][account]["user_id"])) for account in ACCOUNTS]
    assert len(set(ids)) == 3
    assert all(plan["actors"][account]["role"] == role for account, role in ACCOUNTS.items())
    if live:
        assert plan["approval_status"] == "approved" and plan["publication_api_live_enable_authorized"] is True
        expiry = datetime.fromisoformat(plan["expires_at"])
        assert expiry.tzinfo and expiry > datetime.now(UTC)
    return CASES[scenario]


async def run(args):
    plan = json.loads(Path(args.plan).read_text())
    account = validate_plan(plan, args.scenario, live=not args.preflight_only)
    state = dict(
        scenario=args.scenario,
        account=account,
        checks=[],
        ids={},
        preflight_only=args.preflight_only,
        started_at=datetime.now(UTC).isoformat(),
    )
    path = Path(args.output)
    with path.open("x") as stream:
        stream.write("{}\n")
    connection, clients = None, {}

    def save():
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n")

    def check(name, valid):
        state["checks"].append(dict(name=name, passed=bool(valid)))
        save()
        print(json.dumps(state["checks"][-1]), flush=True)
        assert valid, name

    async def request(who, method, route, expected=200, **kwargs):
        response = await clients[who].request(method, route, **kwargs)
        check(who + " " + method + " " + route + ": " + str(response.status_code), response.status_code == expected)
        return response.json() if response.content else None

    try:
        connection = await asyncpg.connect(host="/tmp", database="sales_saas", user="postgres", command_timeout=10)  # noqa: S108
        await _initialize_connection(connection)
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE sales_app")
            check(
                "RLS enforced",
                not await connection.fetchval(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ),
            )
            actors = {}
            for code in (account, "XS002"):
                actors[code] = (
                    await IdentityRepository().find_actor_by_account(
                        connection, workspace_external_id="demo-sales-workspace", account_code=code
                    )
                ).context
            actor = actors[account]
            check(
                "exact approved identity",
                actor.workspace_id == WORKSPACE
                and actor.user_id == plan["actors"][account]["user_id"]
                and actor.role.value == ACCOUNTS[account],
            )
            await set_request_context(connection, actor)

            class Pool:
                @asynccontextmanager
                async def acquire(self):
                    yield connection

            facts = await AgentFactsLoader(Database(get_settings(), pool=Pool())).load(
                RunInput("preflight", "none", TEXT, "operating_report", None, actor)
            )
            state["facts"] = dict(
                counts={key: len(facts[key]) for key in ("tasks", "visits", "risks", "opportunities")},
                sha256=fingerprint(facts),
                json_bytes=len(json.dumps(to_jsonable_python(facts), ensure_ascii=False).encode()),
                scope_label=facts["scope"]["scope_label"],
            )
            check(
                "facts use actual role and scope",
                facts["scope"]["role"] == actor.role.value and facts["scope"]["user_id"] == actor.user_id,
            )

            async def protected():
                return {
                    table: fingerprint(
                        [
                            dict(row)
                            for row in await connection.fetch(
                                f"SELECT * FROM {table} ORDER BY "  # noqa: S608 - fixed table list
                                + (
                                    "task_id,assignee_user_ref_id,responsibility"
                                    if table == "workflow.task_assignee"
                                    else "id"
                                )
                            )
                        ]  # noqa: S608
                    )
                    for table in (
                        "crm.customer",
                        "crm.opportunity",
                        "activity.visit",
                        "workflow.task",
                        "workflow.task_assignee",
                        "insight.risk",
                        "workflow.notification",
                    )
                }

            before = state["protected_before"] = await protected()
            if args.preflight_only:
                state.update(passed=True, model_calls=0, application_sessions=0, business_writes=0)
                return
            for code in actors:
                clients[code] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                result = await request(
                    code,
                    "POST",
                    "/auth/session",
                    json={
                        "workspace": "demo-sales-workspace",
                        "account_code": code,
                        "client": {"purpose": "operating-report-acceptance"},
                    },
                )
                check("session belongs to " + code, result["actor"]["user_id"] == actors[code].user_id)
                clients[code].headers["Authorization"] = "Bearer " + result["access_token"]
            conv = await request(account, "POST", "/conversations", 201, json={"mode": "operating_report"})
            cid = state["ids"]["conversation_id"] = conv["id"]
            route = "/conversations/" + cid + "/messages"
            body = {"text": TEXT, "client_message_id": "report-acceptance-" + uuid4().hex, "input_source": "text"}
            started = monotonic()
            accepted = await request(account, "POST", route, 202, json=body)
            run_id = state["ids"]["run_id"] = accepted["run_id"]
            save()
            check(
                "same message replays same run",
                (await request(account, "POST", route, 202, json=body))["run_id"] == run_id,
            )
            await request(account, "POST", route, 409, json={**body, "text": TEXT + "不同正文"})
            public = {}
            while monotonic() - started < 75:
                response = await clients[account].get("/agent/runs/" + run_id)
                assert response.status_code == 200
                public = response.json()
                if public["status"] in {"succeeded", "failed", "cancelled", "waiting_human"}:
                    break
                await asyncio.sleep(1)
            state.update(status=public.get("status"), elapsed_ms=round((monotonic() - started) * 1000))
            stored = await connection.fetchrow(
                "SELECT business_context,error_code FROM agent.run WHERE id=$1::uuid", run_id
            )
            state["inference_route"] = stored["business_context"].get("inference_route")
            state["business_error_code"] = stored["error_code"]
            attempts = state["attempts"] = [
                dict(row)
                for row in await connection.fetch(
                    "SELECT provider_code,status,http_status,error_code,operation_id::text, "
                    "request_snapshot->>'network_dispatch_suppressed' AS dispatch_suppressed "
                    "FROM agent.model_invocation WHERE run_id=$1::uuid "
                    "AND record_kind='provider_attempt' ORDER BY started_at",
                    run_id,
                )
            ]
            check("report completed", public.get("status") == "succeeded")
            result = public["result"]
            title, sections = INSTANT_SUMMARY_CONTRACT[actor.role]
            check(
                "same role card contract",
                result["title"] == title
                and result["period"] == "当前实时状态"
                and result["scope"] == facts["scope"]["scope_label"]
                and isinstance(result["summary"], str)
                and bool(result["summary"].strip())
                and all(isinstance(result[key], list) for key in sections),
            )
            check(
                "no model operations or provider fields in public card",
                set(result)
                <= set(sections)
                | {"title", "period", "scope", "summary", "run_id", "artifact_id", "mode", "data_as_of"}
                and all(
                    isinstance(item, dict) and set(item) <= {"title", "detail"}
                    for key in sections
                    for item in result[key]
                ),
            )
            check("formal business and notifications unchanged", before == await protected())
            check(
                "one answer and no draft artifact",
                await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id)
                == 1
                and await connection.fetchval("SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 0,
            )
            for _ in range(20):
                jobs = await connection.fetch(
                    "SELECT id,status,attempts FROM ops.job WHERE aggregate_id=$1::uuid", run_id
                )
                if len(jobs) == 1 and jobs[0]["status"] == "succeeded":
                    break
                await asyncio.sleep(0.1)
            check(
                "one worker job and receipt",
                len(jobs) == 1
                and jobs[0]["status"] == "succeeded"
                and jobs[0]["attempts"] == 1
                and await connection.fetchval(
                    "SELECT count(*) FROM ops.job_effect WHERE job_id=$1::uuid", jobs[0]["id"]
                )
                == 1,
            )
            check(
                "real provider final HTTP success in one operation",
                attempts
                and attempts[-1]["status"] == "succeeded"
                and attempts[-1]["http_status"] == 200
                and len({r["operation_id"] for r in attempts}) == 1,
            )
            trace = state["inference_route"]
            if args.scenario == "off":
                check("original only", trace is None and all(r["provider_code"] == "senseaudio" for r in attempts))
            elif args.scenario == "blocked":
                check(
                    "controlled suppression then original fallback",
                    trace
                    and trace["provider"] == "senseaudio"
                    and trace["fallback_reason"] == "ControlledPlatformBlock"
                    and attempts[0]["dispatch_suppressed"] == "true",
                )
            else:
                check(
                    "actual platform dispatch and valid final result",
                    trace
                    and attempts[0]["provider_code"] == "agent_platform"
                    and attempts[0]["dispatch_suppressed"] == "false"
                    and (trace["provider"] == "agent_platform" or bool(trace.get("fallback_reason"))),
                )
            await request("XS002", "GET", "/agent/runs/" + run_id, 404)
            check("private conversation unreadable", await request("XS002", "GET", route) == [])
            await set_request_context(connection, actors["XS002"])
            check(
                "private report RLS unreadable",
                await connection.fetchval("SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id)
                == 0,
            )
            state.update(
                passed=True,
                real_business_verified=True,
                platform_accepted=bool(trace and trace["provider"] == "agent_platform"),
                new_report_cards=1,
                formal_business_writes=0,
            )
    except Exception as exc:
        state.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        cleanup = True
        for code, client in clients.items():
            try:
                if "Authorization" in client.headers:
                    valid = (await client.post("/auth/logout")).status_code == 204
                    state["checks"].append(dict(name=code + " own session revoked", passed=valid))
                    cleanup = cleanup and valid
            except Exception:
                cleanup = False
            finally:
                await client.aclose()
        if not cleanup:
            state["passed"] = False
        if connection:
            await connection.close()
        state["finished_at"] = datetime.now(UTC).isoformat()
        save()
        if not cleanup:
            raise RuntimeError("Own session cleanup failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--scenario", choices=CASES, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    asyncio.run(run(parser.parse_args()))
