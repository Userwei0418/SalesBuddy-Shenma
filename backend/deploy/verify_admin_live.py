"""One authorized XS001 report through HTTPS, Worker and real providers.

Retains IDs and status metadata only. No company policy changes, no formal
customer/opportunity/task/risk writes; the report card is kept as audit evidence.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

import asyncpg
import httpx

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository


async def main(output):
    state = {"started_at": datetime.now(UTC).isoformat(), "account": "XS001", "checks": []}
    with output.open("x") as stream:
        stream.write("{}\n")

    def save():
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n")

    async with httpx.AsyncClient(base_url="https://www.ericepc.com/api/v1", timeout=15) as client:
        logged_in = False
        try:
            response = await client.post("/auth/session", json={
                "account_code": "XS001", "workspace": "demo-sales-workspace",
                "client": {"purpose": "admin-release-verification"},
            })
            assert response.status_code == 200, f"session HTTP {response.status_code}"
            session = response.json()
            client.headers["Authorization"] = "Bearer " + session["access_token"]
            logged_in = True
            home = await client.get("/assistant/home")
            assert home.status_code == 200
            assert home.json()["display_policy"]["definition"]["message_order"] == "desc"
            state["checks"].append("home_consumes_desc_policy")
            denied = await client.get("/console/ai/runs")
            assert denied.status_code == 403
            state["checks"].append("sales_cannot_access_management_audit")
            response = await client.post("/conversations", json={"mode": "operating_report"})
            assert response.status_code == 201
            cid = state["conversation_id"] = response.json()["id"]
            body = {"text": "请根据当前权限范围的客户经营事实生成即时总结，行动方案只提供建议。",
                    "client_message_id": "admin-release-"+uuid4().hex, "input_source": "text"}
            save()
            response = await client.post(f"/conversations/{cid}/messages", json=body)
            assert response.status_code == 202
            rid = state["run_id"] = response.json()["run_id"]
            save()
            started = monotonic()
            while monotonic()-started < 80:
                response = await client.get(f"/agent/runs/{rid}")
                assert response.status_code == 200
                state["business_status"] = response.json()["status"]
                if state["business_status"] in ("succeeded", "failed", "cancelled", "waiting_human"):
                    break
                await asyncio.sleep(1)
            state["elapsed_ms"] = round((monotonic()-started)*1000)
            connection = await asyncpg.connect(  # Unix socket.
                host="/tmp", database="sales_saas", user="postgres", command_timeout=15  # noqa: S108
            )
            try:
                await _initialize_connection(connection)
                async with connection.transaction(readonly=True):
                    await connection.execute("SET LOCAL ROLE sales_runtime")
                    row = await connection.fetchrow(
                        "SELECT * FROM security.resolve_demo_actor('demo-sales-workspace','XS001')"
                    )
                    actor = IdentityRepository._actor(row).context
                    assert actor.user_id == session["actor"]["user_id"]
                    await set_request_context(connection, actor)
                    receipts = await connection.fetch(
                        "SELECT id::text,status,capability,trace,configuration,events FROM agent.inference_operation "
                        "WHERE run_id=$1::uuid ORDER BY started_at", rid
                    )
                    state["receipts"] = [{
                        "operation_id": r["id"], "status": r["status"], "capability": r["capability"],
                        "provider": r["trace"].get("provider"), "fallback_reason": r["trace"].get("fallback_reason"),
                        "configuration": r["configuration"],
                        "phases": [{k: e.get(k) for k in ("phase", "provider", "elapsed_ms", "error_code")}
                                   for e in r["events"]],
                    } for r in receipts]
                    state["assistant_results"] = await connection.fetchval(
                        "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid AND sender_type='assistant'",
                        rid,
                    )
                    state["job"] = [dict(r) for r in await connection.fetch(
                        "SELECT status,attempts FROM ops.job WHERE aggregate_id=$1::uuid", rid
                    )]
            finally:
                await connection.close()
            state["passed"] = (state["business_status"] == "succeeded" and state["assistant_results"] == 1
                               and len(receipts) == 1 and receipts[0]["status"] == "accepted")
        finally:
            if logged_in:
                state["own_session_revoked"] = (await client.post("/auth/logout")).status_code == 204
            save()
    print(json.dumps(state, ensure_ascii=False, default=str), flush=True)
    assert state.get("passed"), "Real report/audit verification did not pass"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.output))
