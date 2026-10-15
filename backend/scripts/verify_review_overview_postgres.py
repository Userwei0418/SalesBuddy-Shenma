# ruff: noqa: E402, E501, S108 -- Standalone opt-in fixture, fixed SQL, explicit private test socket.
"""Opt-in Review facts proof on an explicitly named disposable Unix socket."""

import argparse
import asyncio
import io
import json
import sys
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "database/tests"), str(ROOT / "database/scripts")]
from verify_runtime_grants_postgres import migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.review_overview import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(("/private/tmp/sb-review-pg.", "/tmp/sb-review-pg."))
    config = dict(host=str(socket), port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    name, role = "sb_review_overview_" + uuid4().hex[:12], "sb_review_reader_" + uuid4().hex[:12]
    c, checks = None, []
    try:
        await admin.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'")
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 240)
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        policy = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())["services"]["api"]
        for schema in policy["schemas"]:
            await c.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
        for table, privileges in policy["tables"].items():
            if await c.fetchval("SELECT to_regclass($1) IS NOT NULL", table):
                await c.execute(f'GRANT {",".join(privileges)} ON {table} TO "{role}"')
        for table, permissions in policy["columns"].items():
            for privilege, columns in permissions.items():
                await c.execute(f'GRANT {privilege}({",".join(columns)}) ON {table} TO "{role}"')
        for signature in policy["functions"]:
            if await c.fetchval("SELECT to_regprocedure($1) IS NOT NULL", signature):
                await c.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')
        await c.execute("SELECT security.reconcile_runtime_grants()")
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        actors, ids = {}, {}
        for label in ("own", "foreign"):
            ws, team, sales, manager, customer = [uuid4() for _ in range(5)]
            ids[label] = dict(ws=ws, team=team, sales=sales, manager=manager, customer=customer)
            await c.execute(
                "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)", ws, str(ws)
            )
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
            await c.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'TEST','Synthetic team')", team, ws
            )
            for uid, code, role_code in ((sales, "S", "sales"), (manager, "M", "supervisor")):
                await c.execute(
                    "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)",
                    uid,
                    ws,
                    code + uid.hex[:8],
                )
                await c.execute(
                    "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)",
                    ws,
                    uid,
                    role_code,
                    "team" if role_code == "supervisor" else "self",
                    team,
                )
                await c.execute(
                    "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) VALUES($1,$2,$3,$4)",
                    ws,
                    team,
                    uid,
                    role_code,
                )
                actors[label + "_" + code] = ActorContext(
                    workspace_id=str(ws),
                    user_id=str(uid),
                    role=role_code,
                    data_scope="team" if role_code == "supervisor" else "self",
                    team_ids=[str(team)],
                )
            await c.execute(
                "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,$3,$4,$4,$5)",
                customer,
                ws,
                label + " customer",
                sales,
                team,
            )
        anchor = await c.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)
        quarter = (anchor.month - 1) // 3 + 1
        # Same source set has a low-confidence >=70 row, a high-confidence 30
        # row, a known zero, lost retained probability, and missing forecast.
        examples = [
            dict(name="high30", p=30, stage="qualified", confidence="high", collection="100.01", recognized="500"),
            dict(name="low70", p=70, stage="proposal", confidence="low", collection="200.02", recognized="300.03"),
            dict(name="zero", p=70, stage="proposal", confidence="high", collection="0.00", recognized="0.00"),
            dict(
                name="lost",
                p=90,
                stage="negotiation",
                confidence="high",
                collection="9999",
                recognized="9999",
                status="lost",
            ),
            dict(name="missing", p=70, stage="proposal", confidence=None, collection=None, recognized=None),
        ]
        opportunity_ids = {}
        for label in ("own", "foreign"):
            spec = ids[label]
            await c.execute(
                "SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),"
                "set_config('app.role_code','sales',false),set_config('app.team_ids',$3,false)",
                str(spec["ws"]),
                str(spec["sales"]),
                str(spec["team"]),
            )
            for ex in examples:
                ident = uuid4()
                opportunity_ids[label + "_" + ex["name"]] = ident
                await c.execute("SELECT set_config('app.native_opportunity_create',$1,false)", str(ident))
                await c.execute(
                    """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
                  amount,currency,probability,stage_code,status,created_by_user_ref_id)
                  VALUES($1,$2,$3,$4,$5,$6,1000,'CNY',$7,$8,$9,$5)""",
                    ident,
                    spec["ws"],
                    spec["customer"],
                    ex["name"],
                    spec["sales"],
                    spec["team"],
                    ex["p"],
                    ex["stage"],
                    ex.get("status", "open"),
                )
                if ex["name"] != "missing":
                    await c.execute(
                        """INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence)
                      VALUES($1,$2,$3,$4,$5::text::numeric,$6::text::numeric,$7)""",
                        spec["ws"],
                        ident,
                        anchor.year,
                        quarter,
                        ex["collection"],
                        ex["recognized"],
                        ex["confidence"],
                    )
            for kind, value in (
                ("collection", "1000"),
                ("collection_ppl", "2000"),
                ("forecast_recognized", "3000"),
                ("recognized", "1"),
            ):
                await c.execute(
                    """INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end)
                  VALUES($1,$2,'person',$3,$4,$5::text::numeric,$3,'quarter',$6,($6::date+interval '3 months - 1 day')::date)""",
                    spec["ws"],
                    anchor.year,
                    spec["sales"],
                    kind,
                    value,
                    start,
                )
        await c.execute("SELECT set_config('app.native_opportunity_create','',false)")
        own = ids["own"]
        await c.execute(
            "SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),"
            "set_config('app.role_code','sales',false),set_config('app.team_ids',$3,false)",
            str(own["ws"]),
            str(own["sales"]),
            str(own["team"]),
        )
        changed = opportunity_ids["own_high30"]
        version = await c.fetchval(
            "UPDATE crm.opportunity SET probability=50,stage_code='solution' WHERE id=$1 RETURNING version_no", changed
        )
        await c.execute(
            """INSERT INTO crm.business_change(workspace_id,customer_id,opportunity_id,actor_user_ref_id,version_no,changes)
          VALUES($1,$2,$3,$4,$5,$6::jsonb)""",
            own["ws"],
            own["customer"],
            changed,
            own["sales"],
            version,
            [{"label": "赢单概率", "before": "30%", "after": "50%"}],
        )
        await c.execute("UPDATE crm.opportunity SET name=name WHERE id=$1", changed)
        form = await c.fetchval(
            "SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1"
        )
        visit = await c.fetchval(
            """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
          recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record,next_action)
          VALUES($1,$2,$3,$4,$5,$4,$6,'archived',clock_timestamp(),clock_timestamp(),'真实合成跟进','准备方案') RETURNING id""",
            own["ws"],
            own["customer"],
            None,
            own["sales"],
            own["team"],
            form,
        )
        await c.executemany(
            "INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3)",
            [(own["ws"], visit, changed), (own["ws"], visit, opportunity_ids["own_low70"])],
        )
        for state in ("completed", "pending_review", "deferred"):
            task = await c.fetchval(
                """INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,creator_team_id,
              due_at,status,completed_at,completion_note,customer_id,opportunity_id)
              VALUES($1,$2,$2,$3,$4,clock_timestamp()+interval '1 day',$5,
                CASE WHEN $5='completed' THEN clock_timestamp() END,'合成完成说明',$6,$7) RETURNING id""",
                own["ws"],
                state,
                own["sales"],
                own["team"],
                state,
                own["customer"],
                changed,
            )
            for person in (own["sales"], own["manager"]):
                await c.execute(
                    """INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,
                  assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'sales','owner')""",
                    own["ws"],
                    task,
                    person,
                    own["team"],
                )
        await c.execute("SET row_security=on")
        active = actors["own_S"]

        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False, isolation="read_committed"):
                async with c.transaction(readonly=readonly, isolation=isolation):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c, actor)
                    assert not await c.fetchval(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                    yield c

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: DB()
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=active)

        @app.exception_handler(WorkReportError)
        async def error(request, exc):
            return JSONResponse({"code": exc.code, "detail": exc.detail}, status_code=exc.status)

        @app.exception_handler(PermissionError)
        async def forbidden(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=403)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:

            async def get(endpoint="overview", status=200, **params):
                response = await client.get("/api/v1/review/" + endpoint, params={"anchor_date": str(anchor), **params})
                assert response.status_code == status, (endpoint, response.status_code, response.text)
                return response.json()

            overview = await get()
            card = {v["metric_code"]: v for v in overview["metrics"]}
            assert (
                card["forecast_collection"]["value"] is None
                and card["forecast_collection"]["known_subtotal"] == "100.01"
            )
            assert (
                card["collection_ppl"]["known_subtotal"] == "300.03"
                and card["collection_ppl"]["missing_amount_count"] == 1
            )
            assert (
                card["forecast_recognized"]["known_subtotal"] == "300.03"
                and card["forecast_recognized"]["target"]["value"] == "3000.00"
            )
            assert all(v["rate"] is None and v["baseline"]["status"] == "no_baseline" for v in card.values())
            checks.append(
                "real limited-role cards preserve missing/zero, qualification, tax identity and no fabricated baseline"
            )
            page = await get("opportunities", metric_code="collection_ppl", limit=2)
            assert page["total"] == 4 and len(page["items"]) == 2 and page["has_more"]
            page2 = await get("opportunities", metric_code="collection_ppl", limit=2, offset=2)
            assert {r["id"] for r in page["items"]}.isdisjoint(r["id"] for r in page2["items"])
            assert {r["id"] for r in page["items"] + page2["items"]} == {
                str(v) for k, v in opportunity_ids.items() if k.startswith("own_") and k != "own_lost"
            }
            checks.append("metric detail paginates same owner population without foreign/lost rows")
            await get(status=403, scope="member", member_id=str(ids["foreign"]["sales"]))
            await get(status=422, scope="self", team_id=str(ids["own"]["team"]))
            checks.append("cross-tenant selection denied and contradictory scope rejected")
            active = actors["own_M"]
            team = await get(scope="team", team_id=str(ids["own"]["team"]))
            assert (
                team["metrics"][1]["known_subtotal"] == "300.03"
                and team["metrics"][1]["target"]["status"] == "no_target"
            )
            team_counts = {r["code"]: r for r in team["activities"]}
            assert team_counts["completed_tasks"]["count"] == 1 and team_counts["pending_tasks"]["count"] == 2
            checks.append("authorized manager team uses owner money once and no fabricated team target")
            active = actors["own_S"]
            for activity in (
                "new_claimed_customers",
                "formal_visits",
                "new_opportunities",
                "lost_opportunities",
                "updated_opportunities",
                "completed_tasks",
                "pending_tasks",
            ):
                result = await get("activities", activity=activity)
                assert len(result["items"]) == result["known_total"]
                if activity == "formal_visits":
                    assert result["known_total"] == 1 and len(result["items"][0]["opportunities"]) == 2
                    assert result["items"][0]["recorded_at"] and result["items"][0]["next_action"] == "准备方案"
                if activity == "updated_opportunities":
                    assert result["known_total"] == 1 and result["items"][0]["event_count"] == 1
                    assert result["items"][0]["events"][0]["changes"][0]["after"] == "50%"
                if activity == "completed_tasks":
                    assert result["known_total"] == 1 and result["items"][0]["completion_note"] == "合成完成说明"
                if activity == "pending_tasks":
                    assert result["known_total"] == 2 and {r["status"] for r in result["items"]} == {
                        "pending_review",
                        "deferred",
                    }
            checks.append("all seven activity routes execute actual scoped SQL and count/detail shape")
            checks.append(
                "multi-opportunity visit once; real updates exclude insert/no-op; completed distinct from pending review"
            )
            stale = await get("opportunities", filter="stale")
            assert stale["available"] is True and stale["total"] == 0
            checks.append("new native opportunity without a visit is not yet stale at seven days")
            # Add an explicit zero for the previously missing quarter, then
            # a fresh request must see it; no snapshot cache masks source updates.
            await c.execute(
                """INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence)
              VALUES($1,$2,$3,$4,0,0,'low')""",
                ids["own"]["ws"],
                opportunity_ids["own_missing"],
                anchor.year,
                quarter,
            )
            fresh = await get()
            assert fresh["metrics"][1]["value"] == "300.03" and fresh["metrics"][1]["rate"] == "0.15001500"
            checks.append("refresh reflects real committed forecast update and precise effective denominator")
            output = {
                "status": "passed_isolated_postgres_asgi",
                "checks": checks,
                "check_count": len(checks),
                "sample": fresh,
                "scope": "synthetic private socket; trusted injected HTTP actor, real SQL ACL/RLS and source calculations; no production/model",
                "cleanup": "own database and role dropped in finally",
            }
            Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"status": output["status"], "check_count": len(checks), "output": args.output}))
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(verify(parser.parse_args()))
