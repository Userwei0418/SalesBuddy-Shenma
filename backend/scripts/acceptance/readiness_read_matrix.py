"""Read deployed API handlers under real RLS identities, with all transactions read-only.

This is a server-side read-path probe, not public HTTPS/login acceptance. No
sessions, jobs, model calls, credentials, or business content enter its output.
Only the explicitly named existing demonstration workspace may be inspected.
"""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx
from fastapi import FastAPI

from agent_provider_recheck import ReadOnlyDatabase
from sales_backend.api.dependencies import RequestIdentity, get_identity
from sales_backend.config import get_settings
from sales_backend.main import (
    assistant_router, business_router, collaboration_router, customer_assets_router,
    customers_router, fde_profile_router, profile_router, tasks_router,
)
from sales_backend.repositories.identity import IdentityRepository

ACCOUNTS = {"XS001": "sales", "ZJ001": "supervisor", "ZJL001": "manager",
            "FDE001": "fde", "FDEL001": "fde_lead"}
COMMON = ["/assistant/home", "/tasks/overview", "/tasks?page_size=20",
          "/customer-assets/map", "/opportunities?page_size=20", "/directory/members"]


async def main(output):
    settings = get_settings()
    db = ReadOnlyDatabase(settings)
    await db.connect()
    app = FastAPI()
    # Reuse real route validation/serialization but omit audit/log middleware:
    # even authentication/audit writes are outside this diagnostic's purpose.
    for router in (assistant_router, business_router, collaboration_router,
                   customer_assets_router, customers_router, fde_profile_router,
                   profile_router, tasks_router):
        app.include_router(router)
    app.state.database, app.state.settings = db, settings
    results = []
    try:
        for account, role in ACCOUNTS.items():
            async with db.connection() as connection:
                row = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor($1,$2,$3)",
                    "demo-sales-workspace", account, role)
                if row is None:
                    results.append({"account": account, "status": "actor_unavailable"})
                    continue
                profile = IdentityRepository._actor(row)
            identity = RequestIdentity(profile.context, "diagnostic-no-session", profile,
                                       auth_method="password", must_change_password=False)

            async def resolved_identity():
                return identity

            app.dependency_overrides[get_identity] = resolved_identity
            paths = COMMON + (["/fde/dashboard?scope=self", "/fde/profile?scope=self"]
                              if role.startswith("fde") else
                              ["/profile/performance?scope=self", "/profile/scope-options",
                               "/dashboard?personal=true"])
            if role == "fde_lead":
                paths += ["/fde/dashboard?scope=team", "/fde/scope-options"]
            if role in {"supervisor", "manager"}:
                paths += ["/profile/performance?scope=" + ("department" if role == "manager" else "team")]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://readiness.local", timeout=20) as client:
                async def read(path, phase):
                    start = monotonic()
                    record = {"account": account, "role": role, "path": path, "phase": phase}
                    try:
                        async with asyncio.timeout(20):
                            response = await client.get("/api/v1" + path)
                        record.update(status=response.status_code, bytes=len(response.content))
                    except Exception as exc:
                        record.update(status=None, error_type=type(exc).__name__)
                    record["ms"] = round((monotonic() - start) * 1000, 1)
                    results.append(record)
                for path in paths:
                    await read(path, "sequential")
                # Bounded concurrent reads, retaining a single trusted actor.
                await asyncio.gather(*(read(path, "parallel-3") for path in paths[:3]))
            print(json.dumps({"account": account, "requests": len(paths) + 3}), flush=True)
    finally:
        await db.close()
    timings = sorted(row["ms"] for row in results if "ms" in row)
    report = {"created_at": datetime.now(UTC).isoformat(), "workspace": "demo-sales-workspace",
              "business_writes": False, "authentication_tested": False,
              "public_network_tested": False, "measurements": results,
              "requests": len(results), "errors": sum(row["status"] != 200 for row in results),
              "p95_ms": timings[min(int(len(timings) * .95), len(timings) - 1)] if timings else None,
              "max_ms": max(timings) if timings else None}
    with output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "measurements"}), flush=True)
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(main(parser.parse_args().output))
