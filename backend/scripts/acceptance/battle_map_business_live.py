"""Authorized synthetic customer PATCH -> real Worker/providers -> restricted RLS audit.

Run on the database host via postgres peer authentication. SET ROLE to the app
role before querying, use XS001's API session, and revoke only our own sessions.
Routing is selected separately by the operator. No ambiguous write is retried.
The baseline field is kept in a private file, never in the evidence report.
"""

import argparse
import asyncio
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
import httpx

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.battle_map_reviews import quadrant_code


async def run(args):
    output = Path(args.output)
    with output.open("x") as stream:
        stream.write("{}\n")
    state = {"scenario": args.scenario, "started_at": datetime.now(UTC).isoformat(), "checks": []}
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
        check(f"{account} {method} {path} expected HTTP {expected}, actual {response.status_code}",
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
                actors[account] = (await IdentityRepository().find_actor_by_account(
                    connection, workspace_external_id="demo-sales-workspace", account_code=account,
                )).context
                clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                session = await request(account, "POST", "/auth/session", json={
                    "account_code": account, "workspace": "demo-sales-workspace",
                    "client": {"purpose": "battle-map-business-acceptance", "label": args.scenario},
                })
                clients[account].headers["Authorization"] = "Bearer " + session["access_token"]
                check(account + " uses own sales identity", session["actor"]["user_id"] == actors[account].user_id)
            actor = actors["XS001"]
            await set_request_context(connection, actor)
            cid = args.customer_id
            before = await request("XS001", "GET", f"/customers/{cid}")
            check("explicit synthetic customer matches", before["name"] == args.customer_name)
            check("synthetic name marker", any(s in before["name"] for s in ("验收", "联调", "合成")))
            check("test actor owns customer", await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm.customer_sales_member "
                "WHERE customer_id=$1::uuid AND user_ref_id=$2::uuid)", cid, actor.user_id,
            ))
            baseline = Path(args.baseline)
            if baseline.exists():
                original = json.loads(baseline.read_text())
                check("baseline belongs to selected customer", original["customer_id"] == cid)
            else:
                check("restore requires baseline", not args.restore)
                field = next((key for key in ("next_action", "demand_summary") if before.get(key)), None)
                check("existing nonempty field can be restored", field is not None)
                original = {"customer_id": cid, "field": field, "value": before[field]}
                fd = os.open(baseline, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump(original, stream, ensure_ascii=False)
            value = original["value"] if args.restore else (
                original["value"] + "\n【中台业务验收 " + args.scenario + " " + uuid4().hex[:8] + "】"
            )
            check("field change stays within contract", len(value) <= 5000 and value != before[original["field"]])
            version = before["version_no"]
            key = str(uuid4())
            body = {original["field"]: value, "version_no": version}
            state.update(customer_id=cid, version_before=version, idempotency_key=key)
            state["submitted_at"] = datetime.now(UTC).isoformat()
            save()
            started = monotonic()
            changed = await request("XS001", "PATCH", f"/customers/{cid}", json=body,
                                    headers={"Idempotency-Key": key})
            check("existing business flow queued map review", changed.get("battle_map_review") == "queued")
            replay = await request("XS001", "PATCH", f"/customers/{cid}", json=body,
                                   headers={"Idempotency-Key": key})
            check("acknowledged replay returns same result", changed == replay)
            await request("XS001", "PATCH", f"/customers/{cid}", 409,
                          json={**body, original["field"]: value + "不同"}, headers={"Idempotency-Key": key})
            deadline = monotonic() + 70
            job = None
            while monotonic() < deadline:
                rows = await connection.fetch(
                    "SELECT j.id::text,j.status,j.attempts,j.payload->>'trigger_id' AS event_id "
                    "FROM ops.job j JOIN crm.business_change b ON b.id=j.correlation_id "
                    "WHERE b.customer_id=$1::uuid AND b.version_no=$2 AND b.actor_user_ref_id=$3::uuid "
                    "AND j.job_type='battle_map.review'", cid, version + 1, actor.user_id,
                )
                if len(rows) != 1:
                    check("exactly one queued business job", False)
                job = dict(rows[0])
                state["job"] = job
                save()
                if job["status"] in {"succeeded", "failed", "dead_letter", "cancelled"}:
                    break
                await asyncio.sleep(1)
            state["elapsed_ms"] = round((monotonic() - started) * 1000)
            check("business job succeeded once", job and job["status"] == "succeeded" and job["attempts"] == 1)
            scores = await connection.fetch(
                "SELECT id::text,potential_score,relationship_score,quadrant_code,input_snapshot,evidence "
                "FROM insight.quadrant_score WHERE customer_id=$1::uuid "
                "AND input_snapshot->'trigger'->>'id'=$2", cid, job["event_id"],
            )
            check("one score persisted for acknowledged business change", len(scores) == 1)
            score = scores[0]
            state["score_id"] = score["id"]
            trace = score["input_snapshot"].get("inference_route")
            state["inference_route"] = trace
            check("one current score", await connection.fetchval(
                "SELECT count(*) FROM insight.quadrant_score WHERE customer_id=$1::uuid "
                "AND valid_to='infinity'", cid,
            ) == 1)
            check("one transactional job completion receipt", await connection.fetchval(
                "SELECT count(*) FROM ops.job_effect WHERE job_id=$1::uuid", job["id"],
            ) == 1)
            check("valid scores and backend quadrant", all(
                math.isfinite(float(score[k])) and 0 <= score[k] <= 100
                for k in ("potential_score", "relationship_score")
            ) and score["quadrant_code"] == quadrant_code(score["potential_score"], score["relationship_score"]))
            allowed = {
                kind: set(score["input_snapshot"][kind + "_ids"]) for kind in ("visit", "contact", "opportunity")
            }
            check("evidence IDs come from scoped input", all(
                item["source_id"] in allowed.get(item["source_type"], set()) for item in score["evidence"]
            ))
            check("business notification received completed review", await connection.fetchval(
                "SELECT count(*) FROM workflow.notification WHERE payload->>'event_id'=$1 "
                "AND recipient_user_ref_id=$2::uuid AND payload ? 'ai_review'", job["event_id"], actor.user_id,
            ) == 1)
            if trace:
                check("private trace matches Worker job", trace["job_id"] == job["id"])
                attempts = await connection.fetch(
                    "SELECT provider_code,status,http_status,error_code,operation_id::text, "
                    "request_snapshot->>'network_dispatch_suppressed' AS dispatch_suppressed "
                    "FROM agent.model_invocation WHERE operation_id=$1::uuid ORDER BY started_at",
                    trace["operation_id"],
                )
            else:
                attempts = await connection.fetch(
                    "SELECT provider_code,status,http_status,error_code,operation_id::text "
                    "FROM agent.model_invocation WHERE actor_user_ref_id=$1::uuid "
                    "AND operation_code='battle_map_review' "
                    "AND started_at >= $2::timestamptz AND record_kind='provider_attempt' ORDER BY started_at",
                    actor.user_id, datetime.fromisoformat(state["submitted_at"]),
                )
            state["attempts"] = [dict(row) for row in attempts]
            check("provider retries share one inference operation", attempts and
                  len({row["operation_id"] for row in attempts}) == 1)
            if args.scenario == "off":
                check("disabled route uses real original API", trace is None and attempts
                      and all(row["provider_code"] == "senseaudio" for row in attempts)
                      and attempts[-1]["http_status"] == 200)
            elif args.scenario == "blocked":
                check("same-operation controlled fallback succeeds", trace and trace["provider"] == "senseaudio"
                      and trace["fallback_reason"] == "ControlledPlatformBlock" and len(attempts) >= 2
                      and attempts[0]["dispatch_suppressed"] == "true" and attempts[0]["http_status"] is None
                      and all(row["provider_code"] == "senseaudio" for row in attempts[1:])
                      and attempts[-1]["http_status"] == 200)
            else:
                check("real platform attempted", trace and attempts and attempts[0]["provider_code"] == "agent_platform"
                      and attempts[0]["dispatch_suppressed"] == "false")
                state["platform_accepted"] = trace["provider"] == "agent_platform" and trace["fallback_reason"] is None
                check("platform accepted or bounded timeout recovered", state["platform_accepted"] or (
                    trace["fallback_reason"] == "TimeoutError" and len(attempts) >= 2
                    and attempts[-1]["provider_code"] == "senseaudio" and attempts[-1]["http_status"] == 200
                ))
            check("final real provider succeeded", attempts[-1]["status"] == "succeeded")
            public = await request("XS001", "GET", f"/customers/{cid}")
            check("public contract and one customer version", public["version_no"] == version + 1
                  and float(public["potential_score"]) == float(score["potential_score"])
                  and float(public["relationship_score"]) == float(score["relationship_score"])
                  and all(key not in public for key in ("inference_route", "provider", "fallback_reason")))
            if args.restore:
                check("original synthetic customer field restored", public[original["field"]] == original["value"])
            await set_request_context(connection, actors["XS002"])
            check("other sales user cannot read the private score", await connection.fetchval(
                "SELECT count(*) FROM insight.quadrant_score WHERE customer_id=$1::uuid", cid,
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
    parser.add_argument("--customer-id", type=lambda value: str(UUID(value)), required=True)
    parser.add_argument("--customer-name", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--role", default="sales_app", choices=("sales_app", "sales_runtime"))
    asyncio.run(run(parser.parse_args()))
