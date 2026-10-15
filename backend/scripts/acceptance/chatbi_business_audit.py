"""Read-only, restricted-role audit of one acknowledged real ChatBI business run.

Runs locally on the DB host as postgres via peer auth, immediately SET ROLE to
an existing non-super/non-BYPASSRLS app role, then uses the resolved sales actor.
No service env, credentials, prompts, answer bodies or business values are output.
"""

import argparse
import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput


async def run(args):
    connection = await asyncpg.connect(host="/tmp", database="sales_saas", user="postgres", command_timeout=10)  # noqa: S108 - existing PostgreSQL socket
    await _initialize_connection(connection)
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "run_id": args.run_id,
        "scenario": args.scenario,
        "checks": [],
    }

    def check(name, passed):
        evidence["checks"].append({"name": name, "passed": bool(passed)})

    class Database:
        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            assert readonly
            yield connection

    try:
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE " + args.role)
            check(
                "restricted role, no RLS bypass",
                not await connection.fetchval(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ),
            )
            if not evidence["checks"][-1]["passed"]:
                raise RuntimeError("RLS role rejected")
            record = await IdentityRepository().find_actor_by_account(
                connection, workspace_external_id="demo-sales-workspace", account_code=args.account
            )
            if record is None:
                raise RuntimeError("Actor missing")
            actor = record.context
            await set_request_context(connection, actor)
            row = await connection.fetchrow(
                """SELECT r.id::text, r.conversation_id::text, r.status, r.business_context, r.model_ref,
                          m.structured_content AS answer
                     FROM agent.run r JOIN agent.message m ON m.source_run_id=r.id AND m.sender_type='assistant'
                    WHERE r.id=$1::uuid""",
                args.run_id,
            )
            if not row:
                raise RuntimeError("Completed visible run missing")
            check("successful run", row["status"] == "succeeded")
            context = row["business_context"]
            trace = context.get("inference_route")
            evidence["route"] = trace
            check(
                "one persisted assistant",
                await connection.fetchval(
                    "SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid AND sender_type='assistant'",
                    args.run_id,
                )
                == 1,
            )
            check(
                "one queued business job",
                await connection.fetchval("SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid", args.run_id) == 1,
            )
            attempts = await connection.fetch(
                """SELECT provider_code, status, http_status, error_code, operation_id::text,
                          request_snapshot->>'test_fault_injected' AS fault_injected,
                          request_snapshot->>'network_dispatch_suppressed' AS dispatch_suppressed
                     FROM agent.model_invocation WHERE run_id=$1::uuid AND record_kind='provider_attempt'
                     ORDER BY started_at""",
                args.run_id,
            )
            evidence["attempts"] = [dict(item) for item in attempts]
            if args.scenario == "normal":
                check(
                    "real platform result accepted",
                    trace and trace["provider"] == "agent_platform" and trace["fallback_reason"] is None,
                )
                check(
                    "one real platform HTTP 200",
                    len(attempts) == 1
                    and attempts[0]["provider_code"] == "agent_platform"
                    and attempts[0]["status"] == "succeeded"
                    and attempts[0]["http_status"] == 200
                    and attempts[0]["fault_injected"] == "false",
                )
                check(
                    "platform runtime IDs recorded", bool(trace and trace["platform_run"]["ids"].get("conversation_id"))
                )
            elif args.scenario == "off":
                check("original code path retained", trace is None)
                check(
                    "one real original HTTP 200",
                    len(attempts) == 1
                    and attempts[0]["provider_code"] == "senseaudio"
                    and attempts[0]["status"] == "succeeded"
                    and attempts[0]["http_status"] == 200,
                )
            else:
                controlled = args.scenario == "blocked"
                reason = "ControlledPlatformBlock" if controlled else "TimeoutError"
                check(
                    "same-run automatic fallback",
                    trace
                    and trace["provider"] == "senseaudio"
                    and trace["fallback_reason"] == reason,
                )
                check(
                    "platform failure then real original HTTP 200",
                    len(attempts) == 2
                    and attempts[0]["provider_code"] == "agent_platform"
                    and attempts[0]["status"] == ("failed" if controlled else "cancelled")
                    and attempts[0]["http_status"] is None
                    and attempts[0]["error_code"] == ("ControlledPlatformBlock" if controlled else "CancelledError")
                    and attempts[0]["fault_injected"] == ("true" if controlled else "false")
                    and attempts[0]["dispatch_suppressed"] == ("true" if controlled else "false")
                    and attempts[1]["provider_code"] == "senseaudio"
                    and attempts[1]["http_status"] == 200
                    and attempts[1]["status"] == "succeeded",
                )
                check(
                    "both attempts share operation",
                    len(attempts) == 2
                    and attempts[0]["operation_id"] == attempts[1]["operation_id"] == trace["operation_id"],
                )
            facts = await AgentFactsLoader(Database()).load(
                RunInput(args.run_id, row["conversation_id"], "", context["mode"], context.get("customer_id"), actor)
            )
            metrics = row["answer"].get("metrics", [])
            values = []
            for metric in metrics:
                raw = str(metric.get("value", "")).replace(",", "").strip()
                if re.fullmatch(r"\d+(?:\.0+)?(?:个|项|条)?", raw):
                    values.append(Decimal(re.sub(r"[个项条]$", "", raw)))
            check(
                "answer matches current actor-scoped open task aggregate",
                len(values) == 1 and values[0] == facts["summary"]["open_tasks"],
            )
            evidence["facts_verification"] = "same restricted actor aggregate at audit time; values redacted"
    finally:
        await connection.close()
    evidence["passed"] = all(item["passed"] for item in evidence["checks"])
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=lambda value: str(UUID(value)))
    parser.add_argument("--scenario", required=True, choices=("normal", "off", "blocked", "timeout-fallback"))
    parser.add_argument("--role", default="sales_app", choices=("sales_app", "sales_runtime"))
    parser.add_argument("--account", default="XS001")
    asyncio.run(run(parser.parse_args()))
