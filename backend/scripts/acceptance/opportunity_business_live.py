"""Authorized candidate-only business acceptance; never confirms a CRM mutation.

Runs on the DB host via peer auth, SET ROLE to the existing app role and the
resolved sales identity for read-only audit. Credentials and customer data stay
in memory. The operator configures routing separately; writes are not retried
after ambiguous responses. Each execution writes a new evidence file.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
import httpx

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def run(args):
    output = Path(args.output)
    with output.open("x") as stream:
        stream.write("{}\n")
    state = {"scenario": args.scenario, "intent": args.intent, "checks": [], "ids": {},
             "started_at": datetime.now(UTC).isoformat(), "preflight_only": args.preflight_only}
    clients = {}
    connection = None

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
        check(f"{account} {method} {path} expected {expected}, actual {response.status_code}",
              response.status_code == expected)
        return response.json() if response.content else None

    try:
        connection = await asyncpg.connect(
            host="/tmp", database="sales_saas", user="postgres", command_timeout=10,  # noqa: S108
        )
        await _initialize_connection(connection)
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE " + args.role)
            check("restricted role without RLS bypass", not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user",
            ))
            actors = {}
            for account in ("XS001", "XS002"):
                record = await IdentityRepository().find_actor_by_account(
                    connection, workspace_external_id="demo-sales-workspace", account_code=account,
                )
                actors[account] = record.context
                clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                session = await request(account, "POST", "/auth/session", json={
                    "account_code": account, "workspace": "demo-sales-workspace",
                    "client": {"purpose": "opportunity-candidate-acceptance",
                               "label": args.scenario + ":" + args.intent},
                })
                clients[account].headers["Authorization"] = "Bearer " + session["access_token"]
                check(account + " own sales identity", session["actor"]["user_id"] == record.context.user_id
                      and session["actor"]["role"] == "sales")
            actor = actors["XS001"]
            await set_request_context(connection, actor)
            cid = state["ids"]["customer_id"] = args.customer_id
            customer = await request("XS001", "GET", f"/customers/{cid}")
            check("explicit synthetic customer matches", customer["name"] == args.customer_name
                  and any(marker in customer["name"] for marker in ("验收", "联调", "合成")))
            check("test actor owns selected customer", await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm.customer_sales_member WHERE customer_id=$1::uuid "
                "AND user_ref_id=$2::uuid)", cid, actor.user_id,
            ))

            async def crm_state():
                rows = await connection.fetch(
                    "SELECT * FROM crm.opportunity WHERE customer_id=$1::uuid ORDER BY id", cid,
                )
                changes = await connection.fetchval(
                    "SELECT count(*) FROM crm.business_change WHERE customer_id=$1::uuid", cid,
                )
                return fingerprint([dict(row) for row in rows]), changes

            before = await crm_state()
            state["crm_before_fingerprint"] = before[0]
            target = None
            close_date = (date.today() + timedelta(days=90)).isoformat()
            name = "中台候选验收-" + uuid4().hex[:8]
            if args.intent == "create":
                text = (f"本次客户明确新增一个独立项目，名称是{name}。ACV为8万元，阶段是方案沟通50%，"
                        f"预计关单日期为{close_date}。没有指定合作伙伴或产品线。只生成新建候选供人工确认。")
            elif args.intent == "update":
                target = await connection.fetchrow(
                    "SELECT id::text,name FROM crm.opportunity WHERE customer_id=$1::uuid "
                    "AND deleted_at IS NULL AND status='open' ORDER BY updated_at DESC LIMIT 1", cid,
                )
                check("owned open opportunity exists for update case", target is not None)
                state["ids"]["matched_opportunity_id"] = target["id"]
                text = (f"本次只更新已有商机“{target['name']}”，ACV调整为1200元。"
                        "没有讨论阶段、概率、预计关单日期、伙伴或产品线，这些字段不要提出修改。只生成更新候选。")
            else:
                text = "本次普通拜访演示客服检索，不关联商机，没有新增项目，也不更新任何已有商机。"
            if args.preflight_only:
                state["preflight_passed"] = True
                state["real_business_verified"] = False
                return
            conversation = await request("XS001", "POST", "/conversations", 201,
                                         json={"mode": "opportunity_draft", "customer_id": cid})
            conversation_id = state["ids"]["conversation_id"] = conversation["id"]
            body = {"text": text, "client_message_id": "opportunity-acceptance-" + uuid4().hex, "input_source": "text"}
            state["submitted_at"] = datetime.now(UTC).isoformat()
            save()
            started = monotonic()
            path = f"/conversations/{conversation_id}/messages"
            accepted = await request("XS001", "POST", path, 202, json=body)
            run_id = state["ids"]["run_id"] = accepted["run_id"]
            save()
            replay = await request("XS001", "POST", path, 202, json=body)
            check("message replay returns same run", replay["run_id"] == run_id)
            await request("XS001", "POST", path, 409, json={**body, "text": text + "不同内容"})
            deadline = monotonic() + 75
            while monotonic() < deadline:
                response = await clients["XS001"].get(f"/agent/runs/{run_id}")
                if response.status_code != 200:
                    check("run polling remains readable", False)
                public = response.json()
                if public["status"] in {"waiting_human", "succeeded", "failed", "cancelled"}:
                    break
                await asyncio.sleep(1)
            state["elapsed_ms"] = round((monotonic() - started) * 1000)
            state["status"] = public["status"]
            check("candidate waits for human confirmation", public["status"] == "waiting_human")
            result = public["result"]
            check("candidate action matches business intent", result.get("action") == args.intent)
            if args.intent == "create":
                check("new candidate preserves explicit values", result["name"] == name
                      and result["amount"] == 80000 and result["probability"] == 50
                      and result["expected_close_date"] == close_date and not result["opportunity_id"])
            elif args.intent == "update":
                check("update targets visible opportunity and changed value only",
                      result["opportunity_id"] == target["id"] and result["amount"] == 1200
                      and result["probability"] is None and result["expected_close_date"] is None
                      and not result["partner_name"] and not result["product_line"])
            else:
                check("no association clears candidate business fields", not result["opportunity_id"]
                      and not result["name"] and result["amount"] is None
                      and result["probability"] is None and result["missing_fields"] == [])
            check("route metadata stays out of public candidate", all(
                field not in result for field in ("inference_route", "provider", "fallback_reason", "operation_id")
            ))
            check("candidate did not mutate formal opportunities or change cards", await crm_state() == before)
            jobs = await connection.fetch(
                "SELECT id::text,status,attempts FROM ops.job WHERE aggregate_id=$1::uuid", run_id,
            )
            # Result and job receipt commit before the Worker acknowledges its row.
            for _ in range(10):
                if len(jobs) == 1 and jobs[0]["status"] == "succeeded":
                    break
                await asyncio.sleep(.1)
                jobs = await connection.fetch(
                    "SELECT id::text,status,attempts FROM ops.job WHERE aggregate_id=$1::uuid", run_id,
                )
            check("one completed Worker job without whole-job replay", len(jobs) == 1
                  and jobs[0]["status"] == "succeeded" and jobs[0]["attempts"] == 1)
            state["ids"]["job_id"] = jobs[0]["id"]
            check("one completion receipt", await connection.fetchval(
                "SELECT count(*) FROM ops.job_effect WHERE job_id=$1::uuid", jobs[0]["id"],
            ) == 1)
            artifacts = await connection.fetch(
                "SELECT id::text,status,model_ref FROM agent.artifact WHERE run_id=$1::uuid", run_id,
            )
            check("one pending candidate artifact", len(artifacts) == 1
                  and artifacts[0]["status"] in {"pending_confirm", "pending_supplement"})
            state["ids"]["artifact_id"] = artifacts[0]["id"]
            check("one assistant result", await connection.fetchval(
                "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid", run_id,
            ) == 1)
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
            check("attempts share one inference operation", attempts
                  and len({row["operation_id"] for row in attempts}) == 1)
            check("final real provider completed", attempts[-1]["status"] == "succeeded"
                  and attempts[-1]["http_status"] == 200)
            if args.scenario == "off":
                check("disabled route keeps original API", trace is None
                      and all(row["provider_code"] == "senseaudio" for row in attempts))
            else:
                check("artifact model reference matches selected provider", trace
                      and artifacts[0]["model_ref"] == trace["model_ref"])
                if args.scenario == "blocked":
                    check("controlled failure falls back within same operation", trace["provider"] == "senseaudio"
                          and trace["fallback_reason"] == "ControlledPlatformBlock" and len(attempts) >= 2
                          and attempts[0]["dispatch_suppressed"] == "true" and attempts[0]["http_status"] is None)
                else:
                    check("real platform dispatched", attempts[0]["provider_code"] == "agent_platform"
                          and attempts[0]["dispatch_suppressed"] == "false")
                    state["platform_accepted"] = trace["provider"] == "agent_platform"
                    check("platform accepted or validated automatic fallback", state["platform_accepted"] or (
                        trace["provider"] == "senseaudio"
                        and trace["fallback_reason"] in {"TimeoutError", "InvalidAgentResult"}
                        and len(attempts) >= 2 and attempts[-1]["provider_code"] == "senseaudio"
                    ))
            await request("XS002", "GET", f"/agent/runs/{run_id}", 404)
            hidden = await request("XS002", "GET", path)
            check("other sales user cannot read messages", hidden == [])
            await set_request_context(connection, actors["XS002"])
            check("other sales user cannot read candidate artifact", await connection.fetchval(
                "SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id,
            ) == 0)
        state["passed"] = True
    except Exception as exc:
        state.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        for account, client in clients.items():
            try:
                if "Authorization" in client.headers:
                    check(account + " own session revoked", (await client.post("/auth/logout")).status_code == 204)
            finally:
                await client.aclose()
        if connection:
            await connection.close()
        state["finished_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("normal", "off", "blocked", "production"), required=True)
    parser.add_argument("--intent", choices=("create", "update", "none"), required=True)
    parser.add_argument("--customer-id", type=lambda value: str(UUID(value)), required=True)
    parser.add_argument("--customer-name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight-only", action="store_true", help="Check test scope without submitting a candidate")
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--role", default="sales_app", choices=("sales_app", "sales_runtime"))
    asyncio.run(run(parser.parse_args()))
